"""DGE publication, the trusted publication-service boundary, reconciliation and
verification (PACKET-2-REVIEW-ROUND-1.md: R1, R3 and R5 approved; R2/R4 revised).

TEST ADAPTERS ONLY. `Publisher` refuses any Drive adapter that does not declare
`FIXTURE_ONLY = True`; the only such adapter is `fakes.FakeDrive`. No live cloud
writer exists or is enabled here, and evidence from these classes is fixture
evidence, never live-cloud evidence.

Object order for a commit: intent.json, the three payloads, then (after
`mark_publishing`) commit.json, the visibility point. Every object is created by
its pre-generated ID; a 409 on retry is resolved by comparing the provider's
SHA-256 of the stored bytes with the frozen SHA-256, never by overwriting.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any, Mapping

from .. import contract
from ..errors import failure
from . import envelopes
from .canonical import canonical_bytes, sha256_hex
from .envelopes import OBJECT_ROLES, PAYLOAD_ROLES
from .fakes import ConflictError, NotFoundError, TransientError
from .repository import CoordinatorRepository

# Records are single-request multipart creates only; no resumable session exists
# that could outlive a revoked credential (R2 revision section 2.2).
MULTIPART_LIMIT_BYTES = 5 * 1024 * 1024

_ROLE_PROPERTY = {"intent.json": "intent", "registry.json": "payload", "audit.json": "payload",
                  "index.json": "payload", "commit.json": "commit"}


def _now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _payloads(bundle: Mapping[str, Any]) -> dict[str, bytes]:
    return {"registry.json": canonical_bytes(bundle["registry"]), "audit.json": canonical_bytes(bundle["audit"]),
            "index.json": canonical_bytes(bundle["index"])}


class Publisher:
    """Publishes frozen reservations and intents to the DGE through a test adapter."""

    def __init__(self, repo: CoordinatorRepository, drive, *, retries: int = 3, fault=None):
        if not getattr(drive, "FIXTURE_ONLY", False):
            raise RuntimeError("Only test adapters are approved; no live Drive writer is enabled")
        self.repo, self.drive, self.retries = repo, drive, retries
        self.fault = fault or (lambda point: None)

    def _put(self, object_id: str, name: str, data: bytes, *, parent: str, props: dict) -> None:
        if len(data) > MULTIPART_LIMIT_BYTES:
            raise failure("CONTRACT_MISMATCH", "commit", "Record too large for a single-request upload",
                          details=[(name, f"{len(data)} bytes; resumable uploads are not used for records")])
        expected = sha256_hex(data)
        for attempt in range(self.retries):
            try:
                self.drive.create(object_id, name=name, parent=parent, data=data, app_properties=props)
                break
            except ConflictError:
                break  # already created (a lost response or another worker): verify below
            except TransientError:
                if attempt == self.retries - 1:
                    raise failure("STORAGE_UNAVAILABLE", "commit", "DGE write failed; retry with the same IDs",
                                  retryable=True, retry_after_seconds=5) from None
        try:
            stored = self.drive.metadata(object_id)["sha256Checksum"]  # computed from the stored bytes
        except NotFoundError:
            raise failure("STORAGE_UNAVAILABLE", "commit", "DGE object missing after write",
                          retryable=True, retry_after_seconds=5) from None
        if stored != expected:
            raise failure("INTEGRITY_FAILED", "commit", "DGE object holds different bytes",
                          details=[(name, f"{object_id} stored {stored[:12]}..., frozen {expected[:12]}...")])

    @staticmethod
    def _parent(scope: contract.Scope, asset_id: str) -> str:
        return f"{scope.tenant_id}/{scope.workspace_id}/{asset_id}"

    # -- reservations -------------------------------------------------------------------

    def publish_reservation(self, scope: contract.Scope, ingestion_id: str, *, reconcile: bool = False) -> None:
        record = self.repo.reservation_record(scope, ingestion_id)
        if not record["published"]:
            self._put(record["record_object_id"], "reservation.json", record["record_bytes"],
                      parent=self._parent(scope, record["asset_id"]),
                      props={"role": "reservation", "ingestion_id": ingestion_id})
            self.repo.mark_reservation_published(scope=scope, ingestion_id=ingestion_id, reconcile=reconcile)

    def admit(self, scope: contract.Scope, *, idempotency_key: str, request_digest: str,
              request: Mapping[str, Any], pinned: Mapping[str, Any] | None = None,
              defer_first_commit: bool = False) -> contract.Reservation:
        """Reserve, then publish reservation.json before the ingestion is accepted."""
        (record_id,) = self.drive.generate_ids(1)
        reservation = self.repo.reserve(scope=scope, idempotency_key=idempotency_key,
                                        request_digest=request_digest, request=request, pinned=pinned,
                                        record_object_id=record_id, defer_first_commit=defer_first_commit)
        self.publish_reservation(scope, reservation.ingestion_id)
        return reservation

    # -- commits -------------------------------------------------------------------------

    def freeze(self, scope: contract.Scope, bundle: Mapping[str, Any], generation: int,
               *, published_at: str | None = None) -> dict:
        """Freeze the bundle once; a retry returns the originally frozen object IDs."""
        parts = _payloads(bundle)
        try:
            existing = self.repo.frozen_intent(scope, bundle["commit_id"])
        except contract.PipelineFailure as exc:
            if exc.error["code"] != "NOT_FOUND":
                raise
        else:
            if existing["payloads"] != parts:
                raise failure("INTEGRITY_FAILED", "commit", "Same commit ID with different bytes")
            return existing["object_ids"]
        ids = dict(zip(OBJECT_ROLES, self.drive.generate_ids(len(OBJECT_ROLES))))
        _, previous = self.repo.head_marker(scope, bundle["registry"]["asset_id"])
        marker = envelopes.build_marker(scope=scope, bundle=bundle, object_ids=ids, payloads=parts,
                                        previous_marker_sha=previous, published_at=published_at or _now_text())
        self.repo.freeze_intent(scope=scope, commit_id=bundle["commit_id"], payloads=parts, object_ids=ids,
                                marker=marker, generation=generation)
        return ids

    def upload(self, scope: contract.Scope, commit_id: str, generation: int, *,
               reconcile: bool = False) -> tuple[str, str]:
        """Upload the exact frozen objects; returns (marker ID, marker SHA-256)."""
        frozen = self.repo.frozen_intent(scope, commit_id)
        ids, parent = frozen["object_ids"], self._parent(scope, frozen["asset_id"])
        files = dict(frozen["payloads"], **{"intent.json": frozen["intent"], "commit.json": frozen["marker"]})
        props = {"commit_id": commit_id, "asset_id": frozen["asset_id"],
                 "registry_version": str(frozen["registry_version"])}
        for role in ("intent.json",) + PAYLOAD_ROLES:
            self._put(ids[role], role, files[role], parent=parent, props=dict(props, role=_ROLE_PROPERTY[role]))
        if frozen["state"] == "frozen":
            self.repo.mark_publishing(scope=scope, commit_id=commit_id, generation=generation, reconcile=reconcile)
        self.fault("publisher:before_marker")
        self._put(ids["commit.json"], "commit.json", files["commit.json"], parent=parent,
                  props=dict(props, role="commit"))
        self.fault("publisher:after_marker")
        return ids["commit.json"], sha256_hex(files["commit.json"])

    def execute(self, scope: contract.Scope, commit_id: str, generation: int, *,
                processing_fingerprint: str | None = None, reconcile: bool = False) -> contract.CommitReceipt:
        frozen = self.repo.frozen_intent(scope, commit_id)
        if frozen["state"] == "published":  # replay: the stored receipt
            return self.repo.record_published(scope=scope, commit_id=commit_id,
                                              marker_object_id=frozen["object_ids"]["commit.json"],
                                              marker_sha256=sha256_hex(frozen["marker"]), generation=generation)
        marker_id, marker_sha = self.upload(scope, commit_id, generation, reconcile=reconcile)
        return self.repo.record_published(scope=scope, commit_id=commit_id, marker_object_id=marker_id,
                                          marker_sha256=marker_sha, generation=generation,
                                          processing_fingerprint=processing_fingerprint, reconcile=reconcile)

    def publish(self, scope: contract.Scope, bundle: Mapping[str, Any], generation: int, *,
                processing_fingerprint: str | None = None, published_at: str | None = None) -> contract.CommitReceipt:
        self.freeze(scope, bundle, generation, published_at=published_at)
        return self.execute(scope, bundle["commit_id"], generation, processing_fingerprint=processing_fingerprint)


# -- R4: the trusted publication service ----------------------------------------------------

class WorkerCredentials:
    """Test stand-in for the service's identity provider: short-lived HMAC tokens
    naming a worker and the one scope it may publish into."""

    def __init__(self, key: bytes):
        if len(key) < 32:
            raise ValueError("key must be at least 32 bytes")
        self._key = key

    def issue(self, worker_id: str, scope: contract.Scope, *, ttl_seconds: int = 300) -> str:
        body = canonical_bytes({"worker": worker_id, "tenant_id": scope.tenant_id,
                                "workspace_id": scope.workspace_id, "expires": time.time() + ttl_seconds})
        return base64.urlsafe_b64encode(hmac.new(self._key, body, hashlib.sha256).digest() + body).decode()

    def verify(self, token: str, scope: contract.Scope) -> str:
        try:
            raw = base64.urlsafe_b64decode(token.encode())
            mac, body = raw[:32], raw[32:]
            if not hmac.compare_digest(mac, hmac.new(self._key, body, hashlib.sha256).digest()):
                raise ValueError("signature")
            claims = json.loads(body)
            if claims["expires"] < time.time():
                raise ValueError("expired")
            if (claims["tenant_id"], claims["workspace_id"]) != (scope.tenant_id, scope.workspace_id):
                raise ValueError("scope")
            return claims["worker"]
        except (ValueError, KeyError, TypeError):
            raise failure("CONTRACT_MISMATCH", "commit", "Publication request is not authorized for this scope",
                          details=[("credential", "invalid, expired or for another scope")]) from None


class PublicationService:
    """The only component holding DGE write access (R4). Ordinary ingestion, OCR and
    API workers hold a credential for this service, never a Drive credential.

    Its whole surface is two append-only operations. It validates the caller's
    scope, the bundle (via the coordinator's freeze checks), and publishes exactly
    the frozen bytes to the frozen object IDs. It has no update or delete
    operation for published content. A compromise of this service, or of a Drive
    administrator, is not prevented by these checks: it is detected by the
    Verifier and quarantined.
    """

    def __init__(self, publisher: Publisher, credentials: WorkerCredentials, generation: int):
        self._publisher, self._credentials, self._generation = publisher, credentials, generation

    def admit(self, credential: str, *, scope: contract.Scope, idempotency_key: str, request_digest: str,
              request: Mapping[str, Any], pinned: Mapping[str, Any] | None = None) -> contract.Reservation:
        self._credentials.verify(credential, scope)
        return self._publisher.admit(scope, idempotency_key=idempotency_key, request_digest=request_digest,
                                     request=request, pinned=pinned)

    def commit(self, credential: str, *, scope: contract.Scope, bundle: Mapping[str, Any], expected_version: int,
               processing_fingerprint: str | None = None) -> contract.CommitReceipt:
        self._credentials.verify(credential, scope)
        if bundle.get("scope") != envelopes.scope_json(scope):
            raise failure("CONTRACT_MISMATCH", "commit", "Bundle scope differs from the authorized scope")
        if bundle["registry"]["canonical_record_version"] != expected_version + 1:
            raise failure("VERSION_CONFLICT", "commit", "Bundle is not the expected version",
                          details=[("expected_version", str(expected_version))])
        return self._publisher.publish(scope, bundle, self._generation, processing_fingerprint=processing_fingerprint)


# -- verification and reconciliation ----------------------------------------------------------

class Verifier:
    """Re-checks every recorded object in the DGE against the coordinator's SHA-256
    (P2-E detection). Missing, deleted or edited objects quarantine the asset."""

    def __init__(self, repo: CoordinatorRepository, drive):
        self.repo, self.drive = repo, drive

    def run(self) -> list[tuple[contract.Scope, str, str]]:
        found = []
        for record in self.repo.published_objects():
            problems = []
            for role, (object_id, expected) in record["objects"].items():
                try:
                    stored = self.drive.metadata(object_id)["sha256Checksum"]
                except NotFoundError:
                    problems.append(f"v{record['registry_version']} {role} missing")
                    continue
                if stored != expected:
                    problems.append(f"v{record['registry_version']} {role} edited")
            if problems:
                reason = "; ".join(problems)
                self.repo.quarantine(record["scope"], record["asset_id"], reason)
                found.append((record["scope"], record["asset_id"], reason))
        return found


class Reconciler:
    """Closed-mode reconciliation in the current generation (P2-B, R2 revision).

    Precondition: the coordinator is in recovery mode under `generation` and the
    old publication authority has been fenced (see the R2 procedure). Public
    admissions and reads stay closed throughout; `resume` is the last step and is
    refused while anything is unresolved.
    """

    def __init__(self, repo: CoordinatorRepository, drive, *, fault=None):
        self.repo, self.drive = repo, drive
        self.publisher = Publisher(repo, drive, fault=fault)

    def _download(self, meta: dict) -> bytes:
        data = self.drive.download(meta["id"])
        if sha256_hex(data) != meta["sha256Checksum"]:
            raise failure("STORAGE_UNAVAILABLE", "commit", "DGE download did not match its checksum",
                          retryable=True, retry_after_seconds=5)
        return data

    def run(self, generation: int, *, resume: bool = True) -> dict:
        current, mode = self.repo.publisher_generation()
        if (current, mode) != (generation, "recovery"):
            raise failure("CONTRACT_MISMATCH", "commit", "Reconcile only in recovery mode, current generation")
        report = {"reservations_imported": 0, "intents_imported": 0, "completed": [], "quarantined": []}

        # 1. Rebuild reservations and intents from the DGE (after PostgreSQL loss or an old backup).
        for meta in self.drive.query(role="reservation"):
            data = self._download(meta)
            reservation = envelopes.parse_reservation(data)
            if meta["id"] != reservation["record_object_id"]:
                scope = contract.Scope(**reservation["scope"])
                self.repo.quarantine(scope, reservation["asset_id"], "Reservation object ID mismatch")
                report["quarantined"].append((scope.tenant_id, scope.workspace_id, reservation["asset_id"]))
                continue
            if self.repo.import_reservation(data, generation, source_object_id=meta["id"]) == "imported":
                report["reservations_imported"] += 1
        intents = []
        for meta in self.drive.query(role="intent"):
            data = self._download(meta)
            e = envelopes.parse_intent(data)  # an unattributable envelope stops reconciliation (fail closed)
            if meta["id"] != e["object_ids"]["intent.json"]:
                scope = e["scope_obj"]
                self.repo.quarantine(scope, e["asset_id"], "Intent object ID mismatch")
                report["quarantined"].append((scope.tenant_id, scope.workspace_id, e["asset_id"]))
                continue
            intents.append(((e["scope"]["tenant_id"], e["scope"]["workspace_id"], e["asset_id"],
                             e["registry_version"]), data, meta["id"]))
        known_commits = set()
        for _, data, object_id in sorted(intents, key=lambda item: item[0]):
            outcome = self.repo.import_intent(data, generation, source_object_id=object_id)
            report["intents_imported"] += outcome == "imported"
            e = envelopes.parse_intent(data)
            known_commits.add(e["commit_id"])
            if outcome == "conflict":
                report["quarantined"].append((e["scope"]["tenant_id"], e["scope"]["workspace_id"], e["asset_id"]))

        # 2. A marker with no intent anywhere was not written by the service: quarantine.
        for meta in self.drive.query(role="commit"):
            m = json.loads(self._download(meta))
            if m.get("commit_id") not in known_commits:
                scope = contract.Scope(m["scope"]["tenant_id"], m["scope"]["workspace_id"])
                try:
                    self.repo.frozen_intent(scope, m["commit_id"])
                except contract.PipelineFailure:
                    self.repo.quarantine(scope, m["asset_id"], "DGE marker without a frozen intent")
                    report["quarantined"].append((scope.tenant_id, scope.workspace_id, m["asset_id"]))

        # 3. Reservation records that never reached the DGE.
        for r in self.repo.unpublished_reservations():
            self.publisher.publish_reservation(contract.Scope(r["tenant_id"], r["workspace_id"]),
                                               r["ingestion_id"], reconcile=True)

        # 4. Complete every unpublished intent with its frozen bytes, in version order.
        for intent in self.repo.unpublished_intents():
            if intent["quarantined"]:
                continue
            scope = contract.Scope(intent["tenant_id"], intent["workspace_id"])
            if intent["generation"] != generation:
                self.repo.adopt_intent(scope=scope, commit_id=intent["commit_id"], generation=generation)
            try:
                self.publisher.execute(scope, intent["commit_id"], generation, reconcile=True)
            except contract.PipelineFailure as exc:
                if exc.error["code"] != "INTEGRITY_FAILED":
                    raise  # transient: stay closed, run again
                self.repo.quarantine(scope, intent["asset_id"], exc.error["message"])
                report["quarantined"].append((scope.tenant_id, scope.workspace_id, intent["asset_id"]))
                continue
            report["completed"].append(intent["commit_id"])

        # 5. Verify every recorded object against the DGE before reopening.
        report["quarantined"] += [(s.tenant_id, s.workspace_id, a) for s, a, _ in Verifier(self.repo, self.drive).run()]
        if resume:
            self.repo.resume(generation)
        return report

