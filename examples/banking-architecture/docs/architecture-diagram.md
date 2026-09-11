# Banking governance architecture diagram

Editable draw.io source: `taskforge-banking-integration.xml`
Rendered export: `taskforge-banking-integration.png`

Open the XML in [draw.io / diagrams.net](https://app.diagrams.net) (or drag it there). It has
two pages, mirroring the Docling RAG example's diagram:

1. **Architecture** — how the TaskForge platform connects to the dedicated banking systems.
2. **Banking Workflow (dag-3851ca63)** — the concrete end-to-end run as a swimlane diagram.

Re-export the PNG after edits (both pages):

```bash
docker run --rm -v "$PWD":/data rlespinasse/drawio-desktop-headless \
  -x -f png --all-pages \
  -o /data/taskforge-banking-integration.png \
  /data/taskforge-banking-integration.xml
```

## Page 1 — Architecture

- **Entry points**: TaskForge frontend `:3000`, API gateway `:8080`, control plane `:8000`.
- **Platform**: Temporal `:7233` (+UI), the Temporal worker (`DAGWorkflow` /
  `DAGNodeWorkflow` / `start_agent_container` / `run_block`), the image registry with
  DinD/gVisor sandboxing, and control-plane state (tasks, DAGs, skills v2, approval state).
- **Sandboxed execution**: agent containers run the dedicated image
  `registry:5000/openclaw-agent:banking`, which bakes in `pymongo[srv]` and
  `psycopg2-binary`, and load the bound skills v2:
  - `banking-credit-analysis` — analysis workflow (KYC/exposure/collateral, LTV+DSCR,
    memo, authority check + routing).
  - `banking-data-access` — MongoDB/PostgreSQL connectivity rules (endpoint-only, no
    scanning, RBAC logins).
- **Endpoint injection**: agent containers have no DNS on DinD, so the worker resolves the
  DAG node's `config.extra_service_env` entries and injects reachable IP endpoints
  (`BANKING_MONGO_HOST`, `BANKING_POSTGRES_HOST`) plus the demo role passwords. Explicit
  grants win; the banking image also receives the demo defaults.
- **Banking systems** (example compose on the platform network):
  - `banking-mongo :27017` — `banking_docs`: `policies`, `loan_applications`,
    `credit_memos`, `credit_decision_archive` (users `dms_ai`, `dms_archivist`).
  - `banking-postgres :5432` — `banking_enterprise`: `iam`, `crm`, `core`, `collateral`,
    `risk`, `gl`; RBAC logins `ai_agent_v1` (read iam/crm/core/collateral/risk + audit
    INSERT, **no `gl`**), `risk_consumer`, `gl_consumer`, `credit_officer`.
- **Governance (defense in depth)**: IAM business authorizations (AI approve ≤ $100,000
  with human co-signer; HUMAN_CREDIT_OFFICER ≤ $10,000,000), the human decision gate, and
  post-approval-only effects (archive append-only, risk 1,000,000 → 4,000,000, two GL
  journal entries).

Legend: green = data/orchestration, blue = platform/DB, purple = agent runtime + skills,
yellow = governance/injection, red = human gate. Host ports are debug-only; the boundary is
Docker network + DB RBAC + agent authority + human approval.

## Page 2 — How dag-3851ca63 worked

Objective: analyze `APP-2026-0899` (TechFlow Solutions LLC, USD 3,000,000 term loan) to
produce a credit recommendation under the bank's governance model.

Planner prompt that produced this exact run (and the granular nodes below):

> Analyze commercial loan application APP-2026-0899 (TechFlow Solutions LLC, USD 3,000,000
> term loan) using the bank's governance data and produce a credit recommendation. Retrieve
> the application and policy from the banking_docs document system (MongoDB), verify IAM
> authority, customer KYC/AML status, existing credit exposure, collateral, then calculate
> LTV and DSCR against Commercial Lending Policy POL-COM-2026. Draft a credit memo, and route
> the decision to a HUMAN_CREDIT_OFFICER because the amount exceeds the AI approval limit.
> Never self-approve and never post GL or risk updates: those happen only in post-approval
> steps. Make sure steps are covered granularly.

Observed run (planner-generated DAG, banking image, skills v2 bound):

| # | Node | Type | Skill | Status / timing |
|---|------|------|-------|-----------------|
| 1 | `fetch-application-and-policy` | agent | `banking-data-access` | completed 09:25:15 → 09:26:14 (~59s) |
| 2 | `verify-iam-authority` | agent | `banking-data-access` | completed 09:26:15 → 09:27:21 (~66s) |
| 3 | `kyc-aml-exposure-collateral` | agent | `banking-credit-analysis` | completed 09:26:15 → 09:28:17 (~2m) |
| 4 | `compute-ltv-dscr` | agent | `banking-credit-analysis` | completed 09:28:17 → 09:29:46 (~1.5m) |
| 5 | `draft-credit-memo` | agent | `banking-credit-analysis` | completed 09:29:46 → 09:33:37 (~4m) |
| 6 | `human-credit-officer-decision` | decision | — | **running** (paused since 09:33:38) |
| 7 | `rework-credit-memo` | agent | `banking-credit-analysis` | pending (reject branch) |
| 8 | `post-approval-gl-risk-updates` | agent | `banking-data-access` | pending (approve branch) |

Flow:
1. The worker launched each node on the `banking` image and injected the resolved
   `BANKING_*` endpoints; nodes loaded their bound skill as `SKILL_INSTRUCTIONS`.
2. Steps 1–5 read `banking_docs`/`banking_enterprise` (pymongo / psycopg2) — application +
   policy, IAM limits, KYC/AML, exposure, collateral — with read-only `ai_agent_v1`, and
   computed LTV = 3M/5M = 60% (≤ 0.75) and DSCR = 3.5M/(0.5M+0.7M) = 2.92 (≥ 1.25).
3. `draft-credit-memo` wrote the recommendation (`APPROVE`) to `credit_memos` and reached the
   authority conclusion: requested $3M > AI limit $100K, so **cannot self-approve** → route
   to `HUMAN_CREDIT_OFFICER`.
4. The DAG is parked at the human decision node. No archive/risk/GL effect has happened.
5. On **approve**, the post-approval node will (idempotently) append the archive decision and
   update risk to 4,000,000 and post the two GL entries; on **reject**, the rework branch runs
   instead.

This is why the workflow is a durable TaskForge/Temporal run rather than ad-hoc scripts:
retries and per-node visibility, a durable human-approval pause, and a governance guarantee
enforced by the worker/IAM/RBAC rather than by convention.
