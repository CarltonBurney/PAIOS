#!/bin/bash
# Creates n8n's database alongside the PAIOS one.
#
# n8n owns its own schema and migrations. Keeping it in a separate database on
# the same server avoids table collisions while keeping the dev stack to one
# Postgres instance.
#
# Runs only on first initialisation of an empty data volume.
set -euo pipefail

N8N_DB_NAME="${N8N_DB_NAME:-n8n}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
	SELECT 'CREATE DATABASE "$N8N_DB_NAME"'
	WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$N8N_DB_NAME')\gexec
SQL

echo "postgres init: ensured database '$N8N_DB_NAME' exists"
