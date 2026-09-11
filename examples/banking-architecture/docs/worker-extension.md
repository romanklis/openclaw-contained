# Worker extension: endpoint env injection for demo systems (`config.extra_service_env`)

The curated DAG declares per-node system grants, e.g.

```json
"extra_service_env": {
  "BANKING_POSTGRES_HOST": "banking-postgres",
  "BANKING_POSTGRES_PORT": "5432"
}
```

The TaskForge worker must resolve these hostnames to reachable IPs (agents run on DinD with
isolated DNS, exactly like the existing control-plane / credential-gateway resolution) and
inject them into the agent container environment.

## Status

Not yet wired into `services/temporal-worker/worker.py`. The demo runs fully at the data/RBAC
level today; agent nodes that need Postgres/Mongo connectivity require this patch.

## Patch sketch (apply when wiring)

In `start_agent_container` (`services/temporal-worker/worker.py`, activity ~line 934), add a
last optional parameter:

```python
extra_service_env: dict | None = None,
```

After `agent_env` is built (~line 1265) and before `containers.run`, resolve + merge:

```python
def _resolve(name: str) -> str:
    import socket
    try:
        return socket.gethostbyname(name)
    except Exception:
        return ""

if extra_service_env:
    for env_key, host in extra_service_env.items():
        if not isinstance(host, str) or ":" not in host:
            continue
        svc, port = host.rsplit(":", 1)
        ip = _resolve(svc)
        if not ip:
            raise RuntimeError(f"cannot resolve service '{svc}' for {env_key}")
        agent_env[env_key] = f"{ip}:{port}"
```

Thread the value through the workflow callers of `start_agent_container` (e.g.
`AgentStepWorkflow.run`) from the DAG node `config.extra_service_env`, defaulting to `None` so
existing DAGs are unaffected. Node config arrives in the agent-node branch of
`DAGNodeWorkflow`/`AgentTaskWorkflow`; pass `node_config.get("extra_service_env")` through the
child-workflow arguments.

## Validation after wiring

- A banking DAG agent node can `psql` to `banking-postgres` as `ai_agent_v1` and `mongosh` to
  `banking-mongo` as `dms_ai` from inside its container.
- Nodes without the grant cannot resolve the host (clear capability error).
