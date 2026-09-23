"""Three-record validator (A21) and canonical digests. No database needed."""
import pytest

from conftest import error_of
from persistence_helpers import chain, deep, rehash
from paios_ingestion import contract
from paios_ingestion.persistence.canonical import canonical_bytes, is_canonical, request_digest
from paios_ingestion.persistence.validation import validate_bundle

SCOPE = contract.Scope("tenant-v", "workspace-v")


@pytest.fixture
def bundles():
    return chain(SCOPE)


def test_contract_example_chain_is_valid(bundles):
    validate_bundle(bundles[0])
    validate_bundle(bundles[1], bundles[0]["registry"])
    validate_bundle(bundles[2], bundles[1]["registry"])
    failed = chain(SCOPE, "failed")
    validate_bundle(failed[2], failed[1]["registry"])


def fails(bundle, previous, field):
    with pytest.raises(contract.PipelineFailure) as excinfo:
        validate_bundle(bundle, previous)
    error = error_of(excinfo)
    assert error["code"] == "INTEGRITY_FAILED"
    fields = [d["field"] for d in error["details"]]
    assert any(f.startswith(field) for f in fields), fields


def completed(bundles, mutate, recompute=True):
    b = deep(bundles[2])
    mutate(b)
    return rehash(b) if recompute else b


@pytest.mark.parametrize("field,mutate", [
    ("scope", lambda b: b["index"]["scope"].update(tenant_id="other")),
    ("asset_id", lambda b: b["index"].update(asset_id="12345678-1234-4234-8234-123456789012")),
    ("registry_version", lambda b: b["index"].update(registry_version=4)),
    ("event_id", lambda b: b["index"].update(event_id="12345678-1234-4234-8234-123456789012")),
    ("status", lambda b: b["index"].update(status="partial")),
    ("index_metadata", lambda b: b["index"]["index_metadata"].update(title="different")),
    ("source", lambda b: b["audit"]["source"].update(version="2")),
    ("original", lambda b: b["index"].update(original=None)),
    ("normalized.pages", lambda b: b["registry"]["normalized"]["pages"][0].update(page_number=2)),
    ("normalized.pages[1]", lambda b: b["registry"]["normalized"]["pages"][0].update(sha256="b" * 64)),
    ("ocr.full_text", lambda b: b["registry"]["ocr"].update(full_text="Other")),
    ("index.search_text", lambda b: b["index"].update(search_text="Other")),
    ("registry.updated_at", lambda b: b["registry"].update(updated_at="2026-09-22T19:00:00+01:00")),
    ("audit.event_type", lambda b: b["audit"].update(event_type="ingestion_failed")),
    ("relationships", lambda b: b["registry"].update(relationships=[
        {"type": "exact_duplicate_of", "asset_id": b["registry"]["asset_id"]}])),
])
def test_cross_record_violations_are_rejected(bundles, field, mutate):
    fails(completed(bundles, mutate), bundles[1]["registry"], field)


def test_registry_and_index_hashes_bind_the_exact_records(bundles):
    b = completed(bundles, lambda b: b["index"].update(search_text="Hello"), recompute=False)
    b["registry"]["extensions"] = {"paios.test": {"x": 1}}  # changed after hashing
    fails(b, bundles[1]["registry"], "audit.registry_sha256")
    b = deep(bundles[2])
    b["index"]["updated_at"] = "2026-09-22T18:00:01Z"
    fails(b, bundles[1]["registry"], "audit.index_sha256")


def test_index_metadata_rules(bundles):
    prev = bundles[1]["registry"]

    def meta(**changes):
        def apply(b):
            for record in (b["registry"], b["audit"], b["index"]):
                record["index_metadata"].update(changes)
        return apply

    fails(completed(bundles, meta(page_count=2)), prev, "index_metadata.page_count")
    fails(completed(bundles, meta(media_type="image/jpeg")), prev, "index_metadata.media_type")
    fails(completed(bundles, meta(text_sha256="c" * 64)), prev, "index_metadata.text_sha256")
    fails(completed(bundles, meta(tags=["a", "a"])), prev, "index_metadata.tags")
    fails(completed(bundles, meta(ocr_status="pending")), prev, "index_metadata.ocr_status")


def test_error_presence_is_enforced_by_the_schema(bundles):
    """failed => error and completed => no error are schema rules: CONTRACT_MISMATCH."""
    failed = chain(SCOPE, "failed")
    b = deep(failed[2])
    b["registry"]["error"] = b["audit"]["error"] = None
    b2 = deep(bundles[2])
    b2["registry"]["error"] = b2["audit"]["error"] = failed[2]["registry"]["error"]
    for bundle, previous in ((rehash(b), failed[1]["registry"]), (rehash(b2), bundles[1]["registry"])):
        with pytest.raises(contract.PipelineFailure) as excinfo:
            validate_bundle(bundle, previous)
        assert error_of(excinfo)["code"] == "CONTRACT_MISMATCH"


def test_status_rules_for_failed_and_early_records():
    failed = chain(SCOPE, "failed")
    b = deep(failed[2])
    b["index"]["search_text"] = "leak"
    fails(rehash(b), failed[1]["registry"], "index.search_text")
    early = chain(SCOPE)
    b = deep(early[1])
    b["audit"]["event_type"] = "asset_accepted"
    b["audit"]["stage"] = "accepted"
    fails(rehash(b), early[0]["registry"], "audit.event_type")


def test_completed_non_duplicate_requires_media(bundles):
    def strip(b):
        b["registry"]["normalized"] = None
    fails(completed(bundles, strip), bundles[1]["registry"], "registry")


def test_chain_rules(bundles):
    fails(bundles[1], None, "canonical_record_version")  # v2 needs its predecessor
    fails(bundles[2], bundles[0]["registry"], "canonical_record_version")  # gap
    b = deep(bundles[2])
    b["audit"]["previous_event_id"] = "12345678-1234-4234-8234-123456789012"
    fails(rehash(b), bundles[1]["registry"], "audit.previous_event_id")
    b = deep(bundles[2])
    b["registry"]["created_at"] = "2026-09-22T17:00:00Z"
    fails(rehash(b), bundles[1]["registry"], "registry.created_at")


def test_terminal_record_only_changes_by_correction(bundles):
    terminal = bundles[2]["registry"]
    b = deep(bundles[1])
    b["registry"]["canonical_record_version"] = b["audit"]["registry_version"] = b["index"]["registry_version"] = 4
    b["audit"]["previous_registry_version"] = 3
    b["audit"]["previous_event_id"] = terminal["last_event_id"]
    fails(rehash(b), terminal, "status")


def test_schema_and_non_finite_numbers_are_contract_mismatch(bundles):
    b = deep(bundles[0])
    b["registry"]["extensions"] = {"paios.test": {"x": float("nan")}}
    with pytest.raises(contract.PipelineFailure) as excinfo:
        validate_bundle(b)
    assert error_of(excinfo)["code"] in ("CONTRACT_MISMATCH", "INTEGRITY_FAILED")
    b = deep(bundles[0])
    del b["audit"]["reason"]
    with pytest.raises(contract.PipelineFailure) as excinfo:
        validate_bundle(b)
    assert error_of(excinfo)["code"] == "CONTRACT_MISMATCH"


def test_request_digest_normalizes_language_order_and_binds_scope():
    request = {"schema_version": "1.0.0", "workspace_id": "w", "force_reprocess": False,
               "context_release_id": None,
               "source": {"connector": "onedrive", "root_id": "r", "item_id": "i", "version": "1",
                          "original_filename": "a.png"},
               "ocr": {"enabled": True, "languages": ["fr", "en"], "provider_profile": "p"}}
    reordered = dict(request, ocr=dict(request["ocr"], languages=["en", "fr", "en"]))
    a = contract.Scope("t", "w")
    assert request_digest(a, request) == request_digest(a, reordered)
    assert request_digest(a, request) != request_digest(contract.Scope("t2", "w"), request)


def test_canonical_bytes():
    assert canonical_bytes({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode()
    assert is_canonical(b'{"a":1}') and not is_canonical(b'{"a": 1}')
    with pytest.raises(ValueError):
        canonical_bytes({"x": float("inf")})
