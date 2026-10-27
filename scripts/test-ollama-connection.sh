#!/usr/bin/env bash
# Detect and verify a reachable Ollama endpoint from WSL2 (or Linux).
#
# Ollama on Windows: `host.docker.internal` points at the WSL VM (the Docker
# host), NOT the Windows host. This script tests the Windows LAN IP(s), the WSL
# gateway IP, host.docker.internal, and localhost, then recommends a working
# OLLAMA_URL. It also verifies /api/tags, /api/show capabilities, and the
# System One /v1/systemone decision endpoint.
#
# Usage:
#   ./scripts/test-ollama-connection.sh [--write-env] [--model NAME] [--json]
#
# Examples:
#   make test-ollama
#   make set-ollama-url
#   ./scripts/test-ollama-connection.sh --model kev:0.8b-q8_0 --json

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
MODEL="kev:0.8b-q8_0"
WRITE_ENV=0
JSON=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --write-env) WRITE_ENV=1; shift ;;
    --model)
      MODEL="${2:-}"
      shift 2
      ;;
    --json) JSON=1; shift ;;
    -h|--help)
      sed -n '2,18p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
done

results=()

add_result() { results+=("$1"); }

probe() {
  local url="$1"
  local version_json version tags_json models_count model_present show_json caps sy_json p

  version_json="$(curl -sS --max-time 3 "$url/api/version" 2>/dev/null || true)"
  if [[ -z "$version_json" ]]; then
    add_result "$url|unreachable|0|no||"
    return 1
  fi
  version="$(printf '%s' "$version_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version","?"))' 2>/dev/null || echo "?")"

  tags_json="$(curl -sS --max-time 5 "$url/api/tags" 2>/dev/null || true)"
  models_count="$(printf '%s' "$tags_json" | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("models",[])))' 2>/dev/null || echo 0)"
  if printf '%s' "$tags_json" | MODEL="$MODEL" python3 -c 'import json,os,sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
names = [m.get("name") or m.get("model") for m in d.get("models", [])]
sys.exit(0 if os.environ["MODEL"] in names else 1)' 2>/dev/null; then
    model_present=yes
  else
    model_present=no
  fi

  show_json="$(curl -sS --max-time 5 -X POST "$url/api/show" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"$MODEL\"}" 2>/dev/null || true)"
  caps="$(printf '%s' "$show_json" | python3 -c 'import json,sys; print(",".join(json.load(sys.stdin).get("capabilities",[])))' 2>/dev/null || echo "")"

  p=""
  if [[ "$caps" == *decision* ]]; then
    sy_json="$(curl -sS --max-time 30 -X POST "$url/v1/systemone" \
      -H 'Content-Type: application/json' \
      -d "{\"model\":\"$MODEL\",\"state\":\"hello world\",\"stream\":false,\"questions\":{\"is_greeting\":{\"type\":\"noul\",\"instructions\":\"Does the state text contain a greeting?\"}}}" \
      2>/dev/null || true)"
    p="$(printf '%s' "$sy_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("answers",{}).get("is_greeting",{}).get("noul",""))' 2>/dev/null || echo "")"
  fi

  add_result "$url|ok|$models_count|$model_present|$caps|$p"
  return 0
}

candidates=()
add_candidate() {
  local c="$1"
  [[ -z "$c" ]] && return
  local x
  for x in "${candidates[@]:-}"; do
    [[ "$x" == "$c" ]] && return
  done
  candidates+=("$c")
}

current=""
if [[ -f "$ENV_FILE" ]]; then
  current="$(grep -E '^OLLAMA_URL=' "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d '\r')"
fi

add_candidate "${OLLAMA_URL:-}"
add_candidate "$current"
add_candidate "http://host.docker.internal:11434"

# Windows LAN IPs (stable) — prefer these over the WSL gateway, which changes.
if command -v powershell.exe >/dev/null 2>&1; then
  while IFS= read -r ip; do
    ip="$(printf '%s' "$ip" | tr -d '\r')"
    [[ "$ip" =~ ^(127\.|169\.254\.) ]] && continue
    [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && add_candidate "http://$ip:11434"
  done < <(powershell.exe -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { \$_.IPAddress -notmatch '^127\.' -and \$_.InterfaceAlias -notmatch 'Loopback|WSL' } | Select-Object -ExpandProperty IPAddress)" 2>/dev/null || true)
elif command -v ipconfig.exe >/dev/null 2>&1; then
  while IFS= read -r ip; do
    ip="$(printf '%s' "$ip" | tr -d '\r')"
    [[ "$ip" =~ ^(127\.|169\.254\.) ]] && continue
    [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && add_candidate "http://$ip:11434"
  done < <(ipconfig.exe 2>/dev/null | grep -Eo '([0-9]{1,3}\.){3}[0-9]{1,3}' || true)
fi

# WSL gateway (the Windows host under NAT); may change on WSL restart.
gw="$(ip route 2>/dev/null | awk '/default/ {print $3; exit}')"
[[ -n "$gw" ]] && add_candidate "http://$gw:11434"

add_candidate "http://localhost:11434"

for url in "${candidates[@]}"; do
  [[ -z "$url" ]] && continue
  probe "$url" || true
done

recommended=""
for r in "${results[@]}"; do
  IFS='|' read -r url status count present caps p <<< "$r"
  if [[ "$status" == "ok" && "$present" == "yes" ]]; then
    recommended="$url"
    break
  fi
done
if [[ -z "$recommended" ]]; then
  for r in "${results[@]}"; do
    IFS='|' read -r url status count present caps p <<< "$r"
    if [[ "$status" == "ok" ]]; then
      recommended="$url"
      break
    fi
  done
fi

if [[ "$JSON" -eq 1 ]]; then
  python3 - "$MODEL" "$recommended" "${results[@]}" <<'PY'
import json
import sys

model = sys.argv[1]
recommended = sys.argv[2]
rows = sys.argv[3:]
out = {"model": model, "recommended_url": recommended or None, "results": []}
for row in rows:
    parts = (row.split("|") + [""] * 6)[:6]
    url, status, count, present, caps, p = parts
    out["results"].append({
        "url": url,
        "status": status,
        "models_count": int(count) if count.isdigit() else 0,
        "model_present": present == "yes",
        "capabilities": caps.split(",") if caps else [],
        "systemone_p_true": float(p) if p else None,
    })
print(json.dumps(out, indent=2))
PY
else
  echo "Model: $MODEL"
  echo ""
  printf '%-46s %-12s %-7s %-6s %-20s %s\n' "URL" "STATUS" "MODELS" "MODEL" "CAPABILITIES" "P(TRUE)"
  for r in "${results[@]}"; do
    IFS='|' read -r url status count present caps p <<< "$r"
    printf '%-46s %-12s %-7s %-6s %-20s %s\n' "$url" "$status" "$count" "$present" "$caps" "$p"
  done
  echo ""
  if [[ -n "$recommended" ]]; then
    echo "Recommended: OLLAMA_URL=$recommended"
  else
    echo "No reachable Ollama endpoint found."
  fi
fi

if [[ "$WRITE_ENV" -eq 1 ]]; then
  if [[ -z "$recommended" ]]; then
    echo "Refusing to write .env: no reachable endpoint." >&2
    exit 1
  fi
  touch "$ENV_FILE"
  python3 - "$ENV_FILE" "$recommended" <<'PY'
import os
import sys

path, url = sys.argv[1], sys.argv[2]
lines = open(path).read().splitlines() if os.path.exists(path) else []
found = False
out = []
for line in lines:
    if line.startswith("OLLAMA_URL="):
        out.append(f"OLLAMA_URL={url}")
        found = True
    else:
        out.append(line)
if not found:
    out.append(f"OLLAMA_URL={url}")
open(path, "w").write("\n".join(out) + "\n")
PY
  echo "Wrote OLLAMA_URL=$recommended to $ENV_FILE"

  # The running control-plane keeps the URL in its DB config, which takes
  # precedence over .env. Update it in place when the API is reachable.
  api_code="$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' \
    -X POST http://localhost:8000/api/llm/config \
    -H 'Content-Type: application/json' \
    -d "{\"ollama_url\":\"$recommended\"}" 2>/dev/null || true)"
  if [[ "$api_code" == "200" ]]; then
    echo "Updated the running control-plane config to $recommended."
  else
    echo "Control-plane not reachable (HTTP ${api_code:-n/a}); recreate it to load .env:"
    echo "  docker-compose up -d --no-deps --force-recreate control-plane"
  fi
fi

if [[ -z "$recommended" ]]; then
  cat <<'EOF'

Troubleshooting (WSL2 -> Windows Ollama):
  1. On Windows, ensure Ollama is running and "Expose Ollama to the network" is enabled.
  2. Allow TCP 11434 inbound (admin PowerShell):
       New-NetFirewallRule -DisplayName "Ollama 11434" -Direction Inbound -Protocol TCP -LocalPort 11434 -Action Allow
  3. From WSL, use the Windows LAN IP, not host.docker.internal.
  4. Verify from Windows: http://localhost:11434/api/tags
EOF
  exit 1
fi
