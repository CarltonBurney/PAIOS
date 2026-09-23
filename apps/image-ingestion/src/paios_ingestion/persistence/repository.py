"""PostgreSQL coordinator repositories (Packet 2 development foundation).

Scope: reservations, frozen commit intents, heads, append-only revision copies,
jobs, content lookup, the projection outbox, search, and publisher-generation
fencing. No cloud publication happens here: `record_published` is called by a
future publisher (not approved yet) after a verified DGE marker, and in this
unit only by tests with in-memory fakes.

Every scoped operation runs in one transaction with `paios.tenant_id` and
`paios.workspace_id` set, so row-level security also enforces scope.
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
from .canonical import bundle_sha256, canonical_bytes, is_canonical, sha256_hex
from .validation import validate_bundle

PAYLOAD_ROLES = ("registry.json", "audit.json", "index.json")
OBJECT_ROLES = PAYLOAD_ROLES + ("commit.json",)
MARKER_FORMAT = "paios.commit/1"
MAX_PROJECTION_ATTEMPTS = 8
CURSOR_TTL_SECONDS = 15 * 60


def _utc_text(moment: datetime) -> str:
    moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ" if moment.microsecond else "%Y-%m-%dT%H:%M:%SZ")


def _scope_json(scope: contract.Scope) -> dict:
    return {"tenant_id": scope.tenant_id, "workspace_id": scope.workspace_id}


class CoordinatorRepository:
    """Implements the reservation/commit/read half of the IngestionRepository,
    AssetRepository, AuditRepository and IndexRepository protocols against PostgreSQL."""

    def __init__(self, conninfo: str, *, cursor_secret: bytes,
                 fault: Callable[[str], None] | None = None):
        if len(cursor_secret) < 32:
            raise ValueError("cursor_secret must be at least 32 bytes")
        self.conninfo = conninfo
        self.cursor_secret = cursor_secret
        self.fault = fault or (lambda point: None)  # crash-injection hook for tests

    # -- plumbing -----------------------------------------------------------------

    @contextmanager
    def _tx(self, scope: contract.Scope | None = None):
        try:
            conn = psycopg.connect(self.conninfo)
        except psycopg.OperationalError:
            raise failure("PERSISTENCE_UNAVAILABLE", "commit", "Coordinator database unavailable",
                          retryable=True, retry_after_seconds=5) from None
        try:
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
    def _publisher(conn, *, lock: bool = False) -> tuple[int, str]:
        row = conn.execute("SELECT generation, mode FROM publisher_state"
                           + (" FOR SHARE" if lock else "")).fetchone()
        return row[0], row[1]

    def _require_open(self, conn, generation: int | None = None) -> int:
        current, mode = self._publisher(conn, lock=True)
        if mode != "open":
            raise failure("PERSISTENCE_UNAVAILABLE", "commit", "Publication is paused for recovery",
                          retryable=True, retry_after_seconds=30)
        if generation is not None and generation != current:
            raise failure("VERSION_CONFLICT", "commit", "Publisher generation has been fenced",
                          details=[("generation", f"{generation} is not current ({current})")])
        return current

    # -- reservations (A01, A02) ----------------------------------------------------

    def reserve(self, *, scope: contract.Scope, idempotency_key: str, request_digest: str,
                request: Mapping[str, Any], pinned: Mapping[str, Any] | None = None) -> contract.Reservation:
        key_sha = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        with self._tx(scope) as conn:
            self._require_open(conn)
            inserted = conn.execute(
                """INSERT INTO ingestion_reservations (tenant_id, workspace_id, idempotency_key_sha256,
                       request_digest, request, pinned, ingestion_id, asset_id, state)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'reserved')
                   ON CONFLICT (tenant_id, workspace_id, idempotency_key_sha256) DO NOTHING
                   RETURNING 1""",
                (scope.tenant_id, scope.workspace_id, key_sha, request_digest, Jsonb(request),
                 Jsonb(dict(pinned or {})), str(uuid.uuid4()), str(uuid.uuid4()))).fetchone() is not None
            row = conn.execute(
                """SELECT request_digest, ingestion_id, asset_id FROM ingestion_reservations
                   WHERE tenant_id = %s AND workspace_id = %s AND idempotency_key_sha256 = %s""",
                (scope.tenant_id, scope.workspace_id, key_sha)).fetchone()
        stored_digest, ingestion_id, asset_id = row
        if stored_digest != request_digest:
            raise failure("IDEMPOTENCY_CONFLICT", "request",
                          "Idempotency key was already used with a different request")
        return contract.Reservation(ingestion_id=str(ingestion_id), asset_id=str(asset_id),
                                    request_digest=stored_digest, replay=not inserted)

    def reservation_pins(self, scope: contract.Scope, ingestion_id: str) -> dict:
        with self._tx(scope) as conn:
            row = conn.execute("SELECT pinned FROM ingestion_reservations WHERE ingestion_id = %s"
                               " AND tenant_id = %s AND workspace_id = %s", (ingestion_id, scope.tenant_id, scope.workspace_id)).fetchone()
        if row is None:
            raise failure("NOT_FOUND", "request", "Ingestion not found")
        return row[0]

    # -- frozen intents (P2-A) ------------------------------------------------------

    def freeze_intent(self, *, scope: contract.Scope, commit_id: str, payloads: Mapping[str, bytes],
                      object_ids: Mapping[str, str], marker: bytes, generation: int) -> str:
        """Freeze exact payload bytes, object IDs and marker bytes for one revision slot.

        Returns the stored state. Re-freezing the same commit with identical bytes is
        a no-op (replay); different bytes for the same commit are INTEGRITY_FAILED;
        a different commit for an occupied slot is VERSION_CONFLICT.
        """
        if set(payloads) != set(PAYLOAD_ROLES) or set(object_ids) != set(OBJECT_ROLES):
            raise failure("CONTRACT_MISMATCH", "commit", "Intent needs all three payloads and four object IDs")
        for role in PAYLOAD_ROLES:
            if not is_canonical(payloads[role]):
                raise failure("INTEGRITY_FAILED", "commit", "Payload bytes are not canonical JSON",
                              details=[(role, "not canonical UTF-8/sorted/compact JSON")])
        registry = json.loads(payloads["registry.json"])
        bundle = {"schema_version": contract.CONTRACT_RELEASE, "commit_id": commit_id,
                  "scope": _scope_json(scope), "registry": registry,
                  "audit": json.loads(payloads["audit.json"]), "index": json.loads(payloads["index.json"])}
        version = registry["canonical_record_version"]
        asset_id = registry["asset_id"]
        payload_sha = {role: sha256_hex(payloads[role]) for role in PAYLOAD_ROLES}
        identity = bundle_sha256(commit_id, version, payload_sha)
        marker_sha = sha256_hex(marker)
        args = (scope, commit_id, payloads, object_ids, marker, generation, bundle, registry, version,
                asset_id, payload_sha, identity, marker_sha)
        try:
            return self._freeze(*args)
        except psycopg.errors.UniqueViolation:
            return self._freeze(*args)  # a concurrent writer took the slot first; re-evaluate

    def _freeze(self, scope, commit_id, payloads, object_ids, marker, generation, bundle, registry,
                version, asset_id, payload_sha, identity, marker_sha) -> str:
        with self._tx(scope) as conn:
            self._require_open(conn, generation)
            existing = conn.execute(
                "SELECT commit_id, bundle_sha256, marker_sha256, object_ids, state FROM commit_intents "
                "WHERE tenant_id = %s AND workspace_id = %s AND asset_id = %s AND registry_version = %s",
                (scope.tenant_id, scope.workspace_id, asset_id, version)).fetchone()
            if existing:
                same_commit = str(existing[0]) == commit_id
                if not same_commit:
                    raise failure("VERSION_CONFLICT", "commit", "Revision slot already has a commit",
                                  details=[("registry_version", str(version))])
                if (existing[1], existing[2], existing[3]) != (identity, marker_sha, dict(object_ids)):
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
            validate_bundle(bundle, head[1] if head else None)
            self._check_marker(marker, scope, commit_id, registry, payload_sha, payloads, object_ids,
                               head[2] if head else None)
            conn.execute(
                """INSERT INTO commit_intents (tenant_id, workspace_id, asset_id, registry_version, commit_id,
                       ingestion_id, generation, bundle_sha256, object_ids, registry_bytes, audit_bytes,
                       index_bytes, marker_bytes, marker_sha256, state)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'frozen')""",
                (scope.tenant_id, scope.workspace_id, asset_id, version, commit_id, registry["ingestion_id"],
                 generation, identity, Jsonb(dict(object_ids)), payloads["registry.json"],
                 payloads["audit.json"], payloads["index.json"], marker, marker_sha))
            return "frozen"

    @staticmethod
    def _check_marker(marker, scope, commit_id, registry, payload_sha, payloads, object_ids,
                      previous_marker_sha):
        try:
            data = json.loads(marker.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            data = None
        expected_files = {role: {"object_id": object_ids[role], "sha256": payload_sha[role],
                                 "bytes": len(payloads[role])} for role in PAYLOAD_ROLES}
        problems = []
        if not is_canonical(marker) or not isinstance(data, dict):
            problems.append(("commit.json", "must be canonical JSON"))
        else:
            expect = {"format": MARKER_FORMAT, "contract_release": contract.CONTRACT_RELEASE,
                      "scope": _scope_json(scope), "asset_id": registry["asset_id"],
                      "ingestion_id": registry["ingestion_id"], "commit_id": commit_id,
                      "registry_version": registry["canonical_record_version"],
                      "event_id": registry["last_event_id"], "files": expected_files,
                      "previous_marker_sha256": previous_marker_sha}
            for key, value in expect.items():
                if data.get(key) != value:
                    problems.append((f"commit.json.{key}", "does not match the frozen bundle"))
            published_at = data.get("published_at")
            if not (isinstance(published_at, str) and published_at.endswith("Z")):
                problems.append(("commit.json.published_at", "must be frozen UTC time"))
        if problems:
            raise failure("INTEGRITY_FAILED", "commit", "Marker does not match the frozen intent",
                          details=problems)

    def frozen_intent(self, scope: contract.Scope, commit_id: str) -> dict:
        """The exact bytes and IDs a (possibly different) worker must publish."""
        with self._tx(scope) as conn:
            row = conn.execute(
                "SELECT object_ids, registry_bytes, audit_bytes, index_bytes, marker_bytes, state, generation "
                "FROM commit_intents WHERE commit_id = %s AND tenant_id = %s AND workspace_id = %s",
                (commit_id, scope.tenant_id, scope.workspace_id)).fetchone()
        if row is None:
            raise failure("NOT_FOUND", "commit", "Commit intent not found")
        return {"object_ids": row[0], "payloads": {"registry.json": bytes(row[1]), "audit.json": bytes(row[2]),
                "index.json": bytes(row[3])}, "marker": bytes(row[4]), "state": row[5], "generation": row[6]}

    def mark_publishing(self, *, scope: contract.Scope, commit_id: str, generation: int) -> None:
        """Last database check before the marker upload. From here the revision's
        visibility is uncertain until recorded, so reads of this asset return 503."""
        with self._tx(scope) as conn:
            self._require_open(conn)
            row = conn.execute("SELECT state, generation FROM commit_intents WHERE commit_id = %s AND tenant_id = %s AND workspace_id = %s FOR UPDATE",
                               (commit_id, scope.tenant_id, scope.workspace_id)).fetchone()
            if row is None:
                raise failure("NOT_FOUND", "commit", "Commit intent not found")
            state, owner = row
            if owner != generation:
                raise failure("VERSION_CONFLICT", "commit", "Intent belongs to another publisher generation")
            self._require_open(conn, generation)
            if state == "frozen":
                conn.execute("UPDATE commit_intents SET state = 'publishing', updated_at = now() "
                             "WHERE commit_id = %s", (commit_id,))

    def adopt_intent(self, *, scope: contract.Scope, commit_id: str, generation: int) -> None:
        """P2-B takeover: the current generation re-executes the same frozen intent.
        Only ownership changes; payload, marker bytes and object IDs never do."""
        with self._tx(scope) as conn:
            self._require_open(conn, generation)
            updated = conn.execute("UPDATE commit_intents SET generation = %s, updated_at = now() "
                                   "WHERE commit_id = %s AND state <> 'published' AND tenant_id = %s AND workspace_id = %s",
                                   (generation, commit_id, scope.tenant_id, scope.workspace_id)).rowcount
            if not updated:
                raise failure("NOT_FOUND", "commit", "No unpublished intent to adopt")

    # -- publication record (A03, A05, A21) ----------------------------------------------

    def record_published(self, *, scope: contract.Scope, commit_id: str, marker_object_id: str,
                         marker_sha256: str, generation: int,
                         processing_fingerprint: str | None = None) -> contract.CommitReceipt:
        """Make a verified, published marker visible: one transaction advances the head
        (compare-and-set N-1 -> N), appends revision copies, updates the job and
        content lookup, and enqueues projections. Replays return the stored receipt."""
        with self._tx(scope) as conn:
            row = conn.execute(
                "SELECT asset_id, registry_version, generation, state, receipt, marker_sha256, object_ids, "
                "registry_bytes, audit_bytes, index_bytes, ingestion_id FROM commit_intents "
                "WHERE commit_id = %s AND tenant_id = %s AND workspace_id = %s FOR UPDATE", (commit_id, scope.tenant_id, scope.workspace_id)).fetchone()
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
            self._require_open(conn, generation)
            if owner != generation:
                raise failure("VERSION_CONFLICT", "commit", "Intent belongs to another publisher generation")
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
            if registry["status"] in ("completed", "partial", "failed"):
                conn.execute("UPDATE ingestion_reservations SET state = 'terminal', updated_at = now() "
                             "WHERE ingestion_id = %s", (ingestion_id,))
            elif version == 1:
                conn.execute("UPDATE ingestion_reservations SET state = 'accepted', updated_at = now() "
                             "WHERE ingestion_id = %s", (ingestion_id,))
            committed_at = conn.execute("SELECT now()").fetchone()[0]
            receipt = {"commit_id": commit_id, "registry_version": version,
                       "canonical_manifest_id": marker_object_id, "manifest_sha256": marker_sha256,
                       "committed_at": _utc_text(committed_at)}
            self.fault("record_published:before_commit")
            conn.execute("UPDATE commit_intents SET state = 'published', receipt = %s, updated_at = now() "
                         "WHERE commit_id = %s", (Jsonb(receipt), commit_id))
        return contract.CommitReceipt(**receipt)

    def lookup_commit(self, scope: contract.Scope, commit_id: str) -> contract.CommitReceipt | None:
        with self._tx(scope) as conn:
            row = conn.execute("SELECT receipt FROM commit_intents WHERE commit_id = %s AND state = 'published'"
                               " AND tenant_id = %s AND workspace_id = %s", (commit_id, scope.tenant_id, scope.workspace_id)).fetchone()
        return contract.CommitReceipt(**row[0]) if row else None

    # -- reads (A07, A17, P2-C) -------------------------------------------------------------

    def get(self, scope: contract.Scope, asset_id: str) -> dict:
        """Latest committed Registry. If a newer revision may already be visible in the
        DGE (intent in 'publishing'), refuse with 503 instead of serving a stale head."""
        with self._tx(scope) as conn:
            head = conn.execute("SELECT registry_version, registry FROM registry_heads WHERE asset_id = %s"
                                " AND tenant_id = %s AND workspace_id = %s", (asset_id, scope.tenant_id, scope.workspace_id)).fetchone()
            uncertain = conn.execute(
                "SELECT 1 FROM commit_intents WHERE asset_id = %s AND state = 'publishing' "
                "AND registry_version > %s AND tenant_id = %s AND workspace_id = %s",
                (asset_id, head[0] if head else 0, scope.tenant_id, scope.workspace_id)).fetchone()
        if uncertain:
            raise failure("PERSISTENCE_UNAVAILABLE", "commit",
                          "A newer revision is being published; its visibility is not yet known",
                          retryable=True, retry_after_seconds=2)
        if head is None:
            raise failure("NOT_FOUND", "commit", "Asset not found")
        return head[1]

    def get_job(self, scope: contract.Scope, ingestion_id: str) -> dict:
        with self._tx(scope) as conn:
            row = conn.execute("SELECT asset_id, status, registry_version, attempt, error, created_at, updated_at "
                               "FROM jobs WHERE ingestion_id = %s AND tenant_id = %s AND workspace_id = %s",
                               (ingestion_id, scope.tenant_id, scope.workspace_id)).fetchone()
        if row is None:
            raise failure("NOT_FOUND", "commit", "Ingestion not found")
        job = {"schema_version": contract.CONTRACT_RELEASE, "ingestion_id": ingestion_id,
               "asset_id": str(row[0]), "scope": _scope_json(scope), "status": row[1],
               "registry_version": row[2], "attempt": row[3], "created_at": _utc_text(row[5]),
               "updated_at": _utc_text(row[6]), "error": row[4]}
        contract.validate("Job", job)
        return job

    def list_events(self, scope: contract.Scope, asset_id: str, after_version: int, limit: int) -> list[dict]:
        with self._tx(scope) as conn:
            rows = conn.execute("SELECT event FROM audit_events WHERE asset_id = %s AND registry_version > %s "
                                "AND tenant_id = %s AND workspace_id = %s ORDER BY registry_version LIMIT %s",
                                (asset_id, after_version, scope.tenant_id, scope.workspace_id, limit)).fetchall()
        return [r[0] for r in rows]

    def find_reusable(self, scope: contract.Scope, original_sha256: str,
                      processing_fingerprint: str) -> dict | None:
        """Latest committed successful candidate in this scope only (A16/A17/A28)."""
        with self._tx(scope) as conn:
            row = conn.execute(
                """SELECT h.registry FROM content_lookup c JOIN registry_heads h
                     ON h.tenant_id = c.tenant_id AND h.workspace_id = c.workspace_id AND h.asset_id = c.asset_id
                   WHERE c.original_sha256 = %s AND c.processing_fingerprint = %s
                     AND h.registry->>'status' = 'completed'
                     AND c.tenant_id = %s AND c.workspace_id = %s
                   ORDER BY c.committed_at DESC, c.asset_id LIMIT 1""",
                (original_sha256, processing_fingerprint, scope.tenant_id, scope.workspace_id)).fetchone()
        return row[0] if row else None

    # -- recovery fencing (P2-B) ----------------------------------------------------------

    def enter_recovery(self) -> int:
        """Close admissions and publication and fence every older generation."""
        with self._tx() as conn:
            row = conn.execute("UPDATE publisher_state SET generation = generation + 1, mode = 'recovery', "
                               "changed_at = now() RETURNING generation").fetchone()
        return row[0]

    def resume(self, generation: int) -> None:
        with self._tx() as conn:
            updated = conn.execute("UPDATE publisher_state SET mode = 'open', changed_at = now() "
                                   "WHERE generation = %s AND mode = 'recovery'", (generation,)).rowcount
        if not updated:
            raise failure("VERSION_CONFLICT", "commit", "Recovery generation is not current")

    def publisher_generation(self) -> tuple[int, str]:
        with self._tx() as conn:
            return self._publisher(conn)

    def unpublished_intents(self) -> list[dict]:
        """Reconciliation input: every intent whose publication is not recorded."""
        with self._tx() as conn:
            rows = conn.execute("SELECT tenant_id, workspace_id, asset_id, registry_version, commit_id, state, "
                                "generation FROM commit_intents WHERE state <> 'published' "
                                "ORDER BY tenant_id, workspace_id, asset_id, registry_version").fetchall()
        return [dict(zip(("tenant_id", "workspace_id", "asset_id", "registry_version", "commit_id", "state",
                          "generation"), (r[0], r[1], str(r[2]), r[3], str(r[4]), r[5], r[6]))) for r in rows]

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
        """Project the committed index revision. Monotonic: never regresses a newer row.
        Returns False if the claim was lost (lease expired and taken over)."""
        scope = contract.Scope(claim["tenant_id"], claim["workspace_id"])
        with self._tx(scope) as conn:
            owned = conn.execute("SELECT 1 FROM projection_queue WHERE commit_id = %s AND projection = 'search' "
                                 "AND state = 'claimed' AND claimed_by = %s FOR UPDATE",
                                 (claim["commit_id"], worker_id)).fetchone()
            if not owned:
                return False
            entry = conn.execute("SELECT entry FROM master_index_revisions WHERE commit_id = %s",
                                 (claim["commit_id"],)).fetchone()[0]
            meta = entry["index_metadata"]
            conn.execute(
                """INSERT INTO search_rows (tenant_id, workspace_id, asset_id, registry_version, status, title,
                       search_text, tags, updated_at, entry)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (tenant_id, workspace_id, asset_id) DO UPDATE SET
                       registry_version = EXCLUDED.registry_version, status = EXCLUDED.status,
                       title = EXCLUDED.title, search_text = EXCLUDED.search_text, tags = EXCLUDED.tags,
                       updated_at = EXCLUDED.updated_at, entry = EXCLUDED.entry, projected_at = clock_timestamp()
                   WHERE search_rows.registry_version < EXCLUDED.registry_version""",
                (scope.tenant_id, scope.workspace_id, entry["asset_id"], entry["registry_version"],
                 entry["status"], meta["title"], entry["search_text"], meta["tags"], entry["updated_at"],
                 Jsonb(entry)))
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

    # -- search (D2, A23) ---------------------------------------------------------------

    def search(self, scope: contract.Scope, *, q: str | None, tag: str | None, status: str | None,
               limit: int = 25, cursor: str | None = None) -> dict:
        """Case-insensitive literal substring on title/search_text (no tokenizing, no
        wildcards); exact tag and status; updated_at desc, asset_id asc; the opaque
        cursor pins the snapshot, query and scope."""
        if not 1 <= limit <= 100:
            raise failure("INVALID_REQUEST", "search", "limit must be 1..100", details=[("limit", "out of range")])
        query_id = hashlib.sha256(canonical_bytes({"scope": _scope_json(scope), "q": q, "tag": tag,
                                                   "status": status})).hexdigest()
        with self._tx(scope) as conn:
            if cursor:
                state = self._open_cursor(cursor, query_id)
                snapshot = datetime.fromisoformat(state["snapshot"])
                after = (datetime.fromisoformat(state["updated_at"]), state["asset_id"])
            else:
                snapshot = conn.execute("SELECT coalesce(max(projected_at), now()) FROM search_rows "
                                        "WHERE tenant_id = %s AND workspace_id = %s",
                                        (scope.tenant_id, scope.workspace_id)).fetchone()[0]
                after = None
            sql = ["SELECT entry, updated_at, asset_id FROM search_rows WHERE projected_at <= %s"
                   " AND tenant_id = %s AND workspace_id = %s"]
            params: list[Any] = [snapshot, scope.tenant_id, scope.workspace_id]
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
            next_cursor = self._make_cursor({"snapshot": snapshot.isoformat(), "query": query_id,
                                             "updated_at": last[1].isoformat(), "asset_id": str(last[2]),
                                             "expires": datetime.now(timezone.utc).timestamp()
                                             + CURSOR_TTL_SECONDS})
        result = {"schema_version": contract.CONTRACT_RELEASE, "items": items, "next_cursor": next_cursor,
                  "indexed_at": _utc_text(snapshot)}
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
