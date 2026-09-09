"""HTTP surface: the four dashboard endpoints plus the static dashboard itself.

Serving the page and the API from one origin is deliberate — it keeps the browser's
same-origin rules satisfied without a CORS policy to get wrong, and it means the
front-end can address the API with a relative path.
"""

from __future__ import annotations

import json
import mimetypes
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .service import StatusService
from .store import ManualFieldError, parse_manual

# A body larger than this is not an edit-modal submission.
MAX_BODY_BYTES = 64 * 1024

MANUAL_PATH = re.compile(r"^/api/manual/([A-Za-z0-9_.-]+)$")


class CommandCenterHandler(BaseHTTPRequestHandler):
    """Routes one request. Instantiated per request by ThreadingHTTPServer."""

    server_version = "PaiosCommandCenter/0.1"

    # Set on the server instance by serve(); read through self.server.
    @property
    def service(self) -> StatusService:
        return self.server.service  # type: ignore[attr-defined]

    @property
    def static_root(self) -> Path:
        return self.server.static_root  # type: ignore[attr-defined]

    # ---- helpers ----------------------------------------------------------
    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The dashboard polls this endpoint; a cached response would freeze the UI.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status)

    def _read_json_body(self) -> Any:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Content-Length is not a number") from exc
        if length > MAX_BODY_BYTES:
            raise ValueError("request body too large")
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"body is not valid JSON: {exc}") from exc

    # ---- routing ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        path = self.path.split("?", 1)[0]

        if path == "/api/status":
            self._send_json(self.service.status())
        elif path == "/api/log":
            self._send_json(self.service.log())
        elif path == "/health":
            self._send_json({"status": "ok"})
        else:
            self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        path = self.path.split("?", 1)[0]

        if path == "/api/poll":
            self._send_json(self.service.poll(reason="manual"))
            return

        match = MANUAL_PATH.match(path)
        if not match:
            self._send_error_json(HTTPStatus.NOT_FOUND, f"no route for POST {path}")
            return

        try:
            payload = self._read_json_body()
            manual = parse_manual(payload)
        except (ValueError, ManualFieldError) as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return

        try:
            self.service.set_manual(match.group(1), manual)
        except KeyError:
            self._send_error_json(
                HTTPStatus.NOT_FOUND, f"unknown project {match.group(1)!r}"
            )
            return

        self._send_json({"ok": True, "manual": manual.to_json()})

    # ---- static -----------------------------------------------------------
    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path == "/" else path.lstrip("/")
        target = (self.static_root / relative).resolve()

        # resolve() collapses any '..' the client sent, so this comparison is what
        # actually keeps a request from reaching outside the dashboard directory.
        if not target.is_relative_to(self.static_root.resolve()) or not target.is_file():
            self._send_error_json(HTTPStatus.NOT_FOUND, f"no such path {path}")
            return

        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        # BaseHTTPRequestHandler logs every request to stderr; the poll loop would
        # bury anything worth reading.
        return


def make_server(
    service: StatusService,
    static_root: Path,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> ThreadingHTTPServer:
    """Build a server bound to `host`.

    The default is loopback, not 0.0.0.0: this dashboard has no authentication and
    reports the state of private repositories.
    """
    httpd = ThreadingHTTPServer((host, port), CommandCenterHandler)
    httpd.service = service  # type: ignore[attr-defined]
    httpd.static_root = static_root  # type: ignore[attr-defined]
    return httpd
