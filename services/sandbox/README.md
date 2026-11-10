# Agent sandbox

Build, run, test, and briefly expose AI-generated solution code on a dedicated
Docker host. The service never mounts host paths and never exposes the platform
Docker socket. Jobs are workspace context tarballs; the only published route for
a job is the HTTP gateway at `/sandbox/{job_id}/...`.

## Run

```bash
docker-compose up -d sandbox sandbox-dind
curl http://localhost:8090/v1/health
```

The gateway is published at `http://localhost:8090` (inside the compose network:
`http://sandbox:8080`). Worker-launched agents receive `SANDBOX_URL` and
`TASK_ID`; they authenticate with `X-Agent-Token: task:<TASK_ID>`. The control
plane is consulted for every non-admin request.

## API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/v1/jobs` | Submit a multipart job (`context` tar + fields) |
| GET | `/v1/jobs/{id}` | Status, logs, tests, error, artifact list |
| GET | `/v1/jobs/{id}/artifacts/{path}` | Download a collected artifact |
| POST | `/v1/jobs/{id}/cancel` | Stop and remove a job |
| DELETE | `/v1/jobs/{id}` | Delete a finished job record |
| ANY | `/sandbox/{id}/{path}` | Reverse proxy to a `mode=service` job |

Submit fields: `task_id`, `mode` (`job` or `service`), `dockerfile`, `command`,
`tests` (JSON array), `artifacts` (JSON array of in-container paths), `ports`
(JSON array), `ttl_seconds`, `cpu`, `memory_mb`.

```bash
tar -cf /tmp/context.tar -C ./my-project .
curl -sS -X POST http://localhost:8090/v1/jobs \
  -H "X-Sandbox-Token: $SANDBOX_ADMIN_TOKEN" \
  -F context=@/tmp/context.tar \
  -F task_id=demo \
  -F mode=job \
  -F command="python -m pytest -q" \
  -F 'tests=["python -m pytest -q"]' \
  -F 'artifacts=["/artifacts"]'
```

`tests` run in fresh containers from the same built image, after the main
command, and are reported individually. A job is `completed` only when the main
command exits 0; failed tests do not fail the job by themselves, so inspect
`test_results`.

## Security

- Allowed base images are configured with `SANDBOX_ALLOWED_BASE_IMAGES`
  (default: `python:`, `node:`, `openclaw-agent:`, `ubuntu:`, `debian:`,
  `alpine:`).
- Containers run without capabilities, with `no-new-privileges`, PID/memory/CPU
  limits, a tmpfs `/tmp`, and an internal job network.
- `SANDBOX_ADMIN_TOKEN` is operator-only. Agents use task tokens; a task cannot
  read or cancel another task's job.
- Service jobs are removed after `ttl_seconds`; cancel them explicitly when
  validation is finished.
