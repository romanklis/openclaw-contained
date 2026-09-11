#!/usr/bin/env bash
# Documents / asserts the agent capability grants for the demo.
# In this demo the capability boundary is enforced by:
#   1) Docker network membership (banking services on the platform network),
#   2) DB RBAC logins (ai_agent_v1 read-only, no gl schema),
#   3) the IAM business authorization model, and
#   4) the curated DAG's human decision gate.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "cp .env.example .env first"; exit 1; }
set -a; source .env; set +a

NET="${PLATFORM_NETWORK:-openclaw-contained_openclaw-network}"
echo "Capability zone check: banking services must be attached to '$NET'"
for c in banking-postgres banking-mongo; do
  if docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$c" 2>/dev/null | grep -q "$NET"; then
    echo "PASS  $c attached to $NET"
  else
    echo "WARN  $c not found on $NET — run scripts/up.sh after the platform network exists"
  fi
done
echo ""
echo "Agent capabilities granted to AI_CREDIT_AGENT_V1:"
docker exec banking-postgres psql -U "${POSTGRES_USER:-bank_admin}" -d banking_enterprise -c \
  "SELECT action, resource, COALESCE(max_approval_limit_usd::text,'unlimited') AS max_usd, requires_human_co_signer FROM iam.system_authorizations WHERE role_id='AI_CREDIT_AGENT_V1' ORDER BY action;" 2>/dev/null || echo "(postgres not up yet)"
echo ""
echo "Note: agent container network attachment is decided by the TaskForge worker. The curated"
echo "DAG declares endpoint env grants per node (config.extra_service_env) that the worker"
echo "resolves — see docs/worker-extension.md for the platform change that wires this in."
