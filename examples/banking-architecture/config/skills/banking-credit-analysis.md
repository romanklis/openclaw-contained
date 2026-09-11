# Banking Credit Analysis — demo skill

Detailed step prompts for a TaskForge credit-governance workflow. Each step names the exact
system it reads and what the agent may do. The curated template is
`config/dags/banking-credit-governance.json`; this skill lets the planner regenerate the same
DAG from an objective (see `planning-objective.md`).

> **Runtime**: run analysis nodes on the `banking` agent image
> (`registry:5000/openclaw-agent:banking`, built via `make build-banking`) which bakes in
> `pymongo` and `psycopg2`. Connectivity details live in the companion skills
> [`banking-postgres.md`](banking-postgres.md) and [`banking-mongo.md`](banking-mongo.md);
> hard rule: use only injected endpoints, never scan the network.

## System locations

- **Document systems** — MongoDB `banking_docs` at `banking-mongo:27017`, read-only user
  `dms_ai` (password in env `MONGO_DMS_AI_PASSWORD`). Collections: `loan_applications`,
  `policies`, `credit_memos` (insert allowed for drafts), `credit_decision_archive`.
- **Enterprise database** — PostgreSQL `banking_enterprise` at `banking-postgres:5432`.
  - AI analysis login: `ai_agent_v1` (env `DB_AI_AGENT_PASSWORD`) — SELECT on schemas
    `iam`, `crm`, `core`, `collateral`, `risk`; INSERT audit rows into `iam.audit_log` only.
    **No `gl` schema, no business-table DML.**
  - Post-approval logins: `risk_consumer` (env `DB_RISK_CONSUMER_PASSWORD`) and
    `gl_consumer` (env `DB_GL_CONSUMER_PASSWORD`).

## Steps (one agent node per step)

1. **fetch-application** — read `loan_applications` `_id=APP-2026-0899` and `policies`
   `_id=POL-COM-2026`. Record amount 3,000,000 USD TERM_LOAN, financials (revenue 15M,
   EBITDA 3.5M, existing debt service 500K, explicit `proposed_annual_debt_service_usd`
   700K) and policy parameters (max LTV 0.75, min DSCR 1.25, max unsecured 5M, prohibited
   GAMBLING / UNREGULATED_CRYPTO).
2. **check-kyc** — read `crm.customers` + `crm.kyc_records`: C-1001 KYC APPROVED / AML LOW
   (pass); note C-1002 (HIGH / PENDING_REVIEW) as the negative case.
3. **check-exposure-collateral** — read `core.credit_facilities` (FAC-TF-01 1M / 250K drawn),
   `risk.customer_limits` (1M / 250K), `collateral.assets` (COL-TF-991 appraised 5M).
4. **credit-memo-recommend** — compute LTV = 3M/5M = 0.60 (≤0.75) and DSCR =
   3.5M/(0.5M+0.7M) = 2.92 (≥1.25) using the application's explicit debt-service assumption;
   insert draft memo `_id=APP-2026-0899-memo-v1` into `credit_memos`.
5. **authority-check** — read `iam.system_authorizations`: AI approve limit 100,000 (human
   co-signer required); officer limit 10,000,000. Verdict: cannot self-approve 3M, route to
   `HUMAN_CREDIT_OFFICER`.
6. **human-approval** (decision node) — options approve / reject.
7. **finalize-approval** — append APPROVED archive doc `_id=APP-2026-0899:v2`
   (event_id EVT-APP-2026-0899-001); never overwrite a final decision.
8. **update-risk** — as `risk_consumer`: set `risk.customer_limits` to 4,000,000 with an
   `risk.applied_events` marker (idempotent).
9. **post-gl** — as `gl_consumer`: insert 120-001 DEBIT 3M + 200-001 CREDIT 3M
   (reference APP-2026-0899) — unique key prevents duplicates.
10. **verify-downstream** — confirm risk 4M and exactly two GL rows.

Audit actions to log to `iam.audit_log` (application_id, actor, action, result, reason,
correlation_id): APPLICATION_READ, KYC_CHECK, EXPOSURE_CHECK, COLLATERAL_CHECK,
CREDIT_MEMO_DRAFTED, AUTHORITY_CHECK, HUMAN_APPROVAL, DECISION_ARCHIVED, RISK_UPDATED,
GL_POSTED.
