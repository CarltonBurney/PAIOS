"""Stage runner: every asset gets an asset_id and a structured status for every stage."""
import hashlib
import inspect
import json
import re
import time

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
    assert [s.status for s in result.stages] == ["completed"] * 6
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


# --- Review fixes: output budget and deadline enforced by the stage runner ---

def test_cache_budget_applies_to_decoded_output(tmp_path, limits):
    # Review repro: a small JPEG completed with ~152 KB written under a 25 KB cap.
    from PIL import Image
    source = tmp_path / "noise.jpg"
    Image.effect_noise((256, 256), 64).convert("RGB").save(source, "JPEG", quality=30)
    assert source.stat().st_size < 25_000
    cache = WorkCache(tmp_path / "cache", budget_bytes=25_000)
    result = IngestionFoundation(cache).prepare(source, None, limits)
    assert result.status == "failed"
    assert (result.error["code"], result.error["stage"], result.error["retryable"]) == (
        "LIMIT_EXCEEDED", "storage", True)
    assert result.stage("decode").status == "failed"
    assert cache.usage() <= 25_000
    assert list(result.job.output_dir.iterdir()) == []


def test_deadline_expiring_during_processing_fails(tmp_path):
    from conftest import deadline, limited

    class SlowHasher(ContentHashService):
        def perceptual_hash(self, page):
            time.sleep(1.5)
            return super().perceptual_hash(page)

    foundation = IngestionFoundation(WorkCache(tmp_path / "cache"), hasher=SlowHasher())
    result = foundation.prepare(fx.jpeg(tmp_path / "a.jpg"), None, limited(deadline_utc=deadline(1)))
    assert result.status == "failed"
    assert result.error["code"] == "LIMIT_EXCEEDED"
    assert result.error["details"][0]["field"] == "deadline_utc"


# --- Review round 1: R2 multi-page growth, R4 admission/deadline enforcement ---

class CountingHasher(ContentHashService):
    def __init__(self, slow_second_sha=0.0, fail_phash=False):
        self.sha_calls, self.slow_second_sha, self.fail_phash = 0, slow_second_sha, fail_phash

    def sha256(self, source):
        self.sha_calls += 1
        if self.sha_calls == 2:
            time.sleep(self.slow_second_sha)
        return super().sha256(source)

    def perceptual_hash(self, page):
        if self.fail_phash:
            raise RuntimeError("boom")
        return super().perceptual_hash(page)


class SleepyDecoder(PillowDecoder):
    """Stands in for one long native decode call (module-level so the child can import it)."""
    decoder_id = "sleepy"

    def _decode(self, *args):
        time.sleep(60)


class CrashingDecoder(PillowDecoder):
    decoder_id = "crashing"

    def _decode(self, *args):
        import os
        os._exit(3)


def single(decoder):
    return PriorityDecoderRegistry([(1, decoder)])


@pytest.mark.parametrize("override,field", [
    ({"deadline_utc": "2000-01-01T00:00:00Z"}, "deadline_utc"),
    ({"max_source_bytes": 1}, "max_source_bytes"),
])
def test_admission_limits_checked_before_hashing(tmp_path, override, field):
    from conftest import limited
    hasher = CountingHasher()
    result = IngestionFoundation(WorkCache(tmp_path / "cache"), hasher=hasher).prepare(
        fx.png_rgba(tmp_path / "a.png"), None, limited(**override))
    assert hasher.sha_calls == 0
    assert result.stage("admission").status == "failed"
    assert (result.error["code"], result.error["stage"]) == ("LIMIT_EXCEEDED", "request")
    assert result.error["details"][0]["field"] == field
    assert all(s.status == "skipped" for s in result.stages[1:])


def test_slow_single_page_is_stopped_at_the_deadline(tmp_path):
    from conftest import deadline, limited
    foundation = IngestionFoundation(WorkCache(tmp_path / "cache"), registry=single(SleepyDecoder()))
    started = time.monotonic()
    result = foundation.prepare(fx.jpeg(tmp_path / "a.jpg"), None, limited(deadline_utc=deadline(2)))
    assert time.monotonic() - started < 15  # the child sleeps 60 s; it was killed
    assert result.status == "failed" and result.stage("decode").status == "failed"
    assert result.error["code"] == "LIMIT_EXCEEDED"
    assert result.error["details"][0]["field"] == "deadline_utc"
    assert list(result.job.output_dir.iterdir()) == []


def test_decoder_process_crash_is_a_structured_failure(tmp_path, limits):
    foundation = IngestionFoundation(WorkCache(tmp_path / "cache"), registry=single(CrashingDecoder()))
    result = foundation.prepare(fx.jpeg(tmp_path / "a.jpg"), None, limits)
    assert result.error["code"] == "DECODE_FAILED"
    assert result.error["details"] == [{"field": "exit_code", "reason": "3"}]


def test_timeout_during_final_hash_fails(tmp_path):
    from conftest import deadline, limited
    hasher = CountingHasher(slow_second_sha=7.5)  # far past the deadline
    foundation = IngestionFoundation(WorkCache(tmp_path / "cache"), hasher=hasher)
    result = foundation.prepare(fx.jpeg(tmp_path / "a.jpg"), None, limited(deadline_utc=deadline(6)))
    assert hasher.sha_calls == 2
    assert result.status == "failed" and result.error["details"][0]["field"] == "deadline_utc"


def test_perceptual_hash_failure_is_attributed_to_its_substage(tmp_path, limits):
    foundation = IngestionFoundation(WorkCache(tmp_path / "cache"), hasher=CountingHasher(fail_phash=True))
    result = foundation.prepare(fx.jpeg(tmp_path / "a.jpg"), None, limits)
    statuses = {s.stage: s.status for s in result.stages}
    assert statuses["hash"] == "completed" and statuses["perceptual_hash"] == "failed"
    assert (result.error["code"], result.error["stage"]) == ("INTERNAL_ERROR", "hash")


def test_budget_enforced_across_pages_of_one_source(tmp_path, limits):
    from PIL import Image
    source = tmp_path / "noise.tif"
    pages = [Image.effect_noise((200, 200), 80).convert("RGB") for _ in range(3)]
    pages[0].save(source, "TIFF", save_all=True, append_images=pages[1:], compression="tiff_deflate")
    one_page = len(__import__("zlib").compress(pages[0].tobytes()))  # rough PNG size of a page
    cache = WorkCache(tmp_path / "cache", budget_bytes=int(one_page * 1.6))
    result = IngestionFoundation(cache).prepare(source, None, limits)
    assert result.status == "failed" and result.error["code"] == "LIMIT_EXCEEDED"
    assert cache.usage() <= cache.budget_bytes
    assert list(result.job.output_dir.iterdir()) == []


def test_gps_policy_is_bound_per_registry(tmp_path, limits):
    from paios_ingestion.registry import default_registry
    from test_metadata import GPS
    source = fx.jpeg(tmp_path / "gps.jpg", gps=GPS)
    denied = IngestionFoundation(WorkCache(tmp_path / "c1")).prepare(source, None, limits)
    allowed = IngestionFoundation(WorkCache(tmp_path / "c2"),
                                  registry=default_registry(allow_gps=True)).prepare(source, None, limits)
    assert denied.decode.metadata["gps"] is None
    assert allowed.decode.metadata["gps"] is not None
    with pytest.raises(AttributeError):
        default_registry().resolve("image/jpeg").allow_gps = True
