#!/usr/bin/env bash
# Tests for cleanup-dag-images.sh using a mock `docker` in PATH.
# Run: bash scripts/test-cleanup-dag-images.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/cleanup-dag-images.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

RECENT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat > "$TMP/meta" <<EOF
registry:5000/openclaw-agent:dag-dag-7748-task-77f	sha256:0282e1355817aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa	2026-08-20T21:11:54.283374841Z	4190000000
registry:5000/openclaw-agent:dag-dag-264d-task-008	sha256:037de0c93357bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb	2026-08-20T21:11:54.283374841Z	4190000000
registry:5000/openclaw-agent:dag-dag-93f8-task-dac	sha256:0a1b2c3d4e5fcccccccccccccccccccccccccccccccccccccccccccccccc	${RECENT}	3100000000
registry:5000/openclaw-agent:dag-dag-inuse-task-111	sha256:333333333333dddddddddddddddddddddddddddddddddddddddddddd	2026-08-15T00:00:00Z	2500000000
registry:5000/openclaw-agent:openclaw	sha256:999999999999eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee	2026-08-10T00:00:00Z	1200000000
openclaw-agent:task-abc-v1	sha256:111111111111ffffffffffffffffffffffffffffffffffffffffffffffff	2026-08-01T00:00:00Z	500000000
<none>:<none>	sha256:222222222222222222222222222222222222222222222222222222222222	2026-08-01T00:00:00Z	900000000
registry:5000/openclaw-agent:shared-marker	sha256:0282e1355817aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa	2026-08-20T21:11:54.283374841Z	4190000000
EOF

cat > "$TMP/tags" <<EOF
registry:5000/openclaw-agent:dag-dag-7748-task-77f
registry:5000/openclaw-agent:dag-dag-264d-task-008
registry:5000/openclaw-agent:dag-dag-93f8-task-dac
registry:5000/openclaw-agent:dag-dag-inuse-task-111
registry:5000/openclaw-agent:openclaw
openclaw-agent:task-abc-v1
<none>:<none>
registry:5000/openclaw-agent:shared-marker
EOF
printf 'c1\n' > "$TMP/containers"
printf 'c1\tsha256:333333333333dddddddddddddddddddddddddddddddddddddddddddd\n' > "$TMP/container_images"
: > "$TMP/rmi_log"

cat > "$TMP/docker" <<'EOF'
#!/usr/bin/env bash
cmd="$1"; shift
[ "$cmd" = "exec" ] || { echo "mock: expected exec, got $cmd" >&2; exit 1; }
c="$1"; shift
[ "$c" = "openclaw-docker-dind" ] || { echo "mock: bad container $c" >&2; exit 1; }
[ "$1" = "docker" ] || { echo "mock: expected docker" >&2; exit 1; }
shift
sub="$1"; shift
# Docker renders \t in --format output as a real tab; emulate that here so the
# fixtures can keep literal \t escapes.
out() { sed 's/\\t/\t/g'; }
case "$sub" in
  version) exit 0 ;;
  image)
    if [ "$1" = "ls" ]; then cat "$MOCK_TAGS"; exit 0; fi
    if [ "$1" = "inspect" ]; then
      # META lines are: <repotag>\t<inspect-output...>; emulate the 3-field
      # inspect format (Id, Created, Size) by emitting fields 2-4.
      grep -F "$4" "$MOCK_META" | cut -f2- | out || exit 1
      exit 0
    fi
    ;;
  ps) cat "$MOCK_CONTAINERS"; exit 0 ;;
  inspect)
    if [ "$2" = "{{.Image}}" ]; then
      grep -F "$3" "$MOCK_CONTAINER_IMAGES" | out | cut -f2 || exit 1
      exit 0
    fi
    ;;
  rmi) printf '%s\n' "$*" >> "$MOCK_RMI_LOG"; exit 0 ;;
  *) echo "mock: unhandled $sub $*" >&2; exit 1 ;;
esac
EOF
chmod +x "$TMP/docker"

MOCKBIN="$TMP/bin"
mkdir -p "$MOCKBIN"
cat > "$MOCKBIN/docker" <<EOF
#!/usr/bin/env bash
export MOCK_TAGS="$TMP/tags" MOCK_META="$TMP/meta" MOCK_CONTAINERS="$TMP/containers" \
  MOCK_CONTAINER_IMAGES="$TMP/container_images" MOCK_RMI_LOG="$TMP/rmi_log"
exec "$TMP/docker" "\$@"
EOF
chmod +x "$MOCKBIN/docker"

fail() { echo "FAIL: $1" >&2; exit 1; }
have() { grep -q "$2" "$1" || fail "expected '$2' in $1"; }
not_have() { ! grep -q "$2" "$1" || fail "unexpected '$2' in $1"; }

# ── 1. Dry run: no deletion, old DAG candidates listed ─────────────────────
: > "$TMP/rmi_log"
PATH="$MOCKBIN:$PATH" "$SCRIPT" 5 --dry-run > "$TMP/out1" 2>&1
[ ! -s "$TMP/rmi_log" ] || fail "dry-run must not remove anything"
have "$TMP/out1" "dag-dag-7748-task-77f"
have "$TMP/out1" "dag-dag-264d-task-008"
not_have "$TMP/out1" "dag-dag-93f8-task-dac"
not_have "$TMP/out1" "openclaw-agent:openclaw"
not_have "$TMP/out1" "<none>"
have "$TMP/out1" "SKIP (in use): registry:5000/openclaw-agent:dag-dag-inuse-task-111"
have "$TMP/out1" "Total candidates: 2"

# ── 2. Real run: only the two old, unused DAG tags are removed ──────────────
: > "$TMP/rmi_log"
PATH="$MOCKBIN:$PATH" "$SCRIPT" 5 > "$TMP/out2" 2>&1
have "$TMP/rmi_log" "dag-dag-7748-task-77f"
have "$TMP/rmi_log" "dag-dag-264d-task-008"
not_have "$TMP/rmi_log" "dag-dag-93f8-task-dac"
not_have "$TMP/rmi_log" "openclaw-agent:openclaw"
not_have "$TMP/rmi_log" "<none>"
not_have "$TMP/rmi_log" "dag-dag-inuse-task-111"   # in use -> skipped
not_have "$TMP/rmi_log" "shared-marker"            # same image id, other tag kept
have "$TMP/out2" "SKIP (in use): registry:5000/openclaw-agent:dag-dag-inuse-task-111"
have "$TMP/out2" "Removed: 2"

# ── 3. Retention arg: 1000 days keeps everything ────────────────────────────
: > "$TMP/rmi_log"
PATH="$MOCKBIN:$PATH" "$SCRIPT" 1000 > "$TMP/out3" 2>&1
[ ! -s "$TMP/rmi_log" ] || fail "retention=1000 must not remove anything"
have "$TMP/out3" "Total candidates: 0"

# ── 4. DAG_RETENTION_DAYS env var ───────────────────────────────────────────
: > "$TMP/rmi_log"
DAG_RETENTION_DAYS=1000 PATH="$MOCKBIN:$PATH" "$SCRIPT" > "$TMP/out4" 2>&1
[ ! -s "$TMP/rmi_log" ] || fail "DAG_RETENTION_DAYS=1000 must not remove anything"

echo "ALL TESTS PASSED"
