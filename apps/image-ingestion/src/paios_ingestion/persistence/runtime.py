"""Fixture composition root and exact release-1.0.0 repository adapters.

Never expose CoordinatorRepository directly to request handlers. Each new runtime
starts closed even if copied database state says open. Only explicit recovery
with a complete intent ledger and terminated old authority opens this instance.
These assertions are fixture inputs, not a production fencing implementation.
"""
from __future__ import annotations

import uuid

from .. import contract
from ..errors import failure
from .publisher import Publisher, Reconciler
from .repository import CoordinatorRepository, _closed


class FixtureRuntime:
    def __init__(self, app: CoordinatorRepository, ops: CoordinatorRepository, drive):
        self._app, self._ops, self._drive = app, ops, drive
        self._publisher = Publisher(app, drive)  # refuses live adapters
        self._generation = None  # external to backup/restored database state

    def recover(self, *, complete_ledger: bool, old_authority_terminated: bool) -> dict:
        self._generation = None
        generation = self._ops.enter_recovery()
        if not complete_ledger or not old_authority_terminated:
            raise _closed()
        report = Reconciler(self._ops, self._drive).run(generation, resume=False)
        self._ops.resume(generation)
        self._generation = generation
        return report

    def _ready(self):
        if (self._generation is None or not self._app.serving_ready()
                or self._app.publisher_generation() != (self._generation, 'open')):
            raise _closed()

    def repositories(self, scope: contract.Scope):
        self._ready()
        return IngestionAdapter(self, scope), IndexAdapter(self, scope)

    def assets(self, scope: contract.Scope):
        self._ready()
        return AssetAdapter(self, scope)

    def audit(self, scope: contract.Scope):
        self._ready()
        return AuditAdapter(self, scope)


class _Scoped:
    def __init__(self, runtime: FixtureRuntime, scope: contract.Scope):
        self._runtime, self._scope = runtime, scope

    def _check(self, scope):
        self._runtime._ready()
        if scope != self._scope:
            raise failure('NOT_FOUND', 'request', 'Resource not found')


class IngestionAdapter(_Scoped):
    """Protocol signatures remain unchanged; service context is bound at creation."""
    def reserve(self, *, scope: contract.Scope, idempotency_key: str,
                request_digest: str, request: contract.JsonObject) -> contract.Reservation:
        self._check(scope)
        return self._runtime._publisher.admit(scope, idempotency_key=idempotency_key,
            request_digest=request_digest, request=request, defer_first_commit=True)

    def commit(self, *, bundle: contract.JsonObject, expected_version: int) -> contract.CommitReceipt:
        self._check(contract.Scope(**bundle['scope']))
        contract.validate('CommitBundle', bundle)
        # Issued persistence-model.md: expected_version is the current head (0 for v1).
        if bundle['registry']['canonical_record_version'] != expected_version + 1:
            raise failure('VERSION_CONFLICT', 'commit', 'Bundle must advance expected_version by one')
        return self._runtime._publisher.publish(self._scope, bundle, self._runtime._generation)

    def lookup_commit(self, scope: contract.Scope, commit_id: str) -> contract.CommitReceipt | None:
        self._check(scope)
        return self._runtime._app.lookup_commit(scope, commit_id)

    def get_job(self, scope: contract.Scope, ingestion_id: str) -> contract.JsonObject:
        self._check(scope)
        return self._runtime._app.get_job(scope, ingestion_id)


class AssetAdapter(_Scoped):
    def get(self, scope: contract.Scope, asset_id: str) -> contract.JsonObject:
        self._check(scope)
        return self._runtime._app.get(scope, asset_id)

    def find_reusable(self, scope: contract.Scope, original_sha256: str,
                      processing_fingerprint: str) -> contract.JsonObject | None:
        self._check(scope)
        return self._runtime._app.find_reusable(scope, original_sha256, processing_fingerprint)


class AuditAdapter(_Scoped):
    def list_events(self, scope: contract.Scope, asset_id: str,
                    after_version: int, limit: int):
        self._check(scope)
        return self._runtime._app.list_events(scope, asset_id, after_version, limit)


class IndexAdapter(_Scoped):
    def search(self, scope: contract.Scope, *, q: str | None, tag: str | None,
               status: str | None, limit: int, cursor: str | None) -> contract.JsonObject:
        self._check(scope)
        return self._runtime._app.search(scope, q=q, tag=tag, status=status, limit=limit, cursor=cursor)

    def apply_committed(self, receipt: contract.CommitReceipt, row: contract.JsonObject) -> None:
        self._check(contract.Scope(**row['scope']))
        repo = self._runtime._ops
        # A supplied row is never authority. Verify against the committed revision
        # and receipt before claiming its existing durable outbox entry.
        worker = 'protocol-' + uuid.uuid4().hex
        with repo._tx(self._scope) as conn:
            repo._serving(conn)
            record = conn.execute('SELECT i.receipt, m.entry FROM commit_intents i '
                'JOIN master_index_revisions m ON m.commit_id = i.commit_id '
                'WHERE i.commit_id = %s AND i.tenant_id = %s AND i.workspace_id = %s '
                "AND i.state = 'published'", (receipt.commit_id, self._scope.tenant_id,
                                               self._scope.workspace_id)).fetchone()
            if (record is None or contract.CommitReceipt(**record[0]) != receipt or record[1] != row):
                raise failure('INTEGRITY_FAILED', 'commit', 'Projection must match a committed receipt and row')
            claim = conn.execute("UPDATE projection_queue SET state = 'claimed', claimed_by = %s, "
                "lease_until = now() + interval '60 seconds' WHERE commit_id = %s AND projection = 'search' "
                "AND state <> 'done' RETURNING commit_id, tenant_id, workspace_id, asset_id, registry_version",
                (worker, receipt.commit_id)).fetchone()
        if claim:
            data = dict(zip(('commit_id', 'tenant_id', 'workspace_id', 'asset_id', 'registry_version'), claim))
            repo.apply_search_projection(data, worker)

