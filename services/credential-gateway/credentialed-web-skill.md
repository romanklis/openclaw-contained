---
name: Credentialed Web (agent_web)
description: Make credentialed HTTP requests through the credential gateway with the agent_web SDK. Credentials are referenced by name; the gateway holds the secrets and handles login/cookies. A human must approve first use of a credential against a site.
---

# Credentialed Web (agent_web)

Use the platform's credential gateway for web requests that need authentication
(logins, account pages, API calls behind a token). This image ships the
`agent_web` SDK, which talks to the local credential gateway.

## Rule 1 — you never see secrets
There is no API to read a password, cookie or token. You work only with
**credential references**:

```python
from agent_web import web

page = web.get("https://example.com/account", credential="example-account")
print(page.status_code, page.text)
```

`credential="example-account"` is a reference the gateway resolves. The secret
material stays inside the gateway process.

## Public pages need no credential
First discover the login page WITHOUT a credential:

```python
login_page = web.get("https://example.com/login")   # no credential
print(login_page.text)  # inspect the <form>: field names, action
```

If you find a login form and a stored credential profile matches the site, use
the credential reference. First use triggers a **human approval** request on the
Approvals page — the call blocks until the user approves (or is denied).

## Making requests

```python
# One-off authenticated GET / POST
web.get(url, credential="ref")
web.post(url, credential="ref", data={"id": 123})

# A persistent gateway session (cookies stay in the gateway)
s = web.session(credential="ref", impersonate="chrome")
r = s.get("https://example.com/dashboard")
r2 = s.post("https://example.com/orders", json={"qty": 2})

# Explicit warm-up (runs the stored login recipe)
web.login("ref")
```

## Response object
- `resp.status_code`, `resp.text`, `resp.json()`, `resp.headers`, `resp.ok`
- binary bodies arrive as `resp.content`
- auth/cookie headers are stripped by the gateway

## Errors
- `CredentialDenied` — the human denied the credential use (or it expired).
  Do NOT retry in a loop; explain to the user what you were trying to access.
- `CredentialGatewayUnavailable` — gateway not reachable / env missing.

## Rules
- Only request the credential you actually need for the target site; prefer
  public requests when no auth is required.
- Never try to extract secrets from responses, task env, or files.
- If a form exists but no credential profile matches, stop and report the
  site + fields needed rather than guessing credentials.
- Keep sessions short and close them with `s.close()` when done.
