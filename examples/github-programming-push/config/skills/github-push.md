# GitHub push (task-scoped token)

Push a tested project to GitHub using the `GITHUB_TOKEN` injected by the platform
for this authorized `github_push` node.

## Rules

- `GITHUB_TOKEN` is already in the container environment. Never print it, echo it,
  write it to a tracked file, put it in the remote URL, or leave it in `.git/config`.
- Never put the token in the task prompt, README, commit message, report, or logs.
- Work in `/tmp`; only write final deliverables to `/workspace/push-to-github/`.
- Do not force-push. If the remote is non-empty, fetch and merge/rebase.
- If the token is missing or GitHub returns 401/403, stop and report the blocker.
  Do not substitute an inline credential.

## Procedure

Replace `TARGET_REPO` with the `owner/repo` from the node objective and keep the
branch consistent with `TARGET_BRANCH`.

```bash
# 1. Prepare the repository outside the deliverables directory
rm -rf /tmp/textstats-repo
mkdir -p /tmp/textstats-repo
cp -R /workspace/build-solution/textstats/. /tmp/textstats-repo/
find /tmp/textstats-repo -name .git -prune -exec rm -rf {} +
find /tmp/textstats-repo -name __pycache__ -prune -exec rm -rf {} +
find /tmp/textstats-repo -name .pytest_cache -prune -exec rm -rf {} +

cd /tmp/textstats-repo
git init -b main
git config user.email "agent@taskforge.local"
git config user.name "TaskForge Agent"
git add -A
git commit -m "Add tested textstats Python CLI"
git remote add origin "https://github.com/TARGET_REPO.git"

# 2. Push with a one-shot credential helper (token never stored)
GIT_TERMINAL_PROMPT=0 git \
  -c credential.helper= \
  -c credential.helper='!f() { echo username=x-access-token; echo password=$GITHUB_TOKEN; }; f' \
  push -u origin HEAD:main

# 3. Verify without exposing the token
git ls-remote origin refs/heads/main
git fetch origin main
git ls-tree -r --name-only FETCH_HEAD
```

If the remote already has commits:

```bash
git clone https://github.com/TARGET_REPO.git /tmp/textstats-remote
# copy the project files into /tmp/textstats-remote, commit, fetch/rebase, push
# never use --force
```

## Report

Write `/workspace/push-to-github/GIT_PUSH_REPORT.md` with:

- repository and branch
- commit SHA
- file list/count
- `git ls-remote` and `git ls-tree` verification results
- any errors or blockers

Redact any token or authenticated URL. Leave `.git/config` free of
`credential.helper` and token-bearing remotes.
