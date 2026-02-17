#!/usr/bin/env python3
"""
OpenClaw Agent Wrapper — runs inside the agent container.

Responsibilities:
  1. Fetch task description from control plane
  2. Configure OpenClaw to use the control plane's LLM router
     (agent thinks it talks to "the model" but traffic hits the router)
  3. Invoke OpenClaw CLI in local/agent mode
  4. Intercept capability requests (missing packages, etc.)
  5. Write result.json for the Temporal worker to read
"""

import os
import sys
import json
import re
import subprocess
import shutil
from typing import Dict, Any, Optional, Tuple, List

import httpx

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
CONTROL_PLANE_URL = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
TASK_ID = os.getenv("TASK_ID")
ITERATION = os.getenv("ITERATION", "0")
LLM_MODEL = os.getenv("LLM_MODEL", "gemma3:4b")
# The LLM router base URL — OpenClaw will call this as if it were OpenAI
LLM_ROUTER_URL = os.getenv("LLM_ROUTER_URL", f"{CONTROL_PLANE_URL}/api/llm")


# ---------------------------------------------------------------------------
# Control plane helpers
# ---------------------------------------------------------------------------

def fetch_task() -> Optional[Dict[str, Any]]:
    """Fetch task details from control plane."""
    if not TASK_ID:
        print("ERROR: TASK_ID not set", file=sys.stderr)
        return None
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(f"{CONTROL_PLANE_URL}/api/tasks/{TASK_ID}")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        print(f"ERROR fetching task: {e}", file=sys.stderr)
        return None


def request_capability(capability_type: str, packages: List[str]) -> bool:
    """Request a new capability from the control plane."""
    # Map wrapper-internal capability types to API enum values
    # API expects: tool_install, network_access, filesystem_access, database_access
    TYPE_MAP = {
        "python_packages": "tool_install",
        "npm_packages": "tool_install",
        "system_packages": "tool_install",
        "tool_install": "tool_install",
        "TOOL_INSTALL": "tool_install",
        "network_access": "network_access",
        "filesystem_access": "filesystem_access",
        "database_access": "database_access",
    }
    api_type = TYPE_MAP.get(capability_type, "tool_install")

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{CONTROL_PLANE_URL}/api/capabilities/requests",
                json={
                    "task_id": TASK_ID,
                    "capability_type": api_type,
                    "resource_name": ",".join(packages),
                    "justification": f"Required for task execution (iteration {ITERATION})",
                    "details": {"packages": packages, "original_type": capability_type},
                },
            )
            response.raise_for_status()
            data = response.json()
            return data.get("approved", False) or data.get("id") is not None
    except Exception as e:
        print(f"ERROR requesting capability: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# OpenClaw configuration
# ---------------------------------------------------------------------------

def setup_openclaw_config():
    """
    Configure OpenClaw to use the control-plane LLM router.

    The router exposes an OpenAI-compatible endpoint at:
        {CONTROL_PLANE_URL}/api/llm/v1/chat/completions

    We configure OpenClaw to point there so the agent thinks it's
    talking to the actual model, but the router dispatches to
    Ollama / Gemini / Anthropic / OpenAI based on model name.
    """
    openclaw_dir = os.path.expanduser("~/.openclaw")
    agent_dir = os.path.join(openclaw_dir, "agents", "main", "agent")
    os.makedirs(openclaw_dir, exist_ok=True)
    os.makedirs(agent_dir, exist_ok=True)

    # The router URL for OpenAI-compat endpoint
    # LLM_ROUTER_URL is already like http://host:8000/api/llm/v1
    # OpenClaw's OpenAI provider needs the base URL (without /v1 suffix)
    # because it appends /chat/completions itself
    router_base = LLM_ROUTER_URL.rstrip("/")
    # If URL already ends with /v1, use as-is; otherwise append /v1
    if not router_base.endswith("/v1"):
        router_base = f"{router_base}/v1"

    config = {
        "agents": {
            "defaults": {
                "model": {
                    "primary": f"openai/{LLM_MODEL}"
                },
                "workspace": "/workspace"
            }
        },
        "models": {
            "mode": "merge",
            "providers": {
                "openai": {
                    "baseUrl": router_base,
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": LLM_MODEL,
                            "name": LLM_MODEL,
                            "reasoning": False,
                            "input": ["text"],
                            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                            "contextWindow": 200000,
                            "maxTokens": 8192,
                        }
                    ],
                }
            },
        },
    }

    config_path = os.path.join(openclaw_dir, "openclaw.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    # Agent-specific config
    agent_config_path = os.path.join(agent_dir, "openclaw.json")
    with open(agent_config_path, "w") as f:
        json.dump(config, f, indent=2)

    # Auth profile — embed task_id so the LLM router can track per-task interactions
    api_key_value = f"task:{TASK_ID}" if TASK_ID else "task:unknown"
    auth_path = os.path.join(agent_dir, "auth-profiles.json")
    with open(auth_path, "w") as f:
        json.dump({"openai": {"apiKey": api_key_value}}, f)

    print(f"✅ OpenClaw configured")
    print(f"   Router URL: {router_base}")
    print(f"   Model: {LLM_MODEL}")
    print(f"   Config: {config_path}")


# ---------------------------------------------------------------------------
# Workspace context — tells OpenClaw about the capability system
# ---------------------------------------------------------------------------

def setup_workspace_context():
    """Write context files to /workspace that OpenClaw reads on startup.

    These files are injected into the system prompt and tell the agent
    about the capability request system so it doesn't waste iterations
    trying to install packages itself.
    """
    workspace = "/workspace"
    os.makedirs(workspace, exist_ok=True)

    # Build dynamic installed-packages section from Dockerfile
    agent_dockerfile = os.getenv("AGENT_DOCKERFILE", "")
    agent_image = os.getenv("AGENT_IMAGE", "base")
    installed_packages_section = ""
    if agent_dockerfile:
        installed_packages_section = (
            "\n### Container Dockerfile (your current image)\n\n"
            "The following Dockerfile was used to build the image you are running in.\n"
            "All packages listed here are ALREADY INSTALLED — do NOT request them again.\n\n"
            f"```dockerfile\n{agent_dockerfile.strip()}\n```\n"
        )

    agents_md = f"""# AGENTS.md — Managed Execution Environment

You are running inside a managed container. Your workspace is `/workspace`.

## YOUR WORKFLOW (follow this order)

1. **Write** the code/files the task requires into `/workspace`.
2. **Execute** the code using the `exec` tool to verify it works.
   - Example: `exec python3 /workspace/stats.py`
3. **If execution fails** with `ModuleNotFoundError`, ONLY THEN request the package (see below).
4. **If execution succeeds**, you are DONE. Do not output anything else.

**You MUST execute your code before finishing.** Writing a file alone is NOT enough.
The task is only complete when the code runs successfully and produces correct output.

## Package Installation

You cannot install packages yourself (`pip install`, `apt-get`, etc. will fail).

### Pre-installed packages

Already available — do NOT request these:
- Python 3 (standard library: `os`, `sys`, `json`, `re`, `math`, `datetime`, etc.)
- `httpx` (HTTP client)
- Node.js 22 + npm
- `git`, `curl`
{installed_packages_section}

### How to request a missing package

**ONLY** if `python3 -c "import <package>"` fails with `ModuleNotFoundError`:

```
CAPABILITY_REQUEST:tool_install:<package_name>:<reason>
```

After this line, STOP. The system will rebuild your container with the package
and re-run your task automatically.

## Deployment Request

If the task asks you to create a web application, API server, or any long-running
service, do NOT try to start it yourself. Instead:

1. Write all the code files to `/workspace`
2. Test the code logic (unit tests, import checks) but do NOT start the server
3. Output a deployment request:

```
DEPLOYMENT_REQUEST:<app-name>:<port>:<entrypoint command>
```

Example:
```
DEPLOYMENT_REQUEST:fibonacci-app:5000:python app.py
```

The system will build a deployment image from your workspace files and the
user can start/stop it independently.

**IMPORTANT**: Do NOT run the server yourself (no `flask run`, no `python app.py`
with the server starting). Just write the code and request deployment.

## Task Info

- Iteration: {ITERATION}
- Model: {LLM_MODEL}
- Image: {agent_image}
- Workspace: `/workspace` (files here are collected as deliverables)
"""

    agents_path = os.path.join(workspace, "AGENTS.md")
    with open(agents_path, "w") as f:
        f.write(agents_md)

    # Minimal SOUL.md for task-oriented behavior
    soul_md = """# SOUL.md — Task Agent

You are a task execution agent. Your job is to complete the assigned task
efficiently and correctly.

## Principles
- Focus on the task. Don't add unnecessary features.
- Write clean, working code.
- If you need a package that's not installed, request it (see AGENTS.md).
  Do NOT try workarounds — they will fail.
- Test your code if possible before finishing.
- Write all files to `/workspace`.
"""
    with open(os.path.join(workspace, "SOUL.md"), "w") as f:
        f.write(soul_md)


# ---------------------------------------------------------------------------
# OpenClaw invocation
# ---------------------------------------------------------------------------

def invoke_openclaw_agent(prompt: str) -> Tuple[str, int]:
    """
    Invoke OpenClaw agent CLI and return (output, exit_code).
    """
    try:
        setup_openclaw_config()
        setup_workspace_context()

        env = os.environ.copy()
        # Embed task_id in the API key so the LLM router can track per-task interactions
        env["OPENAI_API_KEY"] = f"task:{TASK_ID or 'unknown'}"

        # Find the openclaw binary — installed globally via npm
        openclaw_bin = shutil.which("openclaw")
        if not openclaw_bin:
            # Fallback to known global install paths
            for candidate in [
                "/usr/local/bin/openclaw",
                "/usr/local/lib/node_modules/openclaw/dist/index.js",
            ]:
                if os.path.exists(candidate):
                    openclaw_bin = candidate
                    break

        if not openclaw_bin:
            return ("ERROR: openclaw binary not found. Checked: which openclaw, "
                    "/usr/local/bin/openclaw, /usr/local/lib/node_modules/openclaw/dist/index.js", 1)

        print(f"   Binary: {openclaw_bin}")

        # Build command — if the binary is a node script, run it with node;
        # otherwise it's already executable (npm global bin link)
        if openclaw_bin.endswith(".js"):
            cmd = ["node", openclaw_bin]
        else:
            cmd = [openclaw_bin]

        cmd += [
            "agent",
            "--local",
            "--session-id", TASK_ID or "default",
            "--message", prompt,
            "--thinking", "medium",
            "--json",
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            env=env,
        )
        return (result.stdout + result.stderr, result.returncode)

    except subprocess.TimeoutExpired:
        return ("ERROR: Agent execution timed out after 10 minutes", 1)
    except Exception as e:
        import traceback
        return (f"ERROR invoking OpenClaw: {e}\n{traceback.format_exc()}", 1)


# ---------------------------------------------------------------------------
# Capability detection
# ---------------------------------------------------------------------------

def parse_capability_request(output: str) -> Optional[Tuple[str, List[str]]]:
    """Parse OpenClaw output for missing package errors / capability requests.

    Checks for (in order of priority):
    1. Explicit CAPABILITY_REQUEST marker from agent (preferred)
    2. Python ModuleNotFoundError / ImportError
    3. pip install failures (permission denied, externally-managed, etc.)
    4. npm module not found
    5. python_packages=[...] pattern
    """
    # 1. Explicit capability request from agent (highest priority)
    match = re.search(r"CAPABILITY_REQUEST:(\w+):([^:\n]+):(.+)", output)
    if match:
        cap_type = match.group(1)
        packages = [p.strip() for p in match.group(2).split(",")]
        return (cap_type, packages)

    # 2. Python ModuleNotFoundError / ImportError
    matches = re.findall(
        r"(?:ModuleNotFoundError|ImportError):.*?no module named ['\"]?([a-zA-Z0-9_]+)",
        output, re.IGNORECASE,
    )
    if not matches:
        matches = re.findall(r"no module named ['\"]([^'\"]+)['\"]", output, re.IGNORECASE)
    if matches:
        # Deduplicate, take root package name
        packages = list(dict.fromkeys(m.split(".")[0] for m in matches))
        return ("python_packages", packages)

    # 3. pip install failures — extract package name from the command
    pip_patterns = [
        r"pip3?\s+install\s+([a-zA-Z0-9_-]+).*(?:error|denied|externally.managed|not allowed)",
        r"(?:error|denied|permission).*pip3?\s+install\s+([a-zA-Z0-9_-]+)",
        r"pip3?\s+install\s+([a-zA-Z0-9_-]+).*failed",
    ]
    for pattern in pip_patterns:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            return ("python_packages", [match.group(1)])

    # 4. npm module not found — only match actual package names, not paths
    match = re.search(r"cannot find module ['\"]([^'\"]+)['\"]", output, re.IGNORECASE)
    if match:
        mod = match.group(1)
        if not mod.startswith("/") and not mod.startswith("."):
            return ("npm_packages", [mod])

    # 5. python_packages=[...] pattern
    match = re.search(r"python_packages=\[([^\]]+)\]", output)
    if match:
        packages = [p.strip().strip("'\"") for p in match.group(1).split(",")]
        return ("python_packages", packages)

    return None


def parse_deployment_request(output: str) -> Optional[Dict[str, Any]]:
    """Parse OpenClaw output for deployment request.

    Format: DEPLOYMENT_REQUEST:<name>:<port>:<entrypoint>
    Example: DEPLOYMENT_REQUEST:fibonacci-app:5000:python app.py
    Also handles: DEPLOYMENT_REQUEST:app:8080:sh -c "redis-server && python app.py"
    """
    # Match everything after port until end of line, including quoted strings
    match = re.search(r"DEPLOYMENT_REQUEST:([^:]+):(\d+):(.+)", output)
    if match:
        entrypoint = match.group(3).strip()
        # Strip trailing punctuation and JSON artefacts (the marker is often
        # embedded inside a JSON string so we may pick up a closing quote,
        # comma, bracket, etc.)
        entrypoint = entrypoint.rstrip(".,;\\]})")
        # Strip trailing quotes only if they are unbalanced (JSON artefact)
        while entrypoint and entrypoint[-1] in ('"', "'"):
            # Count occurrences — if odd, the trailing quote is an artefact
            q = entrypoint[-1]
            if entrypoint.count(q) % 2 == 1:
                entrypoint = entrypoint[:-1]
            else:
                break
        # Remove wrapping quotes if the entire entrypoint is quoted
        if entrypoint.startswith('"') and entrypoint.endswith('"'):
            entrypoint = entrypoint[1:-1]
        return {
            "name": match.group(1).strip(),
            "port": int(match.group(2)),
            "entrypoint": entrypoint,
        }
    return None


# ---------------------------------------------------------------------------
# Result writing
# ---------------------------------------------------------------------------

# Sentinel markers so the worker can reliably extract the result JSON from stdout
RESULT_START = "===OPENCLAW_RESULT_JSON_START==="
RESULT_END = "===OPENCLAW_RESULT_JSON_END==="

def collect_workspace_files() -> Dict[str, str]:
    """Scan /workspace for files created/modified by the agent.

    Returns a dict of {relative_path: content} for text files.
    Skips hidden dirs, node_modules, and common non-deliverable files.
    """
    workspace = "/workspace"
    SKIP_DIRS = {".git", "node_modules", ".openclaw", "__pycache__", ".cache", ".npm"}
    SKIP_FILES = {"result.json", "AGENTS.md", "SOUL.md", "TOOLS.md",
                  "IDENTITY.md", "USER.md", "HEARTBEAT.md", "BOOTSTRAP.md",
                  "package-lock.json"}
    MAX_FILE_SIZE = 50_000  # 50 KB per file
    MAX_TOTAL = 200_000     # 200 KB total
    collected: Dict[str, str] = {}
    total_size = 0

    if not os.path.isdir(workspace):
        return collected

    for root, dirs, files in os.walk(workspace):
        # Prune skip dirs
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in files:
            if fname in SKIP_FILES:
                continue
            fpath = os.path.join(root, fname)
            relpath = os.path.relpath(fpath, workspace)
            try:
                size = os.path.getsize(fpath)
                if size == 0 or size > MAX_FILE_SIZE:
                    continue
                if total_size + size > MAX_TOTAL:
                    break
                with open(fpath, "r", errors="replace") as f:
                    content = f.read()
                collected[relpath] = content
                total_size += size
            except Exception:
                pass  # binary file or unreadable
    return collected


def write_result(result: Dict[str, Any]):
    """Write result JSON to /workspace, /tmp, AND stdout (delimited)."""
    result_json = json.dumps(result, indent=2)
    # Try file writes (may fail in DinD if /workspace bind mount is not visible)
    for path in ["/workspace/result.json", "/tmp/result.json"]:
        try:
            with open(path, "w") as f:
                f.write(result_json)
        except Exception:
            pass
    # Always emit to stdout with clear delimiters so the worker can parse it
    print(f"\n{RESULT_START}")
    print(result_json)
    print(RESULT_END)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 80)
    print("🦞 OPENCLAW AGENT WRAPPER")
    print("=" * 80)
    print(f"📋 Task ID:       {TASK_ID}")
    print(f"🔄 Iteration:     {ITERATION}")
    print(f"🤖 Model:         {LLM_MODEL}")
    print(f"🌐 Control Plane: {CONTROL_PLANE_URL}")
    print(f"🔀 LLM Router:    {LLM_ROUTER_URL}")
    print("=" * 80)

    # Fetch task
    print("\n📥 Fetching task from control plane...")
    task = fetch_task()
    prompt = ""
    if task:
        prompt = task.get("description", "") or task.get("prompt", "")
        print(f"✅ Task fetched: {prompt[:150]}...")
    
    # Fallback to TASK_DESCRIPTION env var (passed by worker)
    if not prompt:
        prompt = os.getenv("TASK_DESCRIPTION", "")
        if prompt:
            print(f"📝 Using TASK_DESCRIPTION env var: {prompt[:150]}...")
    
    if not prompt:
        print("❌ ERROR: No task description available", file=sys.stderr)
        write_result({"completed": False, "error": "No description in task and no TASK_DESCRIPTION env"})
        sys.exit(1)

    # If this is a continuation, prepend follow-up instructions prominently
    follow_up = os.getenv("FOLLOW_UP", "").strip()
    if follow_up:
        print(f"\n♻️  CONTINUATION — Follow-up instructions: {follow_up[:200]}")
        # List existing workspace files so the agent knows what it has to work with
        existing_files = []
        for root, dirs, files in os.walk("/workspace"):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), "/workspace")
                if not rel.startswith(".") and rel != "result.json":
                    existing_files.append(rel)
        files_context = ", ".join(existing_files[:30]) if existing_files else "none"
        prompt = (
            f"CONTINUATION: The previous run of this task already completed and produced these files "
            f"in /workspace: [{files_context}]. "
            f"Your job now is to IMPROVE the existing code based on these follow-up instructions:\n\n"
            f"{follow_up}\n\n"
            f"--- Original task description for reference ---\n{prompt}"
        )

    # Execute with OpenClaw
    print(f"\n🚀 Invoking OpenClaw agent...")
    output, exit_code = invoke_openclaw_agent(prompt)

    print("\n" + "=" * 80)
    print("📊 OPENCLAW OUTPUT")
    print("=" * 80)
    # Print full output (container logs capture it for Temporal)
    print(output)
    print("=" * 80)
    print(f"📤 Exit code: {exit_code}")

    # Build result
    result: Dict[str, Any] = {
        "completed": False,
        "capability_requested": False,
        "output": output[:50000],
        "agent_logs": output[:50000],
    }

    # Check for capability requests ALWAYS (agent may emit markers even on success)
    cap = parse_capability_request(output)
    if cap:
        cap_type, packages = cap
        print(f"\n🔐 Capability needed: {cap_type} → {packages}")

        # Verify packages are actually missing before requesting
        if cap_type in ("tool_install", "python_packages", "pip_package"):
            actually_missing = []
            for pkg in packages:
                # Normalize package name for import (e.g. scikit-learn -> sklearn)
                import_name = pkg.replace("-", "_")
                try:
                    result_check = subprocess.run(
                        ["python3", "-c", f"import {import_name}"],
                        capture_output=True, text=True, timeout=10
                    )
                    if result_check.returncode == 0:
                        print(f"   ✅ {pkg} is already installed, skipping request")
                    else:
                        actually_missing.append(pkg)
                        print(f"   ❌ {pkg} is NOT installed")
                except Exception:
                    actually_missing.append(pkg)
            
            if not actually_missing:
                print(f"\n✅ All requested packages are already installed, skipping capability request")
                # Don't request — let the agent retry with packages available
                cap = None
            else:
                packages = actually_missing
                print(f"\n📦 Actually missing packages: {packages}")

    if cap:
        if request_capability(cap_type, packages):
            print("✅ Capability requested — image rebuild required")
            result["capability_requested"] = True
            result["capability"] = {
                "type": "pip_package" if cap_type == "python_packages" else cap_type,
                "resource": ",".join(packages),
                "justification": f"Required {cap_type}: {', '.join(packages)}",
            }
            write_result(result)
            sys.exit(0)
        else:
            print("❌ Capability request failed")
            result["error"] = "Required capability denied"
            write_result(result)
            sys.exit(1)

    # Check for deployment request
    deploy = parse_deployment_request(output)
    if deploy:
        print(f"\n🚀 Deployment requested: {deploy['name']} on port {deploy['port']}")
        print(f"   Entrypoint: {deploy['entrypoint']}")
        result["completed"] = True
        result["deployment_requested"] = True
        result["deployment"] = deploy
        # Collect workspace files for the deployment image
        deliverables = collect_workspace_files()
        if deliverables:
            result["deliverables"] = deliverables
            result["deployment"]["files"] = deliverables
            print(f"   📦 {len(deliverables)} file(s) for deployment")
        result["message"] = f"Deployment requested: {deploy['name']}"
        write_result(result)
        sys.exit(0)

    # Detect LLM-level errors that OpenClaw surfaces as text output but
    # still exits 0 — these should NOT count as task completion.
    LLM_ERROR_MARKERS = [
        "MALFORMED_FUNCTION_CALL",
        "Unhandled stop reason",
        "function_call_filter",
    ]
    llm_error_detected = any(marker in output for marker in LLM_ERROR_MARKERS)
    if llm_error_detected:
        print(f"\n⚠️  LLM error detected in output (exit_code={exit_code}), marking as NOT completed")
        result["completed"] = False
        result["error"] = f"LLM error: {output[:500]}"
        result["agent_failed"] = False  # not a hard failure, workflow should retry
        write_result(result)
        sys.exit(0)  # exit 0 so container is not flagged as crashed

    # Check exit code
    if exit_code != 0:
        print(f"\n⚠️  OpenClaw exited with code {exit_code}")
        print(f"\n❌ Agent failed, no capability request detected")
        result["error"] = output[:1000]

    # Task completed
    if exit_code == 0:
        result["completed"] = True
        result["message"] = "Task completed successfully"
        print("\n✅ Task completed successfully")
    else:
        result["error"] = result.get("error", output[:1000])
        print(f"\n❌ Task failed")

    # Collect workspace deliverables (files the agent created/modified)
    deliverables = collect_workspace_files()
    if deliverables:
        result["deliverables"] = deliverables
        print(f"\n📦 Collected {len(deliverables)} deliverable file(s):")
        for fp in deliverables:
            print(f"   📄 {fp}")
    else:
        print("\n📭 No deliverable files found in /workspace")

    write_result(result)
    print(f"\n🏁 Done. Exit code: {exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
