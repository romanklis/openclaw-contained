#!/bin/sh
# Store a fine-grained GitHub PAT in OpenBao through the credential gateway.
#
# The token is read from the GITHUB_TOKEN environment variable and passed into
# the control-plane container's process environment. It is never written to the
# repository, the task prompt, .env, logs, or a git remote URL.
#
# Usage:
#   read -r -s GITHUB_TOKEN; export GITHUB_TOKEN
#   ./scripts/store-github-credential.sh
#
# Optional:
#   GITHUB_REPOSITORY=owner/repo (default: romanklis/fake-video-detection)
#   GITHUB_BRANCH=main
#   GITHUB_CREDENTIAL_NAME=github
set -eu

if [ -z "${GITHUB_TOKEN:-}" ]; then
    echo "GITHUB_TOKEN is not set; refusing to store an empty token" >&2
    exit 2
fi

REPOSITORY=${GITHUB_REPOSITORY:-romanklis/fake-video-detection}
BRANCH=${GITHUB_BRANCH:-main}
CREDENTIAL=${GITHUB_CREDENTIAL_NAME:-github}
ADMIN_TOKEN=${CREDENTIAL_GATEWAY_ADMIN_TOKEN:-openclaw-admin}

docker exec \
    -e GITHUB_TOKEN \
    -e GITHUB_REPOSITORY="$REPOSITORY" \
    -e GITHUB_BRANCH="$BRANCH" \
    -e GITHUB_CREDENTIAL_NAME="$CREDENTIAL" \
    -e CREDENTIAL_GATEWAY_ADMIN_TOKEN="$ADMIN_TOKEN" \
    openclaw-control-plane \
    python - <<'PY'
import os
import sys

import httpx

name = os.environ.get("GITHUB_CREDENTIAL_NAME", "github")
repository = os.environ.get("GITHUB_REPOSITORY", "romanklis/fake-video-detection")
branch = os.environ.get("GITHUB_BRANCH", "main")
token = os.environ.get("GITHUB_TOKEN", "")
admin = os.environ.get("CREDENTIAL_GATEWAY_ADMIN_TOKEN", "openclaw-admin")

if not token:
    print("GITHUB_TOKEN is empty", file=sys.stderr)
    raise SystemExit(2)

payload = {
    "kind": "bearer",
    "provider": "github",
    "repository": repository,
    "branch": branch,
    "username": "x-access-token",
    "token": token,
}
resp = httpx.post(
    f"http://credential-gateway:8083/v1/credentials/{name}",
    headers={"X-Admin-Token": admin},
    json=payload,
    timeout=15.0,
)
if resp.status_code >= 300:
    print(f"failed: HTTP {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
    raise SystemExit(1)

body = resp.json() if resp.content else {}
print(
    f"stored credential '{name}' for {repository}@{branch} "
    f"(has_token={bool(body.get('has_token', True))})"
)
PY
