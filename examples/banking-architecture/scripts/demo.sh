#!/usr/bin/env bash
# Full demo: import the curated banking DAG into TaskForge, run it, and verify.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "cp .env.example .env first"; exit 1; }
set -a; source .env; set +a

CP="${CONTROL_PLANE_URL:-http://localhost:8000}"
DAG_FILE="${1:-config/dags/banking-credit-governance.json}"

echo "== 1/6 Import curated DAG =="
payload=$(python3 - <<EOF
import json
d=json.load(open("$DAG_FILE"))
print(json.dumps({
  "objective": d["objective"],
  "default_image": "openclaw",
  "default_llm": d.get("llm_model","gemini-flash-lite-latest"),
  "nodes": d["nodes"],
  "edges": d["edges"],
}))
EOF
)
resp=$(curl -s -X POST "$CP/api/dags/manual" -H 'Content-Type: application/json' -d "$payload")
DAG_ID=$(echo "$resp" | python3 -c "import json,sys;print(json.load(sys.stdin).get('id',''))")
[ -n "$DAG_ID" ] || { echo "DAG import failed:"; echo "$resp"; exit 1; }
echo "Imported DAG: $DAG_ID"
echo "$DAG_ID" > .demo-dag-id

echo "== 2/6 Start DAG =="
curl -s -X POST "$CP/api/dags/$DAG_ID/start" -o /dev/null -w "start http=%{http_code}\n"

echo "== 3/6 Watch analysis nodes complete (they pause at the human decision node) =="
echo "   When the DAG reaches 'human-approval', run:"
echo "     bash scripts/approve.sh $DAG_ID approve 'Board approved the data-center expansion facility.'"
echo "   Or approve/reject in the TaskForge UI."
echo "$DAG_ID" > .demo-dag-id
echo "== 4/6 After approval the DAG runs finalize-approval -> update-risk -> post-gl -> verify-downstream =="
echo "== 5/6 Verify platform state =="
echo "   DAG status: $(curl -s "$CP/api/dags/$DAG_ID" | python3 -c "import json,sys;print(json.load(sys.stdin).get('status'))")"
echo "== 6/6 Verify data + run governance tests =="
bash scripts/healthcheck.sh
bash scripts/verify.sh
bash scripts/tests/run.sh
