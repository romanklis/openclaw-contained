#!/usr/bin/env python3
"""Live, LLM-free smoke: block execution, DAG lock, and template instantiation."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = os.getenv("CONTROL_PLANE_URL", "http://localhost:8000").rstrip("/")
TIMEOUT = float(os.getenv("SMOKE_TIMEOUT_SECONDS", "180"))


def request(method: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        raise RuntimeError(f"{method} {path}: HTTP {exc.code} {body}") from exc


def wait_dag(dag_id: str) -> dict:
    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        status, dag = request("GET", f"/api/dags/{dag_id}")
        if status == 200 and dag.get("status") in ("completed", "failed", "cancelled"):
            return dag
        time.sleep(2)
    raise RuntimeError(f"DAG {dag_id} did not finish in {TIMEOUT}s")


def main() -> int:
    suffix = uuid.uuid4().hex[:8]
    block_id = f"smoke-echo-{suffix}"
    dag_ids: list[str] = []
    try:
        status, health = request("GET", "/health")
        if status != 200:
            raise RuntimeError(f"control plane unhealthy: {health}")

        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                _, workers = request("GET", "/api/workers/status")
                if any(not item.get("stale") for item in workers.get("workers", [])):
                    break
            except Exception:
                pass
            time.sleep(2)
        else:
            raise RuntimeError("no ready worker")

        block = {
            "id": block_id,
            "name": "Smoke echo",
            "description": "Deterministic smoke block",
            "entrypoint": "main",
            "status": "active",
            "inputs_schema": {"value": {"type": "integer"}},
            "outputs_schema": {"echo": {"type": "object"}},
            "code": {
                "files": {
                    "main.py": (
                        "def main(inputs):\n"
                        "    return {'echo': inputs}\n"
                    )
                },
                "entry_file": "main.py",
            },
            "conformance": {
                "sample_input": {"value": 7},
                "expect": {"echo": {"value": 7}},
            },
        }
        request("POST", "/api/blocks", block)

        _, dag = request(
            "POST",
            "/api/dags/manual",
            {
                "objective": f"Block smoke {suffix}",
                "nodes": [
                    {
                        "node_id": "echo",
                        "node_type": "block",
                        "description": "Run the smoke echo block",
                        "depends_on": [],
                        "config": {
                            "type": "block",
                            "block": block_id,
                            "inputs": {"value": 7},
                        },
                        "input_mapping": {},
                    }
                ],
                "edges": [],
                "default_image": "openclaw",
                "default_llm": "deepseek-flash",
            },
        )
        source_id = dag["id"]
        dag_ids.append(source_id)
        request("POST", f"/api/dags/{source_id}/start")
        source = wait_dag(source_id)
        if source["status"] != "completed":
            raise RuntimeError(f"source DAG {source_id} ended {source['status']}")

        request(
            "POST",
            f"/api/dags/{source_id}/lock",
            {"parameters": []},
        )
        _, instance = request(
            "POST",
            f"/api/dags/{source_id}/instantiate",
            {"parameters": {}, "auto_start": True},
        )
        instance_id = instance["id"]
        dag_ids.append(instance_id)
        instance = wait_dag(instance_id)
        if instance["status"] != "completed":
            raise RuntimeError(f"template instance {instance_id} ended {instance['status']}")

        print(f"OK block={block_id} source={source_id} instance={instance_id}")
        return 0
    finally:
        for dag_id in dag_ids:
            try:
                request("POST", f"/api/dags/{dag_id}/unlock")
            except Exception:
                pass
            try:
                request("POST", f"/api/dags/{dag_id}/archive")
            except Exception:
                pass
        try:
            request("DELETE", f"/api/blocks/{block_id}")
        except Exception:
            pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"SMOKE FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
