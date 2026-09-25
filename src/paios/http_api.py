"""HTTP surface over the control plane.

The Command Center is .NET; the control plane is Python. Rather than embed one
runtime in the other, the kernel answers HTTP and the Command Center is an
ordinary client. That keeps the governance kernel a single deployable with a
single audit trail, and keeps the .NET side free of any Python process
lifecycle.

Built on the standard library alone. The kernel declares no runtime
dependencies and this module does not change that — a governance component
whose supply chain is one interpreter is easier to vouch for than one that
pulls a web framework and its transitive tree.

Transport is separated from dispatch on purpose: :class:`GovernanceApi` maps a
method, path, query and body to a status and a payload with no socket in sight,
so its behaviour is unit-testable, and :mod:`paios.serve` wraps it in an HTTP
server for the integration path.

SECURITY BOUNDARY — identity is never taken from the request body. A caller
supplies *content*; entitlements come from the credential it presents, resolved
server-side. A body claiming ``"roles": ["platform_admin"]`` gets exactly the
roles its token was configured with, which may be none. The alternative — trust
the body — would make :func:`paios.routing.authorize` decorative, since it
gates on ``identity.authenticated``.

NO AUTOMATIC APPROVAL — the control plane's ``deny_by_default`` handler stays in
place behind HTTP. A request that policy or risk sends to a human is refused
here rather than approved by the absence of one. Approval is a separate
human-facing path; an HTTP 200 must never be able to stand in for it.
"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import parse_qs

from .audit import InMemoryAuditSink
from .control_plane import ControlPlane
from .models import Identity, Request
from .serialization import audit_event_to_dict, outcome_to_dict
from .tools import ToolRegistry

MAX_BODY_BYTES = 1 << 20  # 1 MiB. A prompt is text, not an upload.
MAX_AUDIT_PAGE = 500


class ApiError(Exception):
    """A failure that maps to a specific status and a stable error code."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.status = status
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ApiResponse:
    status: int
    payload: dict[str, Any]

    def to_json(self) -> bytes:
        return json.dumps(self.payload, sort_keys=True, default=str).encode("utf-8")


class Authenticator(Protocol):
    """Resolves a credential into an identity.

    Implementations must not consult the request body. In an Azure deployment
    this is replaced by Entra ID token validation; the shape stays the same.
    """

    @property
    def configured(self) -> bool:
        """Whether a credential is required at all."""
        ...

    def identify(self, authorization: str | None) -> Identity | None:
        """The identity for this credential, or None when it is not valid."""
        ...


@dataclass(frozen=True)
class TokenAuthenticator:
    """Bearer-token authentication against one configured principal.

    The token is compared with :func:`hmac.compare_digest` so a wrong guess
    takes the same time as a right one, and it is never echoed in a response —
    only *whether* one is configured is reported.

    With no token configured the authenticator is open, and returns an
    unauthenticated anonymous identity. That is deliberately fail-closed rather
    than permissive: ``authorize()`` rejects an unauthenticated identity, so
    submissions are blocked and *audited* as AUTHZ-001 instead of quietly
    succeeding. A misconfigured deployment refuses work; it does not run it
    ungoverned.
    """

    token: str | None = None
    subject: str = "anonymous"
    roles: frozenset[str] = frozenset()
    department: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def identify(self, authorization: str | None) -> Identity | None:
        if not self.token:
            return Identity(subject="anonymous", authenticated=False)

        presented = _bearer(authorization)
        if presented is None or not hmac.compare_digest(presented, self.token):
            return None

        return Identity(
            subject=self.subject,
            roles=frozenset(self.roles),
            authenticated=True,
            department=self.department,
        )


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


@dataclass
class GovernanceApi:
    """Routes one request to one handler. No transport, no globals."""

    control_plane: ControlPlane
    tool_registry: ToolRegistry | None = None
    authenticator: Authenticator = field(default_factory=TokenAuthenticator)
    audit_sink: InMemoryAuditSink | None = None

    def dispatch(
        self,
        method: str,
        path: str,
        *,
        query: str = "",
        body: bytes = b"",
        authorization: str | None = None,
    ) -> ApiResponse:
        try:
            return self._route(method, path.rstrip("/") or "/", query, body,
                               authorization)
        except ApiError as exc:
            return ApiResponse(
                exc.status, {"error": {"code": exc.code, "detail": exc.detail}}
            )

    # -- routing -------------------------------------------------------------

    def _route(
        self,
        method: str,
        path: str,
        query: str,
        body: bytes,
        authorization: str | None,
    ) -> ApiResponse:
        # /health answers before authentication so a probe never needs a
        # credential, and reports nothing a caller could not learn by
        # connecting.
        if path == "/health":
            self._only(method, "GET")
            return self._health()

        if path == "/api/governance/status":
            self._only(method, "GET")
            self._authenticate(authorization)
            return self._status()

        if path == "/api/governance/tools":
            self._only(method, "GET")
            self._authenticate(authorization)
            return self._tools()

        if path == "/api/governance/audit":
            self._only(method, "GET")
            self._authenticate(authorization)
            return self._audit(query)

        if path == "/api/governance/requests":
            self._only(method, "POST")
            identity = self._authenticate(authorization)
            return self._submit(identity, body)

        raise ApiError(404, "not_found", f"no route for {path}")

    @staticmethod
    def _only(method: str, allowed: str) -> None:
        if method.upper() != allowed:
            raise ApiError(
                405, "method_not_allowed", f"{method} not allowed; use {allowed}"
            )

    def _authenticate(self, authorization: str | None) -> Identity:
        identity = self.authenticator.identify(authorization)
        if identity is None:
            raise ApiError(
                401, "unauthenticated", "a valid bearer token is required"
            )
        return identity

    # -- handlers ------------------------------------------------------------

    def _health(self) -> ApiResponse:
        """Liveness plus whether the governance documents actually loaded.

        Reporting ``healthy`` while the policy set failed to load would be a
        lie the Command Center would faithfully repeat, so each component is
        probed and a failure degrades the whole.
        """
        checks: dict[str, Any] = {}
        healthy = True

        for name, probe in (
            ("policy", self._probe_policy),
            ("risk_model", self._probe_risk_model),
            ("tool_registry", self._probe_tools),
            ("provider", self._probe_provider),
        ):
            try:
                checks[name] = {"status": "healthy", "detail": probe()}
            except Exception as exc:  # noqa: BLE001 - reported, never raised
                healthy = False
                checks[name] = {"status": "unavailable", "detail": str(exc)}

        return ApiResponse(
            200 if healthy else 503,
            {
                "status": "healthy" if healthy else "degraded",
                "environment": self.control_plane.environment,
                "checks": checks,
            },
        )

    def _probe_policy(self) -> str:
        policies = self.control_plane.policy_engine.policy_set.policies
        return f"{len(policies)} policies loaded"

    def _probe_risk_model(self) -> str:
        model = self.control_plane.risk_engine.model
        return f"risk model '{model.name}' with {len(model.levels)} levels"

    def _probe_tools(self) -> str:
        if self.tool_registry is None:
            return "no tool registry attached"
        return f"{len(self.tool_registry)} tools registered"

    def _probe_provider(self) -> str:
        info = self.control_plane.provider.info
        return f"{info.name}/{info.model}"

    def _status(self) -> ApiResponse:
        """What the kernel is configured to be, for the dashboard card.

        Every number here is counted from a loaded document. Nothing is
        declared.
        """
        policies = self.control_plane.policy_engine.policy_set
        model = self.control_plane.risk_engine.model
        provider = self.control_plane.provider.info

        tools: dict[str, Any] = {"registered": None, "enabled": None}
        if self.tool_registry is not None:
            tools = {
                "registered": len(self.tool_registry),
                "enabled": len(self.tool_registry.enabled_tools()),
            }

        return ApiResponse(
            200,
            {
                "environment": self.control_plane.environment,
                "provider": {"name": provider.name, "model": provider.model},
                "policies": {
                    "name": policies.name,
                    "total": len(policies.policies),
                    "enabled": sum(1 for p in policies.policies if p.enabled),
                },
                "risk_model": {
                    "name": model.name,
                    "default_level": model.default_level.value,
                    "levels": len(model.levels),
                    "detectors": len(model.detectors),
                },
                "tools": tools,
                # Surfaced because it changes what the surface will do, and an
                # operator should not have to read the source to find out
                # whether anything can be approved.
                "approval_handler": _handler_name(
                    self.control_plane.approval_handler
                ),
                "authentication": (
                    "token" if self.authenticator.configured else "none"
                ),
            },
        )

    def _tools(self) -> ApiResponse:
        if self.tool_registry is None:
            return ApiResponse(200, {"tools": [], "detail": "no tool registry"})
        tools = sorted(
            (t.to_dict() for t in self.tool_registry),
            key=lambda t: (t.get("tool_id") or "", str(t.get("version") or "")),
        )
        return ApiResponse(200, {"tools": tools})

    def _audit(self, query: str) -> ApiResponse:
        if self.audit_sink is None:
            raise ApiError(
                501,
                "audit_not_queryable",
                "the configured audit sink does not support reads",
            )

        params = parse_qs(query)
        correlation_id = _single(params, "correlation_id")
        limit = _int_param(params, "limit", default=100, maximum=MAX_AUDIT_PAGE)

        events = (
            self.audit_sink.for_correlation(correlation_id)
            if correlation_id
            else self.audit_sink.events
        )
        window = events[-limit:]
        return ApiResponse(
            200,
            {
                "correlation_id": correlation_id,
                "total": len(events),
                "returned": len(window),
                "events": [audit_event_to_dict(e) for e in window],
            },
        )

    def _submit(self, identity: Identity, body: bytes) -> ApiResponse:
        payload = _parse_json_object(body)

        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ApiError(
                400, "invalid_request", "'content' must be a non-empty string"
            )

        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ApiError(400, "invalid_request", "'metadata' must be an object")

        correlation_id = payload.get("correlation_id")
        if correlation_id is not None and not isinstance(correlation_id, str):
            raise ApiError(
                400, "invalid_request", "'correlation_id' must be a string"
            )

        # Identity comes from the credential, not the body. Anything the caller
        # put under "identity" is ignored rather than merged, so there is no
        # partial-trust path to reason about.
        request = Request(content=content, identity=identity, metadata=metadata)
        outcome = self.control_plane.handle(request, correlation_id)

        # A refusal is a successful answer to "what does governance say", so it
        # is 200 with a disposition, not an HTTP error. The caller reads
        # `disposition`; a 4xx here would conflate "you asked wrongly" with
        # "you may not do this".
        return ApiResponse(200, outcome_to_dict(outcome))


def _handler_name(handler: object) -> str:
    """A readable name for a plain function or a callable instance."""
    return getattr(handler, "__name__", type(handler).__name__)


def _parse_json_object(body: bytes) -> dict[str, Any]:
    if len(body) > MAX_BODY_BYTES:
        raise ApiError(
            413, "payload_too_large", f"body exceeds {MAX_BODY_BYTES} bytes"
        )
    if not body:
        raise ApiError(400, "invalid_request", "a JSON body is required")
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError(400, "invalid_json", str(exc)) from exc
    if not isinstance(parsed, dict):
        raise ApiError(400, "invalid_request", "body must be a JSON object")
    return parsed


def _single(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    if not values:
        return None
    return values[0]


def _int_param(
    params: dict[str, list[str]], key: str, *, default: int, maximum: int
) -> int:
    raw = _single(params, key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ApiError(
            400, "invalid_request", f"'{key}' must be an integer"
        ) from exc
    if value < 1:
        raise ApiError(400, "invalid_request", f"'{key}' must be at least 1")
    return min(value, maximum)
