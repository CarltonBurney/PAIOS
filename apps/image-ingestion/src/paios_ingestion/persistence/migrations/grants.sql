-- Least-privilege grants for the two runtime roles (D8). Not a numbered migration:
-- role creation and passwords are a deployment step; this file is applied after
-- the roles exist. Idempotent.
--   paios_app : request/worker path. Row-level security applies. No DELETE, no
--               TRUNCATE, no UPDATE of append-only revision tables.
--   paios_ops : projector/reconciler. BYPASSRLS (set on the role). Same write limits.
-- A table owner or superuser can still bypass triggers and grants; that boundary
-- is an administrative trust boundary, not an immutability guarantee (P2-E).

GRANT USAGE ON SCHEMA paios_ingest TO paios_app, paios_ops;
GRANT SELECT ON ALL TABLES IN SCHEMA paios_ingest TO paios_app, paios_ops;
GRANT INSERT ON paios_ingest.ingestion_reservations, paios_ingest.commit_intents,
    paios_ingest.registry_heads, paios_ingest.registry_revisions, paios_ingest.audit_events,
    paios_ingest.master_index_revisions, paios_ingest.content_lookup, paios_ingest.jobs,
    paios_ingest.projection_queue
    TO paios_app, paios_ops;
GRANT UPDATE ON paios_ingest.ingestion_reservations, paios_ingest.commit_intents,
    paios_ingest.registry_heads, paios_ingest.jobs
    TO paios_app, paios_ops;
-- FOR SHARE on publisher_state needs an UPDATE privilege on some column; this one is inert.
GRANT UPDATE (changed_at) ON paios_ingest.publisher_state TO paios_app;
GRANT UPDATE ON paios_ingest.publisher_state TO paios_ops;
-- Search rows are a rebuildable projection, not history: the projector may prune
-- superseded versions once no unexpired cursor can see them.
GRANT INSERT, UPDATE, DELETE ON paios_ingest.search_rows TO paios_ops;
GRANT INSERT, DELETE ON paios_ingest.search_prune_marks TO paios_ops;
GRANT UPDATE ON paios_ingest.search_state TO paios_ops;
-- Quarantine is recorded only by the reconciler/verifier.
GRANT INSERT ON paios_ingest.quarantined_assets TO paios_ops;
GRANT UPDATE ON paios_ingest.projection_queue TO paios_ops;
