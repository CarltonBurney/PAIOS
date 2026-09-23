"""Stage runner: every asset gets an asset_id and a structured status for every stage."""
import hashlib
import inspect
import json
import re

import pytest

import fixtures as fx
from paios_ingestion import IngestionFoundation, WorkCache, contract
from paios_ingestion.decoders import HeifDecoder, PdfDecoder, PillowDecoder
from paios_ingestion.detect import SignatureMediaTypeDetector
from paios_ingestion.hashing import ContentHashService
from paios_ingestion.metadata import OriginalMetadataExtractor
from paios_ingestion.registry import PriorityDecoderRegistry

UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
SCOPE = contract.Scope(tenant_id="example-tenant", workspace_id="example-workspace")
GATE = {  # the four acceptance-gate formats
    "jpeg": (fx.jpeg, "photo.jpg", "image/jpeg", 1),
    "png": (fx.png_rgba, "scan.png", "image/png", 1),
    "heic": (fx.heic, "IMG_0001.HEIC", "image/heic", 1),
    "pdf": (fx.pdf, "doc.pdf", "application/pdf", 2),
}


@pytest.fixture
def foundation(tmp_path):
    return IngestionFoundation(WorkCache(tmp_path / "cache"))


def mock_refs(result):
    """Labelled MOCK StorageRefs standing in for Packet 2's verified OneDrive uploads."""
    return {p.page_number: {"connector": "onedrive", "root_id": "mock-root",
                            "item_id": f"mock-item-{p.page_number}", "version": "mock-1",
                            "sha256": p.sha256, "byte_size": p.path.stat().st_size}
            for p in result.decode.pages}


@pytest.mark.parametrize("kind", GATE)
def test_gate_formats_complete(tmp_path, foundation, limits, kind):
    build, name, mime, pages = GATE[kind]
    source = build(tmp_path / name)
    before = source.read_bytes()
    result = foundation.prepare(source, name, limits)

    assert result.status == "completed", result.error
    assert UUID4.match(result.asset_id)
    assert [s.status for s in result.stages] == ["completed"] * 5
    assert result.detected_mime_type == mime
    assert result.original_sha256 == hashlib.sha256(before).hexdigest()
    assert source.read_bytes() == before  # original untouched
    assert len(result.decode.pages) == pages
    assert re.fullmatch(r"[0-9a-f]{16}", result.perceptual_hash["value"])

    report = json.dumps(result.to_dict())
    assert str(tmp_path) not in report  # no local paths leave the process

    media = result.to_normalized_media(SCOPE, mock_refs(result))  # schema-validated
    assert [p["page_number"] for p in media["pages"]] == list(range(1, pages + 1))
    assert media["metadata"]["extensions"]["paios.detect"]["hint_mismatch"] is False


def test_supplied_asset_id_is_kept(tmp_path, foundation, limits):
    asset_id = "11111111-1111-4111-8111-111111111111"
    result = foundation.prepare(fx.jpeg(tmp_path / "a.jpg"), None, limits, asset_id=asset_id)
    assert result.asset_id == asset_id


def test_misnamed_file_routes_by_signature(tmp_path, foundation, limits):
    result = foundation.prepare(fx.png_rgba(tmp_path / "a.png"), "photo.jpg", limits)
    assert result.detected_mime_type == "image/png"
    assert result.decode.metadata["extensions"]["paios.detect"]["hint_mismatch"] is True


@pytest.mark.parametrize("builder,code,failed_stage", [
    (lambda p: (p / "x.txt").write_bytes(b"hello") and p / "x.txt", "UNSUPPORTED_MEDIA", "detect"),
    (lambda p: fx.animated(p / "x.gif", "GIF"), "UNSUPPORTED_MEDIA", "decode"),
    (lambda p: fx.encrypted_pdf(p / "x.pdf"), "ENCRYPTED_MEDIA", "decode"),
    (lambda p: fx.png_header_only(p / "x.png", 20000, 20000), "LIMIT_EXCEEDED", "decode"),
])
def test_failures_are_structured(tmp_path, foundation, limits, builder, code, failed_stage):
    result = foundation.prepare(builder(tmp_path), "x", limits)
    assert result.status == "failed" and UUID4.match(result.asset_id)
    assert result.error["code"] == code
    contract.validate("Error", result.error)
    statuses = {s.stage: s.status for s in result.stages}
    assert statuses[failed_stage] == "failed"
    order = list(statuses)
    assert all(statuses[s] == "completed" for s in order[:order.index(failed_stage)])
    assert all(statuses[s] == "skipped" for s in order[order.index(failed_stage) + 1:])
    assert result.decode is None and result.perceptual_hash is None
    with pytest.raises(ValueError):
        result.to_normalized_media(SCOPE, {})


def test_storage_ref_hash_mismatch_is_integrity_failure(tmp_path, foundation, limits):
    result = foundation.prepare(fx.jpeg(tmp_path / "a.jpg"), None, limits)
    refs = mock_refs(result)
    refs[1]["sha256"] = "0" * 64
    with pytest.raises(contract.PipelineFailure) as excinfo:
        result.to_normalized_media(SCOPE, refs)
    assert excinfo.value.error["code"] == "INTEGRITY_FAILED"


def test_job_cache_released_after_verified_persistence(tmp_path, foundation, limits):
    result = foundation.prepare(fx.jpeg(tmp_path / "a.jpg"), None, limits)
    assert result.job.path.exists()
    assert result.job.mark_persisted() is True
    assert not result.job.path.exists()


@pytest.mark.parametrize("implementation,protocol", [
    (SignatureMediaTypeDetector, contract.MediaTypeDetector),
    (PriorityDecoderRegistry, contract.DecoderRegistry),
    (PillowDecoder, contract.MediaDecoder),
    (HeifDecoder, contract.MediaDecoder),
    (PdfDecoder, contract.MediaDecoder),
    (ContentHashService, contract.HashService),
    (OriginalMetadataExtractor, contract.MetadataExtractor),
])
def test_implements_issued_protocol_signatures(implementation, protocol):
    for name, member in vars(protocol).items():
        if name.startswith("_") or not callable(member):
            continue
        ours = inspect.signature(getattr(implementation, name))
        theirs = inspect.signature(member)
        assert list(ours.parameters) == list(theirs.parameters), name
    for attribute in getattr(protocol, "__annotations__", {}):
        assert hasattr(implementation(), attribute)
