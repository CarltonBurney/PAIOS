-- Additive: do not change the checksummed 0001/0002 migrations.
SET LOCAL search_path = paios_ingest;
CREATE OR REPLACE FUNCTION cluster_fingerprint() RETURNS text
    LANGUAGE plpgsql STABLE SET search_path = pg_catalog AS $$
BEGIN
    RETURN (SELECT system_identifier::text FROM pg_control_system()) || ':'
        || substr(pg_walfile_name(pg_current_wal_lsn()), 1, 8) || ':'
        || (SELECT oid::text FROM pg_database WHERE datname = current_database()) || ':'
        || extract(epoch FROM pg_postmaster_start_time())::text;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END $$;
UPDATE publisher_state SET mode = 'recovery', opened_fingerprint = NULL;
-- A protocol client allocates its own first commit UUID. Null means not allocated
-- at reservation time; the unique asset/revision slot binds it when frozen.
-- Existing reservations with an allocated UUID retain their stricter binding.
ALTER TABLE ingestion_reservations ALTER COLUMN first_commit_id DROP NOT NULL;

