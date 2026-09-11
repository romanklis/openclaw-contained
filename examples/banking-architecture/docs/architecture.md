# Banking AI Governance Demo — architecture

## What this demonstrates

A TaskForge-driven credit workflow for a simulated enterprise bank:

- **Documents & policy** live in MongoDB (`banking_docs`): `loan_applications`, `policies`,
  `credit_memos`, append-only `credit_decision_archive`.
- **Enterprise data** lives in PostgreSQL (`banking_enterprise`) as logical systems:
  `iam` (authorization + audit), `crm`, `core`, `collateral`, `risk`, `gl`.
- **The TaskForge platform orchestrates**: a curated DAG of agentic steps (each with detailed
  prompts naming exactly where each system lives and what the agent may do) ends at a HUMAN
  decision node; only after human approval do archive/risk/GL steps run.

## Defense in depth (layered, not single-boundary)

1. **Network zone** — banking services run on the TaskForge platform Docker network; agent
   connectivity is capability-based (per-node `extra_service_env` grants, resolved by the
   worker — see `docs/worker-extension.md`).
2. **PostgreSQL RBAC** — separate technical logins with least privilege:
   - `ai_agent_v1`: CONNECT + SELECT on `iam/crm/core/collateral/risk`, INSERT only into
     `iam.audit_log`; **no `gl` schema**, no business-table DML.
   - `risk_consumer`: SELECT/UPDATE `risk.customer_limits`, idempotency markers.
   - `gl_consumer`: SELECT chart of accounts, INSERT `gl.journal_entries`.
   - `credit_officer`: IAM read + audit insert.
3. **IAM business authorization** (`iam.system_authorizations`) is separate from DB roles:
   - `AI_CREDIT_AGENT_V1`: READ all databases; DRAFT credit memo; APPROVE up to **$100,000**
     (human co-signer required).
   - `HUMAN_CREDIT_OFFICER`: APPROVE up to **$10,000,000**.
4. **Agent authority model** — the AI never self-approves, never touches `gl` with its own
   login, and never publishes. Its steps only read/analyze/draft/recommend/route.
5. **Human approval** — a TaskForge `decision` node gates archive, risk and GL steps.

## Flow (curated DAG)

`fetch-application` → `check-kyc` → `check-exposure-collateral` → `credit-memo-recommend` →
`authority-check` → `human-approval` (decision) → `finalize-approval` → `update-risk` →
`post-gl` → `verify-downstream`.

Key numbers: LTV = 3,000,000 / 5,000,000 = **0.60** (≤ 0.75); DSCR =
3,500,000 / (500,000 + 700,000) = **2.92** (≥ 1.25) using the explicit
`proposed_annual_debt_service_usd` assumption stored on the application. Post-approval:
`risk.customer_limits` → 4,000,000 and two `gl.journal_entries` (120-001 debit / 200-001
credit, 3,000,000 each, reference APP-2026-0899).

## Idempotency & append-only rules

- Risk and GL steps are guarded by event markers / unique keys
  (`reference_id + event_id + account_code + side`); replaying the same event is safe.
- `credit_decision_archive` uses deterministic `_id = <application_id>:v<version>` with a
  unique index; a final decision is never silently overwritten.
- `iam.audit_log` records APPLICATION_READ, KYC_CHECK, EXPOSURE_CHECK, COLLATERAL_CHECK,
  CREDIT_MEMO_DRAFTED, AUTHORITY_CHECK, HUMAN_APPROVAL, DECISION_ARCHIVED, RISK_UPDATED,
  GL_POSTED.

## Data owners

| System | Where | Who may write |
|---|---|---|
| banking_docs (MongoDB) | `banking-mongo` | agent: read + credit_memos insert; human archivist: readWrite archive |
| banking_enterprise (PostgreSQL) | `banking-postgres` | risk_consumer (risk), gl_consumer (gl), officers/agents (audit only) |

## Agent runtime (banking image)

Analysis nodes run on the dedicated agent image `registry:5000/openclaw-agent:banking`
(`agent-images/banking/Dockerfile`, build: `make build-banking`) with `pymongo[srv]` and
`psycopg2-binary` baked in and a runtime description that forbids network scanning. Endpoints
are injected per node by the worker (`config.extra_service_env` →
`BANKING_MONGO_HOST/PORT`, `BANKING_POSTGRES_HOST/PORT`); without that worker extension the
nodes cannot reach the banking services (see `docs/worker-extension.md`).
