"""Private coordinator envelopes: `paios.reservation/1`, `paios.intent/1` and the
`paios.commit/1` marker (R1, R5 in PACKET-2-REVIEW-ROUND-1.md).

These are not contract records. They exist so a new coordinator can rebuild its
reservation and intent rows from the DGE after losing PostgreSQL. Parsing is
strict: canonical JSON bytes, the exact key set, known format and contract
release, well-formed IDs and hashes, and every embedded value re-derived and
compared. Unknown format or release is CONTRACT_MISMATCH; anything else that
does not match is INTEGRITY_FAILED. No envelope holds credentials or the raw
idempotency key.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any, Mapping

from .. import contract
from ..errors import failure
from .canonical import bundle_sha256, canonical_bytes, is_canonical, normalize_request, request_digest, sha256_hex
from .validation import instant

RESERVATION_FORMAT = "paios.reservation/1"
INTENT_FORMAT = "paios.intent/1"
MARKER_FORMAT = "paios.commit/1"
PAYLOAD_ROLES = ("registry.json", "audit.json", "index.json")
OBJECT_ROLES = ("intent.json",) + PAYLOAD_ROLES + ("commit.json",)

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHA = re.compile(r"[0-9a-f]{64}")
_OBJECT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")

_RESERVATION_KEYS = {"format", "contract_release", "scope", "idempotency_key_sha256", "request_digest",
                     "request", "ingestion_id", "asset_id", "first_commit_id", "pinned",
                     "record_object_id", "created_at"}
_INTENT_KEYS = {"format", "contract_release", "scope", "asset_id", "ingestion_id", "registry_version",
                "commit_id", "object_ids", "payloads", "marker", "bundle_sha256", "marker_sha256", "frozen_at"}
_MARKER_KEYS = {"format", "contract_release", "scope", "asset_id", "ingestion_id", "commit_id",
                "registry_version", "event_id", "files", "previous_marker_sha256", "published_at"}


def scope_json(scope: contract.Scope) -> dict:
    return {"tenant_id": scope.tenant_id, "workspace_id": scope.workspace_id}


class _Problems:
    def __init__(self, name: str):
        self.name, self.items = name, []

    def check(self, condition: bool, field: str, reason: str) -> None:
        if not condition:
            self.items.append((f"{self.name}.{field}", reason))

    def raise_if_any(self, message: str) -> None:
        if self.items:
            raise failure("INTEGRITY_FAILED", "commit", message, details=self.items[:50])


def _load(data: bytes, name: str, keys: set[str], fmt: str) -> dict:
    if not isinstance(data, (bytes, bytearray)) or not is_canonical(bytes(data)):
        raise failure("INTEGRITY_FAILED", "commit", f"{name} is not canonical JSON",
                      details=[(name, "must be canonical UTF-8, sorted, compact JSON")])
    value = json.loads(bytes(data))
    if not isinstance(value, dict):
        raise failure("INTEGRITY_FAILED", "commit", f"{name} must be a JSON object")
    if value.get("format") != fmt or value.get("contract_release") != contract.CONTRACT_RELEASE:
        raise failure("CONTRACT_MISMATCH", "commit", f"Unsupported {name} format or contract release",
                      details=[(f"{name}.format", str(value.get("format"))[:80]),
                               (f"{name}.contract_release", str(value.get("contract_release"))[:80])])
    if set(value) != keys:
        raise failure("INTEGRITY_FAILED", "commit", f"{name} has unexpected or missing fields",
                      details=[(name, f"missing {sorted(keys - set(value))}, extra {sorted(set(value) - keys)}")])
    return value


def _scope_ok(value: Any) -> bool:
    return (isinstance(value, dict) and set(value) == {"tenant_id", "workspace_id"}
            and all(isinstance(v, str) and v for v in value.values()))


def _is(pattern: re.Pattern, value: Any) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _utc_ok(value: Any) -> bool:
    try:
        instant(value)
    except ValueError:
        return False
    return True


def _b64(value: Any) -> bytes | None:
    if not isinstance(value, str):
        return None
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw if base64.b64encode(raw).decode("ascii") == value else None


# -- reservation ------------------------------------------------------------------------

def build_reservation(*, scope: contract.Scope, idempotency_key_sha256: str, request: Mapping[str, Any],
                      digest: str, ingestion_id: str, asset_id: str, first_commit_id: str | None,
                      pinned: Mapping[str, Any], record_object_id: str, created_at: str) -> bytes:
    data = canonical_bytes({
        "format": RESERVATION_FORMAT, "contract_release": contract.CONTRACT_RELEASE,
        "scope": scope_json(scope), "idempotency_key_sha256": idempotency_key_sha256,
        "request_digest": digest, "request": normalize_request(request), "ingestion_id": ingestion_id,
        "asset_id": asset_id, "first_commit_id": first_commit_id, "pinned": dict(pinned),
        "record_object_id": record_object_id, "created_at": created_at})
    parse_reservation(data)  # never write an envelope the parser would refuse
    return data


def parse_reservation(data: bytes) -> dict:
    r = _load(data, "reservation.json", _RESERVATION_KEYS, RESERVATION_FORMAT)
    p = _Problems("reservation.json")
    p.check(_scope_ok(r["scope"]), "scope", "must be {tenant_id, workspace_id} strings")
    for field in ("idempotency_key_sha256", "request_digest"):
        p.check(_is(_SHA, r[field]), field, "must be lowercase SHA-256 hex")
    for field in ("ingestion_id", "asset_id", "first_commit_id"):
        p.check((field == "first_commit_id" and r[field] is None) or _is(_UUID, r[field]),
                field, "must be a lowercase UUID (first commit may be unallocated)")
    # Shape errors must be reported before set operations on untrusted values.
    p.raise_if_any("Reservation record is invalid")
    ids = [r[f] for f in ("ingestion_id", "asset_id", "first_commit_id") if r[f] is not None]
    p.check(len(set(ids)) == len(ids), "ids", "must be distinct")
    p.check(isinstance(r["pinned"], dict), "pinned", "must be an object")
    p.check(_is(_OBJECT_ID, r["record_object_id"]), "record_object_id", "must be a Drive file ID")
    p.check(_utc_ok(r["created_at"]), "created_at", "must be RFC 3339 UTC")
    if isinstance(r["request"], dict) and _scope_ok(r["scope"]):
        scope = contract.Scope(**r["scope"])
        p.check(r["request"] == normalize_request(r["request"]), "request", "must be the normalized request")
        p.check(request_digest(scope, r["request"]) == r["request_digest"], "request_digest",
                "must be the digest of the scoped normalized request")
    else:
        p.check(False, "request", "must be an object")
    p.raise_if_any("Reservation record is invalid")
    return r


# -- commit marker ------------------------------------------------------------------------

def check_marker(marker: bytes, *, scope: contract.Scope, commit_id: str, registry: Mapping[str, Any],
                 payloads: Mapping[str, bytes], object_ids: Mapping[str, str],
                 previous_marker_sha: str | None) -> dict:
    """The marker must describe exactly the frozen bundle and chain to its predecessor."""
    try:
        data = json.loads(bytes(marker).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        data = None
    problems = []
    if not is_canonical(bytes(marker)) or not isinstance(data, dict):
        problems.append(("commit.json", "must be canonical JSON"))
    else:
        files = {role: {"object_id": object_ids[role], "sha256": sha256_hex(payloads[role]),
                        "bytes": len(payloads[role])} for role in PAYLOAD_ROLES}
        expect = {"format": MARKER_FORMAT, "contract_release": contract.CONTRACT_RELEASE,
                  "scope": scope_json(scope), "asset_id": registry["asset_id"],
                  "ingestion_id": registry["ingestion_id"], "commit_id": commit_id,
                  "registry_version": registry["canonical_record_version"],
                  "event_id": registry["last_event_id"], "files": files,
                  "previous_marker_sha256": previous_marker_sha}
        if set(data) != _MARKER_KEYS:
            problems.append(("commit.json", "has unexpected or missing fields"))
        for key, value in expect.items():
            if data.get(key) != value:
                problems.append((f"commit.json.{key}", "does not match the frozen bundle"))
        if not _utc_ok(data.get("published_at")):
            problems.append(("commit.json.published_at", "must be frozen RFC 3339 UTC time"))
    if problems:
        raise failure("INTEGRITY_FAILED", "commit", "Marker does not match the frozen intent", details=problems)
    return data


def build_marker(*, scope: contract.Scope, bundle: Mapping[str, Any], object_ids: Mapping[str, str],
                 payloads: Mapping[str, bytes], previous_marker_sha: str | None, published_at: str) -> bytes:
    r = bundle["registry"]
    return canonical_bytes({
        "format": MARKER_FORMAT, "contract_release": contract.CONTRACT_RELEASE, "scope": scope_json(scope),
        "asset_id": r["asset_id"], "ingestion_id": r["ingestion_id"], "commit_id": bundle["commit_id"],
        "registry_version": r["canonical_record_version"], "event_id": r["last_event_id"],
        "files": {role: {"object_id": object_ids[role], "sha256": sha256_hex(payloads[role]),
                         "bytes": len(payloads[role])} for role in PAYLOAD_ROLES},
        "previous_marker_sha256": previous_marker_sha, "published_at": published_at})


# -- intent ---------------------------------------------------------------------------------

def build_intent(*, scope: contract.Scope, commit_id: str, registry: Mapping[str, Any],
                 object_ids: Mapping[str, str], payloads: Mapping[str, bytes], marker: bytes,
                 frozen_at: str) -> bytes:
    payload_sha = {role: sha256_hex(payloads[role]) for role in PAYLOAD_ROLES}
    data = canonical_bytes({
        "format": INTENT_FORMAT, "contract_release": contract.CONTRACT_RELEASE, "scope": scope_json(scope),
        "asset_id": registry["asset_id"], "ingestion_id": registry["ingestion_id"],
        "registry_version": registry["canonical_record_version"], "commit_id": commit_id,
        "object_ids": {role: object_ids[role] for role in OBJECT_ROLES},
        "payloads": {role: base64.b64encode(payloads[role]).decode("ascii") for role in PAYLOAD_ROLES},
        "marker": base64.b64encode(marker).decode("ascii"),
        "bundle_sha256": bundle_sha256(commit_id, registry["canonical_record_version"], payload_sha),
        "marker_sha256": sha256_hex(marker), "frozen_at": frozen_at})
    parse_intent(data)
    return data


def parse_intent(data: bytes) -> dict:
    """Strictly parse intent.json. Returns the envelope plus decoded `payload_bytes`,
    `marker_bytes`, `registry` and `scope_obj`. The marker is checked against the
    payloads here except for its predecessor link, which needs chain context."""
    e = _load(data, "intent.json", _INTENT_KEYS, INTENT_FORMAT)
    p = _Problems("intent.json")
    p.check(_scope_ok(e["scope"]), "scope", "must be {tenant_id, workspace_id} strings")
    for field in ("asset_id", "ingestion_id", "commit_id"):
        p.check(_is(_UUID, e[field]), field, "must be a lowercase UUID")
    p.check(isinstance(e["registry_version"], int) and not isinstance(e["registry_version"], bool)
            and e["registry_version"] >= 1, "registry_version", "must be an integer >= 1")
    ids = e["object_ids"]
    p.check(isinstance(ids, dict) and set(ids) == set(OBJECT_ROLES)
            and all(_is(_OBJECT_ID, v) for v in ids.values()) and len(set(ids.values())) == len(OBJECT_ROLES),
            "object_ids", "must hold five distinct Drive file IDs for the five roles")
    decoded = {}
    if isinstance(e["payloads"], dict) and set(e["payloads"]) == set(PAYLOAD_ROLES):
        for role in PAYLOAD_ROLES:
            raw = _b64(e["payloads"][role])
            p.check(raw is not None and is_canonical(raw), f"payloads.{role}", "must be base64 canonical JSON")
            decoded[role] = raw
    else:
        p.check(False, "payloads", "must hold exactly the three payload roles")
    marker = _b64(e["marker"])
    p.check(marker is not None, "marker", "must be base64")
    p.check(_is(_SHA, e["bundle_sha256"]), "bundle_sha256", "must be SHA-256 hex")
    p.check(_is(_SHA, e["marker_sha256"]), "marker_sha256", "must be SHA-256 hex")
    p.check(_utc_ok(e["frozen_at"]), "frozen_at", "must be RFC 3339 UTC")
    p.raise_if_any("Intent record is invalid")

    registry = json.loads(decoded["registry.json"])
    scope = contract.Scope(**e["scope"])
    p.check(isinstance(registry, dict) and registry.get("asset_id") == e["asset_id"]
            and registry.get("ingestion_id") == e["ingestion_id"]
            and registry.get("canonical_record_version") == e["registry_version"]
            and registry.get("scope") == e["scope"], "payloads.registry.json",
            "must match the envelope's asset, ingestion, version and scope")
    payload_sha = {role: sha256_hex(decoded[role]) for role in PAYLOAD_ROLES}
    p.check(bundle_sha256(e["commit_id"], e["registry_version"], payload_sha) == e["bundle_sha256"],
            "bundle_sha256", "must identify the embedded payloads")
    p.check(sha256_hex(marker) == e["marker_sha256"], "marker_sha256", "must hash the embedded marker")
    p.raise_if_any("Intent record is invalid")
    try:
        previous = json.loads(marker).get("previous_marker_sha256")
    except (ValueError, AttributeError):
        previous = None
    check_marker(marker, scope=scope, commit_id=e["commit_id"], registry=registry, payloads=decoded,
                 object_ids=ids, previous_marker_sha=previous)
    return dict(e, payload_bytes=decoded, marker_bytes=marker, registry=registry, scope_obj=scope)

