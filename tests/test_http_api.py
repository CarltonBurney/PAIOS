"""Tests for the control plane HTTP surface.

Split deliberately: most cases exercise :meth:`GovernanceApi.dispatch` directly
because that is where the behaviour lives, and a final group goes over a real
socket so the transport wrapper is not taken on trust either.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from paios.audit import InMemoryAuditSink
from paios.config import DEFAULT_TOOL_REGISTRY_PATH
from paios.control_plane import ControlPlane
from paios.http_api import (
    MAX_BODY_BYTES,
    GovernanceApi,
    TokenAuthenticator,
)
from paios.models import Approval, ApprovalState
from paios.serve import make_handler
from paios.tools import ToolRegistry

TOKEN = "test-token-value"


def build(*, token: str | None = TOKEN, registry: bool = True, **kwargs):
    sink = InMemoryAuditSink()
    tools = ToolRegistry.from_file(DEFAULT_TOOL_REGISTRY_PATH) if registry else None
    return GovernanceApi(
        control_plane=ControlPlane(audit_sink=sink, **kwargs),
        tool_registry=tools,
        authenticator=TokenAuthenticator(
            token=token, subject="command-center", roles=frozenset({"analyst"})
        ),
        audit_sink=sink,
    )


def auth(token: str = TOKEN) -> str:
    return f"Bearer {token}"


# -- health ------------------------------------------------------------------


def test_health_needs_no_credential():
    api = build()
    response = api.dispatch("GET", "/health")
    assert response.status == 200
    assert response.payload["status"] == "healthy"


def test_health_reports_each_component():
    api = build()
    checks = api.dispatch("GET", "/health").payload["checks"]
    assert set(checks) == {"policy", "risk_model", "tool_registry", "provider"}
    assert all(c["status"] == "healthy" for c in checks.values())


def test_health_degrades_when_a_component_fails():
    """A broken component must not be reported as healthy.

    The Command Center repeats whatever this says, so a lie here becomes a lie
    on the dashboard.
    """
    api = build()

    class Exploding:
        @property
        def policy_set(self):
            raise RuntimeError("policy document is unreadable")

    api.control_plane.policy_engine = Exploding()

    response = api.dispatch("GET", "/health")
    assert response.status == 503
    assert response.payload["status"] == "degraded"
    assert response.payload["checks"]["policy"]["status"] == "unavailable"
    assert "unreadable" in response.payload["checks"]["policy"]["detail"]
    # One failure degrades the whole, but the others still report truthfully.
    assert response.payload["checks"]["provider"]["status"] == "healthy"


def test_health_without_a_tool_registry_is_still_healthy():
    api = build(registry=False)
    response = api.dispatch("GET", "/health")
    assert response.status == 200
    assert response.payload["checks"]["tool_registry"]["status"] == "healthy"


# -- authentication ----------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer", "Bearer ", "Basic abc", TOKEN, "Bearer wrong-token"],
)
def test_unusable_credentials_are_refused(header):
    api = build()
    assert api.dispatch("GET", "/api/governance/status",
                        authorization=header).status == 401


def test_valid_credential_is_accepted():
    api = build()
    assert api.dispatch("GET", "/api/governance/status",
                        authorization=auth()).status == 200


def test_token_of_a_different_length_is_refused():
    """compare_digest raises on length mismatch if misused; it must not here."""
    api = build()
    response = api.dispatch(
        "GET", "/api/governance/status", authorization=auth("x")
    )
    assert response.status == 401
    assert response.payload["error"]["code"] == "unauthenticated"


def test_no_configured_token_leaves_reads_open():
    api = build(token=None)
    assert api.dispatch("GET", "/api/governance/status").status == 200
    assert api.dispatch("GET", "/api/governance/status").payload[
        "authentication"
    ] == "none"


def test_a_configured_token_is_never_echoed():
    api = build()
    body = json.dumps(
        api.dispatch(
            "GET", "/api/governance/status", authorization=auth()
        ).payload
    )
    assert TOKEN not in body


# -- status ------------------------------------------------------------------


def test_status_counts_come_from_loaded_documents():
    api = build()
    payload = api.dispatch(
        "GET", "/api/governance/status", authorization=auth()
    ).payload

    expected_policies = api.control_plane.policy_engine.policy_set.policies
    assert payload["policies"]["total"] == len(expected_policies)
    assert payload["policies"]["enabled"] == sum(
        1 for p in expected_policies if p.enabled
    )
    assert payload["tools"]["registered"] == len(api.tool_registry)
    assert payload["risk_model"]["levels"] == len(
        api.control_plane.risk_engine.model.levels
    )
    assert payload["environment"] == api.control_plane.environment


def test_status_reports_no_tool_counts_rather_than_zero():
    """An absent registry is unknown, not empty. Zero would be a fabrication."""
    api = build(registry=False)
    tools = api.dispatch(
        "GET", "/api/governance/status", authorization=auth()
    ).payload["tools"]
    assert tools == {"registered": None, "enabled": None}


def test_status_surfaces_the_approval_handler():
    api = build()
    payload = api.dispatch(
        "GET", "/api/governance/status", authorization=auth()
    ).payload
    assert payload["approval_handler"] == "deny_by_default"


# -- tools -------------------------------------------------------------------


def test_tools_are_listed_with_governance_fields():
    api = build()
    tools = api.dispatch(
        "GET", "/api/governance/tools", authorization=auth()
    ).payload["tools"]
    assert tools
    first = tools[0]
    for key in ("tool_id", "risk_level", "operation_type", "availability"):
        assert key in first


def test_tools_without_a_registry_says_so():
    api = build(registry=False)
    payload = api.dispatch(
        "GET", "/api/governance/tools", authorization=auth()
    ).payload
    assert payload["tools"] == []
    assert "no tool registry" in payload["detail"]


# -- submission --------------------------------------------------------------


def submit(api, body: dict, *, token: str | None = TOKEN):
    return api.dispatch(
        "POST",
        "/api/governance/requests",
        body=json.dumps(body).encode("utf-8"),
        authorization=auth(token) if token else None,
    )


def test_a_routine_request_is_executed():
    api = build()
    response = submit(api, {"content": "summarise the project roadmap"})
    assert response.status == 200
    assert response.payload["disposition"] == "auto_execute"
    assert response.payload["delivered"] is True
    assert response.payload["execution"]["output"]


def test_an_unauthenticated_submission_is_blocked_and_audited():
    """With no token configured the pipeline still runs, and refuses.

    This is the fail-closed property: a deployment that forgot to set a token
    does not execute ungoverned work, and the refusal leaves a record.
    """
    api = build(token=None)
    response = submit(api, {"content": "deploy to production"}, token=None)

    assert response.status == 200
    assert response.payload["disposition"] == "blocked"
    assert response.payload["delivered"] is False
    assert [v["policy_id"] for v in response.payload["violations"]] == ["AUTHZ-001"]
    assert response.payload["audit_ids"]

    stages = [e.stage.value for e in api.audit_sink.events]
    assert "blocked" in stages


def test_identity_in_the_body_is_ignored():
    """The security boundary: entitlements come from the credential only."""
    api = build()
    response = submit(
        api,
        {
            "content": "summarise the roadmap",
            "identity": {
                "subject": "attacker",
                "roles": ["platform_admin", "registry_admin"],
                "authenticated": True,
            },
        },
    )
    identity = response.payload["request"]["identity"]
    assert identity["subject"] == "command-center"
    assert identity["roles"] == ["analyst"]


#: Classifies L3 with a privacy domain, which policy sends to a human.
NEEDS_A_HUMAN = "export all employee salary records"

#: A governance change from a principal without the governance role. Policy
#: denies it outright, which is a different and stronger refusal than waiting
#: for an approver.
POLICY_DENIES = "change the governance policy thresholds"


def test_a_request_needing_a_human_is_not_approved_by_default():
    """No human present means no approval, over HTTP as everywhere else."""
    api = build()
    response = submit(api, {"content": NEEDS_A_HUMAN})

    assert response.payload["routing"]["requires_human"] is True
    assert response.payload["delivered"] is False
    assert response.payload["approval"]["state"] == ApprovalState.TIMED_OUT.value
    assert "no approval handler" in response.payload["approval"]["note"]


def test_an_injected_approval_handler_is_honoured():
    """The surface does not hard-code refusal — it declines to invent consent."""
    api = build(approval_handler=lambda _: Approval(
        state=ApprovalState.APPROVED, approver="alice", note="reviewed"
    ))
    response = submit(api, {"content": NEEDS_A_HUMAN})
    assert response.payload["approval"]["state"] == "approved"
    assert response.payload["delivered"] is True


def test_a_policy_deny_is_not_approvable():
    """Deny outranks approval, and HTTP does not introduce a way around it.

    Even with a handler that approves everything, a denied request never
    reaches execution and is never asked about — the block happens at routing,
    before any approval is sought.
    """
    api = build(approval_handler=lambda _: Approval(
        state=ApprovalState.APPROVED, approver="alice", note="reviewed"
    ))
    response = submit(api, {"content": POLICY_DENIES})

    assert response.payload["disposition"] == "blocked"
    assert response.payload["delivered"] is False
    assert response.payload["policy"]["decision"] == "deny"
    assert response.payload["approval"] is None
    stages = [e.stage.value for e in api.audit_sink.events]
    assert "approval_requested" not in stages
    assert "blocked" in stages


def test_correlation_id_threads_through_the_audit_trail():
    api = build()
    submit(api, {"content": "summarise the roadmap", "correlation_id": "cor-abc"})
    events = api.audit_sink.for_correlation("cor-abc")
    assert events
    assert {e.correlation_id for e in events} == {"cor-abc"}


@pytest.mark.parametrize(
    "body",
    [{}, {"content": ""}, {"content": "   "}, {"content": 42}, {"content": None}],
)
def test_content_must_be_a_non_empty_string(body):
    api = build()
    response = submit(api, body)
    assert response.status == 400
    assert response.payload["error"]["code"] == "invalid_request"


def test_metadata_must_be_an_object():
    api = build()
    response = submit(api, {"content": "hello", "metadata": ["not", "an", "object"]})
    assert response.status == 400


def test_malformed_json_is_rejected():
    api = build()
    response = api.dispatch(
        "POST",
        "/api/governance/requests",
        body=b"{not json",
        authorization=auth(),
    )
    assert response.status == 400
    assert response.payload["error"]["code"] == "invalid_json"


def test_a_json_array_body_is_rejected():
    api = build()
    response = api.dispatch(
        "POST", "/api/governance/requests", body=b"[1,2,3]", authorization=auth()
    )
    assert response.status == 400


def test_an_empty_body_is_rejected():
    api = build()
    response = api.dispatch(
        "POST", "/api/governance/requests", body=b"", authorization=auth()
    )
    assert response.status == 400


def test_an_oversized_body_is_rejected():
    api = build()
    response = api.dispatch(
        "POST",
        "/api/governance/requests",
        body=b"x" * (MAX_BODY_BYTES + 1),
        authorization=auth(),
    )
    assert response.status == 413


def test_submission_requires_a_credential_when_one_is_configured():
    api = build()
    assert submit(api, {"content": "hello"}, token=None).status == 401


# -- audit -------------------------------------------------------------------


def test_audit_filters_by_correlation_id():
    api = build()
    submit(api, {"content": "summarise the roadmap", "correlation_id": "cor-one"})
    submit(api, {"content": "summarise the roadmap", "correlation_id": "cor-two"})

    payload = api.dispatch(
        "GET",
        "/api/governance/audit",
        query="correlation_id=cor-one",
        authorization=auth(),
    ).payload
    assert payload["events"]
    assert {e["correlation_id"] for e in payload["events"]} == {"cor-one"}


def test_audit_limit_is_clamped():
    api = build()
    submit(api, {"content": "summarise the roadmap"})
    payload = api.dispatch(
        "GET", "/api/governance/audit", query="limit=100000", authorization=auth()
    ).payload
    assert payload["returned"] <= 500


@pytest.mark.parametrize("limit", ["0", "-1", "abc"])
def test_audit_rejects_a_bad_limit(limit):
    api = build()
    response = api.dispatch(
        "GET",
        "/api/governance/audit",
        query=f"limit={limit}",
        authorization=auth(),
    )
    assert response.status == 400


def test_audit_is_unavailable_without_a_queryable_sink():
    api = build()
    api.audit_sink = None
    response = api.dispatch(
        "GET", "/api/governance/audit", authorization=auth()
    )
    assert response.status == 501
    assert response.payload["error"]["code"] == "audit_not_queryable"


# -- routing edges -----------------------------------------------------------


def test_unknown_route_is_404():
    api = build()
    response = api.dispatch("GET", "/api/governance/nope", authorization=auth())
    assert response.status == 404
    assert response.payload["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/health"),
        ("POST", "/api/governance/status"),
        ("POST", "/api/governance/tools"),
        ("GET", "/api/governance/requests"),
    ],
)
def test_wrong_method_is_405(method, path):
    api = build()
    response = api.dispatch(method, path, authorization=auth())
    assert response.status == 405


def test_a_trailing_slash_resolves_to_the_same_route():
    api = build()
    assert api.dispatch("GET", "/health/").status == 200


# -- over a real socket ------------------------------------------------------


class Served:
    """A live server on an ephemeral port."""

    def __init__(self, api: GovernanceApi) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api))
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )

    def __enter__(self) -> str:
        self._thread.start()
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def get(url: str, token: str | None = TOKEN):
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def post(url: str, body: dict, token: str | None = TOKEN):
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST"
    )
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_live_health_over_http():
    with Served(build()) as base:
        status, payload = get(f"{base}/health", token=None)
    assert status == 200
    assert payload["status"] == "healthy"


def test_live_submission_over_http():
    with Served(build()) as base:
        status, payload = post(
            f"{base}/api/governance/requests",
            {"content": "summarise the project roadmap"},
        )
    assert status == 200
    assert payload["disposition"] == "auto_execute"
    assert payload["execution"]["model"]


def test_live_unauthenticated_request_is_401():
    with Served(build()) as base:
        status, payload = get(f"{base}/api/governance/status", token=None)
    assert status == 401
    assert payload["error"]["code"] == "unauthenticated"


def test_live_degraded_health_answers_503():
    api = build()

    class Exploding:
        @property
        def policy_set(self):
            raise RuntimeError("boom")

    api.control_plane.policy_engine = Exploding()
    with Served(api) as base:
        status, payload = get(f"{base}/health", token=None)
    assert status == 503
    assert payload["status"] == "degraded"


def test_live_response_is_json_and_uncached():
    with Served(build()) as base:
        request = urllib.request.Request(f"{base}/health")
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.headers["Content-Type"] == "application/json"
            assert response.headers["Cache-Control"] == "no-store"


def test_live_server_does_not_advertise_its_interpreter():
    with Served(build()) as base:
        request = urllib.request.Request(f"{base}/health")
        with urllib.request.urlopen(request, timeout=5) as response:
            assert "Python" not in response.headers.get("Server", "")
