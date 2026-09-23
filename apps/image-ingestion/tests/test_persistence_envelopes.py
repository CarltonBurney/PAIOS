"""Strict parsing of the private reservation, intent and marker envelopes (R1/R5).
No database needed."""
import base64
import json
import uuid

import pytest

from conftest import error_of
from persistence_helpers import chain, payloads, request
from paios_ingestion import contract
from paios_ingestion.persistence import envelopes
from paios_ingestion.persistence.canonical import canonical_bytes, request_digest
from paios_ingestion.persistence.fakes import FakeDrive
from paios_ingestion.persistence.publisher import Publisher

SCOPE = contract.Scope("tenant-e", "workspace-e")
IDS = {role: f"drive-{role.split('.')[0]}-id" for role in envelopes.OBJECT_ROLES}


def reservation(**changes):
    body = request()
    fields = dict(scope=SCOPE, idempotency_key_sha256="a" * 64, request=body,
                  digest=request_digest(SCOPE, body), ingestion_id=str(uuid.uuid4()), asset_id=str(uuid.uuid4()),
                  first_commit_id=str(uuid.uuid4()), pinned={"profile": "3"}, record_object_id="drive-res",
                  created_at="2026-09-23T06:00:00Z")
    fields.update(changes)
    return envelopes.build_reservation(**fields)


def intent(bundle=None):
    bundle = bundle or chain(SCOPE)[0]
    parts = payloads(bundle)
    marker = envelopes.build_marker(scope=SCOPE, bundle=bundle, object_ids=IDS, payloads=parts,
                                    previous_marker_sha=None, published_at="2026-09-23T06:00:00.5Z")
    return envelopes.build_intent(scope=SCOPE, commit_id=bundle["commit_id"], registry=bundle["registry"],
                                  object_ids=IDS, payloads=parts, marker=marker, frozen_at="2026-09-23T06:00:01Z")


def rewrite(data, mutate):
    value = json.loads(data)
    mutate(value)
    return canonical_bytes(value)


def fails(parse, data, code):
    with pytest.raises(contract.PipelineFailure) as excinfo:
        parse(data)
    assert error_of(excinfo)["code"] == code
    return error_of(excinfo)


def test_round_trips():
    r = envelopes.parse_reservation(reservation())
    assert r["format"] == envelopes.RESERVATION_FORMAT and r["pinned"] == {"profile": "3"}
    bundle = chain(SCOPE)[0]
    e = envelopes.parse_intent(intent(bundle))
    assert e["payload_bytes"] == payloads(bundle) and e["commit_id"] == bundle["commit_id"]
    assert set(e["object_ids"]) == set(envelopes.OBJECT_ROLES)


@pytest.mark.parametrize("parse,build", [(envelopes.parse_reservation, reservation), (envelopes.parse_intent, intent)])
def test_unknown_format_or_release_is_contract_mismatch(parse, build):
    fails(parse, rewrite(build(), lambda v: v.update(format="paios.other/1")), "CONTRACT_MISMATCH")
    fails(parse, rewrite(build(), lambda v: v.update(contract_release="1.1.0")), "CONTRACT_MISMATCH")


@pytest.mark.parametrize("parse,build", [(envelopes.parse_reservation, reservation), (envelopes.parse_intent, intent)])
def test_structure_is_exact(parse, build):
    fails(parse, rewrite(build(), lambda v: v.update(extra=1)), "INTEGRITY_FAILED")
    fails(parse, rewrite(build(), lambda v: v.pop("scope")), "INTEGRITY_FAILED")
    fails(parse, build().replace(b'":', b'": ', 1), "INTEGRITY_FAILED")  # not canonical
    fails(parse, b"[]", "INTEGRITY_FAILED")


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(request_digest="0" * 64),  # digest must bind the scoped request
    lambda v: v["request"]["ocr"].update(languages=["fr", "en"]),  # must be the normalized request
    lambda v: v.update(asset_id="NOT-A-UUID"),
    lambda v: v.update(first_commit_id=v["asset_id"]),  # IDs distinct
    lambda v: v.update(created_at="2026-09-23T06:00:00+00:00"),
    lambda v: v.update(record_object_id="has space"),
    lambda v: v.update(idempotency_key_sha256="raw-key"),  # never the raw key
])
def test_reservation_fields_are_checked(mutate):
    fails(envelopes.parse_reservation, rewrite(reservation(), mutate), "INTEGRITY_FAILED")


def _payload(value, role, mutate):
    raw = json.loads(base64.b64decode(value["payloads"][role]))
    mutate(raw)
    value["payloads"][role] = base64.b64encode(canonical_bytes(raw)).decode()


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(bundle_sha256="0" * 64),
    lambda v: v.update(marker_sha256="0" * 64),
    lambda v: v["object_ids"].update({"audit.json": v["object_ids"]["index.json"]}),  # duplicate IDs
    lambda v: v["object_ids"].pop("intent.json"),
    lambda v: v.update(registry_version=2),  # must match the embedded Registry
    lambda v: v.update(registry_version=True),
    lambda v: v["payloads"].update({"index.json": "not base64!"}),
    lambda v: v["payloads"].update({"index.json": base64.b64encode(b'{"a": 1}').decode()}),  # non-canonical
    lambda v: _payload(v, "index.json", lambda r: r.update(search_text="changed")),  # breaks bundle_sha256
    lambda v: v.update(frozen_at="yesterday"),
])
def test_intent_fields_are_checked(mutate):
    fails(envelopes.parse_intent, rewrite(intent(), mutate), "INTEGRITY_FAILED")


def test_intent_marker_must_describe_the_embedded_payloads():
    bundle = chain(SCOPE)[0]
    parts = payloads(bundle)
    wrong_ids = dict(IDS, **{"audit.json": "drive-elsewhere"})
    marker = envelopes.build_marker(scope=SCOPE, bundle=bundle, object_ids=wrong_ids, payloads=parts,
                                    previous_marker_sha=None, published_at="2026-09-23T06:00:00Z")
    with pytest.raises(contract.PipelineFailure) as excinfo:
        envelopes.build_intent(scope=SCOPE, commit_id=bundle["commit_id"], registry=bundle["registry"],
                               object_ids=IDS, payloads=parts, marker=marker, frozen_at="2026-09-23T06:00:01Z")
    assert error_of(excinfo)["code"] == "INTEGRITY_FAILED"
    fields = [d["field"] for d in error_of(excinfo)["details"]]
    assert "commit.json.files" in fields


def test_marker_rejects_extra_fields_and_unfrozen_time():
    bundle = chain(SCOPE)[0]
    parts = payloads(bundle)
    good = envelopes.build_marker(scope=SCOPE, bundle=bundle, object_ids=IDS, payloads=parts,
                                  previous_marker_sha=None, published_at="2026-09-23T06:00:00Z")
    kwargs = dict(scope=SCOPE, commit_id=bundle["commit_id"], registry=bundle["registry"], payloads=parts,
                  object_ids=IDS, previous_marker_sha=None)
    envelopes.check_marker(good, **kwargs)
    for bad in (rewrite(good, lambda v: v.update(lease="gen-2")),
                rewrite(good, lambda v: v.update(published_at="2026-09-23T06:00:00+02:00"))):
        with pytest.raises(contract.PipelineFailure):
            envelopes.check_marker(bad, **kwargs)


def test_publisher_refuses_any_adapter_that_is_not_a_test_adapter():
    class LiveLooking:
        def create(self, *args, **kwargs):
            raise AssertionError("must never be called")

    with pytest.raises(RuntimeError, match="no live Drive writer"):
        Publisher(object(), LiveLooking())
    Publisher(object(), FakeDrive())  # the fixture adapter is accepted


def test_records_are_never_uploaded_through_a_resumable_session():
    from paios_ingestion.persistence.publisher import MULTIPART_LIMIT_BYTES
    drive = FakeDrive()
    (file_id,) = drive.generate_ids(1)
    with pytest.raises(contract.PipelineFailure) as excinfo:
        Publisher(object(), drive)._put(file_id, "registry.json", b"x" * (MULTIPART_LIMIT_BYTES + 1),
                                        parent="p", props={})
    assert error_of(excinfo)["code"] == "CONTRACT_MISMATCH"
    assert drive.calls == []  # refused before any request
