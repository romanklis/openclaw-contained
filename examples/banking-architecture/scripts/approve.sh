#!/usr/bin/env bash
# Approve (or reject) the human credit-officer decision node for a banking demo DAG.
# usage: approve.sh <dag_id> [approve|reject] [justification]
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "cp .env.example .env first"; exit 1; }
set -a; source .env; set +a

CP="${CONTROL_PLANE_URL:-http://localhost:8000}"
DAG_ID="${1:?dag_id required}"
CHOICE="${2:-approve}"
JUST="${3:-Approved by the human credit officer.}"
NODE_ID="human-approval"

echo "Looking up node '$NODE_ID' state for $DAG_ID ..."
curl -s "$CP/api/dags/$DAG_ID" | python3 -c "
import json,sys
d=json.load(sys.stdin)
for n in d.get('dag_json',{}).get('nodes',[]) + d.get('nodes',[]):
    if n.get('node_id')=='$NODE_ID':
        print('node type:', n.get('node_type'), '| status:', n.get('status'))
        break
"

echo "Submitting '$CHOICE' to the decision node (best-effort API call)..."
curl -s -X POST "$CP/api/dags/$DAG_ID/nodes/$NODE_ID/output" \
  -H 'Content-Type: application/json' \
  -d "{\"status\":\"completed\",\"output_context\":{\"choice\":\"$CHOICE\",\"justification\":\"$JUST\"}}" \
  -w "\nhttp=%{http_code}\n"

echo "If the call above is rejected by the platform schema, approve via the DAG UI instead;"
echo "the decision node is 'human-approval' with options approve / reject."
