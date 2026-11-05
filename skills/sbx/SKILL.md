---
name: sbx
description: Build, run, test, and briefly expose code in the isolated TaskForge agent sandbox. Use before claiming an implementation works.
---

# Agent sandbox (`sbx`)

Use the sandbox for any task where you write software that must actually build or
run. It runs on a dedicated Docker host, not the platform host. Jobs are
context tarballs; never mount host paths or pass the platform Docker socket.

## Connection

- Base URL: `$SANDBOX_URL` (default `http://sandbox:8080`; from a host shell use
  `http://localhost:8090`).
- Auth header: `X-Agent-Token: task:$TASK_ID`. The API checks the task with the
  control plane, so a token can only read or cancel its own jobs.
- Admin token is for operators only; never copy it into agent code.

## Job lifecycle

1. Create `/tmp/sbx-context/` containing a `Dockerfile` and your source/tests.
2. Tar the **contents** (not the parent directory):
   `tar -cf /tmp/sbx-context.tar -C /tmp/sbx-context .`
3. Submit:
   ```bash
   curl -sS -X POST "$SANDBOX_URL/v1/jobs" \
     -H "X-Agent-Token: task:$TASK_ID" \
     -F context=@/tmp/sbx-context.tar \
     -F task_id="$TASK_ID" \
     -F mode=job \
     -F command="python -m pytest -q" \
     -F 'tests=["python -m pytest -q"]' \
     -F 'artifacts=["/artifacts"]' \
     -F ttl_seconds=900 \
     -F cpu=2 \
     -F memory_mb=4096
   ```
   The response contains the job `id` and `status=queued`.
4. Poll `GET $SANDBOX_URL/v1/jobs/<id>` until `completed`, `failed`, or
   `cancelled`. Inspect `logs`, `test_results`, and `error`; do not assume a
   build succeeded from a 201 response.
5. Download artifacts with
   `GET $SANDBOX_URL/v1/jobs/<id>/artifacts/<path>` (the path is relative to the
   collected artifact root, e.g. `artifacts/report.json`).

`mode=service` builds and starts a long-lived container and returns a relative
gateway URL at `/sandbox/<job_id>/`. Use it only for a short validation window;
the job is removed after `ttl_seconds`. Always cancel jobs you no longer need:
`POST /v1/jobs/<id>/cancel`.

## Rules and limits

- The Dockerfile must start with an allowed base (`python:`, `node:`,
  `openclaw-agent:`, `ubuntu:`, `debian:`, `alpine:` plus optional digest).
  Use a digest for reproducibility when possible.
- No host path mounts, no privileged containers, no platform Docker socket.
- Put every deliverable the agent must keep under `/artifacts` (create it in the
  Dockerfile) and declare it in `artifacts`.
- The context is copied into the job; changes made inside the container do not
  propagate back. Copy the validated source back through artifacts or the
  workspace tools.
- Jobs have CPU/memory/PID/TTL caps. Prefer a single job that runs build + tests
  over a loop of tiny jobs.
- Never bake credentials into the image or context. Use the credential gateway
  or a short-lived environment value.

## Minimal Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /artifacts
CMD ["python", "main.py"]
```

## Python helper

A small client is bundled at `sandbox_client.py`:

```python
from sandbox_client import SandboxClient
sbx = SandboxClient()
job = sbx.submit(context_dir=".", command="python -m pytest -q",
                 tests=["python -m pytest -q"], artifacts=["/artifacts"])
job = sbx.wait(job["id"])
assert job["status"] == "completed", job.get("logs")
print(sbx.download(job["id"], "artifacts/report.json", "/tmp/report.json"))
```
