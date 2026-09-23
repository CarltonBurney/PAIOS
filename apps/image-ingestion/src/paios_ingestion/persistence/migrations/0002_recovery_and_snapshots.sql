-- Packet 2 round-1 corrections (PACKET-2-REVIEW-ROUND-1.md) and the approved R1/R5 additions.
-- Additive and applied before any live data exists: the NOT NULL columns below
-- fail the migration if 0001 rows are present, which is intended (fail closed).
SET LOCAL search_path = paios_ingest;

-- U1: authoritative reads and admissions are served only when the coordinator was
-- opened on this exact cluster, WAL timeline and database. A backup restored with
-- mode = 'open' (logical restore, template copy, point-in-time recovery or a
-- promoted replica) therefore starts closed until reconciliation resumes it.
CREATE FUNCTION cluster_fingerprint() RETURNS text
    LANGUAGE plpgsql STABLE SET search_path = pg_catalog AS $$
BEGIN
    RETURN (SELECT system_identifier::text FROM pg_control_system()) || ':'
        || substr(pg_walfile_name(pg_current_wal_lsn()), 1, 8) || ':'
        || (SELECT oid::text FROM pg_database WHERE datname = current_database());
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;  -- e.g. a standby: never ready to serve
END $$;

ALTER TABLE publisher_state ADD COLUMN opened_fingerprint text;
-- Existing and fresh installs start closed; bootstrap is enter_recovery -> reconcile -> resume.
UPDATE publisher_state SET mode = 'recovery', opened_fingerprint = NULL, changed_at = now();

CREATE FUNCTION serving_ready() RETURNS boolean
    LANGUAGE sql STABLE SET search_path = paios_ingest, pg_catalog AS $$
    SELECT mode = 'open' AND opened_fingerprint IS NOT DISTINCT FROM cluster_fingerprint()
           AND opened_fingerprint IS NOT NULL
    FROM publisher_state
$$;

-- R1/R5: reservation.json is published to a pre-generated ID before the ingestion is
-- durably accepted. The exact envelope bytes are frozen with the reservation.
ALTER TABLE ingestion_reservations
    ADD COLUMN first_commit_id uuid NOT NULL UNIQUE,
    ADD COLUMN record_object_id text NOT NULL UNIQUE,
    ADD COLUMN record_bytes bytea NOT NULL,
    ADD COLUMN record_published_at timestamptz;

-- R1/R5: intent.json is the first object uploaded for a commit; its ID joins the
-- other four in object_ids and its exact bytes are frozen with the intent.
ALTER TABLE commit_intents
    ADD COLUMN intent_bytes bytea NOT NULL,
    ADD COLUMN intent_sha256 sha256_hex NOT NULL,
    ADD CONSTRAINT commit_intents_object_roles CHECK (
        object_ids ?& ARRAY['intent.json', 'registry.json', 'audit.json', 'index.json', 'commit.json']
        AND object_ids - ARRAY['intent.json', 'registry.json', 'audit.json', 'index.json',
                               'commit.json'] = '{}'::jsonb);

-- Assets whose DGE state cannot be reconciled (fork, edited or missing objects).
-- Reads fail closed with INTEGRITY_FAILED; nothing picks a winner.
CREATE TABLE quarantined_assets (
    tenant_id      text NOT NULL,
    workspace_id   text NOT NULL,
    asset_id       uuid NOT NULL,
    reason         text NOT NULL,
    quarantined_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, workspace_id, asset_id)
);

-- U3: search rows are versioned. Each projected revision is a new row stamped with
-- the projecting transaction's ID; superseding stamps the old row. A cursor carries
-- the page-1 MVCC snapshot, so later pages see exactly the rows that snapshot saw,
-- with commit (not wall-clock) semantics.
ALTER TABLE search_rows DROP CONSTRAINT search_rows_pkey;
ALTER TABLE search_rows
    ADD COLUMN created_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
    ADD COLUMN superseded_xid xid8,
    ADD COLUMN superseded_at timestamptz,
    ADD PRIMARY KEY (tenant_id, workspace_id, asset_id, registry_version);
CREATE UNIQUE INDEX search_rows_live ON search_rows (tenant_id, workspace_id, asset_id)
    WHERE superseded_xid IS NULL;

-- Pruning superseded rows must not break a live cursor. Each prune run records the
-- current snapshot xmin; rows superseded by a transaction older than a mark taken
-- more than one cursor lifetime ago are invisible to every unexpired cursor.
CREATE TABLE search_prune_marks (
    marked_at timestamptz PRIMARY KEY DEFAULT clock_timestamp(),
    snapshot_xmin xid8 NOT NULL
);
CREATE TABLE search_state (
    singleton     boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    prune_horizon xid8 NOT NULL DEFAULT '0'
);
INSERT INTO search_state DEFAULT VALUES;

ALTER TABLE quarantined_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE quarantined_assets FORCE ROW LEVEL SECURITY;
CREATE POLICY scope_isolation ON quarantined_assets
    USING (tenant_id = current_setting('paios.tenant_id', true)
       AND workspace_id = current_setting('paios.workspace_id', true))
    WITH CHECK (tenant_id = current_setting('paios.tenant_id', true)
       AND workspace_id = current_setting('paios.workspace_id', true));
