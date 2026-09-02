#!/usr/bin/env python3
"""Import an example DAG template into the platform under a project.

Usage: import_dag.py <dag_json_path> <project_id>
Creates the DAG via /api/dags/manual and locks it as a template.
"""
import json
import sys
import urllib.request

BASE = "http://localhost:8000"


def main() -> int:
    path, project = sys.argv[1], sys.argv[2]
    with open(path) as fh:
        dag = json.load(fh)
    dag["project_id"] = project

    req = urllib.request.Request(
        f"{BASE}/api/dags/manual",
        data=json.dumps(dag).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            created = json.loads(r.read().decode())
    except Exception as e:
        print(f"  ERROR creating {path}: {getattr(e, 'read', lambda: b'')()[:300]}")
        return 1

    # Lock as a template (requires a body).
    lock_req = urllib.request.Request(
        f"{BASE}/api/dags/{created['id']}/lock",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(lock_req, timeout=30) as r:
            locked = json.loads(r.read().decode())
        print(f"  imported template {locked.get('id')} (project {project})")
    except Exception as e:
        print(f"  created {created['id']} but lock failed: {getattr(e, 'read', lambda: b'')()[:300]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
