# Credentials

> **Primary store: OpenBao (KV v2).** Credentials live in OpenBao at
> `secret/credentials/<name>` (secrets never touch disk in plaintext). Start the
> platform (`make up` — the `openbao` service is enabled by default), set
> `OPENBAO_ENABLED=true` in `.env` (default), and manage credentials via the
> Credentials page or the API.

This directory is mounted into the `credential-gateway` only as a **deprecated
fallback + cookie-jar location** (`*.cookies.json`, Camoufox browser profiles).
The JSON-file store described below is legacy: the gateway now uses OpenBao when
`OPENBAO_ENABLED=true` (the default). Files here must not be treated as a
secure secret store.

> **Security boundary:** secrets in these files are read ONLY by the
> credential-gateway process. The agent (browser_v4) never receives them — it
> only references a credential by name through the `agent_web` SDK
> (`web.get(url, credential="example-basic")`) and a human approves first use
> on the Approvals page.

## File schema

```json
{
  "name": "example-basic",
  "kind": "basic | form | bearer | header | cookie | imap",
  "username": "…",            // form/basic
  "password": "…",            // form/basic
  "token": "…",               // bearer
  "header_name": "X-API-Key", // header (defaults to Authorization)
  "header_value": "…",        // header
  "cookie_name": "session",   // cookie
  "cookie_value": "…",        // cookie
  "login_url": "https://site/login",   // form: login recipe
  "username_field": "email",  // form (default username)
  "password_field": "password",
  "allowed_origins": ["https://example.com"],  // [] = any origin
  "allowed_methods": ["GET", "POST"]           // DELETE must be listed explicitly
}
```

`allowed_origins` (list of `scheme://netloc`) restricts which sites a
credential may be sent to; leave empty only for trusted local/test targets.
`allowed_methods` defaults to `["GET", "POST"]` — `DELETE` is never allowed
unless listed.

## Adding a credential

1. Create `config/credentials/<name>.json` (schema above).
2. `docker-compose restart credential-gateway` (or `make up`).

For the OpenBao backend (see `.env.example` `OPENBAO_ENABLED`), store each
credential in KV v2 at `secret/data/credentials/<name>` with the same top-level
fields.

## Read-only IMAP mailbox

Agents can read a dedicated mailbox through the credential gateway. The password
stays in OpenBao; the agent only references the credential name. Use a dedicated
service mailbox and an app password, never a personal account password.

```json
{
  "name": "taskforge-mail",
  "kind": "imap",
  "host": "mail.company.com",
  "port": 993,
  "tls": true,
  "username": "taskforge@company.com",
  "password": "…",
  "allowed_folders": ["INBOX"],
  "max_messages": 50,
  "max_body_bytes": 2097152,
  "max_attachment_bytes": 10485760,
  "preapproved_operations": ["READ"]
}
```

`preapproved_operations: ["READ"]` skips per-task approval prompts for this
dedicated, read-only mailbox; every access is still audited. Remove it to require
human approval on the Approvals page. The connector never sends, deletes, moves,
copies, or marks messages as read (`select(..., readonly=True)` and
`BODY.PEEK[...]`).
