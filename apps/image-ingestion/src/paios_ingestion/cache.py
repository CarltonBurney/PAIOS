"""Local temp/cache: isolated per-job directories under a TTL and size budget.

Local storage is working space only. Job directories are deleted once their
output is verified as persisted and no reader holds them; failed work is kept
for the diagnostic TTL. Nothing outside the cache root is ever deleted, and
the root may not sit inside a protected (e.g. OneDrive-synced) folder.

Concurrency: each reader holds an exclusive OS lock on its own marker file,
so a dead reader's lock disappears with its process. A per-job gate lock
serializes reader registration against deletion, and a root budget lock
serializes every byte allocation against the size budget.
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

from ._locks import lock, try_lock, unlock
from .errors import failure

DIAGNOSTIC_TTL_SECONDS = 24 * 60 * 60
TEST_BUDGET_BYTES = 5 * 1024 ** 3
_PREFIX = "job-"
_STATE = ".state.json"
_READERS = ".readers"
_GATE = ".gate"
_BUDGET = ".budget.lock"
_IO_RETRIES = 20


class JobGone(RuntimeError):
    """The job directory was deleted (or is being deleted) and cannot be read."""


def _tree_size(path: Path) -> int:
    total = 0
    for dirpath, _, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except FileNotFoundError:
                pass
    return total


@contextmanager
def _locked(path: Path, *, create: bool = False):
    try:
        handle = open(path, "a+b" if create else "r+b")
    except FileNotFoundError:
        raise JobGone(str(path.name)) from None
    try:
        lock(handle)
        try:
            yield handle
        finally:
            unlock(handle)
    finally:
        handle.close()


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

    def _state(self) -> dict | None:
        """None when the state file is missing, i.e. the job is gone or mid-deletion."""
        for attempt in range(_IO_RETRIES):
            try:
                return json.loads((self.path / _STATE).read_text())
            except FileNotFoundError:
                return None
            except json.JSONDecodeError:
                return {}
            except PermissionError:  # Windows: file briefly locked by a concurrent replace
                time.sleep(0.01 * (attempt + 1))
        raise TimeoutError("job state file stayed locked")

    def _write_state(self, **updates) -> None:
        state = {**(self._state() or {}), **updates}
        tmp = self.path / f"{_STATE}.{uuid.uuid4().hex}.tmp"
        tmp.write_text(json.dumps(state))
        for attempt in range(_IO_RETRIES):
            try:
                tmp.replace(self.path / _STATE)
                return
            except PermissionError:  # Windows: target open in another thread
                time.sleep(0.01 * (attempt + 1))
        tmp.unlink(missing_ok=True)
        raise TimeoutError("job state file stayed locked")

    def active_readers(self) -> int:
        """Count markers whose lock is currently held. Never modifies the directory.

        Safe without the gate: a marker created but not yet locked by a registering
        reader is simply not counted here, and is not removed. Removal of stale
        markers happens only in _reap_readers(), under the gate that registration
        also holds, so a half-registered reader can never be mistaken for a dead one.
        """
        return self._scan(reap=False)

    def _reap_readers(self) -> int:
        """Gate must be held. Remove markers of exited readers; return the live count."""
        return self._scan(reap=True)

    def _scan(self, *, reap: bool) -> int:
        active = 0
        for marker in (self.path / _READERS).glob("*"):
            try:
                handle = open(marker, "r+b")
            except FileNotFoundError:
                continue
            with handle:
                if not try_lock(handle):
                    active += 1
                    continue
                unlock(handle)
            if reap:
                try:
                    marker.unlink(missing_ok=True)  # stale: its reader has exited
                except PermissionError:
                    pass  # Windows: still open elsewhere; retried on the next check
        return active

    @contextmanager
    def reader(self):
        """Protect this directory from cleanup while the caller uses its files."""
        with _locked(self.path / _GATE):
            state = self._state()
            if state is None or state.get("deleted"):
                raise JobGone(self.job_id)
            marker_path = self.path / _READERS / uuid.uuid4().hex
            marker = open(marker_path, "xb")
            try:
                # Blocking: an ungated active_readers() probe may hold it for an instant.
                lock(marker)
            except BaseException:
                marker.close()
                marker_path.unlink(missing_ok=True)
                raise
        try:
            yield self
        finally:
            unlock(marker)
            marker.close()
            marker_path.unlink(missing_ok=True)
            if (self._state() or {}).get("persisted"):
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
        (path / _GATE).touch()
        job = JobDirectory(self, path)
        job._write_state(created_at=time.time())
        return job

    def jobs(self) -> list[JobDirectory]:
        return [JobDirectory(self, p) for p in sorted(self.root.iterdir())
                if p.name.startswith(_PREFIX) and p.is_dir() and not p.is_symlink()]

    def _delete(self, job: JobDirectory) -> bool:
        if job.path.parent != self.root or job.path.is_symlink():
            raise ValueError("refusing to delete outside the cache root")
        try:
            with _locked(job.path / _GATE):
                if job._reap_readers():
                    return False
                job._write_state(deleted=True)  # later readers see this under the gate
        except JobGone:
            return not job.path.exists()
        shutil.rmtree(job.path, ignore_errors=True)
        return not job.path.exists()

    def sweep(self, now: float | None = None) -> SweepReport:
        now = time.time() if now is None else now
        report = SweepReport()
        survivors = []
        for job in self.jobs():
            state = job._state()
            if state is None:
                continue  # being deleted by another process
            if job.active_readers():
                report.active_skipped.append(job.job_id)
                report.bytes_in_use += _tree_size(job.path)
                continue
            reference = state.get("released_at", state.get("created_at", job.path.stat().st_mtime))
            if state.get("persisted") or state.get("deleted") or now - reference > self.ttl_seconds:
                self._remove(job, report)
            else:
                survivors.append((reference, job))
        usage = report.bytes_in_use + sum(_tree_size(j.path) for _, j in survivors)
        for _, job in sorted(survivors, key=lambda item: item[0]):
            if usage <= self.budget_bytes:
                break
            if "released_at" not in (job._state() or {}):
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

    @contextmanager
    def allocate(self, needed_bytes: int):
        """Hold the budget lock while `needed_bytes` are written; fail visibly if over budget.

        The caller writes inside the with-block, so concurrent writers cannot
        both pass the check and jointly exceed the budget.
        """
        with _locked(self.root / _BUDGET, create=True):
            usage = self.usage()
            if usage + needed_bytes > self.budget_bytes:
                self.sweep()
                usage = self.usage()
            if usage + needed_bytes > self.budget_bytes:
                raise failure("LIMIT_EXCEEDED", "storage", "Local cache budget exhausted",
                              retryable=True, retry_after_seconds=60,
                              details=[("cache_budget_bytes",
                                        f"{usage + needed_bytes} > {self.budget_bytes}")])
            yield

    def ensure_capacity(self, needed_bytes: int) -> None:
        with self.allocate(needed_bytes):
            pass
