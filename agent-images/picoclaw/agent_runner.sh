#!/usr/bin/env bash
# PicoClaw Agent Runner — lightweight shell-based agent runtime
# Communicates with the control plane via curl/HTTP.

set -euo pipefail

TASK_ID="${TASK_ID:?TASK_ID environment variable is required}"
CONTROL_PLANE_URL="${CONTROL_PLANE_URL:-http://control-plane:8000}"
MAX_ITERATIONS="${MAX_ITERATIONS:-50}"

echo "═══════════════════════════════════════════════════════════"
echo "🐚 PICOCLAW AGENT STARTING"
echo "   Task ID: ${TASK_ID}"
echo "   Control Plane: ${CONTROL_PLANE_URL}"
echo "   Workspace: ${OPENCLAW_WORKSPACE:-/workspace}"
echo "═══════════════════════════════════════════════════════════"

iteration=0
while [ "$iteration" -lt "$MAX_ITERATIONS" ]; do
    iteration=$((iteration + 1))
    echo ""
    echo "─── Iteration ${iteration}/${MAX_ITERATIONS} ───────────────────────────────────"

    # Placeholder: fetch next instruction from control plane
    echo "💭 Awaiting instruction from control plane..."

    # Check policy before executing
    source /opt/openclaw/policy_check.sh

    sleep 1
done

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "✓ PicoClaw agent completed after ${iteration} iterations"
echo "═══════════════════════════════════════════════════════════"
