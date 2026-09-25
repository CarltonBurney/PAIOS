"""HTTP server for the control plane.

    python -m paios.serve

Transport only. Every decision about what an endpoint means lives in
:mod:`paios.http_api`; this module opens a socket, hands bytes to
:meth:`GovernanceApi.dispatch`, and writes the answer back.

Binds to loopback by default. The surface resolves entitlements from a bearer
token, but a governance kernel reachable from the network on first run is not a
default worth shipping — an operator who wants that sets PAIOS_HTTP_HOST and,
by then, has had reason to think about the token too.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .audit import InMemoryAuditSink, JsonlAuditSink
from .config import Settings
from .control_plane import ControlPlane
from .http_api import MAX_BODY_BYTES, GovernanceApi, TokenAuthenticator
from .tools import ToolRegistry

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8081  # 8080 is the Command Center's.

log = logging.getLogger("paios.serve")


def build_api(settings: Settings | None = None) -> GovernanceApi:
    """Assemble the API from configuration.

    A document that fails to load is not swallowed here: the exception
    propagates and the process refuses to start, rather than starting and
    serving a control plane with no policies in it.
    """
    settings = settings or Settings.from_env()

    # The in-memory sink is what makes /api/governance/audit answerable. When a
    # durable path is configured the JSONL sink is authoritative and the
    # in-memory one is a read-through window over this process's own events;
    # the endpoint then reports only what this process recorded, which is why
    # it says so rather than implying it holds the whole history.
    memory_sink = InMemoryAuditSink()
    sink: object = memory_sink
    if settings.audit_path is not None:
        sink = _TeeAuditSink(JsonlAuditSink(settings.audit_path), memory_sink)

    control_plane = ControlPlane(
        audit_sink=sink,  # type: ignore[arg-type]
        environment=settings.environment,
    )

    # A failed registry load does not stop the process — policy and risk still
    # govern, and refusing to start would take the whole surface down over a
    # document the pipeline does not need. But the failure is carried through to
    # /health rather than looking like an absent registry.
    registry: ToolRegistry | None = None
    registry_error: str | None = None
    try:
        registry = ToolRegistry.from_file(settings.tool_registry_path)
    except Exception as exc:  # noqa: BLE001 - reported by /health as unavailable
        log.exception("tool registry failed to load; continuing without it")
        registry_error = (
            f"tool registry at {settings.tool_registry_path} failed to load: {exc}"
        )

    return GovernanceApi(
        control_plane=control_plane,
        tool_registry=registry,
        authenticator=_authenticator_from_env(),
        audit_sink=memory_sink,
        tool_registry_error=registry_error,
    )


class _TeeAuditSink:
    """Writes each event to every sink. Durable first, so a crash between the
    two loses the queryable copy rather than the record of account."""

    def __init__(self, *sinks: object) -> None:
        self._sinks = sinks

    def record(self, event: object) -> None:
        for sink in self._sinks:
            sink.record(event)  # type: ignore[attr-defined]


def _authenticator_from_env() -> TokenAuthenticator:
    roles = os.environ.get("PAIOS_HTTP_ROLES", "")
    return TokenAuthenticator(
        # Read from the environment and never logged or returned. Only whether
        # one is configured appears in any response.
        token=os.environ.get("PAIOS_HTTP_TOKEN") or None,
        subject=os.environ.get("PAIOS_HTTP_SUBJECT", "command-center"),
        roles=frozenset(r.strip() for r in roles.split(",") if r.strip()),
        department=os.environ.get("PAIOS_HTTP_DEPARTMENT") or None,
    )


def make_handler(api: GovernanceApi) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "paios-control-plane"
        # Suppress the default "Python/3.x" advert; a governance endpoint need
        # not name its interpreter version to every caller.
        sys_version = ""

        def do_GET(self) -> None:  # noqa: N802 - name fixed by the base class
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802 - name fixed by the base class
            self._handle("POST")

        def _handle(self, method: str) -> None:
            parts = urlsplit(self.path)
            body = self._read_body()
            if body is None:
                return

            response = api.dispatch(
                method,
                parts.path,
                query=parts.query,
                body=body,
                authorization=self.headers.get("Authorization"),
            )
            self._write(response.status, response.to_json())

        def _read_body(self) -> bytes | None:
            raw = self.headers.get("Content-Length")
            if raw is None:
                return b""
            try:
                length = int(raw)
            except ValueError:
                self._write_error(400, "invalid_request", "bad Content-Length")
                return None
            if length < 0:
                self._write_error(400, "invalid_request", "bad Content-Length")
                return None
            if length > MAX_BODY_BYTES:
                # Refused before reading, so an oversized body cannot be used
                # to hold the process open.
                self._write_error(
                    413, "payload_too_large", f"body exceeds {MAX_BODY_BYTES} bytes"
                )
                return None
            return self.rfile.read(length)

        def _write(self, status: int, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _write_error(self, status: int, code: str, detail: str) -> None:
            body = json.dumps({"error": {"code": code, "detail": detail}})
            self._write(status, body.encode("utf-8"))

        def log_message(self, fmt: str, *args: object) -> None:
            log.info("%s - %s", self.address_string(), fmt % args)

    return Handler


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("PAIOS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    argv = argv if argv is not None else sys.argv[1:]
    if argv:
        log.error("no positional arguments; configure with PAIOS_HTTP_* env vars")
        return 2

    host = os.environ.get("PAIOS_HTTP_HOST", DEFAULT_HOST)
    port = int(os.environ.get("PAIOS_HTTP_PORT", str(DEFAULT_PORT)))

    api = build_api()
    server = ThreadingHTTPServer((host, port), make_handler(api))

    if not api.authenticator.configured:
        log.warning(
            "PAIOS_HTTP_TOKEN is not set: callers are unauthenticated, so every "
            "submission will be blocked by AUTHZ-001 and audited as such"
        )
    log.info("control plane listening on http://%s:%d", host, port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
