#!/usr/bin/env bash
# Deterministic key-flow tests against a disposable Postgres database.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-openclaw_pass}"
TEST_DB="openclaw_test"
PG_CONTAINER="${POSTGRES_CONTAINER:-openclaw-postgres}"
CP_CONTAINER="${CONTROL_PLANE_CONTAINER:-openclaw-control-plane}"
DATABASE_URL="postgresql+asyncpg://openclaw:${POSTGRES_PASSWORD}@postgres:5432/${TEST_DB}"

echo "Recreating ${TEST_DB}..."
docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" "$PG_CONTAINER" \
  psql -U openclaw -d postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS ${TEST_DB}" \
  -c "CREATE DATABASE ${TEST_DB}"

echo "Running key-flow tests..."
docker exec -e DATABASE_URL="$DATABASE_URL" -e SQLALCHEMY_ECHO=0 -e PYTHONPATH=/app "$CP_CONTAINER" \
  python -m unittest tests.test_key_flows -v
