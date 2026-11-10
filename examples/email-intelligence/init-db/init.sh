#!/usr/bin/env bash
# Postgres init orchestration. Docker runs this inside the initdb phase as the
# POSTGRES_USER superuser. Passwords are substituted from the container env.
set -euo pipefail

DB="${POSTGRES_DB:-email_intelligence}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$DB" -f /init-db/01-schema.sql

roles_tmp="$(mktemp)"
sed -e "s/__TASKFORGE_WRITER_PASSWORD__/${TASKFORGE_WRITER_PASSWORD:-taskforge_writer_pwd_demo}/g" \
    -e "s/__EMAIL_APP_PASSWORD__/${EMAIL_APP_PASSWORD:-email_app_pwd_demo}/g" \
    /init-db/02-roles.sql > "$roles_tmp"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$DB" -f "$roles_tmp"
rm -f "$roles_tmp"

echo "email_intelligence initialized"
