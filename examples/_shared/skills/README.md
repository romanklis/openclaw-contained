# Shared example skills

Portable v2 skill definitions imported as **ACTIVE** whenever an example is brought up
(`make example-up NAME=<example>` imports this directory first), and on demand via:

```bash
make skills-import          # equals: python3 examples/_shared/import_skills.py examples/_shared/skills
```

Each `*.skill.json` names the target agent image and lists markdown files whose contents are
composed into the skill's `instructions`:

```json
{ "name": "browser-pdf-download", "image_id": "browser_v4",
  "description": "…", "tags": ["pdf", "curl_cffi"], "files": ["browser-pdf-download.md"] }
```

## Available shared skills

| Skill | Image | Purpose |
|---|---|---|
| `browser-pdf-download` (`skv2-16a3b5a1`) | `browser_v4` | Find PDF links on a page and download them with `curl_cffi` (TLS impersonation), verifying PDF content-type + `%PDF` magic, writing files + `pdf_manifest.json`; credential-gateway fallback for JS/login-gated sites. |

## Using a skill in a DAG template

Reference it by **name** (ids are resolved at import time, so templates stay portable across
fresh platforms):

```json
"config": {
  "base_image": "browser_v4",
  "selected_skill_name": "browser-pdf-download",
  "node_objective": "…"
}
```

`examples/_shared/import_dag.py` translates `selected_skill_name` → `selected_skill_v2_id`
via the skill API during `make example-up`.
