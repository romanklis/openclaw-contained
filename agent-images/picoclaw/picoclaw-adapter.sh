#!/usr/bin/env bash
# ============================================================================
# PicoClaw Adapter — Pure shell agent adapter for TaskForge
# ============================================================================
#
# Speaks the exact same protocol as openclaw-wrapper.py:
#   - Reads TASK_ID, LLM_MODEL, LLM_ROUTER_URL, CONTROL_PLANE_URL, etc.
#   - Calls the LLM router at /v1/chat/completions with tool_calls
#   - Executes tool calls (write/read/exec/edit) locally
#   - Detects CAPABILITY_REQUEST / DEPLOYMENT_REQUEST markers
#   - Writes result JSON with ===OPENCLAW_RESULT_JSON_START=== markers
#   - Collects workspace deliverables
#
# No Python required — only bash, curl, jq, and POSIX tools.
# ============================================================================

set -uo pipefail

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------
TASK_ID="${TASK_ID:?TASK_ID environment variable is required}"
ITERATION="${ITERATION:-0}"
LLM_MODEL="${LLM_MODEL:-gemma3:4b}"
CONTROL_PLANE_URL="${CONTROL_PLANE_URL:-http://control-plane:8000}"
LLM_ROUTER_URL="${LLM_ROUTER_URL:-${CONTROL_PLANE_URL}/api/llm}"
MAX_TURNS="${MAX_AGENT_TURNS:-30}"
TOOL_TIMEOUT="${TOOL_TIMEOUT:-60}"
IMAGE_TYPE="${OPENCLAW_IMAGE_TYPE:-picoclaw}"
TASK_DESCRIPTION="${TASK_DESCRIPTION:-}"
FOLLOW_UP="${FOLLOW_UP:-}"
AGENT_IMAGE="${AGENT_IMAGE:-picoclaw}"
AGENT_DOCKERFILE="${AGENT_DOCKERFILE:-}"

RESULT_START="===OPENCLAW_RESULT_JSON_START==="
RESULT_END="===OPENCLAW_RESULT_JSON_END==="

WORKSPACE="/workspace"
ALL_OUTPUT=""

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

log() { echo "   $*"; }
log_err() { echo "   ❌ $*" >&2; }

# Append to the combined output buffer
append_output() {
    ALL_OUTPUT="${ALL_OUTPUT}${1}
"
}

# JSON-escape a string for safe embedding
json_escape() {
    printf '%s' "$1" | jq -Rs '.'
}

# Truncate a string to N bytes
truncate_str() {
    local str="$1" max="${2:-50000}"
    if [ ${#str} -gt "$max" ]; then
        echo "${str:0:$max}... (truncated)"
    else
        echo "$str"
    fi
}

# ---------------------------------------------------------------------------
# Fetch task from control plane
# ---------------------------------------------------------------------------
fetch_task() {
    local resp
    resp=$(curl -sf --max-time 30 "${CONTROL_PLANE_URL}/api/tasks/${TASK_ID}" 2>/dev/null) || {
        log_err "Failed to fetch task from control plane"
        return 1
    }
    echo "$resp"
}

# ---------------------------------------------------------------------------
# Request capability from control plane
# ---------------------------------------------------------------------------
request_capability() {
    local cap_type="$1" packages="$2" reason="$3"

    # Map capability types
    local api_type="tool_install"
    case "$cap_type" in
        network_access)   api_type="network_access" ;;
        filesystem_access) api_type="filesystem_access" ;;
        database_access)  api_type="database_access" ;;
    esac

    local payload
    payload=$(jq -n \
        --arg task_id "$TASK_ID" \
        --arg cap_type "$api_type" \
        --arg resource "$packages" \
        --arg justification "[Iteration ${ITERATION}] ${reason}" \
        --arg reason "$reason" \
        --arg iteration "$ITERATION" \
        '{
            task_id: $task_id,
            capability_type: $cap_type,
            resource_name: $resource,
            justification: $justification,
            details: {
                original_type: $cap_type,
                iteration: $iteration,
                reason: $reason
            }
        }')

    local resp
    resp=$(curl -sf --max-time 30 \
        -X POST \
        -H "Content-Type: application/json" \
        -d "$payload" \
        "${CONTROL_PLANE_URL}/api/capabilities/requests" 2>/dev/null) || {
        log_err "Capability request failed"
        return 1
    }

    # Check if approved or at least has an id
    local approved id_val
    approved=$(echo "$resp" | jq -r '.approved // empty' 2>/dev/null)
    id_val=$(echo "$resp" | jq -r '.id // empty' 2>/dev/null)

    if [ "$approved" = "true" ] || [ -n "$id_val" ]; then
        return 0
    fi
    return 1
}

# ---------------------------------------------------------------------------
# Setup workspace context files
# ---------------------------------------------------------------------------
setup_workspace_context() {
    mkdir -p "$WORKSPACE"

    local dockerfile_section=""
    if [ -n "$AGENT_DOCKERFILE" ]; then
        dockerfile_section="
### Container Dockerfile (your current image)

The following Dockerfile was used to build the image you are running in.
All packages listed here are ALREADY INSTALLED — do NOT request them again.

\`\`\`dockerfile
${AGENT_DOCKERFILE}
\`\`\`
"
    fi

    cat > "${WORKSPACE}/AGENTS.md" << AGENTSEOF
# AGENTS.md — Managed Execution Environment

You are running inside a managed container. Your workspace is \`/workspace\`.

## YOUR WORKFLOW (follow this order)

1. **Write** the code/files the task requires into \`/workspace\`.
2. **Execute** the code to verify it works.
   - Use the \`exec\` tool: exec sh -c "your command here"
3. **If execution fails** with a missing tool/package, ONLY THEN request it (see below).
4. **If execution succeeds**, you are DONE. Do not output anything else.

**You MUST execute your code before finishing.** Writing a file alone is NOT enough.

## Package Installation

You cannot install packages yourself (\`apk add\`, \`pip install\`, etc. will fail).

### Pre-installed tools

Already available — do NOT request these:
- \`bash\`, \`sh\`, \`coreutils\`
- \`curl\`, \`wget\`, \`git\`
- \`jq\`, \`yq\`, \`gawk\`, \`sed\`, \`grep\`, \`find\`
- \`openssh-client\`

${dockerfile_section}

### How to request a missing tool

**ONLY** if a command is not found:

\`\`\`
CAPABILITY_REQUEST:tool_install:<tool_name>:<detailed reason why this tool is needed>
\`\`\`

After this line, STOP. The system will rebuild your container with the tool
and re-run your task automatically.

## Deployment Request

If the task asks you to create a web service:

\`\`\`
DEPLOYMENT_REQUEST:<app-name>:<port>:<entrypoint command>
\`\`\`

## Task Info

- Iteration: ${ITERATION}
- Model: ${LLM_MODEL}
- Image: ${AGENT_IMAGE}
- Runtime: picoclaw (shell-only, no Python)
- Workspace: \`/workspace\` (files here are collected as deliverables)
AGENTSEOF

    cat > "${WORKSPACE}/SOUL.md" << 'SOULEOF'
# SOUL.md — Task Agent

You are a task execution agent. Your job is to complete the assigned task
efficiently and correctly.

## Principles
- Focus on the task. Don't add unnecessary features.
- Write clean, working code / scripts.
- If you need a tool that's not installed, request it (see AGENTS.md).
- Test your output if possible before finishing.
- Write all files to `/workspace`.
SOULEOF
}

# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------
execute_tool() {
    local name="$1" args_json="$2"
    local result=""

    case "$name" in
        write)
            local path content dir
            path=$(echo "$args_json" | jq -r '.path')
            content=$(echo "$args_json" | jq -r '.content')
            dir=$(dirname "$path")
            mkdir -p "$dir" 2>/dev/null || true
            printf '%s' "$content" > "$path" 2>/dev/null && \
                result="✅ Written ${#content} bytes to ${path}" || \
                result="ERROR: Could not write to ${path}"
            ;;
        read)
            local path
            path=$(echo "$args_json" | jq -r '.path')
            if [ ! -f "$path" ]; then
                result="ERROR: File not found: ${path}"
            else
                result=$(cat "$path" 2>/dev/null | head -c 50000)
            fi
            ;;
        exec)
            local command
            command=$(echo "$args_json" | jq -r '.command')
            # Run in a new session so we can kill the entire process tree
            # (prevents orphaned grandchildren like nc -lk from hanging)
            local tmpout="/tmp/exec_out.$$"
            local pidfile="/tmp/exec_pid.$$"
            setsid sh -c '
                echo $$ > '"$pidfile"'
                exec sh -c '"'"'$command'"'"'
            ' >"$tmpout" 2>&1 &
            local wrapper_pid=$!
            # Wait up to TOOL_TIMEOUT for the command
            local elapsed=0
            while kill -0 "$wrapper_pid" 2>/dev/null; do
                if [ "$elapsed" -ge "${TOOL_TIMEOUT}" ]; then
                    # Kill the entire process group
                    local sess_pid
                    sess_pid=$(cat "$pidfile" 2>/dev/null || echo "$wrapper_pid")
                    kill -KILL -"$sess_pid" 2>/dev/null || true
                    kill -KILL "$wrapper_pid" 2>/dev/null || true
                    wait "$wrapper_pid" 2>/dev/null || true
                    result=$(head -c 50000 "$tmpout" 2>/dev/null)
                    result="${result}
ERROR: Command timed out after ${TOOL_TIMEOUT}s (process tree killed)"
                    rm -f "$tmpout" "$pidfile"
                    break
                fi
                sleep 1
                elapsed=$((elapsed + 1))
            done
            if [ "$elapsed" -lt "${TOOL_TIMEOUT}" ]; then
                wait "$wrapper_pid" 2>/dev/null
                local rc=$?
                # Kill any lingering children in the session
                local sess_pid
                sess_pid=$(cat "$pidfile" 2>/dev/null || echo "")
                [ -n "$sess_pid" ] && kill -KILL -"$sess_pid" 2>/dev/null || true
                result=$(head -c 50000 "$tmpout" 2>/dev/null)
                if [ $rc -ne 0 ] && [ $rc -ne 137 ]; then
                    result="${result}
(exit code ${rc})"
                fi
                rm -f "$tmpout" "$pidfile"
            fi
            [ -z "$result" ] && result="(no output)"
            ;;
        edit)
            local path old_str new_str
            path=$(echo "$args_json" | jq -r '.path')
            old_str=$(echo "$args_json" | jq -r '.old_string')
            new_str=$(echo "$args_json" | jq -r '.new_string')
            if [ ! -f "$path" ]; then
                result="ERROR: File not found: ${path}"
            elif ! grep -qF "$old_str" "$path" 2>/dev/null; then
                result="ERROR: old_string not found in ${path}"
            else
                # Use awk for reliable string replacement
                local tmp_file="${path}.tmp.$$"
                awk -v old="$old_str" -v new="$new_str" '
                    BEGIN { found=0 }
                    {
                        if (!found) {
                            idx = index($0, old)
                            if (idx > 0) {
                                found = 1
                                # Handle multi-line: read whole file approach is better
                            }
                        }
                        print
                    }
                ' "$path" > "$tmp_file" 2>/dev/null

                # Fall back to python-less sed for single-line replacements
                # For simplicity, use a temp file approach with shell builtins
                local file_content
                file_content=$(cat "$path")
                # Replace first occurrence using bash parameter expansion
                local before="${file_content%%"$old_str"*}"
                local after="${file_content#*"$old_str"}"
                printf '%s%s%s' "$before" "$new_str" "$after" > "$path" 2>/dev/null && \
                    result="✅ Edited ${path}" || \
                    result="ERROR: Failed to edit ${path}"
                rm -f "$tmp_file" 2>/dev/null
            fi
            ;;
        *)
            result="ERROR: Unknown tool '${name}'"
            ;;
    esac

    echo "$result"
}

# ---------------------------------------------------------------------------
# Build the system prompt
# ---------------------------------------------------------------------------
build_system_prompt() {
    local parts=""
    parts="You are a task execution agent running inside a managed container.
Your workspace is /workspace. All files you create there are collected as deliverables.

You have these tools: write, read, exec, edit.
- write: Create/overwrite a file
- read: Read a file's content
- exec: Run a shell command (use to test your code!)
- edit: Replace a string in a file

IMPORTANT RULES:
1. Always write code to /workspace
2. Always exec your code to verify it works
3. If a tool is missing, emit: CAPABILITY_REQUEST:tool_install:<tool>:<reason>
4. For web apps, emit: DEPLOYMENT_REQUEST:<name>:<port>:<entrypoint>
5. Do NOT try apk add or pip install — they will fail
6. This is a shell-only environment (no Python) — write shell scripts or use available tools"

    # Append context files if they exist
    for ctx_file in AGENTS.md SOUL.md; do
        if [ -f "${WORKSPACE}/${ctx_file}" ]; then
            parts="${parts}

--- ${ctx_file} ---
$(cat "${WORKSPACE}/${ctx_file}")"
        fi
    done

    echo "$parts"
}

# ---------------------------------------------------------------------------
# Call LLM router
# ---------------------------------------------------------------------------
call_llm() {
    local messages_json="$1"
    local router_url="${LLM_ROUTER_URL%/}"

    # Ensure URL ends with /v1
    case "$router_url" in
        */v1) ;;
        *) router_url="${router_url}/v1" ;;
    esac

    local api_key="task:${TASK_ID}"
    local tools_json
    tools_json='[
        {"type":"function","function":{"name":"write","description":"Write content to a file. Creates parent directories.","parameters":{"type":"object","properties":{"path":{"type":"string","description":"Absolute file path"},"content":{"type":"string","description":"File content"}},"required":["path","content"]}}},
        {"type":"function","function":{"name":"read","description":"Read a file.","parameters":{"type":"object","properties":{"path":{"type":"string","description":"Absolute file path"}},"required":["path"]}}},
        {"type":"function","function":{"name":"exec","description":"Execute a shell command. Returns stdout+stderr.","parameters":{"type":"object","properties":{"command":{"type":"string","description":"Shell command"}},"required":["command"]}}},
        {"type":"function","function":{"name":"edit","description":"Replace a string in a file.","parameters":{"type":"object","properties":{"path":{"type":"string","description":"File path"},"old_string":{"type":"string","description":"String to find"},"new_string":{"type":"string","description":"Replacement"}},"required":["path","old_string","new_string"]}}}
    ]'

    local payload
    payload=$(jq -n \
        --arg model "$LLM_MODEL" \
        --argjson messages "$messages_json" \
        --argjson tools "$tools_json" \
        '{
            model: $model,
            messages: $messages,
            tools: $tools,
            tool_choice: "auto",
            temperature: 0.2,
            max_tokens: 4096
        }')

    curl -sf --max-time 120 \
        -X POST \
        -H "Authorization: Bearer ${api_key}" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        "${router_url}/chat/completions" 2>/dev/null
}

# ---------------------------------------------------------------------------
# Parse tool_calls from LLM response
# ---------------------------------------------------------------------------

# Returns the number of tool calls
get_tool_call_count() {
    local resp="$1"
    echo "$resp" | jq -r '.choices[0].message.tool_calls // [] | length'
}

get_tool_call_id() {
    local resp="$1" idx="$2"
    echo "$resp" | jq -r ".choices[0].message.tool_calls[$idx].id // \"call_${idx}\""
}

get_tool_call_name() {
    local resp="$1" idx="$2"
    echo "$resp" | jq -r ".choices[0].message.tool_calls[$idx].function.name"
}

get_tool_call_args() {
    local resp="$1" idx="$2"
    echo "$resp" | jq -r ".choices[0].message.tool_calls[$idx].function.arguments"
}

get_content() {
    local resp="$1"
    echo "$resp" | jq -r '.choices[0].message.content // ""'
}

get_finish_reason() {
    local resp="$1"
    echo "$resp" | jq -r '.choices[0].finish_reason // ""'
}

# Extract the assistant message (for appending to conversation)
get_assistant_message() {
    local resp="$1"
    echo "$resp" | jq '.choices[0].message'
}

# ---------------------------------------------------------------------------
# Collect workspace deliverables
# ---------------------------------------------------------------------------
collect_deliverables() {
    local result="{}"
    local total_size=0
    local max_total=2000000

    if [ ! -d "$WORKSPACE" ]; then
        echo "{}"
        return
    fi

    local skip_dirs=".git|node_modules|__pycache__|.cache|.npm|.openclaw"
    local skip_files="result.json|AGENTS.md|SOUL.md|TOOLS.md|IDENTITY.md|USER.md|HEARTBEAT.md|BOOTSTRAP.md|package-lock.json"

    while IFS= read -r -d '' fpath; do
        local relpath="${fpath#${WORKSPACE}/}"
        local fname=$(basename "$fpath")

        # Skip known files
        if echo "$fname" | grep -qE "^(${skip_files})$"; then
            continue
        fi

        local size
        size=$(stat -c%s "$fpath" 2>/dev/null || echo 0)

        # Skip empty or too large
        [ "$size" -eq 0 ] && continue
        [ "$size" -gt 500000 ] && continue

        total_size=$((total_size + size))
        [ "$total_size" -gt "$max_total" ] && break

        local content
        # Check if binary (contains null bytes)
        if head -c 8192 "$fpath" | grep -qP '\x00' 2>/dev/null; then
            content="base64:$(base64 -w0 "$fpath" 2>/dev/null)"
        else
            content=$(cat "$fpath" 2>/dev/null | head -c 500000)
        fi

        # Add to result object
        local escaped_content
        escaped_content=$(json_escape "$content")
        local escaped_path
        escaped_path=$(json_escape "$relpath")
        result=$(echo "$result" | jq --argjson key "$escaped_path" --argjson val "$escaped_content" '. + {($key): $val}')

    done < <(find "$WORKSPACE" -type f \
        -not -path "*/.git/*" \
        -not -path "*/node_modules/*" \
        -not -path "*/__pycache__/*" \
        -not -path "*/.cache/*" \
        -not -path "*/.npm/*" \
        -not -path "*/.openclaw/*" \
        -print0 2>/dev/null | sort -z)

    echo "$result"
}

# ---------------------------------------------------------------------------
# Write result JSON
# ---------------------------------------------------------------------------
write_result() {
    local result_json="$1"

    echo "$result_json" > "${WORKSPACE}/result.json" 2>/dev/null || true
    echo "$result_json" > "/tmp/result.json" 2>/dev/null || true

    echo ""
    echo "$RESULT_START"
    echo "$result_json"
    echo "$RESULT_END"
}

# ---------------------------------------------------------------------------
# Parse capability request from output
# ---------------------------------------------------------------------------
parse_capability_request() {
    local output="$1"

    # Check for CAPABILITY_REQUEST markers
    local match
    match=$(echo "$output" | grep -oP 'CAPABILITY_REQUEST:\w+:[^:\n]+:[^\n]+' | head -1)

    if [ -n "$match" ]; then
        local cap_type packages reason
        cap_type=$(echo "$match" | cut -d: -f2)
        packages=$(echo "$match" | cut -d: -f3)
        reason=$(echo "$match" | cut -d: -f4-)
        echo "${cap_type}|${packages}|${reason}"
        return 0
    fi

    # Fallback: ModuleNotFoundError
    local module
    module=$(echo "$output" | grep -oP "(?:ModuleNotFoundError|ImportError):.*?[Nn]o module named ['\"]?\K[a-zA-Z0-9_]+" | head -1)
    if [ -n "$module" ]; then
        echo "python_packages|${module}|ModuleNotFoundError detected"
        return 0
    fi

    return 1
}

# ---------------------------------------------------------------------------
# Parse deployment request from output
# ---------------------------------------------------------------------------
parse_deployment_request() {
    local output="$1"
    local match
    match=$(echo "$output" | grep -oP 'DEPLOYMENT_REQUEST:[^:]+:\d+:.+' | head -1)

    if [ -n "$match" ]; then
        local name port entrypoint
        name=$(echo "$match" | cut -d: -f2)
        port=$(echo "$match" | cut -d: -f3)
        entrypoint=$(echo "$match" | cut -d: -f4-)
        echo "${name}|${port}|${entrypoint}"
        return 0
    fi
    return 1
}

# ===========================================================================
# Main
# ===========================================================================
main() {
    echo "════════════════════════════════════════════════════════════════════"
    echo "🐚 PICOCLAW AGENT ADAPTER  (TaskForge-native, pure shell)"
    echo "════════════════════════════════════════════════════════════════════"
    echo "📋 Task ID:       ${TASK_ID}"
    echo "🔄 Iteration:     ${ITERATION}"
    echo "🤖 Model:         ${LLM_MODEL}"
    echo "🌐 Control Plane: ${CONTROL_PLANE_URL}"
    echo "🔀 LLM Router:    ${LLM_ROUTER_URL}"
    echo "📦 Image Type:    picoclaw"
    echo "════════════════════════════════════════════════════════════════════"

    # Fetch task
    echo ""
    echo "📥 Fetching task from control plane..."
    local task_json prompt=""

    task_json=$(fetch_task) && {
        prompt=$(echo "$task_json" | jq -r '.description // .prompt // ""')
        if [ -n "$prompt" ]; then
            log "✅ Task fetched: ${prompt:0:150}..."
        fi
    }

    if [ -z "$prompt" ] && [ -n "$TASK_DESCRIPTION" ]; then
        prompt="$TASK_DESCRIPTION"
        log "📝 Using TASK_DESCRIPTION env var: ${prompt:0:150}..."
    fi

    if [ -z "$prompt" ]; then
        log_err "No task description available"
        write_result '{"completed": false, "error": "No description", "capability_requested": false}'
        exit 1
    fi

    # Handle continuation / follow-up
    if [ -n "$FOLLOW_UP" ]; then
        echo ""
        echo "♻️  CONTINUATION — Follow-up: ${FOLLOW_UP:0:200}"
        local existing_files
        existing_files=$(find "$WORKSPACE" -type f -not -name "result.json" -not -path "*/.git/*" -not -name "AGENTS.md" -not -name "SOUL.md" 2>/dev/null | head -30 | xargs -I{} basename {} | paste -sd, -)
        prompt="CONTINUATION: The previous run already produced these files in /workspace: [${existing_files:-none}]. Your job now is to IMPROVE the existing code based on these follow-up instructions:

${FOLLOW_UP}

--- Original task description for reference ---
${prompt}"
    fi

    # Setup workspace context
    setup_workspace_context

    # Build initial messages
    local system_prompt
    system_prompt=$(build_system_prompt)

    local system_escaped user_escaped
    system_escaped=$(json_escape "$system_prompt")
    user_escaped=$(json_escape "$prompt")

    local messages_json="[{\"role\":\"system\",\"content\":${system_escaped}},{\"role\":\"user\",\"content\":${user_escaped}}]"

    echo ""
    echo "🚀 Invoking PicoClaw native agent loop..."
    local router_url="${LLM_ROUTER_URL%/}"
    case "$router_url" in
        */v1) ;;
        *) router_url="${router_url}/v1" ;;
    esac
    log "🔗 LLM endpoint: ${router_url}/chat/completions"
    log "🤖 Model: ${LLM_MODEL}"
    log "🔄 Max turns: ${MAX_TURNS}"

    local turn=0
    local exit_code=0

    while [ "$turn" -lt "$MAX_TURNS" ]; do
        turn=$((turn + 1))
        echo ""
        echo "── Turn ${turn}/${MAX_TURNS} ──"

        # Call LLM
        local llm_resp
        llm_resp=$(call_llm "$messages_json") || {
            log_err "LLM request failed"
            append_output "[LLM_ERROR] Request failed"
            exit_code=1
            break
        }

        # Check for HTTP errors in the response
        local error_msg
        error_msg=$(echo "$llm_resp" | jq -r '.error.message // empty' 2>/dev/null)
        if [ -n "$error_msg" ]; then
            log_err "LLM error: ${error_msg}"
            append_output "[LLM_ERROR] ${error_msg}"
            exit_code=1
            break
        fi

        local content finish_reason tc_count
        content=$(get_content "$llm_resp")
        finish_reason=$(get_finish_reason "$llm_resp")
        tc_count=$(get_tool_call_count "$llm_resp")

        # Append assistant message to conversation
        local assistant_msg
        assistant_msg=$(get_assistant_message "$llm_resp")
        messages_json=$(echo "$messages_json" | jq --argjson msg "$assistant_msg" '. + [$msg]')

        # Handle text content
        if [ -n "$content" ]; then
            log "💬 Assistant: ${content:0:200}$([ ${#content} -gt 200 ] && echo '...')"
            append_output "$content"

            # Check for markers
            if echo "$content" | grep -qE "(CAPABILITY_REQUEST:|DEPLOYMENT_REQUEST:)"; then
                log "⚡ Marker detected, stopping loop"
                break
            fi
        fi

        # Handle tool calls
        if [ "$tc_count" -eq 0 ]; then
            if [ "$finish_reason" = "stop" ]; then
                log "✅ Agent finished (stop)"
            fi
            break
        fi

        # Execute each tool call
        local i=0
        while [ "$i" -lt "$tc_count" ]; do
            local tc_id tc_name tc_args_raw tc_args_json tool_result

            tc_id=$(get_tool_call_id "$llm_resp" "$i")
            tc_name=$(get_tool_call_name "$llm_resp" "$i")
            tc_args_raw=$(get_tool_call_args "$llm_resp" "$i")

            # Parse args (might be a JSON string that needs parsing)
            tc_args_json="$tc_args_raw"

            log "🔧 Tool: ${tc_name}(${tc_args_raw:0:120})"
            tool_result=$(execute_tool "$tc_name" "$tc_args_json")
            log "📤 Result: ${tool_result:0:200}$([ ${#tool_result} -gt 200 ] && echo '...')"

            append_output "[Tool:${tc_name}] ${tool_result}"

            # Add tool result to conversation
            local tool_result_escaped
            tool_result_escaped=$(json_escape "${tool_result:0:10000}")
            messages_json=$(echo "$messages_json" | jq \
                --arg role "tool" \
                --arg tc_id "$tc_id" \
                --argjson content "$tool_result_escaped" \
                '. + [{role: $role, tool_call_id: $tc_id, content: $content}]')

            i=$((i + 1))
        done
    done

    echo ""
    echo "════════════════════════════════════════════════════════════════════"
    echo "📊 PICOCLAW OUTPUT"
    echo "════════════════════════════════════════════════════════════════════"
    echo "${ALL_OUTPUT:0:5000}"
    if [ ${#ALL_OUTPUT} -gt 5000 ]; then
        echo "... (${#ALL_OUTPUT} total chars)"
    fi
    echo "════════════════════════════════════════════════════════════════════"
    echo "📤 Exit code: ${exit_code}"

    # Build result
    local completed="false"
    local cap_requested="false"
    local output_escaped deliverables_json

    output_escaped=$(truncate_str "$ALL_OUTPUT" 50000)

    # Check for capability requests
    local cap_info
    cap_info=$(parse_capability_request "$ALL_OUTPUT") && {
        local cap_type cap_packages cap_reason
        cap_type=$(echo "$cap_info" | cut -d'|' -f1)
        cap_packages=$(echo "$cap_info" | cut -d'|' -f2)
        cap_reason=$(echo "$cap_info" | cut -d'|' -f3-)

        echo ""
        echo "🔐 Capability needed: ${cap_type} → ${cap_packages}"
        echo "   └─ Reason: ${cap_reason}"

        if request_capability "$cap_type" "$cap_packages" "$cap_reason"; then
            echo "✅ Capability requested — image rebuild required"
            cap_requested="true"

            local result_json
            result_json=$(jq -n \
                --argjson completed false \
                --argjson cap_requested true \
                --arg output "$output_escaped" \
                --arg logs "$output_escaped" \
                --arg cap_type "$cap_type" \
                --arg cap_resource "$cap_packages" \
                --arg cap_reason "$cap_reason" \
                '{
                    completed: $completed,
                    capability_requested: $cap_requested,
                    output: $output,
                    agent_logs: $logs,
                    capability: {
                        type: $cap_type,
                        resource: $cap_resource,
                        justification: $cap_reason
                    }
                }')
            write_result "$result_json"
            exit 0
        else
            log_err "Capability request failed"
            local result_json
            result_json=$(jq -n \
                --argjson completed false \
                --argjson cap_requested false \
                --arg output "$output_escaped" \
                --arg logs "$output_escaped" \
                --arg err "Required capability denied: ${cap_type} ${cap_packages}" \
                '{
                    completed: $completed,
                    capability_requested: $cap_requested,
                    output: $output,
                    agent_logs: $logs,
                    error: $err
                }')
            write_result "$result_json"
            exit 1
        fi
    }

    # Check for deployment request
    local deploy_info
    deploy_info=$(parse_deployment_request "$ALL_OUTPUT") && {
        local dep_name dep_port dep_entrypoint
        dep_name=$(echo "$deploy_info" | cut -d'|' -f1)
        dep_port=$(echo "$deploy_info" | cut -d'|' -f2)
        dep_entrypoint=$(echo "$deploy_info" | cut -d'|' -f3-)

        echo ""
        echo "🚀 Deployment requested: ${dep_name} on port ${dep_port}"

        deliverables_json=$(collect_deliverables)

        local result_json
        result_json=$(jq -n \
            --argjson completed true \
            --argjson cap_requested false \
            --arg output "$output_escaped" \
            --arg logs "$output_escaped" \
            --arg dep_name "$dep_name" \
            --argjson dep_port "$dep_port" \
            --arg dep_entry "$dep_entrypoint" \
            --argjson deliverables "$deliverables_json" \
            '{
                completed: $completed,
                capability_requested: $cap_requested,
                output: $output,
                agent_logs: $logs,
                deployment_requested: true,
                deployment: {
                    name: $dep_name,
                    port: $dep_port,
                    entrypoint: $dep_entry,
                    files: $deliverables
                },
                deliverables: $deliverables,
                message: ("Deployment requested: " + $dep_name)
            }')
        write_result "$result_json"
        exit 0
    }

    # Regular completion
    if [ "$exit_code" -eq 0 ]; then
        completed="true"
        echo ""
        echo "✅ Task completed successfully"
    else
        echo ""
        echo "❌ Task failed"
    fi

    deliverables_json=$(collect_deliverables)
    local deliverables_count
    deliverables_count=$(echo "$deliverables_json" | jq 'keys | length')

    if [ "$deliverables_count" -gt 0 ]; then
        echo ""
        echo "📦 Collected ${deliverables_count} deliverable file(s):"
        echo "$deliverables_json" | jq -r 'keys[]' | while read -r fp; do
            echo "   📄 ${fp}"
        done
    else
        echo ""
        echo "📭 No deliverable files found in /workspace"
    fi

    local result_json
    result_json=$(jq -n \
        --argjson completed "$completed" \
        --argjson cap_requested false \
        --arg output "$output_escaped" \
        --arg logs "$output_escaped" \
        --argjson deliverables "$deliverables_json" \
        '{
            completed: $completed,
            capability_requested: $cap_requested,
            output: $output,
            agent_logs: $logs,
            deliverables: $deliverables
        }')

    if [ "$completed" = "true" ]; then
        result_json=$(echo "$result_json" | jq --arg msg "Task completed successfully" '. + {message: $msg}')
    else
        result_json=$(echo "$result_json" | jq --arg err "${output_escaped:0:1000}" '. + {error: $err}')
    fi

    write_result "$result_json"
    echo ""
    echo "🏁 Done. Exit code: ${exit_code}"
    exit "$exit_code"
}

main "$@"
