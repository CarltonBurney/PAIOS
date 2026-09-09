from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from paios_command_center.config import parse_project
from paios_command_center.server import make_server
from paios_command_center.service import StatusService
from paios_command_center.store import Store

GITHUB_OK = {
    "available": True,
    "repos": ["owner/repo"],
    "total_open_issues": 2,
    "last_pushed_at": "2026-09-01T00:00:00Z",
    "primary_repo_url": "https://github.com/owner/repo",
}


@pytest.fixture
def server(tmp_path):
    static_root = tmp_path / "wwwroot"
    static_root.mkdir()
    (static_root / "index.html").write_text("<h1>dashboard</h1>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("do not serve", encoding="utf-8")

    service = StatusService(
        {"paios": parse_project("paios", {"name": "PAIOS", "repos": ["owner/repo"]})},
        Store(tmp_path / "state.json"),
        github_collector=lambda repos: dict(GITHUB_OK),
        perplexity_collector=lambda pid: {"available": False},
    )

    # Port 0 lets the OS pick a free port, so the suite cannot collide with a
    # dashboard the developer already has running on 8000.
    httpd = make_server(service, static_root, host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    httpd.base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield httpd
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()


def get(server, path):
    with urllib.request.urlopen(f"{server.base_url}{path}", timeout=5) as response:
        return response.status, response.read()


def post(server, path, payload=None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    request = urllib.request.Request(
        f"{server.base_url}{path}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read())


def test_health_reports_ok(server):
    status, body = get(server, "/health")
    assert status == 200
    assert json.loads(body) == {"status": "ok"}


def test_status_endpoint_returns_the_project_map(server):
    status, body = get(server, "/api/status")
    assert status == 200
    assert list(json.loads(body)["projects"]) == ["paios"]


def test_status_is_not_cached(server):
    with urllib.request.urlopen(f"{server.base_url}/api/status", timeout=5) as response:
        assert response.headers["Cache-Control"] == "no-store"


def test_poll_endpoint_refreshes_and_reports_errors(server):
    status, body = post(server, "/api/poll")
    assert status == 200
    assert body["errors"] == []
    assert body["status"]["projects"]["paios"]["github"]["total_open_issues"] == 2


def test_log_endpoint_lists_polls_after_one_runs(server):
    post(server, "/api/poll")
    status, body = get(server, "/api/log")
    assert status == 200
    assert json.loads(body)[0]["reason"] == "manual"


def test_manual_edit_is_saved_and_visible_in_status(server):
    status, body = post(
        server, "/api/manual/paios", {"status": "active", "percent_complete": 60}
    )
    assert status == 200
    assert body["ok"] is True

    _, status_body = get(server, "/api/status")
    manual = json.loads(status_body)["projects"]["paios"]["manual"]
    assert manual["status"] == "active"
    assert manual["percent_complete"] == 60


def test_manual_edit_rejects_an_invalid_status(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(server, "/api/manual/paios", {"status": "vibes"})
    assert exc.value.code == 400


def test_manual_edit_rejects_an_unknown_project(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(server, "/api/manual/ghost", {"status": "active"})
    assert exc.value.code == 404


def test_root_serves_the_dashboard(server):
    status, body = get(server, "/")
    assert status == 200
    assert b"dashboard" in body


def test_unknown_path_is_a_404(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(server, "/nope.js")
    assert exc.value.code == 404


def test_traversal_outside_the_dashboard_directory_is_refused(server):
    # A client that walks up out of wwwroot must not reach the state file or
    # anything else on the host.
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(server, "/../secret.txt")
    assert exc.value.code == 404


def test_static_root_is_the_only_thing_served(server):
    root = Path(server.static_root)
    assert (root / "index.html").exists()
