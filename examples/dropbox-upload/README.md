# Dropbox Upload — task example

A minimal, infrastructure-less example: a single `browser_v4` agent node that
logs into Dropbox with the `dropbox` credential (stored in the credential
gateway) via the `agent_web` SDK and uploads a short text file
(`taskforge-hello.txt`).

## Prerequisites
- TaskForge platform running (`make up`) with the **credential gateway** up and
  a credential named **`dropbox`** in the store (see `config/credentials/` or
  the Credentials page at `/credentials`).
- The `openclaw-agent:browser_v4` image built (`make build-browser-v4`) so the
  `agent_web` SDK is available inside the agent.

## Import & run
1. **Import the template**: in the Frontend open *DAGs → New DAG from template*
   and pick **Dropbox Upload**, or POST the file:
   ```bash
   curl -s -X POST http://localhost:8000/api/dags \
     -H "Content-Type: application/json" \
     -d @examples/dropbox-upload/dropbox-upload.dag.json
   ```
   (Top-level fields `objective`, `default_image`, `default_llm`, `nodes`
   mirror the platform's DAG-template import format.)
2. **Run** the DAG.
3. On the **Approvals** page you will see a *Credential Access Request* for
   `dropbox` against `https://www.dropbox.com` — **Approve** it (scoped, 5-min
   TTL). Denying stops the agent.
4. The agent attempts: session login → upload → verify, and writes
   `upload_result.json` as its deliverable.

## Caveat — Dropbox bot protection
Dropbox's login is a JS SPA protected by reCAPTCHA + FingerprintJS. The gateway
uses `curl_cffi` (Chrome TLS impersonation) which sometimes passes, but Dropbox
may still present a challenge. The task is instructed to **stop and report the
blocker** rather than force/retry or fabricate success. A full interactive-login
path (Camoufox inside the gateway, then cookie hand-off to the session store) is
a follow-up enhancement.

## Credential security
The `dropbox` credential lives only in the credential store (gateway reads it;
the agent sees just the reference `"dropbox"`). Approvals scope usage to
`https://www.dropbox.com` with GET/POST. Keep the store contents out of shared
repos (see `.gitignore` note for `config/credentials/`).
