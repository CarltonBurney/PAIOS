"""Bundle builders for the persistence tests (fixture data only)."""
from __future__ import annotations

import copy
import hashlib
import json
import uuid

from paios_ingestion import contract
from paios_ingestion.persistence.canonical import canonical_bytes, digest, request_digest

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
          text: str | None = None, tags: list[str] | None = None, ids: dict | None = None) -> list[dict]:
    """Versions 1..3 (accepted, processing, terminal) with fresh IDs in `scope`.
    `ids` pins asset_id, ingestion_id and the revision-1 commit_id."""
    names = ["accepted", "processing", terminal]
    bundles = [example(n) for n in names]
    pinned, ids = ids, set()
    for b in bundles:
        ids |= {b["commit_id"], b["registry"]["asset_id"], b["registry"]["ingestion_id"],
                b["audit"]["event_id"], b["audit"]["trace_id"]}
        if b["registry"]["ocr"]:
            ids.add(b["registry"]["ocr"]["result_id"])
    mapping = {old: str(uuid.uuid4()) for old in ids}
    if pinned:
        first = bundles[0]
        mapping.update({first["registry"]["asset_id"]: pinned["asset_id"],
                        first["registry"]["ingestion_id"]: pinned["ingestion_id"],
                        first["commit_id"]: pinned["commit_id"]})
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


def request(item="item-1", languages=("en",)):
    return {"schema_version": "1.0.0", "workspace_id": "workspace-1", "force_reprocess": False,
            "context_release_id": None,
            "source": {"connector": "onedrive", "root_id": "root", "item_id": item, "version": "1",
                       "original_filename": "a.png"},
            "ocr": {"enabled": True, "languages": list(languages), "provider_profile": "p"}}


def admitted(publisher, scope, terminal="completed", *, key=None, **kwargs) -> list[dict]:
    """Admit an ingestion (reservation.json published), then build versions 1..3
    bound to the reserved asset, ingestion and first commit IDs."""
    key = key or str(uuid.uuid4())
    body = request(item=key)
    reservation = publisher.admit(scope, idempotency_key=key,
                                  request_digest=request_digest(scope, body), request=body)
    record = publisher.repo.reservation_record(scope, reservation.ingestion_id)
    return chain(scope, terminal, ids={"asset_id": reservation.asset_id, "ingestion_id": reservation.ingestion_id,
                                       "commit_id": record["first_commit_id"]}, **kwargs)


def deep(bundle):
    return copy.deepcopy(bundle)
