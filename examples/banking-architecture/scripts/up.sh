#!/usr/bin/env bash
# Start / restart the banking demo infrastructure (requires the platform network).
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] && { set -a; source .env; set +a; }

docker-compose -f docker-compose.yml up -d
exec ./scripts/healthcheck.sh
