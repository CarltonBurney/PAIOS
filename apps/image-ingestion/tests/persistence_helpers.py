"""Bundle builders and a TEST-ONLY publisher driving the in-memory Drive fake.

The publisher here exists only to exercise the database side end to end. It is
not the production DGE publisher, which is not approved to be built yet
(PACKET-2-ARCHITECTURE-DECISION.md, P2-A..P2-E).
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid

from paios_ingestion import contract
from paios_ingestion.persistence.canonical import canonical_bytes, digest, sha256_hex
from paios_ingestion.persistence.fakes import TransientError
from paios_ingestion.persistence.repository import MARKER_FORMAT, PAYLOAD_ROLES

EXAMPLES = contract.CONTRACTS_DIR / "examples"


def example(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}-bundle.json").read_text(encoding="utf-8"))


def _swap(value, mapping):
    if isinstance(value, dict):
        return {k: _swap(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [_swap(v, mapping) for v in value]
    return mapping.get(value, value) if isinstance(value, str) else value


def rehash(bundle: dict) -> dict:
    bundle["audit"]["registry_sha256"] = digest(bundle["registry"])
    bundle["audit"]["index_sha256"] = digest(bundle["index"])
    return bundle


def set_title(bundle: dict, title: str, tags: list[str] | None = None) -> dict:
    for record in (bundle["registry"], bundle["audit"], bundle["index"]):
        record["index_metadata"]["title"] = title
        if tags is not None:
            record["index_metadata"]["tags"] = list(tags)
    return rehash(bundle)


def set_text(bundle: dict, text: str) -> dict:
    ocr = bundle["registry"]["ocr"]
    ocr["pages"][0]["text"] = text
    ocr["full_text"] = text
    bundle["index"]["search_text"] = text
    text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    for record in (bundle["registry"], bundle["audit"], bundle["index"]):
        record["index_metadata"]["text_sha256"] = text_sha
    return rehash(bundle)


def chain(scope: contract.Scope, terminal: str = "completed", *, title: str | None = None,
          text: str | None = None, tags: list[str] | None = None) -> list[dict]:
    """Versions 1..3 (accepted, processing, terminal) with fresh IDs in `scope`."""
    names = ["accepted", "processing", terminal]
    bundles = [example(n) for n in names]
    ids = set()
    for b in bundles:
        ids |= {b["commit_id"], b["registry"]["asset_id"], b["registry"]["ingestion_id"],
                b["audit"]["event_id"], b["audit"]["trace_id"]}
        if b["registry"]["ocr"]:
            ids.add(b["registry"]["ocr"]["result_id"])
    mapping = {old: str(uuid.uuid4()) for old in ids}
    mapping.update({"example-tenant": scope.tenant_id, "example-workspace": scope.workspace_id})
    out = []
    for b in bundles:
        b = _swap(b, mapping)
        if title is not None or tags is not None:
            set_title(b, title or b["registry"]["index_metadata"]["title"], tags)
        if text is not None and b["registry"]["ocr"]:
            set_text(b, text)
        out.append(rehash(b))
    return out


def payloads(bundle: dict) -> dict[str, bytes]:
    return {"registry.json": canonical_bytes(bundle["registry"]), "audit.json": canonical_bytes(bundle["audit"]),
            "index.json": canonical_bytes(bundle["index"])}


def marker(scope, bundle, object_ids, parts, previous_marker_sha, published_at="2026-09-23T06:00:00Z") -> bytes:
    r = bundle["registry"]
    return canonical_bytes({
        "format": MARKER_FORMAT, "contract_release": contract.CONTRACT_RELEASE,
        "scope": {"tenant_id": scope.tenant_id, "workspace_id": scope.workspace_id},
        "asset_id": r["asset_id"], "ingestion_id": r["ingestion_id"], "commit_id": bundle["commit_id"],
        "registry_version": r["canonical_record_version"], "event_id": r["last_event_id"],
        "files": {role: {"object_id": object_ids[role], "sha256": sha256_hex(parts[role]),
                         "bytes": len(parts[role])} for role in PAYLOAD_ROLES},
        "previous_marker_sha256": previous_marker_sha, "published_at": published_at})


class TestPublisher:
    """Freeze -> mark publishing -> upload exact frozen bytes by frozen IDs -> verify -> record."""

    __test__ = False  # a helper, not a pytest test class

    def __init__(self, repo, drive):
        self.repo, self.drive = repo, drive
        self.last_marker: dict[str, str | None] = {}

    def freeze(self, scope, bundle, generation):
        ids = dict(zip(PAYLOAD_ROLES + ("commit.json",), self.drive.generate_ids(4)))
        parts = payloads(bundle)
        asset = bundle["registry"]["asset_id"]
        mark = marker(scope, bundle, ids, parts, self.last_marker.get(asset))
        self.repo.freeze_intent(scope=scope, commit_id=bundle["commit_id"], payloads=parts,
                                object_ids=ids, marker=mark, generation=generation)
        return ids

    def upload(self, scope, commit_id, generation, *, retries: int = 3):
        frozen = self.repo.frozen_intent(scope, commit_id)
        files = dict(frozen["payloads"], **{"commit.json": frozen["marker"]})
        self.repo.mark_publishing(scope=scope, commit_id=commit_id, generation=generation)
        for role in PAYLOAD_ROLES + ("commit.json",):  # marker last: the visibility point
            for attempt in range(retries):
                try:
                    meta = self.drive.create(frozen["object_ids"][role], name=role, parent=commit_id,
                                             data=files[role])
                    break
                except TransientError:
                    if attempt == retries - 1:
                        raise
            assert meta["sha256Checksum"] == sha256_hex(files[role])  # remote read-back digest
        return frozen["object_ids"]["commit.json"], sha256_hex(frozen["marker"])

    def publish(self, scope, bundle, generation, fingerprint=None):
        self.freeze(scope, bundle, generation)
        marker_id, marker_sha = self.upload(scope, bundle["commit_id"], generation)
        receipt = self.repo.record_published(scope=scope, commit_id=bundle["commit_id"],
                                             marker_object_id=marker_id, marker_sha256=marker_sha,
                                             generation=generation, processing_fingerprint=fingerprint)
        self.last_marker[bundle["registry"]["asset_id"]] = marker_sha
        return receipt


def deep(bundle):
    return copy.deepcopy(bundle)
