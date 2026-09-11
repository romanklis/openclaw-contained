#!/usr/bin/env bash
# verify.sh — checks containers, schemas, data, RBAC and the demo dataset.
set -uo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "cp .env.example .env first"; exit 1; }
set -a; source .env; set +a

fail=0
ok()   { echo "PASS  $1"; }
bad()  { echo "FAIL  $1"; fail=1; }
pg()   { docker exec banking-postgres psql -U "${POSTGRES_USER:-bank_admin}" -d banking_enterprise -tAc "$1" 2>/dev/null; }
pgai() { docker exec banking-postgres env PGPASSWORD="$DB_AI_AGENT_PASSWORD" psql -U ai_agent_v1 -d banking_enterprise -tAc "$1" 2>/dev/null; }
mg()   { docker exec banking-mongo mongosh --quiet mongodb://${MONGO_ROOT_USER:-mongo_admin}:${MONGO_ROOT_PASSWORD:-mongo_admin_pwd_demo}@127.0.0.1:27017/banking_docs?authSource=admin --eval "$1" 2>/dev/null; }

echo "-- containers --"
[ "$(docker inspect -f '{{.State.Running}}' banking-postgres 2>/dev/null)" = "true" ] && ok "banking-postgres running" || bad "banking-postgres running"
[ "$(docker inspect -f '{{.State.Running}}' banking-mongo 2>/dev/null)" = "true" ] && ok "banking-mongo running" || bad "banking-mongo running"

echo "-- postgres schema/data --"
[ "$(pg "SELECT count(*) FROM pg_namespace WHERE nspname IN ('iam','crm','core','collateral','risk','gl')")" = "6" ] && ok "6 schemas present" || bad "6 schemas present"
[ "$(pg "SELECT count(*) FROM crm.customers")" = "2" ] && ok "customers C-1001 + C-1002" || bad "customers C-1001 + C-1002"
[ "$(pg "SELECT status FROM crm.kyc_records WHERE customer_id='C-1001'")" = "APPROVED" ] && ok "KYC C-1001 APPROVED" || bad "KYC C-1001 APPROVED"
[ "$(pg "SELECT status FROM crm.kyc_records WHERE customer_id='C-1002'")" = "PENDING_REVIEW" ] && ok "KYC C-1002 PENDING_REVIEW" || bad "KYC C-1002 PENDING_REVIEW"
[ "$(pg "SELECT approved_limit FROM core.credit_facilities WHERE facility_id='FAC-TF-01'")" = "1000000.00" ] && ok "FAC-TF-01 active 1M" || bad "FAC-TF-01 active 1M"
[ "$(pg "SELECT appraised_value_usd FROM collateral.assets WHERE asset_id='COL-TF-991'")" = "5000000.00" ] && ok "COL-TF-991 appraised 5M" || bad "COL-TF-991 appraised 5M"
[ "$(pg "SELECT total_approved_exposure||'/'||total_utilized_exposure FROM risk.customer_limits WHERE customer_id='C-1001'")" = "1000000.00/250000.00" ] && ok "risk limits 1M / 250K" || bad "risk limits 1M / 250K"
[ "$(pg "SELECT max_approval_limit_usd FROM iam.system_authorizations WHERE role_id='AI_CREDIT_AGENT_V1' AND action='APPROVE'")" = "100000.00" ] && ok "AI approval limit 100K" || bad "AI approval limit 100K"
[ "$(pg "SELECT max_approval_limit_usd FROM iam.system_authorizations WHERE role_id='HUMAN_CREDIT_OFFICER' AND action='APPROVE'")" = "10000000.00" ] && ok "Officer limit 10M" || bad "Officer limit 10M"
[ "$(pg "SELECT count(*) FROM gl.journal_entries")" = "0" ] && ok "GL empty before approval" || bad "GL empty before approval"

echo "-- mongo data --"
[ "$(mg "print(db.policies.countDocuments({_id:'POL-COM-2026'}))")" = "1" ] && ok "policy POL-COM-2026" || bad "policy POL-COM-2026"
[ "$(mg "print(db.loan_applications.countDocuments({_id:'APP-2026-0899'}))")" = "1" ] && ok "application APP-2026-0899" || bad "application APP-2026-0899"

echo "-- postgres RBAC --"
if pgai "INSERT INTO gl.journal_entries(reference_id,event_id,account_code,side,amount_usd) VALUES ('X','Y','120-001','DEBIT',1);" >/dev/null 2>&1; then
  bad "ai_agent_v1 GL INSERT denied"
else
  ok "ai_agent_v1 GL INSERT denied"
fi
[ "$(pgai "SELECT status FROM crm.kyc_records WHERE customer_id='C-1001'")" = "APPROVED" ] && ok "ai_agent_v1 reads CRM KYC" || bad "ai_agent_v1 reads CRM KYC"
[ "$(pgai "SELECT appraised_value_usd FROM collateral.assets WHERE asset_id='COL-TF-991'")" = "5000000.00" ] && ok "ai_agent_v1 reads collateral" || bad "ai_agent_v1 reads collateral"
if pgai "UPDATE risk.customer_limits SET total_approved_exposure=0;" >/dev/null 2>&1; then
  bad "ai_agent_v1 risk UPDATE denied"
else
  ok "ai_agent_v1 risk UPDATE denied"
fi

if [ "$fail" = 0 ]; then echo "ALL CHECKS PASSED"; else echo "SOME CHECKS FAILED"; exit 1; fi
