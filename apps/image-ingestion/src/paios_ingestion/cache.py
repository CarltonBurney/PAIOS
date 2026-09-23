"""Local temp/cache: isolated per-job directories under a TTL and size budget.

Local storage is working space only. Job directories are deleted once their
output is verified as persisted and no reader holds them; failed work is kept
for the diagnostic TTL. Nothing outside the cache root is ever deleted, and
the root may not sit inside a protected (e.g. OneDrive-synced) folder.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .errors import failure

DIAGNOSTIC_TTL_SECONDS = 24 * 60 * 60
TEST_BUDGET_BYTES = 5 * 1024 ** 3
_PREFIX = "job-"
_STATE = ".state.json"
_READERS = ".readers"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _tree_size(path: Path) -> int:
    total = 0
    for dirpath, _, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except FileNotFoundError:
                pass
    return total


@dataclass
class SweepReport:
    deleted: list[str] = field(default_factory=list)
    bytes_freed: int = 0
    active_skipped: list[str] = field(default_factory=list)
    bytes_in_use: int = 0


class JobDirectory:
    def __init__(self, cache: "WorkCache", path: Path):
        self.cache = cache
        self.path = path
        self.job_id = path.name[len(_PREFIX):]

    @property
    def output_dir(self) -> Path:
        return self.path / "out"

    def _state(self) -> dict:
        try:
            return json.loads((self.path / _STATE).read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write_state(self, **updates) -> None:
        state = {**self._state(), **updates}
        tmp = self.path / (_STATE + ".tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(self.path / _STATE)

    def active_readers(self) -> list[Path]:
        readers = []
        for marker in (self.path / _READERS).glob("*"):
            pid = int(marker.name.split("-", 1)[0]) if marker.name.split("-", 1)[0].isdigit() else -1
            if pid > 0 and _pid_alive(pid):
                readers.append(marker)
            else:
                marker.unlink(missing_ok=True)  # stale marker from a dead process
        return readers

    @contextmanager
    def reader(self):
        """Protect this directory from cleanup while the caller uses its files."""
        marker = self.path / _READERS / f"{os.getpid()}-{uuid.uuid4().hex}"
        marker.touch()
        try:
            yield self
        finally:
            marker.unlink(missing_ok=True)
            if self._state().get("persisted"):
                self.cache._delete(self)

    def mark_persisted(self) -> bool:
        """Call only after remote persistence is verified. Returns True if deleted now."""
        self._write_state(persisted=True, released_at=time.time())
        return self.cache._delete(self)

    def mark_failed(self) -> None:
        """Keep bytes for diagnosis; the sweep removes them after the TTL."""
        self._write_state(failed=True, released_at=time.time())


class WorkCache:
    def __init__(self, root: Path, *, ttl_seconds: int = DIAGNOSTIC_TTL_SECONDS,
                 budget_bytes: int = TEST_BUDGET_BYTES, protected_roots: tuple[Path, ...] = ()):
        self.root = Path(root).resolve()
        for protected in protected_roots:
            protected = Path(protected).resolve()
            if self.root == protected or protected in self.root.parents or self.root in protected.parents:
                raise ValueError("cache root must not overlap a protected storage root")
        self.ttl_seconds = ttl_seconds
        self.budget_bytes = budget_bytes
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def new_job(self) -> JobDirectory:
        path = self.root / f"{_PREFIX}{uuid.uuid4()}"
        path.mkdir(mode=0o700)
        (path / _READERS).mkdir()
        (path / "out").mkdir()
        job = JobDirectory(self, path)
        job._write_state(created_at=time.time())
        return job

    def jobs(self) -> list[JobDirectory]:
        return [JobDirectory(self, p) for p in sorted(self.root.iterdir())
                if p.name.startswith(_PREFIX) and p.is_dir() and not p.is_symlink()]

    def _delete(self, job: JobDirectory) -> bool:
        if job.path.parent != self.root or job.path.is_symlink():
            raise ValueError("refusing to delete outside the cache root")
        if job.active_readers():
            return False
        shutil.rmtree(job.path, ignore_errors=True)
        return not job.path.exists()

    def sweep(self, now: float | None = None) -> SweepReport:
        now = time.time() if now is None else now
        report = SweepReport()
        survivors = []
        for job in self.jobs():
            state = job._state()
            if job.active_readers():
                report.active_skipped.append(job.job_id)
                report.bytes_in_use += _tree_size(job.path)
                continue
            reference = state.get("released_at", state.get("created_at", job.path.stat().st_mtime))
            if state.get("persisted") or now - reference > self.ttl_seconds:
                self._remove(job, report)
            else:
                survivors.append((reference, job))
        # Over budget: evict the oldest released, inactive jobs first.
        usage = report.bytes_in_use + sum(_tree_size(j.path) for _, j in survivors)
        for _, job in sorted(survivors, key=lambda item: item[0]):
            if usage <= self.budget_bytes:
                break
            if "released_at" not in job._state():
                continue  # still being produced by a live job
            size = _tree_size(job.path)
            if self._remove(job, report):
                usage -= size
        return report

    def _remove(self, job: JobDirectory, report: SweepReport) -> bool:
        size = _tree_size(job.path)
        if self._delete(job):
            report.deleted.append(job.job_id)
            report.bytes_freed += size
            return True
        return False

    def usage(self) -> int:
        return sum(_tree_size(j.path) for j in self.jobs())

    def ensure_capacity(self, needed_bytes: int) -> None:
        """Enforce the budget before writing: sweep, then fail visibly if still short."""
        if self.usage() + needed_bytes <= self.budget_bytes:
            return
        self.sweep()
        usage = self.usage()
        if usage + needed_bytes > self.budget_bytes:
            raise failure("LIMIT_EXCEEDED", "storage", "Local cache budget exhausted",
                          retryable=True, retry_after_seconds=60,
                          details=[("cache_budget_bytes", f"{usage + needed_bytes} > {self.budget_bytes}")])
