#!/usr/bin/env bash
# Verify the example infrastructure, RBAC, credential, and (optionally) data.
set -uo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

CP="${CONTROL_PLANE_URL:-http://localhost:8000}"
SCHEDULER="${SCHEDULER_URL:-http://localhost:8096}"
DASHBOARD="${DASHBOARD_URL:-http://localhost:8095}"
DB="${POSTGRES_DB:-email_intelligence}"
PGUSER="${POSTGRES_USER:-email_admin}"

fail=0
ok()  { echo "PASS  $1"; }
bad() { echo "FAIL  $1"; fail=1; }
info() { echo "INFO  $1"; }

pg() {
  docker exec email-postgres psql -U "$PGUSER" -d "$DB" -tAc "$1" 2>/dev/null
}
pgwriter() {
  docker exec email-postgres env PGPASSWORD="${TASKFORGE_WRITER_PASSWORD:-taskforge_writer_pwd_demo}" \
    psql -U taskforge_writer -d "$DB" -tAc "$1" 2>/dev/null
}

echo "-- containers --"
for name in email-postgres email-scheduler email-dashboard email-greenmail; do
  if [ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" = "true" ]; then
    ok "$name running"
  else
    bad "$name running"
  fi
done

echo "-- postgres schema --"
tables="$(pg "SELECT count(*) FROM information_schema.tables WHERE table_schema='email_intelligence' AND table_name IN ('emails','attachments','extractions','scheduler_runs','settings')")"
[ "$tables" = "5" ] && ok "5 email_intelligence tables" || bad "5 email_intelligence tables (got '$tables')"
roles="$(pg "SELECT count(*) FROM pg_roles WHERE rolname IN ('taskforge_writer','email_app')")"
[ "$roles" = "2" ] && ok "writer + backend roles" || bad "writer + backend roles (got '$roles')"

echo "-- least-privilege role --"
writer_ok=0
if pgwriter "SELECT 1;" >/dev/null 2>&1; then
  writer_ok=1
  ok "taskforge_writer can connect"
else
  bad "taskforge_writer can connect"
fi
if [ "$writer_ok" = "1" ]; then
  if pgwriter "DELETE FROM email_intelligence.emails WHERE false;" >/dev/null 2>&1; then
    bad "taskforge_writer DELETE is denied"
  else
    ok "taskforge_writer DELETE is denied"
  fi
  if pgwriter "CREATE TABLE email_intelligence.denied_test(id int);" >/dev/null 2>&1; then
    bad "taskforge_writer DDL is denied"
  else
    ok "taskforge_writer DDL is denied"
  fi
  if pgwriter "SELECT count(*) FROM email_intelligence.settings;" >/dev/null 2>&1; then
    bad "taskforge_writer cannot read scheduler settings"
  else
    ok "taskforge_writer cannot read scheduler settings"
  fi
else
  bad "RBAC checks skipped because taskforge_writer cannot connect"
fi

echo "-- mirror mailbox --"
mail_count="$(python3 - <<'PY' 2>/dev/null || true
import imaplib, os
m = imaplib.IMAP4("127.0.0.1", 3143)
m.login(os.getenv("GREENMAIL_USER", "taskforge@local.test"), os.getenv("GREENMAIL_PASSWORD", "taskforge-demo"))
m.select("INBOX")
status, data = m.search(None, "ALL")
print(len((data[0] if data else b"").split()))
m.logout()
PY
)"
if [ -n "$mail_count" ] && [ "$mail_count" -ge 5 ]; then
  ok "mirror INBOX has $mail_count messages"
elif [ -n "$mail_count" ]; then
  bad "mirror INBOX has only $mail_count messages (expected 5; run scripts/reset.sh)"
else
  bad "could not read mirror INBOX on 127.0.0.1:3143"
fi

echo "-- credential --"
if curl -sf "$CP/api/credentials" | python3 -c '
import json, sys
rows = json.load(sys.stdin)
raise SystemExit(0 if any(r.get("name") == "taskforge-mail" for r in rows) else 1)
' 2>/dev/null; then
  ok "credential 'taskforge-mail' registered"
else
  bad "credential 'taskforge-mail' registered (control-plane may be down; run scripts/bootstrap.sh)"
fi

echo "-- scheduler + dashboard --"
health="$(curl -sf "$SCHEDULER/healthz" 2>/dev/null || true)"
if [ -n "$health" ]; then
  ok "scheduler /healthz reachable"
  printf '%s' "$health" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("INFO  scheduler db=%s control_plane=%s mode=%s interval=%ss next=%s target=%s" % (
    d.get("db"), d.get("control_plane"), d.get("mode") or "manual",
    d.get("interval_seconds"), d.get("next_run_at") or "none",
    d.get("target_dag_id") or "unset",
))
' || true
else
  bad "scheduler /healthz reachable"
fi
code="$(curl -s -o /dev/null -w '%{http_code}' "$DASHBOARD" 2>/dev/null || true)"
[ "$code" = "200" ] && ok "dashboard HTTP 200" || bad "dashboard HTTP 200 (got '$code')"

echo "-- pipeline data (informational) --"
emails="$(pg "SELECT count(*) FROM email_intelligence.emails")"
stored="$(pg "SELECT count(*) FROM email_intelligence.emails WHERE status='stored'")"
parsed="$(pg "SELECT count(*) FROM email_intelligence.attachments WHERE parse_status='parsed'")"
extractions="$(pg "SELECT count(*) FROM email_intelligence.extractions")"
runs="$(pg "SELECT count(*) FROM email_intelligence.scheduler_runs")"
info "emails=$emails stored=$stored parsed_attachments=$parsed extractions=$extractions runs=$runs"
if [ "${REQUIRE_DATA:-0}" = "1" ]; then
  [ "$emails" -ge 5 ] && ok "at least 5 emails processed" || bad "at least 5 emails processed"
  [ "$stored" -ge 1 ] && ok "at least 1 stored email" || bad "at least 1 stored email"
  [ "$extractions" -ge 1 ] && ok "at least 1 extraction" || bad "at least 1 extraction"
  [ "$runs" -ge 1 ] && ok "at least 1 scheduler run" || bad "at least 1 scheduler run"
fi

echo ""
if [ "$fail" = 0 ]; then
  echo "ALL CHECKS PASSED"
else
  echo "SOME CHECKS FAILED"
  exit 1
fi
