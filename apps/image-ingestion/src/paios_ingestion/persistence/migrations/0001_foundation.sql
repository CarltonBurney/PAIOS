-- Packet 2 development foundation (PACKET-2-ARCHITECTURE-DECISION.md).
-- PostgreSQL is the coordinator and serving projection. It is not the canonical
-- record store: DGE publication (not built in this unit) remains mandatory.
-- Every row carries (tenant_id, workspace_id); row-level security scopes access.

CREATE SCHEMA IF NOT EXISTS paios_ingest;
SET LOCAL search_path = paios_ingest;

CREATE DOMAIN sha256_hex AS text CHECK (VALUE ~ '^[0-9a-f]{64}$');

-- P2-B: one publisher generation for the whole deployment. Entering recovery
-- closes admissions/publication and advances the generation; work stamped
-- with an older generation can no longer record a publication.
CREATE TABLE publisher_state (
    singleton   boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    generation  bigint NOT NULL CHECK (generation >= 1),
    mode        text NOT NULL CHECK (mode IN ('open', 'recovery')),
    changed_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO publisher_state (generation, mode) VALUES (1, 'open');

CREATE TABLE ingestion_reservations (
    tenant_id               text NOT NULL,
    workspace_id            text NOT NULL,
    idempotency_key_sha256  sha256_hex NOT NULL,
    request_digest          sha256_hex NOT NULL,
    request                 jsonb NOT NULL,
    pinned                  jsonb NOT NULL DEFAULT '{}'::jsonb,
    ingestion_id            uuid NOT NULL UNIQUE,
    asset_id                uuid NOT NULL UNIQUE,
    state                   text NOT NULL CHECK (state IN ('reserved', 'accepted', 'terminal')),
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, workspace_id, idempotency_key_sha256)
);

-- P2-A: an intent freezes the exact bytes and object IDs of one revision before
-- any upload. The primary key allows exactly one commit per revision slot, and
-- a slot is never reallocated (fail closed; recovery re-executes the same intent).
CREATE TABLE commit_intents (
    tenant_id        text NOT NULL,
    workspace_id     text NOT NULL,
    asset_id         uuid NOT NULL,
    registry_version integer NOT NULL CHECK (registry_version >= 1),
    commit_id        uuid NOT NULL UNIQUE,
    ingestion_id     uuid NOT NULL,
    generation       bigint NOT NULL,
    bundle_sha256    sha256_hex NOT NULL,
    object_ids       jsonb NOT NULL,
    registry_bytes   bytea NOT NULL,
    audit_bytes      bytea NOT NULL,
    index_bytes      bytea NOT NULL,
    marker_bytes     bytea NOT NULL,
    marker_sha256    sha256_hex NOT NULL,
    state            text NOT NULL CHECK (state IN ('frozen', 'publishing', 'published')),
    receipt          jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, workspace_id, asset_id, registry_version),
    CHECK ((state = 'published') = (receipt IS NOT NULL))
);

CREATE TABLE registry_heads (
    tenant_id        text NOT NULL,
    workspace_id     text NOT NULL,
    asset_id         uuid NOT NULL,
    registry_version integer NOT NULL CHECK (registry_version >= 1),
    commit_id        uuid NOT NULL,
    marker_sha256    sha256_hex NOT NULL,
    registry         jsonb NOT NULL,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, workspace_id, asset_id)
);

-- Append-only local copies of published records (canonical copies live in DGE).
CREATE TABLE registry_revisions (
    tenant_id        text NOT NULL,
    workspace_id     text NOT NULL,
    asset_id         uuid NOT NULL,
    registry_version integer NOT NULL,
    commit_id        uuid NOT NULL UNIQUE,
    sha256           sha256_hex NOT NULL,
    registry         jsonb NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, asset_id, registry_version)
);

CREATE TABLE audit_events (
    tenant_id         text NOT NULL,
    workspace_id      text NOT NULL,
    asset_id          uuid NOT NULL,
    registry_version  integer NOT NULL,
    event_id          uuid NOT NULL UNIQUE,
    previous_event_id uuid,
    commit_id         uuid NOT NULL UNIQUE,
    sha256            sha256_hex NOT NULL,
    event             jsonb NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, asset_id, registry_version)
);

CREATE TABLE master_index_revisions (
    tenant_id        text NOT NULL,
    workspace_id     text NOT NULL,
    asset_id         uuid NOT NULL,
    registry_version integer NOT NULL,
    commit_id        uuid NOT NULL UNIQUE,
    sha256           sha256_hex NOT NULL,
    entry            jsonb NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, asset_id, registry_version)
);

CREATE TABLE content_lookup (
    tenant_id              text NOT NULL,
    workspace_id           text NOT NULL,
    original_sha256        sha256_hex NOT NULL,
    processing_fingerprint sha256_hex NOT NULL,
    asset_id               uuid NOT NULL,
    registry_version       integer NOT NULL,
    committed_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, workspace_id, asset_id, registry_version)
);
CREATE INDEX content_lookup_by_hash
    ON content_lookup (tenant_id, workspace_id, original_sha256, processing_fingerprint);

CREATE TABLE jobs (
    tenant_id        text NOT NULL,
    workspace_id     text NOT NULL,
    ingestion_id     uuid PRIMARY KEY,
    asset_id         uuid NOT NULL,
    status           text NOT NULL,
    registry_version integer NOT NULL,
    attempt          integer NOT NULL DEFAULT 1 CHECK (attempt >= 1),
    error            jsonb,
    created_at       timestamptz NOT NULL,
    updated_at       timestamptz NOT NULL
);

-- Search projection: may lag, applied monotonically from the outbox.
CREATE TABLE search_rows (
    tenant_id        text NOT NULL,
    workspace_id     text NOT NULL,
    asset_id         uuid NOT NULL,
    registry_version integer NOT NULL,
    status           text NOT NULL,
    title            text NOT NULL,
    search_text      text NOT NULL,
    tags             text[] NOT NULL,
    updated_at       timestamptz NOT NULL,
    entry            jsonb NOT NULL,
    projected_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, workspace_id, asset_id)
);
CREATE INDEX search_rows_order ON search_rows (tenant_id, workspace_id, updated_at DESC, asset_id);

CREATE TABLE projection_queue (
    commit_id        uuid NOT NULL,
    projection       text NOT NULL,
    tenant_id        text NOT NULL,
    workspace_id     text NOT NULL,
    asset_id         uuid NOT NULL,
    registry_version integer NOT NULL,
    state            text NOT NULL CHECK (state IN ('pending', 'claimed', 'done', 'dead')),
    attempts         integer NOT NULL DEFAULT 0,
    available_at     timestamptz NOT NULL DEFAULT now(),
    claimed_by       text,
    lease_until      timestamptz,
    last_error       text,
    PRIMARY KEY (commit_id, projection)
);
CREATE INDEX projection_queue_ready ON projection_queue (projection, state, available_at);

-- Append-only guard. This stops ordinary runtime roles and accidental writes; a
-- table owner or superuser can disable triggers, so it is not a guarantee
-- against privileged administrators (see PACKET-2 P2-E).
CREATE FUNCTION reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'append-only table %: % is not permitted', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE TRIGGER registry_revisions_append_only BEFORE UPDATE OR DELETE ON registry_revisions
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER master_index_revisions_append_only BEFORE UPDATE OR DELETE ON master_index_revisions
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER truncate_guard_registry BEFORE TRUNCATE ON registry_revisions
    FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER truncate_guard_audit BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER truncate_guard_index BEFORE TRUNCATE ON master_index_revisions
    FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation();

-- Scope isolation (A17). The application sets paios.tenant_id / paios.workspace_id
-- per transaction; FORCE applies the policy to the table owner too. Cross-scope
-- maintenance (projector, reconciler) runs as a role with BYPASSRLS.
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['ingestion_reservations', 'commit_intents', 'registry_heads',
        'registry_revisions', 'audit_events', 'master_index_revisions', 'content_lookup',
        'jobs', 'search_rows', 'projection_queue']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format($p$CREATE POLICY scope_isolation ON %I
            USING (tenant_id = current_setting('paios.tenant_id', true)
               AND workspace_id = current_setting('paios.workspace_id', true))
            WITH CHECK (tenant_id = current_setting('paios.tenant_id', true)
               AND workspace_id = current_setting('paios.workspace_id', true))$p$, t);
    END LOOP;
END $$;
