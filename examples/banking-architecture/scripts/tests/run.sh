#!/usr/bin/env bash
# Banking governance demo — automated smoke/integration tests.
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -f .env ] || { echo "cp .env.example .env first"; exit 1; }
set -a; source .env; set +a

EVENT_ID="EVT-APP-2026-0899-001"
APP="APP-2026-0899"
CUST="C-1001"
fail=0
pass() { echo "PASS  $1"; }
fatal() { echo "FAIL  $1"; fail=1; }

pg()  { docker exec banking-postgres psql -U "${POSTGRES_USER:-bank_admin}" -d banking_enterprise -tAc "$1"; }
pgai(){ docker exec banking-postgres env PGPASSWORD="$DB_AI_AGENT_PASSWORD" psql -U ai_agent_v1 -d banking_enterprise -tAc "$1"; }
pgr() { docker exec banking-postgres env PGPASSWORD="$DB_RISK_CONSUMER_PASSWORD" psql -U risk_consumer -d banking_enterprise -tAc "$1"; }
pgg() { docker exec banking-postgres env PGPASSWORD="$DB_GL_CONSUMER_PASSWORD" psql -U gl_consumer -d banking_enterprise -tAc "$1"; }

echo "== Test 1 — AI cannot approve \$3M =="
LIMIT=$(pg "SELECT max_approval_limit_usd FROM iam.system_authorizations WHERE role_id='AI_CREDIT_AGENT_V1' AND action='APPROVE'")
if python3 - <<EOF
limit=float("$LIMIT"); req=3000000
assert limit < req
print("DENIED reason=exceeds AI approval authority (limit=%s, requested=%s)"%(limit,req))
EOF
then pass "AI authority DENIED for 3M"; else fatal "Test1"; fi

echo "== Test 2 — AI cannot write GL =="
if pgai "INSERT INTO gl.journal_entries(reference_id,event_id,account_code,side,amount_usd) VALUES ('T','T','120-001','DEBIT',1);" >/dev/null 2>&1; then
  fatal "ai_agent_v1 GL INSERT was allowed"; else pass "ai_agent_v1 GL INSERT permission denied"; fi

echo "== Test 3 — AI can read CRM =="
[ "$(pgai "SELECT status FROM crm.kyc_records WHERE customer_id='C-1001'")" = "APPROVED" ] \
  && pass "C-1001 KYC APPROVED" || fatal "Test3"

echo "== Test 4 — AI can read collateral =="
[ "$(pgai "SELECT appraised_value_usd FROM collateral.assets WHERE asset_id='COL-TF-991'")" = "5000000.00" ] \
  && pass "COL-TF-991 appraised 5M" || fatal "Test4"

echo "== Test 5 — before approval there is no risk/GL update =="
[ "$(pg "SELECT total_approved_exposure FROM risk.customer_limits WHERE customer_id='$CUST'")" = "1000000.00" ] \
  && pass "risk unchanged (1M)" || fatal "risk baseline"
[ "$(pg "SELECT count(*) FROM gl.journal_entries WHERE reference_id='$APP'")" = "0" ] \
  && pass "no GL rows before approval" || fatal "GL baseline"

echo "   -> simulating the post-approval DAG steps (risk_consumer / gl_consumer logins)"
pgr "INSERT INTO risk.applied_events(event_id, application_id) VALUES ('$EVENT_ID','$APP') ON CONFLICT DO NOTHING; UPDATE risk.customer_limits SET total_approved_exposure=4000000 WHERE customer_id='$CUST';" >/dev/null
pgg "INSERT INTO gl.journal_entries(reference_id,event_id,account_code,side,amount_usd) VALUES
     ('$APP','$EVENT_ID','120-001','DEBIT',3000000),
     ('$APP','$EVENT_ID','200-001','CREDIT',3000000) ON CONFLICT DO NOTHING;" >/dev/null
[ "$(pg "SELECT total_approved_exposure FROM risk.customer_limits WHERE customer_id='$CUST'")" = "4000000.00" ] \
  && pass "risk updated to 4M after approval" || fatal "risk not updated"
[ "$(pg "SELECT count(*) FROM gl.journal_entries WHERE reference_id='$APP'")" = "2" ] \
  && pass "two GL journal entries after approval" || fatal "GL entries"

echo "== Test 6 — replaying the same event is idempotent =="
pgr "UPDATE risk.customer_limits SET total_approved_exposure=4000000 WHERE customer_id='$CUST';" >/dev/null
pgg "INSERT INTO gl.journal_entries(reference_id,event_id,account_code,side,amount_usd) VALUES
     ('$APP','$EVENT_ID','120-001','DEBIT',3000000),
     ('$APP','$EVENT_ID','200-001','CREDIT',3000000) ON CONFLICT DO NOTHING;" >/dev/null
[ "$(pg "SELECT total_approved_exposure FROM risk.customer_limits WHERE customer_id='$CUST'")" = "4000000.00" ] \
  && pass "risk not double-counted on replay" || fatal "risk double-count"
[ "$(pg "SELECT count(*) FROM gl.journal_entries WHERE reference_id='$APP'")" = "2" ] \
  && pass "GL not posted twice on replay" || fatal "GL double-post"

if [ "$fail" = 0 ]; then echo "ALL TESTS PASSED"; else echo "SOME TESTS FAILED"; exit 1; fi
