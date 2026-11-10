#!/usr/bin/env bash
# Reset the example to a clean state: drop volumes (database + mail seed
# sentinel), recreate the stack, and let the seeder inject the demo mailbox.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  DC=(docker compose)
fi

echo "==> Stopping and removing volumes"
"${DC[@]}" down -v

echo "==> Starting fresh infrastructure"
"${DC[@]}" up -d --build

echo "==> Waiting for Postgres"
for _ in $(seq 1 60); do
  health="$(docker inspect -f '{{.State.Health.Status}}' email-postgres 2>/dev/null || true)"
  if [ "$health" = "healthy" ]; then
    break
  fi
  sleep 2
done

echo "==> Waiting for the one-shot mail seeder"
for _ in $(seq 1 60); do
  state="$(docker inspect -f '{{.State.Status}}' email-mail-seed 2>/dev/null || true)"
  if [ "$state" = "exited" ]; then
    break
  fi
  sleep 2
done

echo ""
echo "Reset complete. The scheduler target is cleared because settings live in the database."
echo "Next:"
echo "  bash scripts/bootstrap.sh               # re-register the credential if needed"
echo "  bash scripts/build-dag.sh --set-target  # or create the DAG in Studio"
