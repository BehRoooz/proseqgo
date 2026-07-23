#!/bin/bash
# Creates the durable job-history database (separate from MLflow's POSTGRES_DB).
# Runs only on first Postgres volume init. For existing volumes, run manually:
#   docker compose exec postgres psql -U "$POSTGRES_USER" -c 'CREATE DATABASE proseqgo_jobs;'
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" <<-EOSQL
	SELECT 'CREATE DATABASE proseqgo_jobs'
	WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'proseqgo_jobs')\gexec
EOSQL
