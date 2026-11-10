# Demo runbook — screenshots for the presentation

This is the suggested order for capturing the "TaskForge builds the integration
itself" story. Each checkpoint is a good screenshot.

## 0. Prepare (before the audience)

```bash
make up                                  # platform
make build-base                          # clean machine only
make example-up NAME=email-intelligence  # infra + credential + skill
bash scripts/verify.sh
```

The dashboard should be empty with **Target DAG: not set**. The mailbox already
contains five seeded messages (one with a PDF deck).

## 1. Show the hard infrastructure

- Terminal: `docker ps --format '{{.Names}}\t{{.Status}}' | grep email`.
- Browser: `http://localhost:8095` → Overview empty state.
- Optional: GreenMail admin UI at `http://localhost:8097`.
- Talk track: mirror mailbox, phase-1 database, scheduler, dashboard — all
  running **before** TaskForge touches them.

**Screenshot:** dashboard Overview (empty, target unset).

## 2. Show the credential boundary

- TaskForge → Credentials: `taskforge-mail` (read-only IMAP, preapproved READ).
- Talk track: the agent never gets the mailbox password; the gateway audits
  access and can require approval.

**Screenshot:** credential entry.

## 3. One user command → planner builds the workflow

- TaskForge Studio → project **email-intelligence**.
- Paste `planning-objective.txt`.
- Select agent image **email** and skill **Email Intelligence Pipeline**.
- Click build.

Capture:

1. The composer with the objective and skill selected.
2. The planning state.
3. The finished DAG graph (`list-new-emails → classify-intent →
   parse-attachments → extract-structured → persist-extraction → summarize-run`).

**Screenshots:** command, planning, graph.

## 4. Run the DAG and show extraction

- Start the DAG.
- Open a node's output/deliverable; show the `email-run-summary.json` and the
  `extract-structured` output (company, stage, capital, thesis, traction).
- Optional: `psql` query against `email_intelligence.extractions`.

**Screenshots:** running DAG, node output, Postgres rows.

## 5. Show the monitoring layer

- Dashboard → Overview: totals and stored count.
- Dashboard → Emails: list with intents/companies. Click the NovaGrid pitch:
  body, PDF attachment `parsed`, extraction JSON and links.
- Dashboard → Kanban: read-only columns with cards.

**Screenshots:** Overview, Email detail, Kanban.

## 6. Scheduler: manual and interval modes

- Copy the DAG id from Studio.
- Dashboard → Settings → paste the id.
- Keep **Manual only** selected → **Save scheduler** → press **Run now**.
- Show the target DAG starting in the backoffice DAG dashboard (same DAG, no
  clone). Dashboard → Runs shows a `manual` trigger.
- Switch to **Interval**, set `60` seconds → **Save scheduler**. The Overview
  scheduler panel shows the next run time; after the interval the same DAG starts
  automatically and Runs shows an `auto` trigger.
- If a run is already active, the next trigger is skipped and the panel explains
  why. Switch back to **Manual only** to stop automatic runs; **Run now** still
  works.

**Screenshots:** Settings mode selector, Overview scheduler panel, Runs table,
Kanban after the automatic run.

## 7. Incremental check

```bash
python3 scripts/inject_synthetic_emails.py --count 1
# or a batch of 5-10:
python3 scripts/inject_synthetic_emails.py --count 7
```

Press **Run now** (or use Interval mode for an automatic run). The Overview total
should increase, existing rows should stay unchanged, and the new message should
move through `new → classified → parsed → extracted → stored`. To test duplicate
suppression, re-run the same command with `--seed 42 --count 5` twice; the
dashboard should not create duplicate records.

**Screenshots:** pre/post Overview totals, new Kanban card.

## 8. Reset between rehearsals

```bash
bash scripts/reset.sh
# then rebuild the DAG or point the scheduler at the previous one
```

`reset.sh` drops the database + seed sentinel, recreates the stack, and re-seeds
the mailbox. The credential stays registered in the gateway.

## One-liner for the talk track

> "The infrastructure is already there — mailbox mirror, database, scheduler,
> dashboard. TaskForge is the drop-in integration layer: one command, and it
> builds the process that safely reads the mail, extracts the deal data, stores
> it, and keeps the dashboard live."
