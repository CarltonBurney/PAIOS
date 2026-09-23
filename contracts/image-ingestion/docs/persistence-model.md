# Repository and logical data model

This is a technology-neutral logical model, not executable database migrations. Claude selects database/queue adapters, then demonstrates compliance. DGE canonical publication remains mandatory even if a database transaction stores all three records atomically.

| Logical collection | Keys / constraints |
|---|---|
| ingestion_reservations | Unique (tenant, workspace, idempotency_key); request_digest; stable ingestion/asset IDs; recoverable publication state |
| registry_revisions | Primary (tenant, workspace, asset_id, version); immutable snapshot/hash; unique commit_id |
| registry_heads | Primary (tenant, workspace, asset_id); compare-and-set current version; references a published revision |
| audit_events | Unique event_id and (scope, asset_id, registry_version); predecessor event reference; append-only credentials |
| master_index_revisions | Primary (scope, asset_id, registry_version); references corresponding audit and registry revision |
| master_index_heads | One latest published row per asset, updated monotonically |
| content_lookup | Nonunique (scope, original_sha256, processing_fingerprint); only committed successful candidates eligible |
| jobs | Unique ingestion_id; references asset/current committed revision; projection, not independently authoritative terminal state |
| projection_queue | Unique (commit_id, projection_name); durable delivery state, retry attempts, no local-only queue |

All foreign keys include scope, including relationships and OCR reuse. No SHA uniqueness on asset rows: distinct sources must retain provenance. Content blobs may deduplicate inside scope. The request ledger must retain digest and request or a verifiable canonical request reference for recovery; never store bearer credentials. Append-only audit access is separate from head/index updater access. A maintenance role cannot silently rewrite history through normal repository interfaces.

Repository commit(bundle, expected_version) has all-or-none logical visibility, validates every invariant, fences concurrent writers, and returns a durable receipt only after canonical publication. For N=1 expected_version=0. A stale writer gets VERSION_CONFLICT and must reload; it must not merge arbitrary snapshots. Replaying a known commit ID is idempotent; differing bytes are INTEGRITY_FAILED. No revision gaps. Query-head methods may use cache only if its freshness against the canonical head is established. Projection corruption is repaired from DGE, not by treating an unverified local copy as authority.

Failure boundaries to demonstrate: before reservation, after reservation/before acceptance, between each payload upload, before/after commit marker, after commit/before response, before/after search indexing, and after complete local cache loss. Manifest and reservation formats are adapter-private, but must retain enough information for exactly the semantics above; their format proposal is part of packet 1 review.
