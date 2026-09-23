"""Three-record commit validation (contract-semantics.md, storage-and-consistency.md).

Schema shape is necessary but not sufficient: this enforces the normative
cross-record, status, page, OCR, hash, chain and time rules. Any violation
rejects the whole bundle with INTEGRITY_FAILED, listing every broken rule.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from jsonschema import ValidationError

from .. import contract
from ..errors import failure
from .canonical import digest

_TERMINAL = {"completed", "partial", "failed"}
_EVENT_FOR_STATUS = {
    "accepted": {"asset_accepted"},
    "processing": {"processing_started"},
    "completed": {"ingestion_completed", "duplicate_reused", "asset_reprocessed", "metadata_corrected"},
    "partial": {"ingestion_partial", "asset_reprocessed", "metadata_corrected"},
    "failed": {"ingestion_failed", "metadata_corrected"},
}
_STAGE_FOR_EVENT = {
    "asset_accepted": "accepted", "processing_started": "processing",
    "ingestion_completed": "terminal", "ingestion_partial": "terminal",
    "ingestion_failed": "terminal", "duplicate_reused": "terminal",
    "asset_reprocessed": "terminal", "metadata_corrected": "correction",
}
_ALLOWED_TRANSITIONS = {
    "accepted": {"processing", "completed", "partial", "failed"},  # reuse may skip processing
    "processing": {"completed", "partial", "failed"},
}


class _Checks:
    def __init__(self):
        self.problems: list[tuple[str, str]] = []

    def require(self, condition: bool, field: str, reason: str) -> None:
        if not condition:
            self.problems.append((field, reason))


_RFC3339_UTC = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d+)?Z")


def instant(value: str) -> tuple[datetime, Decimal]:
    """Parse an RFC 3339 UTC ('Z') timestamp into a comparable instant.

    Returns (whole seconds, fractional seconds) so any fractional precision
    compares exactly: 18:00:00.100Z is later than 18:00:00Z, and .1Z equals
    .100Z. Raises ValueError for anything else, including numeric offsets.
    """
    m = _RFC3339_UTC.fullmatch(value) if isinstance(value, str) else None
    if m is None:
        raise ValueError(f"not an RFC 3339 UTC timestamp: {value!r}")
    whole = datetime(*(int(g) for g in m.groups()[:6]), tzinfo=timezone.utc)
    return whole, Decimal("0" + (m.group(7) or ""))


def _utc(value: str | None) -> bool:
    if value is None:
        return True
    try:
        instant(value)
    except ValueError:
        return False
    return True


def _not_before(later: str, earlier: str) -> bool:
    """True when `later` is at or after `earlier`. Unparseable values are reported
    separately by the UTC check, so they do not add an ordering problem here."""
    try:
        return instant(later) >= instant(earlier)
    except ValueError:
        return True


def _check_ocr(c: _Checks, r: dict, i: dict) -> None:
    o, meta = r["ocr"], r["index_metadata"]
    if o is None:
        c.require(meta["ocr_status"] == "pending", "index_metadata.ocr_status",
                  "must be pending until an OCR result exists")
        return
    c.require(o["asset_id"] == r["asset_id"] and o["scope"] == r["scope"], "ocr",
              "OCR asset/scope must match the Registry")
    c.require(meta["ocr_status"] == o["status"], "index_metadata.ocr_status", "must equal ocr.status")
    pages, status = o["pages"], o["status"]
    numbers = [p["page_number"] for p in pages]
    c.require(numbers == sorted(set(numbers)), "ocr.pages", "page numbers must be unique and ordered")
    good = [p for p in pages if p["status"] == "completed"]
    bad = [p for p in pages if p["status"] == "failed"]
    for p in good:
        c.require(p["text"] is not None and p["error"] is None, f"ocr.pages[{p['page_number']}]",
                  "completed page needs text and no error")
    for p in bad:
        c.require(p["text"] is None and p["blocks"] == [] and p["error"] is not None,
                  f"ocr.pages[{p['page_number']}]", "failed page has null text, no blocks, an error")
    c.require((o["reused_from_result_id"] is not None) == (status == "reused"),
              "ocr.reused_from_result_id", "set exactly when status is reused")
    if status == "completed":
        c.require(bool(pages) and not bad and o["error"] is None, "ocr", "completed OCR: all pages succeed")
    if status == "partial":
        c.require(bool(good) and bool(bad), "ocr", "partial OCR needs successful and failed pages")
    if status in ("completed", "partial"):
        c.require(o["full_text"] == "\f".join(p["text"] for p in good), "ocr.full_text",
                  "must join successful page texts with form feed in page order")
        normalized = r["normalized"]
        if normalized is not None:
            c.require(numbers == [p["page_number"] for p in normalized["pages"]], "ocr.pages",
                      "OCR pages must map to the normalized pages")
    if status == "failed":
        c.require(not good and o["full_text"] is None and o["error"] is not None, "ocr",
                  "failed OCR: no successful pages, null text, an error")
    if status in ("skipped", "reused"):
        c.require(not pages and o["full_text"] is None and o["error"] is None, "ocr",
                  f"{status} OCR has no pages, text or error")
    for field in ("started_at", "completed_at"):
        c.require(_utc(o[field]), f"ocr.{field}", "must be UTC with Z")
    if o["started_at"] is not None and o["completed_at"] is not None:
        c.require(_not_before(o["completed_at"], o["started_at"]), "ocr.completed_at",
                  "must not precede started_at")


def _check_search_text(c: _Checks, r: dict, i: dict) -> None:
    o, meta, text = r["ocr"], r["index_metadata"], i["search_text"]
    text_status = o["status"] if o else None
    if text_status in ("completed", "partial"):
        c.require(text == o["full_text"], "index.search_text", "must equal ocr.full_text")
    if text_status in ("completed", "partial", "reused"):
        c.require(meta["text_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest(),
                  "index_metadata.text_sha256", "must hash the UTF-8 search_text")
    else:
        c.require(text == "", "index.search_text", "must be empty without usable OCR text")
        c.require(meta["text_sha256"] is None, "index_metadata.text_sha256",
                  "must be null without usable OCR text")


def _check_media(c: _Checks, r: dict) -> None:
    n, meta, identity, original = r["normalized"], r["index_metadata"], r["identity"], r["original"]
    duplicate = any(rel["type"] == "exact_duplicate_of" for rel in r["relationships"])
    if r["status"] == "completed" and not duplicate:
        c.require(original is not None and identity["sha256"] is not None and n is not None,
                  "registry", "completed non-duplicate needs original, SHA and normalized media")
    if original is not None and identity["sha256"] is not None:
        c.require(original["sha256"] == identity["sha256"], "original.sha256", "must equal identity.sha256")
    if n is None:
        return
    pages = n["pages"]
    c.require(n["asset_id"] == r["asset_id"] and n["scope"] == r["scope"], "normalized",
              "asset/scope must match the Registry")
    c.require(n["original_sha256"] == identity["sha256"], "normalized.original_sha256",
              "must equal identity.sha256")
    c.require([p["page_number"] for p in pages] == list(range(1, len(pages) + 1)),
              "normalized.pages", "page numbers must be contiguous from 1")
    for p in pages:
        c.require(p["sha256"] == p["storage_ref"]["sha256"], f"normalized.pages[{p['page_number']}]",
                  "page sha256 must equal its StorageRef sha256")
    c.require(meta["page_count"] == len(pages), "index_metadata.page_count", "must equal page count")
    c.require(meta["media_type"] == n["detected_mime_type"], "index_metadata.media_type",
              "must equal the verified MIME type")


def _check_status(c: _Checks, r: dict, a: dict) -> None:
    status, o = r["status"], r["ocr"]
    if status == "failed":
        c.require(r["error"] is not None, "registry.error", "failed status requires an error")
    else:
        c.require(r["error"] is None, "registry.error", "only failed status carries an error")
    if status == "completed":
        # Disabled OCR still produces a skipped result; null OCR is never complete.
        c.require(o is not None and o["status"] in ("completed", "skipped", "reused"), "ocr.status",
                  "completed ingestion needs a completed, skipped or reused OCR result")
    if status == "partial":
        c.require(o is not None and o["status"] == "partial", "ocr.status", "partial ingestion needs partial OCR")
    if o is not None and o["status"] == "partial":
        c.require(status == "partial", "status", "partial OCR makes the ingestion partial")
    if status in ("accepted", "processing"):
        c.require(o is None, "ocr", f"{status} records carry no OCR result")
    event = a["event_type"]
    c.require(event in _EVENT_FOR_STATUS[status], "audit.event_type", f"{event} does not fit status {status}")
    c.require(a["stage"] == _STAGE_FOR_EVENT.get(event), "audit.stage", f"stage must match {event}")
    if event == "duplicate_reused":
        c.require(any(rel["type"] == "exact_duplicate_of" for rel in r["relationships"]),
                  "relationships", "duplicate_reused needs an exact_duplicate_of relationship")
    if r["canonical_record_version"] == 1:
        c.require(event == "asset_accepted", "audit.event_type", "version 1 must be asset_accepted")


def _check_lists(c: _Checks, r: dict) -> None:
    meta = r["index_metadata"]
    c.require(len(meta["tags"]) == len(set(meta["tags"])), "index_metadata.tags", "tags must be unique")
    c.require(len(meta["languages"]) == len(set(meta["languages"])), "index_metadata.languages",
              "languages must be unique")
    relations = [(rel["type"], rel["asset_id"]) for rel in r["relationships"]]
    c.require(len(relations) == len(set(relations)), "relationships", "relationships must be unique")
    c.require(all(rel["asset_id"] != r["asset_id"] for rel in r["relationships"]), "relationships",
              "an asset cannot relate to itself")


def _check_chain(c: _Checks, r: dict, a: dict, previous: Mapping[str, Any] | None) -> None:
    version = r["canonical_record_version"]
    if previous is None:
        c.require(version == 1, "canonical_record_version",
                  "a revision after 1 must be validated against its predecessor")
        return
    c.require(version == previous["canonical_record_version"] + 1, "canonical_record_version",
              "must follow the previous version with no gap")
    c.require(a["previous_event_id"] == previous["last_event_id"], "audit.previous_event_id",
              "must link to the previous revision's event")
    for field in ("asset_id", "scope", "ingestion_id", "created_at"):
        c.require(r[field] == previous[field], f"registry.{field}", "must not change between revisions")
    c.require(_not_before(r["updated_at"], previous["updated_at"]), "registry.updated_at",
              "must not go backwards")
    before, after = previous["status"], r["status"]
    if before in _TERMINAL:
        c.require(a["event_type"] == "metadata_corrected" and after == before, "status",
                  "a terminal record changes only through a correction keeping its outcome")
    else:
        c.require(after in _ALLOWED_TRANSITIONS[before], "status", f"{before} -> {after} is not allowed")


def validate_bundle(bundle: Mapping[str, Any], previous: Mapping[str, Any] | None = None) -> None:
    """Raise PipelineFailure(INTEGRITY_FAILED) unless the bundle satisfies every rule.

    `previous` is the committed Registry of version N-1 (None for version 1).
    """
    try:
        contract.validate("CommitBundle", bundle)
    except ValidationError as exc:
        raise failure("CONTRACT_MISMATCH", "commit", "Bundle does not match the schema",
                      details=[("/".join(map(str, exc.absolute_path)) or "bundle", exc.message[:200])]) from None
    except ValueError:  # NaN / Infinity
        raise failure("CONTRACT_MISMATCH", "commit", "Bundle contains a non-finite number") from None
    r, a, i = bundle["registry"], bundle["audit"], bundle["index"]
    c = _Checks()
    c.require(bundle["scope"] == r["scope"] == a["scope"] == i["scope"], "scope", "must match in all records")
    c.require(r["asset_id"] == a["asset_id"] == i["asset_id"], "asset_id", "must match in all records")
    c.require(r["ingestion_id"] == a["ingestion_id"], "ingestion_id", "Registry and Audit must match")
    version = r["canonical_record_version"]
    c.require(version == a["registry_version"] == i["registry_version"], "registry_version",
              "must match in all records")
    c.require(a["previous_registry_version"] == version - 1, "audit.previous_registry_version", "must be N-1")
    c.require((a["previous_event_id"] is None) == (version == 1), "audit.previous_event_id",
              "null exactly for version 1")
    c.require(r["last_event_id"] == a["event_id"] == i["event_id"], "event_id",
              "Registry and Index must point to the Audit event")
    c.require(r["status"] == a["outcome"] == i["status"], "status", "must match in all records")
    c.require(r["index_metadata"] == a["index_metadata"] == i["index_metadata"], "index_metadata",
              "must be the same JSON value in all records")
    c.require(r["source"] == a["source"], "source", "Registry and Audit must match")
    c.require(r["error"] == a["error"], "error", "Registry and Audit must match")
    c.require(r["original"] == i["original"], "original", "Registry and Index must match")
    try:
        c.require(digest(r) == a["registry_sha256"], "audit.registry_sha256", "must hash the Registry")
        c.require(digest(i) == a["index_sha256"], "audit.index_sha256", "must hash the Index")
    except ValueError:
        c.require(False, "bundle", "contains a non-finite number")
    for field, value in (("registry.created_at", r["created_at"]), ("registry.updated_at", r["updated_at"]),
                         ("audit.timestamp", a["timestamp"]), ("index.updated_at", i["updated_at"]),
                         ("source.observed_at", r["source"]["observed_at"]),
                         ("index_metadata.captured_at", r["index_metadata"]["captured_at"])):
        c.require(_utc(value), field, "must be UTC with Z")
    c.require(_not_before(r["updated_at"], r["created_at"]), "registry.updated_at",
              "must not precede created_at")
    _check_status(c, r, a)
    _check_media(c, r)
    _check_ocr(c, r, i)
    _check_search_text(c, r, i)
    _check_lists(c, r)
    _check_chain(c, r, a, previous)
    if c.problems:
        raise failure("INTEGRITY_FAILED", "commit", "Commit bundle violates record invariants",
                      details=c.problems[:50])
