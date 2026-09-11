#!/usr/bin/env python3
"""Import example v2 skills into the platform.

Usage: import_skills.py <skills_dir>

Each ``*.skill.json`` in the directory describes a skill:
    {name, image_id, description, tags, source_type, files: [<md filenames>]}
The listed markdown files (relative to the directory) are concatenated into the
skill's ``instructions``. Skills are created if missing (matched by image_id +
name) and approved so they are ACTIVE and visible to the planner.
"""
import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://localhost:8000"


def _call(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def _find_skill(image_id: str, name: str):
    q = urllib.parse.urlencode({"image_id": image_id})
    status, rows = _call("GET", f"/api/skill-learning/skills?{q}")
    if status == 200 and isinstance(rows, list):
        for s in rows:
            if s.get("name") == name:
                return s
    return None


def main() -> int:
    skills_dir = pathlib.Path(sys.argv[1])
    if not skills_dir.is_dir():
        print(f"  skills dir not found: {skills_dir}")
        return 0
    imported = 0
    for spec_file in sorted(skills_dir.glob("*.skill.json")):
        spec = json.loads(spec_file.read_text())
        name = str(spec.get("name") or "").strip()
        image_id = str(spec.get("image_id") or "").strip()
        if not name or not image_id:
            print(f"  skipping {spec_file.name}: name/image_id required")
            continue

        parts = []
        for rel in spec.get("files") or []:
            p = skills_dir / rel
            if p.is_file():
                parts.append(f"== {rel} ==\n{p.read_text()}")
        instructions = "\n\n".join(parts)

        existing = _find_skill(image_id, name)
        if existing:
            skill_id = existing["id"]
            if existing.get("status") != "active":
                _call("POST", f"/api/skill-learning/skills/{skill_id}/review",
                      {"decision": "approve", "reviewed_by": "example-bootstrap"})
            print(f"  skill '{name}' already present ({skill_id})")
            continue

        status, created = _call("POST", "/api/skill-learning/skills", {
            "name": name,
            "image_id": image_id,
            "description": str(spec.get("description") or ""),
            "instructions": instructions,
            "tags": list(spec.get("tags") or []),
            "source_type": str(spec.get("source_type") or "manual"),
        })
        if status not in (200, 201) or not created.get("id"):
            print(f"  ERROR importing skill '{name}': {status} {created}")
            continue
        skill_id = created["id"]
        _call("POST", f"/api/skill-learning/skills/{skill_id}/review",
              {"decision": "approve", "reviewed_by": "example-bootstrap"})
        print(f"  imported skill {skill_id} '{name}' (image {image_id}, ACTIVE)")
        imported += 1
    print(f"  skills processed ({imported} new)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
