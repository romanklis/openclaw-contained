# GitHub push with a token (no secrets committed)

Commit the generated project to the target repository (e.g. `romanklis/ANN2SNN`) using `git`
with a token supplied by the environment. `git` is baked into the image.

## Rules

- The token comes from env: `GITHUB_TOKEN` (or `GITHUB_PAT`). If the task text contains the
  token, extract it into an env var (e.g. export it for the command) — **never** write it to
  a tracked file, the repo remote, logs or a deliverable.
- Do not commit `.env`, `.cache/`, tokens or generated caches. Ensure `.gitignore`.
- Push only the project you authored; if the repo already has commits, rebase/merge rather
  than force-push.
- Verify the push with `git ls-remote`, then scrub the remote URL.

## Procedure

```bash
cd /workspace/<node_id>/ANN2SNN
git config user.email "agent@taskforge.local"
git config user.name  "TaskForge Agent"

# .gitignore first (secrets/caches)
printf '%s\n' '.cache/' '.env' '__pycache__/' '*.pyc' 'node_modules/' 'dist/' >> .gitignore

git add -A
git commit -m "Add ANN2SNN simulation engine, tests, API and dashboard" || true

# Authenticated remote (token only in the URL for this command; scrub after)
git remote set-url origin "https://x-access-token:${GITHUB_TOKEN}@github.com/romanklis/ANN2SNN.git"
git push -u origin HEAD:main
git remote set-url origin "https://github.com/romanklis/ANN2SNN.git"
git remote -v
git ls-remote origin HEAD          # verify without exposing the token
```

## Failure handling

- `403`/`404` on push → the token lacks `contents:write` (fine-grained PAT) or the repo name
  is wrong; report the HTTP status and STOP — do not retry blindly.
- Non-fast-forward → fetch and merge/rebase, then push again (no force).
- Never print the token; redact URLs in logs (`sed 's#x-access-token:[^@]*@#x-access-token:***@#'`).

## Deliverables

- The pushed branch (`main`) and a `GIT_PUSH_REPORT.md` with: repo, branch, commit SHA,
  file count, `git ls-remote` verification output (token-redacted), and any errors.
