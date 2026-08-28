#!/bin/sh
# cleanup-dag-images.sh
#
# Remove old DAG agent images from the Docker-in-Docker (DinD) daemon.
#
# The DAG workflow tags each task's agent image as:
#     registry:5000/openclaw-agent:dag-dag-<dag-id>-task-<task-id>
# Hundreds of these accumulate in the DinD daemon. This script removes the
# ones older than a retention period (default 5 days), conservatively:
#   - only matches the exact DAG image tag pattern
#     (registry:5000/openclaw-agent:dag-dag-*-task-*),
#   - compares image creation time on the HOST (GNU date), never inside DinD,
#   - never removes an image referenced by an existing (running or stopped)
#     container,
#   - removes by repo:tag (never by image id), so other tags that share the
#     same image id are untouched,
#   - never runs `docker image prune` / `docker system prune`,
#   - does not touch the registry, volumes, <none> images, or other repos.
#
# Usage:
#   scripts/cleanup-dag-images.sh [RETENTION_DAYS] [--dry-run]
#   DAG_RETENTION_DAYS=7 scripts/cleanup-dag-images.sh
#   make docker-clean-dag                      # default 5 days
#   make docker-clean-dag DAG_RETENTION_DAYS=7
#   make docker-clean-dag-dry-run
set -u

DIND_CONTAINER="${DIND_CONTAINER:-openclaw-docker-dind}"
DAG_PATTERN="registry:5000/openclaw-agent:dag-dag-*-task-*"

DRY_RUN=0
RETENTION=""

for a in "$@"; do
  case "$a" in
    --dry-run|-n) DRY_RUN=1 ;;
    *) RETENTION="$a" ;;
  esac
done
[ -n "$RETENTION" ] || RETENTION="${DAG_RETENTION_DAYS:-5}"

case "$RETENTION" in
  ''|*[!0-9]*)
    echo "ERROR: retention '$RETENTION' is not a positive integer" >&2
    exit 2
    ;;
esac
[ "$RETENTION" -ge 1 ] || {
  echo "ERROR: retention must be >= 1 day" >&2
  exit 2
}

DIND() { docker exec "$DIND_CONTAINER" "$@"; }

if ! DIND docker version >/dev/null 2>&1; then
  echo "ERROR: cannot reach DinD container '$DIND_CONTAINER'. Is the stack up? (make up)" >&2
  exit 1
fi

# ── Time (computed on the HOST with GNU date) ───────────────────────────────
NOW_EPOCH=$(date +%s)
CUTOFF_EPOCH=$(( NOW_EPOCH - RETENTION * 86400 ))
CUTOFF_ISO=$(date -u -d "@$CUTOFF_EPOCH" +%Y-%m-%dT%H:%M:%SZ)

echo "DAG image cleanup"
echo "Retention: $RETENTION days"
echo "Cutoff: $CUTOFF_ISO"
echo ""

humansize() {
  printf '%s\n' "$1" | awk '{
    if ($1 >= 1000000000) printf "%.2fGB", $1/1000000000;
    else if ($1 >= 1000000) printf "%.2fMB", $1/1000000;
    else if ($1 >= 1000) printf "%.2fKB", $1/1000;
    else printf "%dB", $1;
  }'
}

# Image ids referenced by existing containers (running or stopped), normalized.
INUSE=$(mktemp) || exit 1
DIND docker ps -aq 2>/dev/null | while read -r cid; do
  [ -z "$cid" ] && continue
  DIND docker inspect --format '{{.Image}}' "$cid" 2>/dev/null
done | sed 's/^sha256://' | sort -u > "$INUSE"

# Enumerate all image tags inside DinD.
TAGS=$(mktemp) || exit 1
DIND docker image ls --format '{{.Repository}}:{{.Tag}}' 2>/dev/null > "$TAGS"

TOTAL=0
EST=0
SKIPPED=0
REMOVED=0

echo "Candidates:"
echo ""

while read -r repotag; do
  [ -n "$repotag" ] || continue
  case "$repotag" in
    $DAG_PATTERN)
      # Timestamps come from `inspect` (RFC3339 with nanoseconds) because GNU
      # date on the host rejects the space-separated `image ls` format.
      meta=$(DIND docker image inspect --format '{{.Id}}\t{{.Created}}\t{{.Size}}' "$repotag" 2>/dev/null) || continue
      full_id=$(printf '%s\n' "$meta" | cut -f1 | sed 's/^sha256://')
      created=$(printf '%s\n' "$meta" | cut -f2)
      size=$(printf '%s\n' "$meta" | cut -f3)
      [ -n "$full_id" ] && [ -n "$created" ] || continue

      created_epoch=$(date -u -d "$created" +%s 2>/dev/null) || continue
      if [ "$created_epoch" -ge "$CUTOFF_EPOCH" ]; then
        continue
      fi

      case "$size" in
        ''|*[!0-9]*) size=0 ;;
      esac
      short_id=$(printf '%s\n' "$full_id" | cut -c1-12)

      # Never remove an image referenced by an existing container.
      if grep -q "^$full_id" "$INUSE" 2>/dev/null || grep -q "^$short_id" "$INUSE" 2>/dev/null; then
        echo "SKIP (in use): $repotag  $short_id  $(humansize "$size")"
        SKIPPED=$((SKIPPED + 1))
        continue
      fi

      EST=$((EST + size))
      TOTAL=$((TOTAL + 1))
      day=$(date -u -d "$created" +%Y-%m-%d)
      echo "$day  $repotag  $short_id  $(humansize "$size")"

      if [ "$DRY_RUN" -eq 0 ]; then
        if DIND docker rmi "$repotag" >/dev/null 2>&1; then
          echo "REMOVE: $repotag  $short_id"
          REMOVED=$((REMOVED + 1))
        else
          echo "SKIP (remove failed): $repotag  $short_id"
          SKIPPED=$((SKIPPED + 1))
        fi
      fi
      ;;
  esac
done < "$TAGS"

EST_HUMAN=$(printf '%d\n' "$EST" | awk '{ printf "%.2fGB", $1/1000000000 }')

echo ""
echo "Total candidates: $TOTAL"
echo "Estimated image size: $EST_HUMAN"
if [ "$DRY_RUN" -eq 1 ]; then
  echo "Dry run — no images were removed."
else
  echo "Removed: $REMOVED"
  echo "Skipped: $SKIPPED"
fi

rm -f "$INUSE" "$TAGS"
exit 0
