"""A22 evidence: active readers protected, released cache reclaimed, nothing outside root touched."""
import os
import time

import pytest

from paios_ingestion import contract
from paios_ingestion.cache import WorkCache


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
