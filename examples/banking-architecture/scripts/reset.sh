#!/usr/bin/env bash
# Reset to a clean, fully initialized state.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] && { set -a; source .env; set +a; }

docker-compose -f docker-compose.yml down -v
docker-compose -f docker-compose.yml up -d
exec ./scripts/healthcheck.sh
