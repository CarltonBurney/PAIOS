from __future__ import annotations

import json

import pytest

from paios_command_center.store import (
    MAX_LOG_ENTRIES,
    ManualFieldError,
    ManualState,
    Store,
    parse_manual,
)


def test_manual_defaults_to_ongoing_with_empty_text():
    manual = parse_manual({})
    assert manual.status == "ongoing"
    assert manual.percent_complete is None
    assert manual.phase == ""


def test_manual_round_trips_every_field():
    manual = parse_manual(
        {
            "status": "blocked",
            "phase": "Phase 2",
            "next_action": "Unblock CI",
            "percent_complete": 40,
            "notes": "waiting on review",
        }
    )
    assert manual.to_json() == {
        "status": "blocked",
        "phase": "Phase 2",
        "next_action": "Unblock CI",
        "percent_complete": 40,
        "notes": "waiting on review",
    }


def test_unknown_status_is_rejected():
    with pytest.raises(ManualFieldError, match="status"):
        parse_manual({"status": "vibes"})


@pytest.mark.parametrize("percent", [-1, 101, "40", True])
def test_out_of_range_or_mistyped_percent_is_rejected(percent):
    # True is an int in Python; a boolean here is a bug in the caller, not 1%.
    with pytest.raises(ManualFieldError, match="percent_complete"):
        parse_manual({"percent_complete": percent})


def test_null_percent_clears_the_progress_bar():
    assert parse_manual({"percent_complete": None}).percent_complete is None


def test_non_object_body_is_rejected():
    with pytest.raises(ManualFieldError, match="JSON object"):
        parse_manual(["not", "an", "object"])


def test_manual_edits_survive_a_restart(tmp_path):
    path = tmp_path / "state.json"
    Store(path).set_manual("paios", ManualState(status="active", phase="Phase 1"))

    reopened = Store(path)
    assert reopened.get_manual("paios").status == "active"
    assert reopened.get_manual("paios").phase == "Phase 1"


def test_unknown_project_gets_default_manual_state(tmp_path):
    assert Store(tmp_path / "state.json").get_manual("nope").status == "ongoing"


def test_corrupt_state_file_starts_clean_rather_than_refusing_to_boot(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ truncated", encoding="utf-8")
    assert Store(path).log() == []


def test_snapshots_are_kept_per_source(tmp_path):
    store = Store(tmp_path / "state.json")
    store.set_snapshot("paios", "github", {"available": True, "total_open_issues": 3})
    store.set_snapshot("paios", "perplexity", {"available": True, "session_count": 9})
    store.save()

    reopened = Store(tmp_path / "state.json")
    assert reopened.get_snapshot("paios", "github")["total_open_issues"] == 3
    assert reopened.get_snapshot("paios", "perplexity")["session_count"] == 9
    assert reopened.get_snapshot("paios", "absent") is None


def test_log_is_newest_first_and_bounded(tmp_path):
    store = Store(tmp_path / "state.json")
    for i in range(MAX_LOG_ENTRIES + 10):
        store.append_log(f"poll-{i}", ok=True, errors=[])

    log = store.log()
    assert len(log) == MAX_LOG_ENTRIES
    assert log[0]["reason"] == f"poll-{MAX_LOG_ENTRIES + 9}"


def test_save_leaves_no_temporary_files_behind(tmp_path):
    store = Store(tmp_path / "state.json")
    store.append_log("poll", ok=True, errors=[])
    store.save()

    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["log"]
