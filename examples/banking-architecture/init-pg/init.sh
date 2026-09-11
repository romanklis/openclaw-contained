#!/usr/bin/env bash
# Postgres init orchestration. Docker runs this inside the initdb phase as the
# POSTGRES_USER superuser. SQL files live in /init-pg/sql and are applied in
# numeric order; 03-roles.sql receives passwords via token substitution.
set -euo pipefail

DB="banking_enterprise"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$DB" -f /init-pg/sql/01-schemas.sql
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$DB" -f /init-pg/sql/02-data.sql

roles_tmp="$(mktemp)"
sed -e "s/__AI_PASSWORD__/${DB_AI_AGENT_PASSWORD}/g" \
    -e "s/__RISK_PASSWORD__/${DB_RISK_CONSUMER_PASSWORD}/g" \
    -e "s/__GL_PASSWORD__/${DB_GL_CONSUMER_PASSWORD}/g" \
    -e "s/__OFFICER_PASSWORD__/${DB_CREDIT_OFFICER_PASSWORD}/g" \
    /init-pg/sql/03-roles.sql > "$roles_tmp"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$DB" -f "$roles_tmp"
rm -f "$roles_tmp"

echo "banking_enterprise initialized"
