#!/usr/bin/env bash
# Register the read-only mirror mailbox credential with the credential gateway.
# Called automatically by examples/_shared/example.sh after config import.
set -uo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

CP="${CONTROL_PLANE_URL:-http://localhost:8000}"

echo "  waiting for control-plane at $CP ..."
ready=0
for _ in $(seq 1 30); do
  if curl -sf "$CP/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done

if [ "$ready" != "1" ]; then
  echo "  control-plane not reachable; register the credential later with:"
  echo "    bash scripts/bootstrap.sh"
  exit 0
fi

payload="$(python3 - <<'PY'
import json
print(json.dumps({
    "kind": "imap",
    "host": "greenmail",
    "port": 3143,
    "tls": False,
    "username": "taskforge@local.test",
    "password": "taskforge-demo",
    "allowed_folders": ["INBOX"],
    "max_messages": 50,
    "max_body_bytes": 2097152,
    "max_attachment_bytes": 10485760,
    "max_message_bytes": 10485760,
    "preapproved_operations": ["READ"],
}))
PY
)"

if curl -sf -X POST "$CP/api/credentials/taskforge-mail" \
    -H "Content-Type: application/json" \
    -d "$payload" >/dev/null; then
  echo "  registered credential 'taskforge-mail' (read-only IMAP)"
else
  echo "  failed to register 'taskforge-mail'; open the Credentials page and add it manually"
fi

echo "  dashboard: ${DASHBOARD_URL:-http://localhost:8095}"
echo "  next: create the DAG in TaskForge Studio (or run scripts/build-dag.sh)"
