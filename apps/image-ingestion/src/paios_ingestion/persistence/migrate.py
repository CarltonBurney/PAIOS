"""Apply numbered SQL migrations once each, recording a checksum so edits are detected."""
from __future__ import annotations

import hashlib
import re
from importlib import resources

MIGRATIONS_PACKAGE = "paios_ingestion.persistence.migrations"


def _migrations() -> list[tuple[str, str]]:
    files = sorted(p for p in resources.files(MIGRATIONS_PACKAGE).iterdir()
                   if re.fullmatch(r"\d{4}_[a-z0-9_]+\.sql", p.name))
    return [(p.name, p.read_text(encoding="utf-8")) for p in files]


def apply_migrations(conn) -> list[str]:
    """Apply pending migrations in one transaction each. Returns the names applied.

    A changed checksum for an applied migration is an error: migrations are
    append-only, like the records they create.
    """
    applied = []
    with conn.transaction():
        conn.execute("CREATE SCHEMA IF NOT EXISTS paios_ingest")
        conn.execute("""CREATE TABLE IF NOT EXISTS paios_ingest.schema_migrations (
            name text PRIMARY KEY, sha256 text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now())""")
    for name, sql in _migrations():
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        with conn.transaction():
            conn.execute("LOCK TABLE paios_ingest.schema_migrations IN EXCLUSIVE MODE")
            row = conn.execute("SELECT sha256 FROM paios_ingest.schema_migrations WHERE name = %s",
                               (name,)).fetchone()
            if row:
                if row[0] != checksum:
                    raise RuntimeError(f"applied migration {name} has been modified")
                continue
            conn.execute(sql)
            conn.execute("INSERT INTO paios_ingest.schema_migrations (name, sha256) VALUES (%s, %s)",
                         (name, checksum))
            applied.append(name)
    return applied


def grants_sql() -> str:
    """Least-privilege grants for paios_app / paios_ops (applied after roles exist)."""
    return resources.files(MIGRATIONS_PACKAGE).joinpath("grants.sql").read_text(encoding="utf-8")
