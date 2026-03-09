#!/usr/bin/env bash
# PicoClaw Policy Check — calls the policy engine via curl

POLICY_URL="${OPENCLAW_POLICY_ENFORCER:-http://policy-engine:8001}"
TASK_ID="${TASK_ID:?TASK_ID is required}"

policy_check() {
    local action="$1"
    local resource="$2"

    local response
    response=$(curl -sf -X POST "${POLICY_URL}/evaluate" \
        -H "Content-Type: application/json" \
        -d "{\"task_id\": \"${TASK_ID}\", \"action\": \"${action}\", \"resource\": \"${resource}\"}" \
        2>/dev/null) || {
        echo "⚠ Policy check failed (network error)"
        return 1
    }

    local allowed
    allowed=$(echo "$response" | jq -r '.allowed // false')

    if [ "$allowed" = "true" ]; then
        echo "✓ Policy: ${action} on ${resource} — allowed"
        return 0
    else
        echo "✗ Policy: ${action} on ${resource} — denied"
        return 1
    fi
}
