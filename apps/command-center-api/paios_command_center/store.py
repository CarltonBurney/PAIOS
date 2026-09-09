"""Durable state for the dashboard.

Three things outlive a poll and so are written to disk:

* **manual overrides** — status, phase, next action, percent complete and notes,
  typed by a person in the edit modal. No collector may overwrite these.
* **last-known collector snapshots** — so a project whose source is unreachable
  renders its previous numbers behind an "unavailable" banner rather than blanks.
* **the poll log** — what ran, when, and what failed.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Enough log rows to explain a bad afternoon, few enough to stay a small file.
MAX_LOG_ENTRIES = 50

STATUSES = ("active", "planning", "blocked", "ongoing", "paused", "done")


class ManualFieldError(ValueError):
    """Raised when a manual edit is not something the dashboard can store."""


@dataclass
class ManualState:
    """The human-owned half of a project card."""

    status: str = "ongoing"
    phase: str = ""
    next_action: str = ""
    percent_complete: int | None = None
    notes: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def parse_manual(payload: Any) -> ManualState:
    """Validate an edit-modal submission.

    Rejecting here rather than coercing keeps a typo out of the file that every
    later poll reads back.
    """
    if not isinstance(payload, dict):
        raise ManualFieldError("body must be a JSON object")

    status = payload.get("status", "ongoing")
    if status not in STATUSES:
        raise ManualFieldError(f"status must be one of {list(STATUSES)}")

    percent = payload.get("percent_complete")
    if percent is not None:
        if isinstance(percent, bool) or not isinstance(percent, (int, float)):
            raise ManualFieldError("percent_complete must be a number or null")
        percent = int(percent)
        if not 0 <= percent <= 100:
            raise ManualFieldError("percent_complete must be between 0 and 100")

    text_fields = {}
    for key in ("phase", "next_action", "notes"):
        value = payload.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ManualFieldError(f"{key} must be a string")
        text_fields[key] = value

    return ManualState(status=status, percent_complete=percent, **text_fields)


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    """JSON-file persistence, written atomically so a crash cannot truncate state."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, Any] = {"manual": {}, "snapshots": {}, "log": []}
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            # A missing file is the normal first run. A corrupt one is rare enough
            # that starting clean beats refusing to boot the dashboard.
            return
        if isinstance(raw, dict):
            for key in ("manual", "snapshots", "log"):
                if key in raw:
                    self._data[key] = raw[key]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename: readers never observe a half-written file.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._data, handle, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # ---- manual overrides -------------------------------------------------
    def get_manual(self, project_id: str) -> ManualState:
        raw = self._data["manual"].get(project_id)
        if not isinstance(raw, dict):
            return ManualState()
        try:
            return parse_manual(raw)
        except ManualFieldError:
            return ManualState()

    def set_manual(self, project_id: str, manual: ManualState) -> None:
        self._data["manual"][project_id] = manual.to_json()
        self.save()

    # ---- last-known collector output --------------------------------------
    def get_snapshot(self, project_id: str, source: str) -> dict[str, Any] | None:
        return self._data["snapshots"].get(project_id, {}).get(source)

    def set_snapshot(self, project_id: str, source: str, value: dict[str, Any]) -> None:
        self._data["snapshots"].setdefault(project_id, {})[source] = value

    # ---- poll log ---------------------------------------------------------
    def append_log(self, reason: str, ok: bool, errors: list[str]) -> None:
        self._data["log"].insert(
            0, {"time": _utcnow(), "ok": ok, "reason": reason, "errors": errors}
        )
        del self._data["log"][MAX_LOG_ENTRIES:]

    def log(self) -> list[dict[str, Any]]:
        return list(self._data["log"])
