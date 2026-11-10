# GitHub Programming & Push

A two-node example that shows the full loop:

1. an agent **writes and tests** a small Python CLI (`textstats`);
2. a second agent **commits and pushes** it to GitHub with a repo-scoped,
   task-scoped token, then **verifies the remote tree**.

No token is placed in the DAG, task prompt, repository, remote URL, or reports.
The worker leases `GITHUB_TOKEN` from the credential gateway and injects it only
into the authorized push node's ephemeral container.

## What gets built

- `textstats` — a stdlib-only Python CLI:
  - word / line / character counts
  - case-insensitive top-N words
  - file or stdin input, `--top`, `--json`
  - pytest tests, `pyproject.toml`, README, `.gitignore`, sample text
- `GIT_PUSH_REPORT.md` — repo, branch, commit SHA, file list/count, and remote
  verification output (token-redacted).

## Prerequisites

- TaskForge platform up (`make up`), with the `openclaw` agent image.
- A GitHub repository you own (empty is easiest), e.g.
  `YOUR_GITHUB_OWNER/taskforge-textstats`.
- A fine-grained PAT scoped to that repository:
  - **Contents: Read and write** (required)
  - **Metadata: Read**
  - Optional: Actions read, Pull requests read/write.

## 1. Configure the target repo

Edit `config/dags/github-programming-push.dag.json` **before** importing the
template and replace both placeholders:

```json
"github_push": {
  "enabled": true,
  "authorized": true,
  "repository": "YOUR_GITHUB_OWNER/taskforge-textstats",
  "branch": "main",
  "credential": "github-textstats",
  "require_approval": true
}
```

Also set `TARGET_REPO` and `TARGET_BRANCH` in the push node objective to the same
values. The credential repository/branch and the node scope must match exactly;
the worker fails closed if they do not.

## 2. Store the token

### Frontend (recommended)

Open <http://localhost:3000/credentials>:

| Field | Value |
|---|---|
| Type | GitHub |
| Name | `github-textstats` |
| Repository | `YOUR_GITHUB_OWNER/taskforge-textstats` |
| Branch | `main` |
| Username | `x-access-token` |
| PAT | your fine-grained token |

The table shows only a masked token (`🔑 token`). The token is stored in OpenBao
and is never returned to the browser again.

### CLI alternative

```bash
cd /home/roman/projects/openclaw-contained
read -r -s GITHUB_TOKEN; export GITHUB_TOKEN

GITHUB_REPOSITORY=YOUR_GITHUB_OWNER/taskforge-textstats \
GITHUB_CREDENTIAL_NAME=github-textstats \
./scripts/store-github-credential.sh

unset GITHUB_TOKEN
```

The helper passes the token into the control-plane container's process
environment and writes it to OpenBao through the credential gateway. It never
logs or prints the token.

## 3. Import and run

```bash
make example-up NAME=github-programming-push
```

This example has no extra infrastructure. The command creates the example
project, imports the `github-push` skill and the DAG template, and locks the
template.

Then open the UI, instantiate the **GitHub Programming & Push** template, and
run it. Or, if you use the API, instantiate the locked template from the DAG
library.

## What happens

1. `build-solution` writes the package, tests, README, `.gitignore`, and sample
   input under `/workspace/build-solution/textstats/`, runs
   `python -m pytest -q`, and writes `build_report.md`.
2. `push-to-github` copies the project to `/tmp/textstats-repo`, commits it, and
   pushes with a one-shot git credential helper using the injected
   `GITHUB_TOKEN`. It then verifies the remote branch and writes
   `GIT_PUSH_REPORT.md`.
3. The token never appears in `.git/config`, the remote URL, the repository, the
   task prompt, or the report.

## Expected result

- Remote branch `main` contains:
  - `textstats/__init__.py`
  - `textstats/cli.py`
  - `tests/test_textstats.py`
  - `README.md`
  - `pyproject.toml`
  - `.gitignore`
  - `examples/sample.txt`
- `build-solution` test suite passes.
- `push-to-github/GIT_PUSH_REPORT.md` includes the commit SHA and verification.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `BLOCKER: GITHUB_TOKEN not injected` | The node is not authorized, or the credential is missing/mismatched. Check `github_push.authorized` and the credential repository/branch. |
| `401` / `403` | Token lacks Contents write, is expired/revoked, or targets a different repository. Rotate the credential; do not put the token inline. |
| `repository is not allowed` / lease denied | The credential repository and `github_push.repository` differ. Make them identical. |
| Non-fast-forward | The remote has commits. The push node clones/fetches and rebases; it never force-pushes. |
| Template not editable | Edit the DAG JSON before `make example-up`; locked templates are not edited in place. |

## Security and rotation

- Rotate by editing the credential on `/credentials` (blank token = keep existing)
  or re-run the CLI helper. No service restart is required.
- Revoke by deleting the credential; authorized push nodes then fail closed.
- The token is present only in the push node's ephemeral container and is removed
  with the container.
- Never paste the token into the task prompt, DAG JSON, README, commit message,
  or git remote URL.

## Related

- Fine-grained PAT permissions: Contents read/write, Metadata read.
- Platform credential storage: `config/credentials/README.md`.
- GitHub App short-lived token lease is the planned Phase 2 replacement for the
  PAT; the node/agent interface stays the same.
