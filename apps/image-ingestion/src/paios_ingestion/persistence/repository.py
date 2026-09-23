"""PostgreSQL coordinator repositories (Packet 2 development foundation).

Scope: reservations, frozen commit intents, heads, append-only revision copies,
jobs, content lookup, the projection outbox, versioned search, publisher-generation
fencing, the closed-mode reconciliation path and quarantine. No cloud publication
happens here: `persistence.publisher` drives it, and only against test adapters.

Every scoped operation runs in one transaction with `paios.tenant_id` and
`paios.workspace_id` set, so row-level security also enforces scope, and every
scoped query also filters on tenant and workspace explicitly.

Serving states (publisher_state):
  open      admissions, publication and authoritative reads are served, but only
            while the stored fingerprint matches this cluster/timeline/database
            (U1: a restored backup starts closed).
  recovery  public admissions, publication and reads are closed. The privileged
            reconciliation path (a BYPASSRLS or superuser role, current
            generation) may import, adopt, complete and quarantine intents.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import psycopg
from psycopg.types.json import Jsonb

from .. import contract
from ..errors import failure
from . import envelopes
from .canonical import bundle_sha256, canonical_bytes, is_canonical, request_digest as compute_request_digest, sha256_hex
from .envelopes import OBJECT_ROLES, PAYLOAD_ROLES, scope_json as _scope_json
from .validation import validate_bundle

MAX_PROJECTION_ATTEMPTS = 8
CURSOR_TTL_SECONDS = 15 * 60
PRUNE_MARGIN_SECONDS = 5 * 60


def _utc_text(moment: datetime) -> str:
    moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ" if moment.microsecond else "%Y-%m-%dT%H:%M:%SZ")


def _now_text() -> str:
    return _utc_text(datetime.now(timezone.utc))


def _closed(stage: str = "commit"):
    return failure("PERSISTENCE_UNAVAILABLE", stage,
                   "Coordinator is closed for recovery; it reopens after reconciliation",
                   retryable=True, retry_after_seconds=30)


class CoordinatorRepository:
    """Implements the reservation/commit/read half of the IngestionRepository,
    AssetRepository, AuditRepository and IndexRepository protocols against PostgreSQL."""

    def __init__(self, conninfo: str, *, cursor_secret: bytes,
                 fault: Callable[[str], None] | None = None):
        if len(cursor_secret) < 32:
            raise ValueError("cursor_secret must be at least 32 bytes")
        self.conninfo = conninfo
        self.cursor_secret = cursor_secret
        self.fault = fault or (lambda point: None)  # crash/barrier hook for tests

    # -- plumbing -----------------------------------------------------------------

    @contextmanager
    def _tx(self, scope: contract.Scope | None = None, *, snapshot: bool = False):
        """One transaction. `snapshot=True` is a read-only REPEATABLE READ transaction:
        every statement sees the same database state (U2)."""
        try:
            conn = psycopg.connect(self.conninfo)
        except psycopg.OperationalError:
            raise failure("PERSISTENCE_UNAVAILABLE", "commit", "Coordinator database unavailable",
                          retryable=True, retry_after_seconds=5) from None
        try:
            if snapshot:
                conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
                conn.read_only = True
            with conn.transaction():
                conn.execute("SELECT set_config('search_path', 'paios_ingest', true)")
                if scope is not None:
                    conn.execute("SELECT set_config('paios.tenant_id', %s, true), "
                                 "set_config('paios.workspace_id', %s, true)",
                                 (scope.tenant_id, scope.workspace_id))
                yield conn
        except psycopg.OperationalError:
            raise failure("PERSISTENCE_UNAVAILABLE", "commit", "Coordinator transaction failed",
                          retryable=True, retry_after_seconds=5) from None
        finally:
            conn.close()

    @staticmethod
    def _publisher(conn, *, lock: bool = False) -> tuple[int, str, bool]:
        row = conn.execute("SELECT generation, mode, opened_fingerprint IS NOT NULL AND "
                           "opened_fingerprint = cluster_fingerprint() FROM publisher_state"
                           + (" FOR SHARE" if lock else "")).fetchone()
        return row[0], row[1], bool(row[2])

    @staticmethod
    def _serving(conn, stage: str = "commit") -> None:
        """U1: authoritative reads need an open coordinator on this exact database."""
        if not conn.execute("SELECT serving_ready()").fetchone()[0]:
            raise _closed(stage)

    @staticmethod
    def _not_quarantined(conn, scope: contract.Scope, asset_id: str) -> None:
        if conn.execute("SELECT 1 FROM quarantined_assets WHERE asset_id = %s AND tenant_id = %s "
                        "AND workspace_id = %s", (asset_id, scope.tenant_id, scope.workspace_id)).fetchone():
            raise failure("INTEGRITY_FAILED", "commit", "Asset is quarantined pending investigation")

    def _authorize(self, conn, generation: int | None = None, *, reconcile: bool = False) -> int:
        """Public path: open, on this database, current generation. Reconciliation
        path: recovery mode, current generation and a privileged role."""
        current, mode, same_db = self._publisher(conn, lock=True)
        if reconcile:
            if mode != "recovery":
                raise failure("CONTRACT_MISMATCH", "commit", "Reconciliation runs only in recovery mode")
            privileged = conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles "
                                      "WHERE rolname = current_user").fetchone()[0]
            if not privileged:
                raise failure("CONTRACT_MISMATCH", "commit", "Reconciliation needs the privileged role")
        elif mode != "open" or not same_db:
            raise _closed()
        if generation is not None and generation != current:
            raise failure("VERSION_CONFLICT", "commit", "Publisher generation has been fenced",
                          details=[("generation", f"{generation} is not current ({current})")])
        return current

    # -- reservations (A01, A02, R1) ------------------------------------------------------

    def reserve(self, *, scope: contract.Scope, idempotency_key: str, request_digest: str,
                request: Mapping[str, Any], pinned: Mapping[str, Any] | None = None,
                record_object_id: str | None = None) -> contract.Reservation:
        """Allocate IDs once per scoped key and freeze reservation.json bytes. The
        record must be published (`mark_reservation_published`) before revision 1
        can be frozen, so no ingestion is durably accepted without it."""
        if not record_object_id:
            raise failure("CONTRACT_MISMATCH", "request", "A pre-generated reservation record ID is required")
        if compute_request_digest(scope, request) != request_digest:
            raise failure("CONTRACT_MISMATCH", "request", "request_digest does not match the scoped request")
        key_sha = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        ingestion_id, asset_id, first_commit = (str(uuid.uuid4()) for _ in range(3))
        record = envelopes.build_reservation(
            scope=scope, idempotency_key_sha256=key_sha, request=request, digest=request_digest,
            ingestion_id=ingestion_id, asset_id=asset_id, first_commit_id=first_commit,
            pinned=dict(pinned or {}), record_object_id=record_object_id, created_at=_now_text())
        parsed = envelopes.parse_reservation(record)
        with self._tx(scope) as conn:
            self._authorize(conn)
            inserted = conn.execute(
                """INSERT INTO ingestion_reservations (tenant_id, workspace_id, idempotency_key_sha256,
                       request_digest, request, pinned, ingestion_id, asset_id, state, first_commit_id,
                       record_object_id, record_bytes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'reserved', %s, %s, %s)
                   ON CONFLICT (tenant_id, workspace_id, idempotency_key_sha256) DO NOTHING
                   RETURNING 1""",
                (scope.tenant_id, scope.workspace_id, key_sha, request_digest, Jsonb(parsed["request"]),
                 Jsonb(parsed["pinned"]), ingestion_id, asset_id, first_commit, record_object_id,
                 record)).fetchone() is not None
            row = conn.execute(
                """SELECT request_digest, ingestion_id, asset_id FROM ingestion_reservations
                   WHERE tenant_id = %s AND workspace_id = %s AND idempotency_key_sha256 = %s""",
                (scope.tenant_id, scope.workspace_id, key_sha)).fetchone()
        stored_digest, stored_ingestion, stored_asset = row
        if stored_digest != request_digest:
            raise failure("IDEMPOTENCY_CONFLICT", "request",
                          "Idempotency key was already used with a different request")
        return contract.Reservation(ingestion_id=str(stored_ingestion), asset_id=str(stored_asset),
                                    request_digest=stored_digest, replay=not inserted)

    def reservation_record(self, scope: contract.Scope, ingestion_id: str) -> dict:
        """The frozen reservation.json bytes and its object ID (for the publisher)."""
        with self._tx(scope) as conn:
            row = conn.execute(
                "SELECT record_object_id, record_bytes, record_published_at IS NOT NULL, first_commit_id, "
                "asset_id FROM ingestion_reservations WHERE ingestion_id = %s AND tenant_id = %s "
                "AND workspace_id = %s", (ingestion_id, scope.tenant_id, scope.workspace_id)).fetchone()
        if row is None:
            raise failure("NOT_FOUND", "request", "Ingestion not found")
        return {"record_object_id": row[0], "record_bytes": bytes(row[1]), "published": row[2],
                "first_commit_id": str(row[3]), "asset_id": str(row[4]), "ingestion_id": ingestion_id}

    def mark_reservation_published(self, *, scope: contract.Scope, ingestion_id: str,
                                   reconcile: bool = False) -> None:
        """Called only after reservation.json was verified in the DGE by its SHA-256."""
        with self._tx(scope) as conn:
            self._authorize(conn, reconcile=reconcile)
            updated = conn.execute(
                "UPDATE ingestion_reservations SET record_published_at = coalesce(record_published_at, now()) "
                "WHERE ingestion_id = %s AND tenant_id = %s AND workspace_id = %s",
                (ingestion_id, scope.tenant_id, scope.workspace_id)).rowcount
        if not updated:
            raise failure("NOT_FOUND", "request", "Ingestion not found")

    def reservation_pins(self, scope: contract.Scope, ingestion_id: str) -> dict:
        with self._tx(scope, snapshot=True) as conn:
            self._serving(conn)
            row = conn.execute("SELECT pinned FROM ingestion_reservations WHERE ingestion_id = %s"
                               " AND tenant_id = %s AND workspace_id = %s",
                               (ingestion_id, scope.tenant_id, scope.workspace_id)).fetchone()
        if row is None:
            raise failure("NOT_FOUND", "request", "Ingestion not found")
        return row[0]

    def head_marker(self, scope: contract.Scope, asset_id: str) -> tuple[int, str | None]:
        """(version, marker SHA-256) of the recorded head; (0, None) before revision 1."""
        with self._tx(scope) as conn:
            row = conn.execute("SELECT registry_version, marker_sha256 FROM registry_heads WHERE asset_id = %s"
                               " AND tenant_id = %s AND workspace_id = %s",
                               (asset_id, scope.tenant_id, scope.workspace_id)).fetchone()
        return (row[0], row[1]) if row else (0, None)

    # -- frozen intents (P2-A, R1) ----------------------------------------------------------

    def freeze_intent(self, *, scope: contract.Scope, commit_id: str, payloads: Mapping[str, bytes],
                      object_ids: Mapping[str, str], marker: bytes, generation: int) -> str:
        """Freeze exact payload, marker and intent.json bytes and all five object IDs
        for one revision slot. Returns the stored state.

        Re-freezing the same commit with identical payloads, marker and IDs is a
        replay and keeps the originally frozen intent.json bytes; different bytes
        for the same commit are INTEGRITY_FAILED; a different commit for an occupied
        slot is VERSION_CONFLICT.
        """
        if set(payloads) != set(PAYLOAD_ROLES) or set(object_ids) != set(OBJECT_ROLES):
            raise failure("CONTRACT_MISMATCH", "commit", "Intent needs three payloads and five object IDs")
        for role in PAYLOAD_ROLES:
            if not is_canonical(payloads[role]):
                raise failure("INTEGRITY_FAILED", "commit", "Payload bytes are not canonical JSON",
                              details=[(role, "not canonical UTF-8/sorted/compact JSON")])
        registry = json.loads(payloads["registry.json"])
        bundle = {"schema_version": contract.CONTRACT_RELEASE, "commit_id": commit_id,
                  "scope": _scope_json(scope), "registry": registry,
                  "audit": json.loads(payloads["audit.json"]), "index": json.loads(payloads["index.json"])}
        if not isinstance(registry, dict) or not isinstance(registry.get("canonical_record_version"), int):
            raise failure("CONTRACT_MISMATCH", "commit", "registry.json is not a Registry record")
        try:
            return self._freeze(scope, commit_id, dict(payloads), dict(object_ids), bytes(marker), generation,
                                bundle, registry)
        except psycopg.errors.UniqueViolation:  # a concurrent writer took the slot first; re-evaluate
            return self._freeze(scope, commit_id, dict(payloads), dict(object_ids), bytes(marker), generation,
                                bundle, registry)

    def _freeze(self, scope, commit_id, payloads, object_ids, marker, generation, bundle, registry) -> str:
        version, asset_id = registry["canonical_record_version"], registry["asset_id"]
        payload_sha = {role: sha256_hex(payloads[role]) for role in PAYLOAD_ROLES}
        identity = bundle_sha256(commit_id, version, payload_sha)
        marker_sha = sha256_hex(marker)
        with self._tx(scope) as conn:
            self._authorize(conn, generation)
            self._not_quarantined(conn, scope, asset_id)
            existing = conn.execute(
                "SELECT commit_id, bundle_sha256, marker_sha256, object_ids, state FROM commit_intents "
                "WHERE tenant_id = %s AND workspace_id = %s AND asset_id = %s AND registry_version = %s",
                (scope.tenant_id, scope.workspace_id, asset_id, version)).fetchone()
            if existing:
                if str(existing[0]) != commit_id:
                    raise failure("VERSION_CONFLICT", "commit", "Revision slot already has a commit",
                                  details=[("registry_version", str(version))])
                if (existing[1], existing[2], existing[3]) != (identity, marker_sha, object_ids):
                    raise failure("INTEGRITY_FAILED", "commit", "Same commit ID with different bytes")
                return existing[4]
            head = conn.execute(
                "SELECT registry_version, registry, marker_sha256 FROM registry_heads "
                "WHERE tenant_id = %s AND workspace_id = %s AND asset_id = %s FOR UPDATE",
                (scope.tenant_id, scope.workspace_id, asset_id)).fetchone()
            head_version = head[0] if head else 0
            if version != head_version + 1:
                raise failure("VERSION_CONFLICT", "commit", "Stale expected version",
                              details=[("registry_version", f"{version} after head {head_version}")])
            if version == 1:
                self._require_published_reservation(conn, scope, registry, commit_id)
            validate_bundle(bundle, head[1] if head else None)
            envelopes.check_marker(marker, scope=scope, commit_id=commit_id, registry=registry,
                                   payloads=payloads, object_ids=object_ids,
                                   previous_marker_sha=head[2] if head else None)
            intent = envelopes.build_intent(scope=scope, commit_id=commit_id, registry=registry,
                                            object_ids=object_ids, payloads=payloads, marker=marker,
                                            frozen_at=_now_text())
            self._insert_intent(conn, scope, registry, commit_id, generation, identity, object_ids, payloads,
                                marker, marker_sha, intent)
            return "frozen"

    @staticmethod
    def _require_published_reservation(conn, scope, registry, commit_id) -> None:
        row = conn.execute(
            "SELECT asset_id, first_commit_id, record_published_at IS NOT NULL FROM ingestion_reservations "
            "WHERE ingestion_id = %s AND tenant_id = %s AND workspace_id = %s",
            (registry["ingestion_id"], scope.tenant_id, scope.workspace_id)).fetchone()
        if row is None or str(row[0]) != registry["asset_id"] or str(row[1]) != commit_id:
            raise failure("CONTRACT_MISMATCH", "commit",
                          "Revision 1 must use the reserved asset, ingestion and first commit IDs")
        if not row[2]:
            raise failure("CONTRACT_MISMATCH", "commit",
                          "reservation.json must be published before the ingestion is accepted")

    @staticmethod
    def _insert_intent(conn, scope, registry, commit_id, generation, identity, object_ids, payloads, marker,
                       marker_sha, intent) -> None:
        conn.execute(
            """INSERT INTO commit_intents (tenant_id, workspace_id, asset_id, registry_version, commit_id,
                   ingestion_id, generation, bundle_sha256, object_ids, registry_bytes, audit_bytes,
                   index_bytes, marker_bytes, marker_sha256, state, intent_bytes, intent_sha256)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'frozen',%s,%s)""",
            (scope.tenant_id, scope.workspace_id, registry["asset_id"], registry["canonical_record_version"],
             commit_id, registry["ingestion_id"], generation, identity, Jsonb(dict(object_ids)),
             payloads["registry.json"], payloads["audit.json"], payloads["index.json"], marker, marker_sha,
             intent, sha256_hex(intent)))

    def frozen_intent(self, scope: contract.Scope, commit_id: str) -> dict:
        """The exact bytes and IDs a (possibly different) worker must publish."""
        with self._tx(scope) as conn:
            row = conn.execute(
                "SELECT object_ids, registry_bytes, audit_bytes, index_bytes, marker_bytes, state, generation, "
                "intent_bytes, asset_id, registry_version FROM commit_intents WHERE commit_id = %s "
                "AND tenant_id = %s AND workspace_id = %s",
                (commit_id, scope.tenant_id, scope.workspace_id)).fetchone()
        if row is None:
            raise failure("NOT_FOUND", "commit", "Commit intent not found")
        return {"object_ids": row[0], "payloads": {"registry.json": bytes(row[1]), "audit.json": bytes(row[2]),
                "index.json": bytes(row[3])}, "marker": bytes(row[4]), "state": row[5], "generation": row[6],
                "intent": bytes(row[7]), "asset_id": str(row[8]), "registry_version": row[9]}

    def mark_publishing(self, *, scope: contract.Scope, commit_id: str, generation: int,
                        reconcile: bool = False) -> None:
        """Last database check before the marker upload. From here the revision's
        visibility is uncertain until recorded, so reads of this asset return 503."""
        with self._tx(scope) as conn:
            self._authorize(conn, generation, reconcile=reconcile)
            row = conn.execute("SELECT state, generation, asset_id FROM commit_intents WHERE commit_id = %s "
                               "AND tenant_id = %s AND workspace_id = %s FOR UPDATE",
                               (commit_id, scope.tenant_id, scope.workspace_id)).fetchone()
            if row is None:
                raise failure("NOT_FOUND", "commit", "Commit intent not found")
            state, owner, asset_id = row
            if owner != generation:
                raise failure("VERSION_CONFLICT", "commit", "Intent belongs to another publisher generation")
            self._not_quarantined(conn, scope, str(asset_id))
            if state == "frozen":
                conn.execute("UPDATE commit_intents SET state = 'publishing', updated_at = now() "
                             "WHERE commit_id = %s AND tenant_id = %s AND workspace_id = %s",
                             (commit_id, scope.tenant_id, scope.workspace_id))

    # -- publication record (A03, A05, A21) ----------------------------------------------

    def record_published(self, *, scope: contract.Scope, commit_id: str, marker_object_id: str,
                         marker_sha256: str, generation: int, processing_fingerprint: str | None = None,
                         reconcile: bool = False) -> contract.CommitReceipt:
        """Make a verified, published marker visible: one transaction advances the head
        (compare-and-set N-1 -> N), appends revision copies, updates the job and
        content lookup, and enqueues projections. Replays return the stored receipt."""
        with self._tx(scope) as conn:
            row = conn.execute(
                "SELECT asset_id, registry_version, generation, state, receipt, marker_sha256, object_ids, "
                "registry_bytes, audit_bytes, index_bytes, ingestion_id FROM commit_intents "
                "WHERE commit_id = %s AND tenant_id = %s AND workspace_id = %s FOR UPDATE",
                (commit_id, scope.tenant_id, scope.workspace_id)).fetchone()
            if row is None:
                raise failure("NOT_FOUND", "commit", "Commit intent not found")
            (asset_id, version, owner, state, receipt, frozen_marker_sha, object_ids,
             registry_bytes, audit_bytes, index_bytes, ingestion_id) = row
            if state == "published":
                if receipt["manifest_sha256"] != marker_sha256:
                    raise failure("INTEGRITY_FAILED", "commit", "Replay with a different marker")
                return contract.CommitReceipt(**receipt)
            if state != "publishing":
                raise failure("CONTRACT_MISMATCH", "commit", "Intent was not marked publishing before upload")
            if marker_sha256 != frozen_marker_sha or marker_object_id != object_ids["commit.json"]:
                raise failure("INTEGRITY_FAILED", "commit", "Published marker differs from the frozen intent")
            self._authorize(conn, generation, reconcile=reconcile)
            if owner != generation:
                raise failure("VERSION_CONFLICT", "commit", "Intent belongs to another publisher generation")
            self._not_quarantined(conn, scope, str(asset_id))
            self.fault("record_published:before_head")
            registry = json.loads(bytes(registry_bytes))
            audit = json.loads(bytes(audit_bytes))
            index = json.loads(bytes(index_bytes))
            if version == 1:
                moved = conn.execute(
                    """INSERT INTO registry_heads (tenant_id, workspace_id, asset_id, registry_version,
                           commit_id, marker_sha256, registry) VALUES (%s,%s,%s,1,%s,%s,%s)
                       ON CONFLICT DO NOTHING""",
                    (scope.tenant_id, scope.workspace_id, asset_id, commit_id, marker_sha256,
                     Jsonb(registry))).rowcount
            else:
                moved = conn.execute(
                    """UPDATE registry_heads SET registry_version = %s, commit_id = %s, marker_sha256 = %s,
                           registry = %s, updated_at = now()
                       WHERE tenant_id = %s AND workspace_id = %s AND asset_id = %s AND registry_version = %s""",
                    (version, commit_id, marker_sha256, Jsonb(registry), scope.tenant_id,
                     scope.workspace_id, asset_id, version - 1)).rowcount
            if moved != 1:
                raise failure("VERSION_CONFLICT", "commit", "Head moved; reload and retry",
                              details=[("registry_version", str(version))])
            self.fault("record_published:after_head")
            keys = (scope.tenant_id, scope.workspace_id, asset_id, version, commit_id)
            conn.execute("INSERT INTO registry_revisions VALUES (%s,%s,%s,%s,%s,%s,%s)",
                         keys + (sha256_hex(bytes(registry_bytes)), Jsonb(registry)))
            conn.execute("INSERT INTO audit_events VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                         keys[:4] + (audit["event_id"], audit["previous_event_id"], commit_id,
                                     sha256_hex(bytes(audit_bytes)), Jsonb(audit)))
            conn.execute("INSERT INTO master_index_revisions VALUES (%s,%s,%s,%s,%s,%s,%s)",
                         keys + (sha256_hex(bytes(index_bytes)), Jsonb(index)))
            self.fault("record_published:after_revisions")
            conn.execute(
                """INSERT INTO jobs (tenant_id, workspace_id, ingestion_id, asset_id, status,
                       registry_version, error, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (ingestion_id) DO UPDATE SET status = EXCLUDED.status,
                       registry_version = EXCLUDED.registry_version, error = EXCLUDED.error,
                       updated_at = EXCLUDED.updated_at
                   WHERE jobs.registry_version < EXCLUDED.registry_version""",
                (scope.tenant_id, scope.workspace_id, ingestion_id, asset_id, registry["status"], version,
                 Jsonb(registry["error"]) if registry["error"] else None,
                 registry["created_at"], registry["updated_at"]))
            if processing_fingerprint and registry["status"] == "completed" and registry["identity"]["sha256"] \
                    and not any(r["type"] == "exact_duplicate_of" for r in registry["relationships"]):
                conn.execute("INSERT INTO content_lookup (tenant_id, workspace_id, original_sha256, "
                             "processing_fingerprint, asset_id, registry_version) VALUES (%s,%s,%s,%s,%s,%s)",
                             (scope.tenant_id, scope.workspace_id, registry["identity"]["sha256"],
                              processing_fingerprint, asset_id, version))
            conn.execute("INSERT INTO projection_queue (commit_id, projection, tenant_id, workspace_id, "
                         "asset_id, registry_version, state) VALUES (%s,'search',%s,%s,%s,%s,'pending')",
                         (commit_id, scope.tenant_id, scope.workspace_id, asset_id, version))
            new_state = "terminal" if registry["status"] in ("completed", "partial", "failed") else \
                "accepted" if version == 1 else None
            if new_state:
                conn.execute("UPDATE ingestion_reservations SET state = %s, updated_at = now() "
                             "WHERE ingestion_id = %s AND tenant_id = %s AND workspace_id = %s",
                             (new_state, ingestion_id, scope.tenant_id, scope.workspace_id))
            committed_at = conn.execute("SELECT now()").fetchone()[0]
            receipt = {"commit_id": commit_id, "registry_version": version,
                       "canonical_manifest_id": marker_object_id, "manifest_sha256": marker_sha256,
                       "committed_at": _utc_text(committed_at)}
            self.fault("record_published:before_commit")
            conn.execute("UPDATE commit_intents SET state = 'published', receipt = %s, updated_at = now() "
                         "WHERE commit_id = %s AND tenant_id = %s AND workspace_id = %s",
                         (Jsonb(receipt), commit_id, scope.tenant_id, scope.workspace_id))
        return contract.CommitReceipt(**receipt)

    def lookup_commit(self, scope: contract.Scope, commit_id: str) -> contract.CommitReceipt | None:
        with self._tx(scope, snapshot=True) as conn:
            self._serving(conn)
            row = conn.execute("SELECT receipt FROM commit_intents WHERE commit_id = %s AND state = 'published'"
                               " AND tenant_id = %s AND workspace_id = %s",
                               (commit_id, scope.tenant_id, scope.workspace_id)).fetchone()
        return contract.CommitReceipt(**row[0]) if row else None

    # -- reads (A07, A17, P2-C, U1, U2) ------------------------------------------------------

    @staticmethod
    def _publication_uncertain(conn, scope, asset_id, head_version) -> bool:
        return conn.execute(
            "SELECT 1 FROM commit_intents WHERE asset_id = %s AND state = 'publishing' "
            "AND registry_version > %s AND tenant_id = %s AND workspace_id = %s",
            (asset_id, head_version, scope.tenant_id, scope.workspace_id)).fetchone() is not None

    def get(self, scope: contract.Scope, asset_id: str) -> dict:
        """Latest committed Registry. Closed during recovery (U1). The readiness,
        head and uncertainty reads share one REPEATABLE READ snapshot (U2), so a
        publication committing between them cannot yield an obsolete head: the
        snapshot either predates the commit (intent still publishing: 503) or
        includes it (head N)."""
        with self._tx(scope, snapshot=True) as conn:
            self._serving(conn)
            self._not_quarantined(conn, scope, asset_id)
            head = conn.execute("SELECT registry_version, registry FROM registry_heads WHERE asset_id = %s"
                                " AND tenant_id = %s AND workspace_id = %s",
                                (asset_id, scope.tenant_id, scope.workspace_id)).fetchone()
            self.fault("get:between_reads")
            uncertain = self._publication_uncertain(conn, scope, asset_id, head[0] if head else 0)
        if uncertain:
            raise failure("PERSISTENCE_UNAVAILABLE", "commit",
                          "A newer revision is being published; its visibility is not yet known",
                          retryable=True, retry_after_seconds=2)
        if head is None:
            raise failure("NOT_FOUND", "commit", "Asset not found")
        return head[1]

    def get_job(self, scope: contract.Scope, ingestion_id: str) -> dict:
        """Job status, with the same recovery gate and snapshot rule as `get`."""
        with self._tx(scope, snapshot=True) as conn:
            self._serving(conn)
            row = conn.execute("SELECT asset_id, status, registry_version, attempt, error, created_at, updated_at "
                               "FROM jobs WHERE ingestion_id = %s AND tenant_id = %s AND workspace_id = %s",
                               (ingestion_id, scope.tenant_id, scope.workspace_id)).fetchone()
            if row is not None:
                self._not_quarantined(conn, scope, str(row[0]))
                self.fault("get_job:between_reads")
                if self._publication_uncertain(conn, scope, str(row[0]), row[2]):
                    raise failure("PERSISTENCE_UNAVAILABLE", "commit",
                                  "A newer revision is being published; its visibility is not yet known",
                                  retryable=True, retry_after_seconds=2)
        if row is None:
            raise failure("NOT_FOUND", "commit", "Ingestion not found")
        job = {"schema_version": contract.CONTRACT_RELEASE, "ingestion_id": ingestion_id,
               "asset_id": str(row[0]), "scope": _scope_json(scope), "status": row[1],
               "registry_version": row[2], "attempt": row[3], "created_at": _utc_text(row[5]),
               "updated_at": _utc_text(row[6]), "error": row[4]}
        contract.validate("Job", job)
        return job

    def list_events(self, scope: contract.Scope, asset_id: str, after_version: int, limit: int) -> list[dict]:
        with self._tx(scope, snapshot=True) as conn:
            self._serving(conn)
            self._not_quarantined(conn, scope, asset_id)
            rows = conn.execute("SELECT event FROM audit_events WHERE asset_id = %s AND registry_version > %s "
                                "AND tenant_id = %s AND workspace_id = %s ORDER BY registry_version LIMIT %s",
                                (asset_id, after_version, scope.tenant_id, scope.workspace_id, limit)).fetchall()
        return [r[0] for r in rows]

    def find_reusable(self, scope: contract.Scope, original_sha256: str,
                      processing_fingerprint: str) -> dict | None:
        """Latest committed successful candidate in this scope only (A16/A17/A28)."""
        with self._tx(scope, snapshot=True) as conn:
            self._serving(conn)
            row = conn.execute(
                """SELECT h.registry FROM content_lookup c JOIN registry_heads h
                     ON h.tenant_id = c.tenant_id AND h.workspace_id = c.workspace_id AND h.asset_id = c.asset_id
                   WHERE c.original_sha256 = %s AND c.processing_fingerprint = %s
                     AND h.registry->>'status' = 'completed'
                     AND c.tenant_id = %s AND c.workspace_id = %s
                     AND NOT EXISTS (SELECT 1 FROM quarantined_assets q WHERE q.tenant_id = c.tenant_id
                         AND q.workspace_id = c.workspace_id AND q.asset_id = c.asset_id)
                     AND NOT EXISTS (SELECT 1 FROM commit_intents i WHERE i.tenant_id = c.tenant_id
                         AND i.workspace_id = c.workspace_id AND i.asset_id = c.asset_id
                         AND i.state = 'publishing')
                   ORDER BY c.committed_at DESC, c.asset_id LIMIT 1""",
                (original_sha256, processing_fingerprint, scope.tenant_id, scope.workspace_id)).fetchone()
        return row[0] if row else None

    # -- recovery fencing and reconciliation (P2-B, R2) -------------------------------------

    def enter_recovery(self) -> int:
        """Close admissions, publication and reads, and fence every older generation.
        Waits for in-flight record_published transactions (they hold FOR SHARE)."""
        with self._tx() as conn:
            row = conn.execute("UPDATE publisher_state SET generation = generation + 1, mode = 'recovery', "
                               "opened_fingerprint = NULL, changed_at = now() RETURNING generation").fetchone()
        return row[0]

    def resume(self, generation: int) -> None:
        """Reopen only when reconciliation has resolved every uncertain intent: none
        is still publishing and every unpublished one belongs to this generation
        (quarantined assets excepted). Binds the open state to this database."""
        with self._tx() as conn:
            row = conn.execute("SELECT generation, mode, cluster_fingerprint() FROM publisher_state "
                               "FOR UPDATE").fetchone()
            if row[0] != generation or row[1] != "recovery":
                raise failure("VERSION_CONFLICT", "commit", "Recovery generation is not current")
            if row[2] is None:
                raise failure("PERSISTENCE_UNAVAILABLE", "commit", "Cannot identify this database; stay closed",
                              retryable=True, retry_after_seconds=30)
            unresolved = conn.execute(
                """SELECT count(*) FROM commit_intents i
                   WHERE i.state <> 'published' AND (i.state = 'publishing' OR i.generation <> %s)
                     AND NOT EXISTS (SELECT 1 FROM quarantined_assets q WHERE q.tenant_id = i.tenant_id
                         AND q.workspace_id = i.workspace_id AND q.asset_id = i.asset_id)""",
                (generation,)).fetchone()[0]
            if unresolved:
                raise failure("CONTRACT_MISMATCH", "commit", "Reconciliation is incomplete",
                              details=[("commit_intents", f"{unresolved} unresolved intent(s)")])
            conn.execute("UPDATE publisher_state SET mode = 'open', opened_fingerprint = %s, changed_at = now()",
                         (row[2],))

    def publisher_generation(self) -> tuple[int, str]:
        with self._tx() as conn:
            generation, mode, _ = self._publisher(conn)
            return generation, mode

    def serving_ready(self) -> bool:
        with self._tx() as conn:
            return bool(conn.execute("SELECT serving_ready()").fetchone()[0])

    def unpublished_intents(self) -> list[dict]:
        """Reconciliation input: every intent whose publication is not recorded."""
        with self._tx() as conn:
            rows = conn.execute(
                """SELECT i.tenant_id, i.workspace_id, i.asset_id, i.registry_version, i.commit_id, i.state,
                          i.generation, q.asset_id IS NOT NULL
                   FROM commit_intents i LEFT JOIN quarantined_assets q ON q.tenant_id = i.tenant_id
                        AND q.workspace_id = i.workspace_id AND q.asset_id = i.asset_id
                   WHERE i.state <> 'published'
                   ORDER BY i.tenant_id, i.workspace_id, i.asset_id, i.registry_version""").fetchall()
        return [dict(zip(("tenant_id", "workspace_id", "asset_id", "registry_version", "commit_id", "state",
                          "generation", "quarantined"), (r[0], r[1], str(r[2]), r[3], str(r[4]), r[5], r[6], r[7])))
                for r in rows]

    def unpublished_reservations(self) -> list[dict]:
        with self._tx() as conn:
            rows = conn.execute("SELECT tenant_id, workspace_id, ingestion_id FROM ingestion_reservations "
                                "WHERE record_published_at IS NULL ORDER BY created_at").fetchall()
        return [{"tenant_id": r[0], "workspace_id": r[1], "ingestion_id": str(r[2])} for r in rows]

    def published_objects(self) -> list[dict]:
        """Verifier baseline: every recorded commit's object IDs and expected SHA-256."""
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT tenant_id, workspace_id, asset_id, registry_version, commit_id, object_ids, intent_sha256, "
                "registry_bytes, audit_bytes, index_bytes, marker_sha256 FROM commit_intents "
                "WHERE state = 'published' ORDER BY tenant_id, workspace_id, asset_id, registry_version").fetchall()
        out = []
        for r in rows:
            expected = {"intent.json": r[6], "registry.json": sha256_hex(bytes(r[7])),
                        "audit.json": sha256_hex(bytes(r[8])), "index.json": sha256_hex(bytes(r[9])),
                        "commit.json": r[10]}
            out.append({"scope": contract.Scope(r[0], r[1]), "asset_id": str(r[2]), "registry_version": r[3],
                        "commit_id": str(r[4]), "objects": {role: (r[5][role], expected[role])
                                                            for role in OBJECT_ROLES}})
        return out

    def adopt_intent(self, *, scope: contract.Scope, commit_id: str, generation: int) -> None:
        """P2-B takeover, on the reconciliation path only: the current generation
        re-executes the same frozen intent. Only ownership changes; payload, marker
        and intent bytes and object IDs never do."""
        with self._tx(scope) as conn:
            self._authorize(conn, generation, reconcile=True)
            updated = conn.execute("UPDATE commit_intents SET generation = %s, updated_at = now() "
                                   "WHERE commit_id = %s AND state <> 'published' AND tenant_id = %s "
                                   "AND workspace_id = %s",
                                   (generation, commit_id, scope.tenant_id, scope.workspace_id)).rowcount
            if not updated:
                raise failure("NOT_FOUND", "commit", "No unpublished intent to adopt")

    def import_reservation(self, record: bytes, generation: int) -> str:
        """Rebuild a reservation row from a verified reservation.json (after PostgreSQL
        loss). Returns 'imported' or 'present'; different bytes for the same key or
        IDs are INTEGRITY_FAILED."""
        r = envelopes.parse_reservation(record)
        scope = contract.Scope(**r["scope"])
        with self._tx(scope) as conn:
            self._authorize(conn, generation, reconcile=True)
            existing = conn.execute(
                "SELECT record_bytes FROM ingestion_reservations WHERE tenant_id = %s AND workspace_id = %s "
                "AND (idempotency_key_sha256 = %s OR ingestion_id = %s OR asset_id = %s OR first_commit_id = %s "
                "OR record_object_id = %s)",
                (scope.tenant_id, scope.workspace_id, r["idempotency_key_sha256"], r["ingestion_id"],
                 r["asset_id"], r["first_commit_id"], r["record_object_id"])).fetchall()
            if existing:
                if len(existing) == 1 and bytes(existing[0][0]) == bytes(record):
                    conn.execute("UPDATE ingestion_reservations SET record_published_at = "
                                 "coalesce(record_published_at, now()) WHERE ingestion_id = %s AND tenant_id = %s "
                                 "AND workspace_id = %s", (r["ingestion_id"], scope.tenant_id, scope.workspace_id))
                    return "present"
                raise failure("INTEGRITY_FAILED", "commit", "Reservation record conflicts with the coordinator")
            conn.execute(
                """INSERT INTO ingestion_reservations (tenant_id, workspace_id, idempotency_key_sha256,
                       request_digest, request, pinned, ingestion_id, asset_id, state, first_commit_id,
                       record_object_id, record_bytes, record_published_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'reserved',%s,%s,%s, now())""",
                (scope.tenant_id, scope.workspace_id, r["idempotency_key_sha256"], r["request_digest"],
                 Jsonb(r["request"]), Jsonb(r["pinned"]), r["ingestion_id"], r["asset_id"],
                 r["first_commit_id"], r["record_object_id"], bytes(record)))
        return "imported"

    def import_intent(self, data: bytes, generation: int) -> str:
        """Rebuild or confirm a commit intent from a verified intent.json. Returns
        'imported', 'present' or 'conflict' (the asset is quarantined: two intents
        for one slot, or an intent that does not chain). Never allocates a new commit."""
        e = envelopes.parse_intent(data)
        scope = e["scope_obj"]
        version, asset_id = e["registry_version"], e["asset_id"]
        with self._tx(scope) as conn:
            self._authorize(conn, generation, reconcile=True)
            slot = conn.execute(
                "SELECT commit_id, intent_sha256 FROM commit_intents WHERE tenant_id = %s AND workspace_id = %s "
                "AND asset_id = %s AND registry_version = %s",
                (scope.tenant_id, scope.workspace_id, asset_id, version)).fetchone()
            if slot:
                if str(slot[0]) == e["commit_id"] and slot[1] == sha256_hex(bytes(data)):
                    return "present"
                self._quarantine(conn, scope, asset_id, f"slot {version}: DGE intent differs from coordinator")
                return "conflict"
            previous = None
            if version > 1:
                previous = conn.execute(
                    "SELECT registry_bytes, marker_sha256 FROM commit_intents WHERE tenant_id = %s "
                    "AND workspace_id = %s AND asset_id = %s AND registry_version = %s",
                    (scope.tenant_id, scope.workspace_id, asset_id, version - 1)).fetchone()
            problem = None
            bundle = {"schema_version": contract.CONTRACT_RELEASE, "commit_id": e["commit_id"],
                      "scope": _scope_json(scope), "registry": e["registry"],
                      "audit": json.loads(e["payload_bytes"]["audit.json"]),
                      "index": json.loads(e["payload_bytes"]["index.json"])}
            if version > 1 and previous is None:
                problem = f"slot {version}: predecessor intent is missing"
            else:
                try:
                    validate_bundle(bundle, json.loads(bytes(previous[0])) if previous else None)
                    envelopes.check_marker(e["marker_bytes"], scope=scope, commit_id=e["commit_id"],
                                           registry=e["registry"], payloads=e["payload_bytes"],
                                           object_ids=e["object_ids"],
                                           previous_marker_sha=previous[1] if previous else None)
                    if version == 1:
                        self._require_published_reservation(conn, scope, e["registry"], e["commit_id"])
                except contract.PipelineFailure as exc:
                    problem = f"slot {version}: {exc.error['message']}"
            if problem:
                self._quarantine(conn, scope, asset_id, problem)
                return "conflict"
            self._insert_intent(conn, scope, e["registry"], e["commit_id"], generation, e["bundle_sha256"],
                                e["object_ids"], e["payload_bytes"], e["marker_bytes"], e["marker_sha256"],
                                bytes(data))
        return "imported"

    @staticmethod
    def _quarantine(conn, scope, asset_id, reason) -> None:
        conn.execute("INSERT INTO quarantined_assets (tenant_id, workspace_id, asset_id, reason) "
                     "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                     (scope.tenant_id, scope.workspace_id, asset_id, reason[:500]))

    def quarantine(self, scope: contract.Scope, asset_id: str, reason: str) -> None:
        """Verifier/reconciler only (privileged role); allowed in either mode."""
        with self._tx(scope) as conn:
            if not conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
                                ).fetchone()[0]:
                raise failure("CONTRACT_MISMATCH", "commit", "Quarantine needs the privileged role")
            self._quarantine(conn, scope, asset_id, reason)

    def quarantined(self, scope: contract.Scope, asset_id: str) -> str | None:
        with self._tx(scope) as conn:
            row = conn.execute("SELECT reason FROM quarantined_assets WHERE asset_id = %s AND tenant_id = %s "
                               "AND workspace_id = %s", (asset_id, scope.tenant_id, scope.workspace_id)).fetchone()
        return row[0] if row else None

    # -- projection outbox (A07) ----------------------------------------------------------

    def claim_projections(self, projection: str, worker_id: str, *, limit: int = 10,
                          lease_seconds: int = 60) -> list[dict]:
        """Claim ready work across scopes (projector role must bypass RLS).
        Expired claims are reclaimable, so a crashed projector loses nothing."""
        with self._tx() as conn:
            rows = conn.execute(
                """UPDATE projection_queue q SET state = 'claimed', claimed_by = %s,
                       lease_until = now() + make_interval(secs => %s), attempts = attempts + 1
                   WHERE (commit_id, projection) IN (
                       SELECT commit_id, projection FROM projection_queue
                       WHERE projection = %s AND available_at <= now()
                         AND (state = 'pending' OR (state = 'claimed' AND lease_until < now()))
                       ORDER BY available_at, commit_id LIMIT %s FOR UPDATE SKIP LOCKED)
                   RETURNING commit_id, tenant_id, workspace_id, asset_id, registry_version, attempts""",
                (worker_id, lease_seconds, projection, limit)).fetchall()
        return [dict(zip(("commit_id", "tenant_id", "workspace_id", "asset_id", "registry_version", "attempts"),
                         (str(r[0]), r[1], r[2], str(r[3]), r[4], r[5]))) for r in rows]

    def apply_search_projection(self, claim: Mapping[str, Any], worker_id: str) -> bool:
        """Project the committed index revision as a new search row version and stamp
        the previous live version superseded, in this transaction (U3). Monotonic:
        an older revision never replaces a newer one. Returns False if the claim was
        lost (lease expired and taken over)."""
        scope = contract.Scope(claim["tenant_id"], claim["workspace_id"])
        with self._tx(scope) as conn:
            owned = conn.execute("SELECT 1 FROM projection_queue WHERE commit_id = %s AND projection = 'search' "
                                 "AND state = 'claimed' AND claimed_by = %s FOR UPDATE",
                                 (claim["commit_id"], worker_id)).fetchone()
            if not owned:
                return False
            entry = conn.execute("SELECT entry FROM master_index_revisions WHERE commit_id = %s "
                                 "AND tenant_id = %s AND workspace_id = %s",
                                 (claim["commit_id"], scope.tenant_id, scope.workspace_id)).fetchone()[0]
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                         (f"search:{scope.tenant_id}:{scope.workspace_id}:{entry['asset_id']}",))
            live = conn.execute("SELECT registry_version FROM search_rows WHERE asset_id = %s AND tenant_id = %s "
                                "AND workspace_id = %s AND superseded_xid IS NULL",
                                (entry["asset_id"], scope.tenant_id, scope.workspace_id)).fetchone()
            if live is None or live[0] < entry["registry_version"]:
                if live is not None:
                    conn.execute("UPDATE search_rows SET superseded_xid = pg_current_xact_id(), "
                                 "superseded_at = clock_timestamp() WHERE asset_id = %s AND registry_version = %s "
                                 "AND tenant_id = %s AND workspace_id = %s",
                                 (entry["asset_id"], live[0], scope.tenant_id, scope.workspace_id))
                meta = entry["index_metadata"]
                conn.execute(
                    """INSERT INTO search_rows (tenant_id, workspace_id, asset_id, registry_version, status, title,
                           search_text, tags, updated_at, entry) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (scope.tenant_id, scope.workspace_id, entry["asset_id"], entry["registry_version"],
                     entry["status"], meta["title"], entry["search_text"], meta["tags"], entry["updated_at"],
                     Jsonb(entry)))
            self.fault("search_projection:before_commit")
            conn.execute("UPDATE projection_queue SET state = 'done', lease_until = NULL "
                         "WHERE commit_id = %s AND projection = 'search'", (claim["commit_id"],))
        return True

    def fail_projection(self, claim: Mapping[str, Any], worker_id: str, error: str, *,
                        base_delay: float = 1.0) -> str:
        """Durable retry with exponential backoff and jitter; dead after the attempt bound."""
        with self._tx() as conn:
            row = conn.execute(
                """UPDATE projection_queue SET
                       state = CASE WHEN attempts >= %s THEN 'dead' ELSE 'pending' END,
                       available_at = now() + make_interval(secs => %s * power(2, attempts - 1) * (0.5 + random())),
                       last_error = %s, lease_until = NULL, claimed_by = NULL
                   WHERE commit_id = %s AND projection = %s AND claimed_by = %s
                   RETURNING state""",
                (MAX_PROJECTION_ATTEMPTS, base_delay, error[:500], claim["commit_id"], "search",
                 worker_id)).fetchone()
        return row[0] if row else "lost"

    def prune_search_versions(self, *, ttl_seconds: float | None = None,
                              margin_seconds: float = PRUNE_MARGIN_SECONDS) -> int:
        """Delete superseded search rows no unexpired cursor can see. Records a mark
        (current snapshot xmin); rows superseded by transactions older than a mark
        taken more than ttl+margin ago are invisible to every cursor issued since
        that mark, and older cursors have expired. Returns rows deleted."""
        ttl = CURSOR_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        with self._tx() as conn:
            conn.execute("INSERT INTO search_prune_marks (snapshot_xmin) "
                         "VALUES (pg_snapshot_xmin(pg_current_snapshot()))")
            mark = conn.execute("SELECT marked_at, snapshot_xmin FROM search_prune_marks "
                                "WHERE marked_at <= clock_timestamp() - make_interval(secs => %s) "
                                "ORDER BY marked_at DESC LIMIT 1", (ttl + margin_seconds,)).fetchone()
            if mark is None:
                return 0
            conn.execute("UPDATE search_state SET prune_horizon = greatest(prune_horizon, %s)", (mark[1],))
            deleted = conn.execute("DELETE FROM search_rows WHERE superseded_xid IS NOT NULL "
                                   "AND superseded_xid < %s", (mark[1],)).rowcount
            conn.execute("DELETE FROM search_prune_marks WHERE marked_at < %s", (mark[0],))
        return deleted

    # -- search (D2, A23, U3) ---------------------------------------------------------------

    def search(self, scope: contract.Scope, *, q: str | None, tag: str | None, status: str | None,
               limit: int = 25, cursor: str | None = None) -> dict:
        """Case-insensitive literal substring on title/search_text (no tokenizing, no
        wildcards); exact tag and status; updated_at desc, asset_id asc.

        Page 1 captures a PostgreSQL MVCC snapshot. Every page returns the search-row
        versions visible in that snapshot: created by a transaction it sees and not
        superseded by one it sees. Updates committed later (new title, status or
        tags) never remove, duplicate or alter an original member. The signed cursor
        pins the snapshot, query and scope, and expires."""
        if not 1 <= limit <= 100:
            raise failure("INVALID_REQUEST", "search", "limit must be 1..100", details=[("limit", "out of range")])
        query_id = hashlib.sha256(canonical_bytes({"scope": _scope_json(scope), "q": q, "tag": tag,
                                                   "status": status})).hexdigest()
        with self._tx(scope) as conn:
            self._serving(conn, "search")
            # Snapshots are transaction IDs of one database: a cursor never crosses a
            # recovery (new generation) or a restore (new fingerprint).
            epoch = "{}:{}".format(*conn.execute("SELECT generation, opened_fingerprint FROM publisher_state"
                                                 ).fetchone())
            if cursor:
                state = self._open_cursor(cursor, query_id)
                snapshot, indexed_at = state["snapshot"], state["indexed_at"]
                after = (datetime.fromisoformat(state["updated_at"]), state["asset_id"])
                if state["epoch"] != epoch or conn.execute(
                        "SELECT pg_snapshot_xmin(%s::pg_snapshot) < prune_horizon FROM search_state",
                        (snapshot,)).fetchone()[0]:
                    raise failure("INVALID_REQUEST", "search", "Invalid cursor",
                                  details=[("cursor", "snapshot is no longer available; restart the search")])
            else:
                snapshot, now = conn.execute("SELECT pg_current_snapshot()::text, now()").fetchone()
                indexed_at, after = _utc_text(now), None
            sql = ["SELECT entry, updated_at, asset_id FROM search_rows WHERE tenant_id = %s AND workspace_id = %s",
                   "AND pg_visible_in_snapshot(created_xid, %s::pg_snapshot)",
                   "AND (superseded_xid IS NULL OR NOT pg_visible_in_snapshot(superseded_xid, %s::pg_snapshot))"]
            params: list[Any] = [scope.tenant_id, scope.workspace_id, snapshot, snapshot]
            if q:
                sql.append("AND (strpos(lower(title), lower(%s)) > 0 OR strpos(lower(search_text), lower(%s)) > 0)")
                params += [q, q]
            if tag:
                sql.append("AND %s = ANY(tags)")
                params.append(tag)
            if status:
                sql.append("AND status = %s")
                params.append(status)
            if after:
                sql.append("AND (updated_at < %s OR (updated_at = %s AND asset_id > %s))")
                params += [after[0], after[0], after[1]]
            sql.append("ORDER BY updated_at DESC, asset_id ASC LIMIT %s")
            params.append(limit + 1)
            rows = conn.execute(" ".join(sql), params).fetchall()
        items = [r[0] for r in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = self._make_cursor({"snapshot": snapshot, "epoch": epoch, "indexed_at": indexed_at,
                                             "query": query_id,
                                             "updated_at": last[1].isoformat(), "asset_id": str(last[2]),
                                             "expires": datetime.now(timezone.utc).timestamp()
                                             + CURSOR_TTL_SECONDS})
        result = {"schema_version": contract.CONTRACT_RELEASE, "items": items, "next_cursor": next_cursor,
                  "indexed_at": indexed_at}
        contract.validate("SearchResult", result)
        return result

    def _make_cursor(self, state: dict) -> str:
        body = canonical_bytes(state)
        mac = hmac.new(self.cursor_secret, body, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac + body).decode("ascii").rstrip("=")

    def _open_cursor(self, cursor: str, query_id: str) -> dict:
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            mac, body = raw[:32], raw[32:]
            if not hmac.compare_digest(mac, hmac.new(self.cursor_secret, body, hashlib.sha256).digest()):
                raise ValueError("bad signature")
            state = json.loads(body)
            if state["query"] != query_id:
                raise ValueError("cursor belongs to another query or scope")
            if state["expires"] < datetime.now(timezone.utc).timestamp():
                raise ValueError("cursor expired")
            return state
        except (ValueError, KeyError, TypeError):
            raise failure("INVALID_REQUEST", "search", "Invalid cursor",
                          details=[("cursor", "tampered, expired or from another query")]) from None
