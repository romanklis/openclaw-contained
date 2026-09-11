# TaskForge objective — Banking Credit Governance demo

Paste this objective into the TaskForge "New DAG" planner to regenerate a proper DAG, or use
the curated `banking-dag.json` for a deterministic run.

> Analyze commercial loan application **APP-2026-0899** (TechFlow Solutions LLC, USD
> 3,000,000 term loan, purpose "Expansion of data center infrastructure") and produce a
> credit recommendation using the bank's governance data.
>
> System locations:
> - Document systems (MongoDB, db `banking_docs`, read-only user `dms_ai`): host
>   `banking-mongo`, port `27017`. Contains `policies` (POL-COM-2026: max LTV 0.75, min DSCR
>   1.25, max unsecured 5,000,000 USD, prohibited GAMBLING/UNREGULATED_CRYPTO),
>   `loan_applications` (APP-2026-0899 with financials incl. explicit
>   `proposed_annual_debt_service_usd = 700000`), `credit_memos` (agent may insert drafts).
> - Enterprise database (PostgreSQL, db `banking_enterprise`, read-only login `ai_agent_v1`,
>   password `DB_AI_AGENT_PASSWORD` env): host `banking-postgres`, port `5432`. Schemas:
>   `iam` (roles, system_authorizations, audit_log), `crm` (customers, kyc_records), `core`
>   (accounts, credit_facilities, transactions), `collateral` (assets), `risk`
>   (customer_limits). The `gl` schema is NOT accessible to the AI.
>
> Steps to produce (one agent node each): (1) read the application + policy; (2) check KYC
> (C-1001 APPROVED / AML LOW) and note C-1002 as the negative case; (3) read existing exposure
> (FAC-TF-01 1M drawn 250k; risk limits 1M/250k) and collateral COL-TF-991 ($5M); (4) compute
> LTV = 3M/5M = 60% (<=75% PASS) and DSCR = 3.5M/(0.5M+0.7M) = 2.92 (>=1.25 PASS) using the
> explicit application assumption, and draft a credit memo (recommendation APPROVE) into
> `credit_memos`; (5) authority check: 3M > AI limit 100k, required approver
> HUMAN_CREDIT_OFFICER, cannot self-approve; (6) a HUMAN decision node with options
> approve/reject; on approve continue to (7) archive decision to
> `credit_decision_archive` (append-only v2, event id EVT-APP-2026-0899-001), (8) update
> `risk.customer_limits` to 4,000,000 using the `risk_consumer` login (idempotent), (9) post
> two GL journal entries (120-001 debit 3M, 200-001 credit 3M, reference APP-2026-0899) using
> the `gl_consumer` login (idempotent), (10) verify risk = 4M and exactly two GL rows.
>
> The AI must never approve the facility itself, never publish anything, and never touch the
> `gl` schema with its own login.
