#!/usr/bin/env bash
# Resolve the email skill, ask the TaskForge planner to build the DAG from the
# objective, and print the resulting DAG id. Optionally point the scheduler at it.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

CP="${CONTROL_PLANE_URL:-http://localhost:8000}"
OBJECTIVE_FILE="${OBJECTIVE_FILE:-planning-objective.txt}"
PROJECT="${PROJECT:-email-intelligence}"
SET_TARGET=0
for arg in "$@"; do
  case "$arg" in
    --set-target) SET_TARGET=1 ;;
    *) echo "unknown argument: $arg"; exit 2 ;;
  esac
done

if [ ! -f "$OBJECTIVE_FILE" ]; then
  echo "objective file not found: $OBJECTIVE_FILE"
  exit 1
fi

echo "==> Resolving skill 'Email Intelligence Pipeline' for image 'email'"
SKILL_ID="$(curl -sf "$CP/api/skill-learning/skills?image_id=email" | python3 -c '
import json, sys
rows = json.load(sys.stdin)
match = next((s for s in rows if s.get("name") == "Email Intelligence Pipeline" and s.get("status") == "active"), None)
print(match["id"] if match else "")
')"

if [ -z "$SKILL_ID" ]; then
  echo "skill not found or not ACTIVE. Run 'make example-up NAME=email-intelligence' first."
  exit 1
fi
echo "    skill id: $SKILL_ID"

echo "==> Creating planner-generated DAG (this can take a few minutes)"
payload="$(python3 - "$OBJECTIVE_FILE" "$PROJECT" "$SKILL_ID" <<'PY'
import json, sys
objective = open(sys.argv[1], encoding="utf-8").read().strip()
print(json.dumps({
    "objective": objective,
    "project_id": sys.argv[2],
    "base_image": "email",
    "skill_ids": [sys.argv[3]],
    "auto_start": False,
}))
PY
)"

response="$(curl -sf --max-time 300 -X POST "$CP/api/dags" \
  -H "Content-Type: application/json" \
  -d "$payload")" || {
  echo "planner request failed. Is the control-plane and an LLM provider configured?"
  exit 1
}

DAG_ID="$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))')"
STATUS="$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))')"

if [ -z "$DAG_ID" ]; then
  echo "planner did not return a DAG id:"
  printf '%s\n' "$response"
  exit 1
fi

echo "==> Waiting for planning to finish (dag $DAG_ID)"
for _ in $(seq 1 100); do
  detail="$(curl -sf "$CP/api/dags/$DAG_ID")"
  STATUS="$(printf '%s' "$detail" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))')"
  case "$STATUS" in
    ready|completed) break ;;
    failed)
      echo "planning failed:"
      printf '%s' "$detail" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get("dag_json",{}), indent=2)[:4000])'
      exit 1
      ;;
  esac
  sleep 3
done

if [ "$STATUS" != "ready" ] && [ "$STATUS" != "completed" ]; then
  echo "planning did not finish in time (last status: $STATUS)"
  exit 1
fi

echo ""
echo "DAG id:   $DAG_ID"
echo "status:   $STATUS"
echo "project:  $PROJECT"
echo ""
echo "Next:"
echo "  1. Paste the DAG id into the dashboard Settings field, or run:"
echo "       bash scripts/set-target.sh $DAG_ID"
echo "  2. Start the DAG once in TaskForge Studio to confirm the workflow."
echo "  3. The scheduler will instantiate it every INTERVAL_SECONDS."

if [ "$SET_TARGET" = "1" ]; then
  bash scripts/set-target.sh "$DAG_ID"
fi
