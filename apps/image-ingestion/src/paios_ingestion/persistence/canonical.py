"""Canonical JSON bytes and digests, as specified in storage-and-consistency.md step 4."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .. import contract


def canonical_bytes(value: Any) -> bytes:
    """UTF-8, sorted keys, compact separators, no NaN/Infinity."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(value: Any) -> str:
    return sha256_hex(canonical_bytes(value))


def is_canonical(data: bytes) -> bool:
    try:
        return canonical_bytes(json.loads(data.decode("utf-8"))) == data
    except (UnicodeDecodeError, ValueError):
        return False


def bundle_sha256(commit_id: str, registry_version: int, payload_sha256: Mapping[str, str]) -> str:
    """Identity of a frozen bundle: payload digests plus slot. Excludes the marker,
    whose own bytes reference these digests, so nothing hashes itself."""
    return digest({"commit_id": commit_id, "registry_version": registry_version,
                   "files": dict(payload_sha256), "contract_release": contract.CONTRACT_RELEASE})


def normalize_request(request: Mapping[str, Any]) -> dict:
    """Language order alone must not change request identity (contract-semantics.md)."""
    normalized = json.loads(canonical_bytes(request))
    options = normalized.get("ocr") if isinstance(normalized.get("ocr"), dict) else None
    if options and isinstance(options.get("languages"), list):
        options["languages"] = sorted(set(options["languages"]))
    return normalized


def request_digest(scope: contract.Scope, request: Mapping[str, Any]) -> str:
    return digest({"scope": {"tenant_id": scope.tenant_id, "workspace_id": scope.workspace_id},
                   "contract_release": contract.CONTRACT_RELEASE,
                   "request": normalize_request(request)})
