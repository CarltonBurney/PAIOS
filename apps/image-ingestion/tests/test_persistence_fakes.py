"""The Drive/Graph fakes model the provider behaviours the Packet 2 decisions rely on."""
import hashlib

import pytest

from paios_ingestion.persistence.fakes import (ConflictError, FakeDrive, FakeGraph, NotFoundError,
                                              TransientError, quick_xor_hash)


def test_drive_retry_with_pre_generated_id_is_safe():
    drive = FakeDrive()
    (file_id,) = drive.generate_ids(1)
    drive.faults.inject("create", "lost_response")
    with pytest.raises(TransientError):
        drive.create(file_id, name="a", parent="p", data=b"x")  # written, response lost
    meta = drive.create(file_id, name="a", parent="p", data=b"x")  # exact retry
    assert meta["sha256Checksum"] == hashlib.sha256(b"x").hexdigest()
    assert len(drive.list_children("p")) == 1  # no duplicate object
    with pytest.raises(ConflictError):
        drive.create(file_id, name="a", parent="p", data=b"y")  # same ID, other bytes


def test_drive_rejects_ids_it_did_not_generate():
    with pytest.raises(ValueError):
        FakeDrive().create("made-up", name="a", parent="p", data=b"x")


def test_drive_fail_before_writes_nothing():
    drive = FakeDrive()
    (file_id,) = drive.generate_ids(1)
    drive.faults.inject("create", "fail_before")
    with pytest.raises(TransientError):
        drive.create(file_id, name="a", parent="p", data=b"x")
    with pytest.raises(NotFoundError):
        drive.metadata(file_id)


def test_drive_content_restriction_is_not_immutability():
    drive = FakeDrive()
    (file_id,) = drive.generate_ids(1)
    original = drive.create(file_id, name="a", parent="p", data=b"x")["sha256Checksum"]
    drive.set_read_only(file_id, True)
    with pytest.raises(PermissionError):
        drive.update_content(file_id, b"tampered")
    drive.set_read_only(file_id, False)  # any editor can lift it (Drive docs)
    drive.update_content(file_id, b"tampered")
    assert drive.metadata(file_id)["sha256Checksum"] != original  # only detection remains


def test_graph_reports_no_sha256_and_pins_versions():
    graph = FakeGraph()
    item = graph.upload("t/w/ingestion/a/original/h", b"v1")
    assert set(item["file"]["hashes"]) == {"quickXorHash"}
    pinned = item["currentVersionId"]
    newer = graph.replace_content(item["id"], b"v2")  # changes between upload and verification
    assert graph.item(item["id"])["currentVersionId"] == newer != pinned
    assert graph.download(item["id"], pinned) == b"v1"  # the pinned version, not latest
    graph.prune_version(item["id"], pinned)
    with pytest.raises(NotFoundError):
        graph.download(item["id"], pinned)  # missing version fails; never falls back to latest


def test_graph_conflict_behavior_and_faults():
    graph = FakeGraph()
    graph.upload("p", b"a")
    with pytest.raises(ConflictError):
        graph.upload("p", b"b")
    graph.faults.inject("upload", "lost_response")
    with pytest.raises(TransientError):
        graph.upload("q", b"c")
    assert graph.item_by_path("q")["size"] == 1  # written despite the lost response


def test_quick_xor_hash():
    assert quick_xor_hash(b"") == "AAAAAAAAAAAAAAAAAAAAAAAAAAA="
    assert quick_xor_hash(b"a") != quick_xor_hash(b"b")
    assert len(quick_xor_hash(b"x" * 1000)) == 28
