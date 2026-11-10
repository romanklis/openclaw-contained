#!/usr/bin/env bash
# Point the scheduler at a planner-generated DAG.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

DAG_ID="${1:-${TARGET_DAG_ID:-}}"
SCHEDULER="${SCHEDULER_URL:-http://localhost:8096}"

if [ -z "$DAG_ID" ]; then
  echo "usage: bash scripts/set-target.sh <dag-id>"
  exit 2
fi

payload="$(python3 - "$DAG_ID" <<'PY'
import json, sys
print(json.dumps({"dag_id": sys.argv[1]}))
PY
)"

curl -sf -X POST "$SCHEDULER/api/target" \
  -H "Content-Type: application/json" \
  -d "$payload" | python3 -m json.tool
