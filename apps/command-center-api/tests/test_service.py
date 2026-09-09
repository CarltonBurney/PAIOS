from __future__ import annotations

from paios_command_center.collectors import CollectorError
from paios_command_center.config import parse_project
from paios_command_center.service import StatusService
from paios_command_center.store import ManualState, Store

GITHUB_OK = {
    "available": True,
    "repos": ["owner/repo"],
    "total_open_issues": 4,
    "last_pushed_at": "2026-09-01T00:00:00Z",
    "primary_repo_url": "https://github.com/owner/repo",
}


def build_service(tmp_path, github=None, perplexity=None):
    projects = {
        "paios": parse_project(
            "paios", {"name": "PAIOS", "color": "primary", "repos": ["owner/repo"]}
        ),
        "docs": parse_project("docs", {"name": "Docs", "color": "gold"}),
    }
    return StatusService(
        projects,
        Store(tmp_path / "state.json"),
        github_collector=github or (lambda repos: dict(GITHUB_OK)),
        perplexity_collector=perplexity or (lambda pid: {"available": False}),
    )


def test_status_before_any_poll_has_no_timestamp_but_lists_projects(tmp_path):
    status = build_service(tmp_path).status()
    assert status["generated_at"] is None
    assert list(status["projects"]) == ["paios", "docs"]


def test_poll_records_github_results_and_a_timestamp(tmp_path):
    service = build_service(tmp_path)
    result = service.poll(reason="startup")

    assert result["errors"] == []
    github = result["status"]["projects"]["paios"]["github"]
    assert github["available"] is True
    assert github["total_open_issues"] == 4
    assert result["status"]["generated_at"] is not None


def test_project_without_repos_is_not_polled_for_github(tmp_path):
    calls: list[tuple[str, ...]] = []

    def github(repos):
        calls.append(repos)
        return dict(GITHUB_OK)

    service = build_service(tmp_path, github=github)
    service.poll()

    assert calls == [("owner/repo",)]
    docs = service.status()["projects"]["docs"]["github"]
    # No repos configured, so the dashboard shows no "unavailable" banner either.
    assert docs == {"available": False, "repos": []}


def test_one_failing_collector_does_not_block_the_others(tmp_path):
    projects = {
        "a": parse_project("a", {"name": "A", "repos": ["owner/a"]}),
        "b": parse_project("b", {"name": "B", "repos": ["owner/b"]}),
    }

    def github(repos):
        if repos == ("owner/a",):
            raise CollectorError("rate limited")
        return dict(GITHUB_OK)

    service = StatusService(
        projects,
        Store(tmp_path / "state.json"),
        github_collector=github,
        perplexity_collector=lambda pid: {"available": False},
    )
    result = service.poll()

    assert result["errors"] == ["a: rate limited"]
    assert result["status"]["projects"]["b"]["github"]["available"] is True


def test_failed_poll_falls_back_to_the_last_good_snapshot(tmp_path):
    state = {"fail": False}

    def github(repos):
        if state["fail"]:
            raise CollectorError("unreachable")
        return dict(GITHUB_OK)

    service = build_service(tmp_path, github=github)
    service.poll()
    state["fail"] = True
    result = service.poll()

    github_view = result["status"]["projects"]["paios"]["github"]
    # The card keeps its numbers rather than blanking out, which is what the
    # dashboard's "showing last known state" banner promises.
    assert github_view["total_open_issues"] == 4
    assert result["errors"] == ["paios: unreachable"]


def test_perplexity_snapshot_is_never_reported_as_fresh(tmp_path):
    live = {"available": True, "session_count": 7, "recent_sessions": [{"title": "s"}]}
    service = build_service(tmp_path, perplexity=lambda pid: dict(live))
    service.poll()

    view = service.status()["projects"]["paios"]["perplexity"]
    assert view["session_count"] == 7
    assert view["available"] is False


def test_manual_edits_survive_a_poll(tmp_path):
    service = build_service(tmp_path)
    service.set_manual("paios", ManualState(status="blocked", phase="Phase 2"))
    service.poll()

    manual = service.status()["projects"]["paios"]["manual"]
    assert manual["status"] == "blocked"
    assert manual["phase"] == "Phase 2"


def test_editing_an_unknown_project_is_rejected(tmp_path):
    service = build_service(tmp_path)
    try:
        service.set_manual("ghost", ManualState())
    except KeyError as exc:
        assert "ghost" in str(exc)
    else:
        raise AssertionError("expected KeyError for an unregistered project")


def test_poll_writes_a_log_entry_carrying_its_errors(tmp_path):
    def github(repos):
        raise CollectorError("boom")

    service = build_service(tmp_path, github=github)
    service.poll(reason="startup")

    entry = service.log()[0]
    assert entry["reason"] == "startup"
    assert entry["ok"] is False
    assert entry["errors"] == ["paios: boom"]
