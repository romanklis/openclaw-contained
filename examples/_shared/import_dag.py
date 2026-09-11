#!/usr/bin/env python3
"""Import an example DAG template into the platform under a project.

Usage: import_dag.py <dag_json_path> <project_id>
Creates the DAG via /api/dags/manual and locks it as a template.
"""
import json
import sys
import urllib.parse
import urllib.request

BASE = "http://localhost:8000"


def _resolve_skill(image_id: str, name: str):
    q = urllib.parse.urlencode({"image_id": image_id})
    try:
        with urllib.request.urlopen(f"{BASE}/api/skill-learning/skills?{q}", timeout=15) as r:
            rows = json.loads(r.read().decode() or "[]")
    except Exception:
        return None
    for s in rows if isinstance(rows, list) else []:
        if s.get("name") == name:
            return s.get("id")
    return None


def _bind_named_skills(dag: dict) -> None:
    """Translate config.selected_skill_name -> config.selected_skill_v2_id.

    Lets example templates stay portable across fresh databases (no hardcoded skv2 ids)."""
    for node in dag.get("nodes") or []:
        cfg = node.get("config") or {}
        name = cfg.get("selected_skill_name")
        if not name or cfg.get("selected_skill_v2_id"):
            continue
        image_id = cfg.get("base_image") or "openclaw"
        skill_id = _resolve_skill(image_id, str(name))
        if skill_id:
            cfg["selected_skill_v2_id"] = skill_id
            node["config"] = cfg
        else:
            print(f"  WARNING: skill '{name}' (image {image_id}) not found for node "
                  f"'{node.get('node_id')}'")


def main() -> int:
    path, project = sys.argv[1], sys.argv[2]
    with open(path) as fh:
        dag = json.load(fh)
    dag["project_id"] = project
    _bind_named_skills(dag)

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
