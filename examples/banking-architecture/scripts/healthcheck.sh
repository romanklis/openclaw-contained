#!/usr/bin/env bash
# Wait until the banking demo infrastructure is healthy.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "Waiting for banking-postgres ..."
for i in $(seq 1 30); do
  if docker exec banking-postgres pg_isready -U "${POSTGRES_USER:-bank_admin}" -d banking_enterprise >/dev/null 2>&1; then
    echo "banking-postgres ready"; break
  fi
  [ "$i" = 30 ] && { echo "banking-postgres NOT ready"; exit 1; }
  sleep 2
done

echo "Waiting for banking-mongo ..."
for i in $(seq 1 40); do
  if docker exec banking-mongo mongosh --quiet --eval 'db.runCommand({ping:1}).ok' \
      "mongodb://${MONGO_ROOT_USER:-mongo_admin}:${MONGO_ROOT_PASSWORD:-mongo_admin_pwd_demo}@127.0.0.1:27017/admin?authSource=admin" 2>/dev/null | grep -q 1; then
    echo "banking-mongo ready"; break
  fi
  [ "$i" = 40 ] && { echo "banking-mongo NOT ready"; exit 1; }
  sleep 2
done

echo "All banking infrastructure healthy."
