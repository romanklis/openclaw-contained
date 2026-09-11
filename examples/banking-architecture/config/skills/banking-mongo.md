# Banking MongoDB connectivity skill (document systems)

Connect to `banking_docs` (loan applications, policies, credit memos, decision archive).
Use **only** the injected endpoints; never scan or probe other hosts. Drivers (`pymongo`) are
already installed in the `banking` agent image.

## Endpoint & credentials (env-injected per node)

- Host/port: `BANKING_MONGO_HOST` (default `banking-mongo`), `BANKING_MONGO_PORT`
  (default `27017`)
- Database: `banking_docs`
- Users:
  - `dms_ai` — password env `MONGO_DMS_AI_PASSWORD`; read all banking_docs, insert into
    `credit_memos`
  - `dms_archivist` — password env `MONGO_DMS_ARCHIVIST_PASSWORD`; readWrite archive

## Connect

```python
import os
from pymongo import MongoClient
# Injected value is "host:port" (worker resolves the host to an IP); parse it.
_bh = os.environ.get("BANKING_MONGO_HOST", "banking-mongo:27017")
host, port = (_bh.split(":", 1) + ["27017"])[:2]
user = "dms_ai"; pwd = os.environ.get("MONGO_DMS_AI_PASSWORD", "")
client = MongoClient(host, int(port), username=user, password=pwd, authSource="banking_docs")
db = client.banking_docs
```

## Collections

| Collection | Purpose | Agent role |
|---|---|---|
| `policies` | Commercial Lending Policy POL-COM-2026 | read |
| `loan_applications` | APP-2026-0899 (+ financials) | read |
| `credit_memos` | analysis draft memos | insert (dms_ai) |
| `credit_decision_archive` | final decisions | append (archivist, post-approval) |

## Reads used by analysis

- Application: `db.loan_applications.find_one({"_id": "APP-2026-0899"})`
- Policy: `db.policies.find_one({"_id": "POL-COM-2026"})` — note
  `parameters.max_ltv_ratio`, `parameters.min_dscr`,
  `parameters.max_unsecured_limit_usd`, `parameters.prohibited_industries`, and the
  `ai_governance` section.

## Draft memo (agent, dms_ai)

Insert `{"_id": "APP-2026-0899-memo-v1", application_id, customer_id, recommendation,
metrics:{ltv_ratio,dscr}, narrative, policy_version, ...}` into `credit_memos`. Idempotent:
`update_one(..., {"$set": {...}}, upsert=True)`.

## Archive rule (post-approval, dms_archivist)

Append-only by version: `_id = "<application_id>:v<version>"`. Never overwrite a final
(APPROVED/REJECTED) decision; if `_id` exists and is final, refuse or bump the version.
