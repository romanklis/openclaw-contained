# Banking PostgreSQL connectivity skill

Connect to the bank's PostgreSQL enterprise database for credit analysis and post-approval
posting. Use **only** the endpoints injected for the node; never scan or probe other hosts.
Drivers (`psycopg2`) are already installed in the `banking` agent image.

## Endpoint & credentials (env-injected per node)

- Host/port: `BANKING_POSTGRES_HOST` (default `banking-postgres`), `BANKING_POSTGRES_PORT`
  (default `5432`)
- Database: `banking_enterprise`
- Logins (choose by role, never the admin):
  - `ai_agent_v1` — password env `DB_AI_AGENT_PASSWORD`; analysis reads
  - `risk_consumer` — password env `DB_RISK_CONSUMER_PASSWORD`; post-approval risk update
  - `gl_consumer` — password env `DB_GL_CONSUMER_PASSWORD`; post-approval GL posting

## Connect

```python
import os, psycopg2
# Injected value is "host:port" (worker resolves the host to an IP); parse it.
_bh = os.environ.get("BANKING_POSTGRES_HOST", "banking-postgres:5432")
host, port = (_bh.split(":", 1) + ["5432"])[:2]
user = "ai_agent_v1"; pwd = os.environ.get("DB_AI_AGENT_PASSWORD", "")
conn = psycopg2.connect(host=host, port=int(port), dbname="banking_enterprise", user=user, password=pwd)
cur = conn.cursor()
```

## Schema map

| Schema | Purpose | AI (`ai_agent_v1`) |
|---|---|---|
| `iam` | roles, system_authorizations, audit_log | SELECT (audit INSERT allowed) |
| `crm` | customers, kyc_records | SELECT |
| `core` | accounts, credit_facilities, transactions | SELECT |
| `collateral` | assets | SELECT |
| `risk` | customer_limits, applied_events | SELECT |
| `gl` | chart_of_accounts, journal_entries | **no access** |

## Queries used by analysis steps

- KYC: `SELECT status FROM crm.kyc_records WHERE customer_id='C-1001'` (expect `APPROVED`)
- Exposure: `SELECT approved_limit, current_drawn FROM core.credit_facilities WHERE customer_id='C-1001'`
- Risk limits: `SELECT total_approved_exposure, total_utilized_exposure FROM risk.customer_limits WHERE customer_id='C-1001'`
- Collateral: `SELECT appraised_value_usd FROM collateral.assets WHERE customer_id='C-1001'`
- Authority: `SELECT action, max_approval_limit_usd, requires_human_co_signer FROM iam.system_authorizations WHERE role_id='AI_CREDIT_AGENT_V1'`
- Audit: `INSERT INTO iam.audit_log(application_id, actor, action, result, reason, correlation_id) VALUES (%s,%s,%s,%s,%s,%s)`

## Post-approval steps (different roles, idempotent)

- Risk (`risk_consumer`): `INSERT INTO risk.applied_events(event_id, application_id) VALUES ('EVT-APP-2026-0899-001','APP-2026-0899') ON CONFLICT DO NOTHING;` then
  `UPDATE risk.customer_limits SET total_approved_exposure=4000000 WHERE customer_id='C-1001';`
- GL (`gl_consumer`): insert rows with unique `(reference_id, event_id, account_code, side)` so
  replays never double-post.
