#!/usr/bin/env bash
# example.sh — manage OpenClaw usage examples.
# Usage: examples/_shared/example.sh <up|down|status|list> [NAME]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXAMPLES="$ROOT/examples"
CONTROL_PLANE="${CONTROL_PLANE:-http://localhost:8000}"

usage() {
  echo "Usage: example.sh <up|down|status|list> [NAME]"
  exit 1
}

list_examples() {
  for d in "$EXAMPLES"/*/; do
    [ -f "$d/example.yaml" ] || continue
    name=$(basename "$d")
    desc=$(grep -m1 '^description:' "$d/example.yaml" | sed 's/^description: *//' || echo "")
    echo "  $name  —  $desc"
  done
}

apply_config() {
  local dir="$1" name="$2"
  # Create the project namespace (idempotent).
  curl -sf -X POST "$CONTROL_PLANE/api/projects" \
    -H 'Content-Type: application/json' \
    -d "{\"id\":\"$name\",\"name\":\"$name\"}" >/dev/null 2>&1 || true
  # Ensure example agent-image profiles are registered (idempotent; e.g. image "banking").
  curl -sf -X POST "$CONTROL_PLANE/api/agent-images/sync-from-yaml" \
    -H 'Content-Type: application/json' -d '{}' >/dev/null 2>&1 || true
  # Import shared v2 skills (ACTIVE) that any example may bind, then the example's own.
  if ls "$ROOT"/examples/_shared/skills/*.skill.json >/dev/null 2>&1; then
    python3 "$ROOT/examples/_shared/import_skills.py" "$ROOT/examples/_shared/skills"
  fi
  # Import example v2 skills (ACTIVE) before DAGs — templates reference them by name.
  if ls "$dir"/config/skills/*.skill.json >/dev/null 2>&1; then
    python3 "$ROOT/examples/_shared/import_skills.py" "$dir/config/skills"
  fi
  # Import DAG templates (dag_json files → manual DAG + lock).
  for f in "$dir"/config/dags/*.json; do
    [ -f "$f" ] || continue
    python3 "$ROOT/examples/_shared/import_dag.py" "$f" "$name"
  done
  echo "  config applied for '$name'"
}

cmd="${1:-}"; shift || true
case "$cmd" in
  list)
    list_examples
    ;;
  up)
    name="${1:?NAME required}"
    dir="$EXAMPLES/$name"
    [ -f "$dir/example.yaml" ] || { echo "no example '$name'"; exit 1; }
    echo "==> Starting infra for $name"
    docker-compose -f "$dir/docker-compose.yml" up -d
    # Build any example-specific agent images declared in example.yaml: build: [banking]
    builds=$(sed -n 's/^build: *\[\(.*\)\].*/\1/p' "$dir/example.yaml" | tr ',' ' ')
    for b in $builds; do
      b=$(echo "$b" | tr -d ' ')
      [ -z "$b" ] && continue
      if docker exec openclaw-docker-dind docker image inspect "registry:5000/openclaw-agent:$b" >/dev/null 2>&1; then
        echo "==> agent image openclaw-agent:$b already present"
      else
        echo "==> building agent image openclaw-agent:$b ..."
        make -C "$ROOT" "build-$b"
      fi
    done
    echo "==> Applying config to platform"
    apply_config "$dir" "$name"
    echo "==> Done. Open the frontend and instantiate a '$name' template."
    ;;
  down)
    name="${1:?NAME required}"
    dir="$EXAMPLES/$name"
    docker-compose -f "$dir/docker-compose.yml" down
    ;;
  status)
    name="${1:?NAME required}"
    dir="$EXAMPLES/$name"
    docker-compose -f "$dir/docker-compose.yml" ps
    ;;
  *) usage ;;
esac
