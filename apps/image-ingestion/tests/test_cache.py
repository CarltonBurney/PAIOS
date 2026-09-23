"""A22 evidence: active readers protected, released cache reclaimed, nothing outside root touched."""
import multiprocessing
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from paios_ingestion import contract
from paios_ingestion.cache import JobGone, WorkCache


def fill(job, size):
    (job.output_dir / "page-000001.png").write_bytes(b"\x00" * size)


def test_active_reader_blocks_deletion_until_released(tmp_path):
    cache = WorkCache(tmp_path / "c")
    job = cache.new_job()
    with job.reader():
        assert job.mark_persisted() is False  # reader active
        assert cache.sweep().active_skipped == [job.job_id]
        assert job.path.exists()
    assert not job.path.exists()  # deleted on reader release


def test_failed_job_kept_for_diagnostic_ttl(tmp_path):
    cache = WorkCache(tmp_path / "c", ttl_seconds=100)
    job = cache.new_job()
    fill(job, 10)
    job.mark_failed()
    assert cache.sweep().deleted == []
    report = cache.sweep(now=time.time() + 101)
    assert report.deleted == [job.job_id] and report.bytes_freed >= 10


def test_budget_evicts_oldest_released_first_and_skips_active(tmp_path):
    cache = WorkCache(tmp_path / "c", budget_bytes=2500)
    old, new, active = cache.new_job(), cache.new_job(), cache.new_job()
    for job in (old, new, active):
        fill(job, 1000)
    old.mark_failed()
    time.sleep(0.01)
    new.mark_failed()
    with active.reader():
        report = cache.sweep()
    assert report.deleted == [old.job_id]
    assert new.path.exists() and active.path.exists()


def test_ensure_capacity_fails_visibly(tmp_path):
    cache = WorkCache(tmp_path / "c", budget_bytes=1000)
    job = cache.new_job()
    with job.reader():
        fill(job, 900)
        with pytest.raises(contract.PipelineFailure) as excinfo:
            cache.ensure_capacity(500)
    error = excinfo.value.error
    contract.validate("Error", error)
    assert (error["code"], error["retryable"]) == ("LIMIT_EXCEEDED", True)


def test_stale_reader_marker_from_dead_process_is_ignored(tmp_path):
    cache = WorkCache(tmp_path / "c")
    job = cache.new_job()
    (job.path / ".readers" / "999999999-dead").touch()
    assert job.mark_persisted() is True


def test_cache_root_may_not_overlap_protected_storage(tmp_path):
    onedrive = tmp_path / "OneDrive"
    for root in (onedrive, onedrive / "cache", tmp_path):
        with pytest.raises(ValueError):
            WorkCache(root, protected_roots=(onedrive,))


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need elevated rights on Windows")
def test_sweep_never_follows_symlinks_outside_root(tmp_path):
    outside = tmp_path / "original.jpg"
    outside.write_bytes(b"synced original")
    cache = WorkCache(tmp_path / "c", ttl_seconds=0)
    os.symlink(tmp_path, cache.root / "job-symlink")
    job = cache.new_job()
    os.symlink(outside, job.output_dir / "link.jpg")
    job.mark_failed()
    cache.sweep(now=time.time() + 10)
    assert outside.read_bytes() == b"synced original"
    assert (cache.root / "job-symlink").is_symlink()


# --- Review fixes: budget on actual output, reader/deletion races, no PID signalling ---


def test_liveness_never_signals_processes(tmp_path, monkeypatch):
    def forbidden(*args):
        raise AssertionError("os.kill must not be used for liveness")

    monkeypatch.setattr(os, "kill", forbidden)
    cache = WorkCache(tmp_path / "c", ttl_seconds=0)
    job = cache.new_job()
    (job.path / ".readers" / "stale").touch()
    with job.reader():
        assert cache.sweep(now=time.time() + 10).active_skipped == [job.job_id]
    job.mark_failed()
    assert cache.sweep(now=time.time() + 10).deleted == [job.job_id]


def test_reader_after_deletion_is_refused(tmp_path):
    cache = WorkCache(tmp_path / "c")
    job = cache.new_job()
    assert job.mark_persisted() is True
    with pytest.raises(JobGone):
        with job.reader():
            pass


def test_concurrent_sweeps_never_delete_an_active_job(tmp_path):
    cache = WorkCache(tmp_path / "c", ttl_seconds=0, budget_bytes=0)
    job = cache.new_job()
    fill(job, 100)
    job.mark_failed()
    stop = threading.Event()
    errors = []

    def hammer():
        while not stop.is_set():
            try:
                cache.sweep(now=time.time() + 10)
                job.mark_persisted()
            except FileNotFoundError:
                pass  # state file removed after the reader finished
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    with job.reader():
        for t in threads:
            t.start()
        for _ in range(200):
            assert (job.output_dir / "page-000001.png").exists()
            time.sleep(0.001)
        stop.set()
        for t in threads:
            t.join()
        assert job.path.exists()
    assert not errors
    assert not job.path.exists()  # persisted, so removed when the reader released it


def _hold_reader(job_path, ready, release):
    from paios_ingestion.cache import JobDirectory, WorkCache as Cache
    cache = Cache(Path(job_path).parent)
    with JobDirectory(cache, Path(job_path)).reader():
        ready.set()
        release.wait(30)


def test_reader_in_another_process_protects_until_it_exits(tmp_path):
    cache = WorkCache(tmp_path / "c", ttl_seconds=0)
    job = cache.new_job()
    job.mark_failed()
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    proc = ctx.Process(target=_hold_reader, args=(str(job.path), ready, release))
    proc.start()
    try:
        assert ready.wait(60)
        assert cache.sweep(now=time.time() + 10).active_skipped == [job.job_id]
        assert job.path.exists()
        assert proc.is_alive()  # checking liveness never signals or terminates the reader
    finally:
        proc.kill()  # abrupt exit: the OS drops the lock, no cleanup code runs
        proc.join()
    assert cache.sweep(now=time.time() + 10).deleted == [job.job_id]


def test_concurrent_allocations_respect_budget(tmp_path):
    cache = WorkCache(tmp_path / "c", budget_bytes=10_000)
    jobs = [cache.new_job() for _ in range(8)]
    outcomes = []

    def write(job):
        try:
            with cache.allocate(3000):
                (job.output_dir / "page-000001.png").write_bytes(b"\x00" * 3000)
            outcomes.append("ok")
        except contract.PipelineFailure:
            outcomes.append("limit")

    readers = [job.reader() for job in jobs]
    for r in readers:
        r.__enter__()
    try:
        threads = [threading.Thread(target=write, args=(job,)) for job in jobs]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert cache.usage() <= 10_000
        assert outcomes.count("ok") >= 1 and "limit" in outcomes
    finally:
        for r in readers:
            r.__exit__(None, None, None)


def test_reader_arriving_during_deletion_gets_a_clean_refusal(tmp_path, monkeypatch):
    """Review R3 interleaving: pause deletion right before rmtree, then try to read."""
    import paios_ingestion.cache as cache_module
    cache = WorkCache(tmp_path / "c")
    job = cache.new_job()
    paused, resume = threading.Event(), threading.Event()
    real_rmtree = cache_module.shutil.rmtree

    def slow_rmtree(path, **kwargs):
        paused.set()
        resume.wait(10)
        real_rmtree(path, **kwargs)

    monkeypatch.setattr(cache_module.shutil, "rmtree", slow_rmtree)
    deleter = threading.Thread(target=job.mark_persisted)
    deleter.start()
    assert paused.wait(10)
    with pytest.raises(JobGone):
        with job.reader():
            pass
    resume.set()
    deleter.join()
    assert not job.path.exists()


def test_reader_first_blocks_mark_persisted_until_release(tmp_path):
    cache = WorkCache(tmp_path / "c")
    job = cache.new_job()
    fill(job, 10)
    with job.reader():
        results = []
        t = threading.Thread(target=lambda: results.append(job.mark_persisted()))
        t.start()
        t.join()
        assert results == [False] and (job.output_dir / "page-000001.png").exists()
    assert not job.path.exists()
