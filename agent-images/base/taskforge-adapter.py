#!/usr/bin/env python3
"""
TaskForge Native Agent Adapter — runs inside NanoBot / ZeroClaw containers.

Drop-in replacement for openclaw-wrapper.py that speaks the SAME protocol
(env vars, result markers, capability/deployment detection, deliverables)
but uses a lightweight Python agentic loop instead of the openclaw npm CLI.

This keeps the images small (no Node.js required) while remaining fully
compatible with the Temporal worker's collect_agent_result() contract.
"""

import os
import sys
import json
import re
import signal
import subprocess
import time
import traceback
from typing import Dict, Any, Optional, Tuple, List

import httpx

# ---------------------------------------------------------------------------
# Configuration from environment  (identical to openclaw-wrapper.py)
# ---------------------------------------------------------------------------
CONTROL_PLANE_URL = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
TASK_ID = os.getenv("TASK_ID")
ITERATION = os.getenv("ITERATION", "0")
LLM_MODEL = os.getenv("LLM_MODEL", "gemma3:4b")
LLM_ROUTER_URL = os.getenv("LLM_ROUTER_URL", f"{CONTROL_PLANE_URL}/api/llm")
IMAGE_TYPE = os.getenv("OPENCLAW_IMAGE_TYPE", "nanobot")

MAX_TURNS = int(os.getenv("MAX_AGENT_TURNS", "30"))
TOOL_TIMEOUT = int(os.getenv("TOOL_TIMEOUT", "60"))


def _kill_tree(proc):
    """Kill a process and its entire process group.

    Mirrors _kill_process_tree() from openclaw-wrapper.py.
    The agent may spawn long-running children (Flask servers, nc -lk, etc.)
    that inherit stdout.  Using os.killpg ensures communicate() won't hang
    waiting for grandchildren to close the pipe.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass


# ---------------------------------------------------------------------------
# Control-plane helpers  (copied verbatim from openclaw-wrapper.py)
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


def _resolve_package_versions(packages: List[str], capability_type: str) -> Dict[str, str]:
    """Try to resolve exact versions of requested packages."""
    versions: Dict[str, str] = {}
    if capability_type in ("python_packages", "pip_package", "tool_install"):
        for pkg in packages:
            try:
                result = subprocess.run(
                    ["pip3", "show", pkg],
                    capture_output=True, text=True, timeout=15
                )
                if result.returncode == 0:
                    import re as _re
                    match = _re.search(r"^Version:\s*(.+)", result.stdout, _re.MULTILINE)
                    if match:
                        versions[pkg] = match.group(1).strip()
            except Exception:
                pass
    return versions


def request_capability(capability_type: str, packages: List[str], justification: str = "") -> bool:
    """Request a new capability from the control plane."""
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

    if not justification:
        justification = f"Required {capability_type}: {', '.join(packages)}"

    versions = _resolve_package_versions(packages, capability_type)
    if versions:
        version_str = ", ".join(f"{p}=={v}" for p, v in versions.items())
        print(f"   📌 Resolved versions: {version_str}")

    task_desc = ""
    task_name = ""
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{CONTROL_PLANE_URL}/api/tasks/{TASK_ID}")
            if resp.status_code == 200:
                task_data = resp.json()
                task_desc = task_data.get("description", "")
                task_name = task_data.get("name", "")
    except Exception:
        pass

    parts = [f"[Iteration {ITERATION}] {justification}"]
    if versions:
        parts.append(f"\nRequested versions: {', '.join(f'{p}=={v}' for p, v in versions.items())}")
    if task_desc:
        parts.append(f"\nTask: {task_name or 'N/A'} — {task_desc[:300]}")
    full_justification = "".join(parts)

    resource_parts = []
    for pkg in packages:
        resource_parts.append(f"{pkg}=={versions[pkg]}" if pkg in versions else pkg)
    resource_name = ",".join(resource_parts)

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{CONTROL_PLANE_URL}/api/capabilities/requests",
                json={
                    "task_id": TASK_ID,
                    "capability_type": api_type,
                    "resource_name": resource_name,
                    "justification": full_justification,
                    "details": {
                        "packages": packages,
                        "original_type": capability_type,
                        "iteration": ITERATION,
                        "reason": justification,
                        "versions": versions,
                        "task_description": task_desc[:500] if task_desc else None,
                    },
                },
            )
            response.raise_for_status()
            data = response.json()
            return data.get("approved", False) or data.get("id") is not None
    except Exception as e:
        print(f"ERROR requesting capability: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Workspace context  (adapted per image type)
# ---------------------------------------------------------------------------

def get_runtime_description() -> str:
    """Return the pre-installed packages description for this image type."""
    if IMAGE_TYPE == "nanobot":
        return (
            "- Python 3.11 (Alpine, standard library)\n"
            "- `httpx` (HTTP client)\n"
            "- `pydantic` (data validation)\n"
            "- `structlog` (logging)\n"
            "- `git`, `curl`, `jq`, `bash`\n"
        )
    elif IMAGE_TYPE == "zeroclaw":
        return (
            "- Python 3 (Debian, standard library)\n"
            "- `httpx` (HTTP client)\n"
            "- `pydantic` (data validation)\n"
            "- `requests`, `structlog`, `pytest`\n"
            "- `curl`, `git`\n"
            "- Rust toolchain available at /opt/openclaw/zeroclaw-agent\n"
        )
    else:
        return (
            "- Python 3 (standard library)\n"
            "- `httpx` (HTTP client)\n"
            "- `curl`, `git`\n"
        )


def setup_workspace_context():
    """Write context files to /workspace — same content as openclaw-wrapper.py
    but adapted for the native adapter's tool names."""
    workspace = "/workspace"
    os.makedirs(workspace, exist_ok=True)

    agent_dockerfile = os.getenv("AGENT_DOCKERFILE", "")
    agent_image = os.getenv("AGENT_IMAGE", IMAGE_TYPE)
    installed_packages_section = ""
    if agent_dockerfile:
        installed_packages_section = (
            "\n### Container Dockerfile (your current image)\n\n"
            "The following Dockerfile was used to build the image you are running in.\n"
            "All packages listed here are ALREADY INSTALLED — do NOT request them again.\n\n"
            f"```dockerfile\n{agent_dockerfile.strip()}\n```\n"
        )

    runtime_desc = get_runtime_description()

    agents_md = f"""# AGENTS.md — Managed Execution Environment

You are running inside a managed container. Your workspace is `/workspace`.

## YOUR WORKFLOW (follow this order)

1. **Write** the code/files the task requires into `/workspace`.
2. **Execute** the code to verify it works.
   - Use the `exec` tool: exec python3 /workspace/my_script.py
3. **If execution fails** with `ModuleNotFoundError`, ONLY THEN request the package (see below).
4. **If execution succeeds**, you are DONE. Do not output anything else.

**You MUST execute your code before finishing.** Writing a file alone is NOT enough.
The task is only complete when the code runs successfully and produces correct output.

## Package Installation

You cannot install packages yourself (`pip install`, `apt-get`, etc. will fail).

### Pre-installed packages

Already available — do NOT request these:
{runtime_desc}
{installed_packages_section}

### How to request a missing package

**ONLY** if `python3 -c "import <package>"` fails with `ModuleNotFoundError`:

```
CAPABILITY_REQUEST:tool_install:<package_name>:<detailed reason why this package is needed>
```

The `<detailed reason>` MUST explain:
- What functionality the package provides
- Why it's needed for the current task
- What will fail without it

Examples:
```
CAPABILITY_REQUEST:tool_install:pandas:Data analysis library required to read CSV files and compute statistical aggregations
CAPABILITY_REQUEST:tool_install:flask:Web microframework needed to build the HTTP API server
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

The system will build a deployment image and the user can start/stop it.

## Task Info

- Iteration: {ITERATION}
- Model: {LLM_MODEL}
- Image: {agent_image}
- Runtime: {IMAGE_TYPE}
- Workspace: `/workspace` (files here are collected as deliverables)
"""

    with open(os.path.join(workspace, "AGENTS.md"), "w") as f:
        f.write(agents_md)

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
# Tool definitions for the native agentic loop
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Write content to a file in the workspace. Creates parent directories automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path (must be under /workspace)"},
                    "content": {"type": "string", "description": "File content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read the content of a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path to read"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exec",
            "description": "Execute a shell command and return stdout+stderr. Use this to run scripts, test code, check imports, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "Replace a specific string in a file with new content. Use for targeted edits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path to edit"},
                    "old_string": {"type": "string", "description": "Exact string to find and replace"},
                    "new_string": {"type": "string", "description": "Replacement string"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
]


def execute_tool(name: str, arguments: Dict[str, Any]) -> str:
    """Execute a tool call and return the result as a string."""
    try:
        if name == "write":
            path = arguments["path"]
            content = arguments["content"]
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            return f"✅ Written {len(content)} bytes to {path}"

        elif name == "read":
            path = arguments["path"]
            if not os.path.exists(path):
                return f"ERROR: File not found: {path}"
            with open(path, "r", errors="replace") as f:
                content = f.read()
            if len(content) > 50000:
                content = content[:50000] + "\n... (truncated)"
            return content

        elif name == "exec":
            command = arguments["command"]
            # Launch in a new session so we can kill the entire process tree
            # (prevents orphaned grandchildren like servers from hanging)
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd="/workspace",
                start_new_session=True,
            )
            try:
                stdout, stderr = proc.communicate(timeout=TOOL_TIMEOUT)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                stdout, stderr = proc.communicate(timeout=5)
                output = ""
                if stdout:
                    output += stdout
                if stderr:
                    output += ("\n" if output else "") + stderr
                return (output[:49000] +
                        f"\nERROR: Command timed out after {TOOL_TIMEOUT}s"
                        " (process tree killed)")
            finally:
                # Always clean up any lingering children
                _kill_tree(proc)
            output = ""
            if stdout:
                output += stdout
            if stderr:
                output += ("\n" if output else "") + stderr
            if not output:
                output = f"(no output, exit code {proc.returncode})"
            elif proc.returncode != 0:
                output += f"\n(exit code {proc.returncode})"
            return output[:50000]

        elif name == "edit":
            path = arguments["path"]
            old_string = arguments["old_string"]
            new_string = arguments["new_string"]
            if not os.path.exists(path):
                return f"ERROR: File not found: {path}"
            with open(path, "r") as f:
                content = f.read()
            if old_string not in content:
                return f"ERROR: old_string not found in {path}"
            content = content.replace(old_string, new_string, 1)
            with open(path, "w") as f:
                f.write(content)
            return f"✅ Edited {path}"

        else:
            return f"ERROR: Unknown tool '{name}'"

    except subprocess.TimeoutExpired:
        return f"ERROR: Command timed out after {TOOL_TIMEOUT}s"
    except Exception as e:
        return f"ERROR: {e}"


# ---------------------------------------------------------------------------
# Native agentic loop  (replaces invoke_openclaw_agent)
# ---------------------------------------------------------------------------

def build_system_prompt() -> str:
    """Build the system prompt from workspace context files (like OpenClaw does)."""
    parts = [
        "You are a task execution agent running inside a managed container.",
        "Your workspace is /workspace. All files you create there are collected as deliverables.",
        "",
        "You have these tools: write, read, exec, edit.",
        "- write: Create/overwrite a file",
        "- read: Read a file's content",
        "- exec: Run a shell command (use to test your code!)",
        "- edit: Replace a string in a file",
        "",
        "IMPORTANT RULES:",
        "1. Always write code to /workspace",
        "2. Always exec your code to verify it works",
        "3. If you get ModuleNotFoundError, emit: CAPABILITY_REQUEST:tool_install:<pkg>:<reason>",
        "4. For web apps, emit: DEPLOYMENT_REQUEST:<name>:<port>:<entrypoint>",
        "5. Do NOT try pip install or apt-get — they will fail",
        "",
    ]

    # Inject workspace context files if they exist
    for ctx_file in ["AGENTS.md", "SOUL.md"]:
        ctx_path = f"/workspace/{ctx_file}"
        if os.path.exists(ctx_path):
            with open(ctx_path, "r") as f:
                parts.append(f"--- {ctx_file} ---")
                parts.append(f.read())
                parts.append("")

    return "\n".join(parts)


def invoke_native_agent(prompt: str) -> Tuple[str, int]:
    """Run a native agentic tool-use loop against the LLM router.

    This replaces invoke_openclaw_agent() but produces identical output
    format so the rest of main() (capability detection, deliverables, etc.)
    works unchanged.
    """
    router_url = LLM_ROUTER_URL.rstrip("/")
    if not router_url.endswith("/v1"):
        router_url = f"{router_url}/v1"
    completions_url = f"{router_url}/chat/completions"

    api_key = f"task:{TASK_ID or 'unknown'}"

    system_prompt = build_system_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    all_output_parts: List[str] = []
    turn = 0

    print(f"   🔗 LLM endpoint: {completions_url}")
    print(f"   🤖 Model: {LLM_MODEL}")
    print(f"   🔄 Max turns: {MAX_TURNS}")

    try:
        with httpx.Client(timeout=120.0) as client:
            while turn < MAX_TURNS:
                turn += 1
                print(f"\n── Turn {turn}/{MAX_TURNS} ──")

                # Call LLM
                try:
                    resp = client.post(
                        completions_url,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": LLM_MODEL,
                            "messages": messages,
                            "tools": TOOLS,
                            "tool_choice": "auto",
                            "temperature": 0.2,
                            "max_tokens": 4096,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except httpx.HTTPStatusError as e:
                    error_body = e.response.text[:500]
                    msg = f"[LLM_ERROR] HTTP {e.response.status_code}: {error_body}"
                    print(f"   ❌ {msg}")
                    all_output_parts.append(msg)
                    return "\n".join(all_output_parts), 1
                except Exception as e:
                    msg = f"[LLM_ERROR] Request failed: {e}"
                    print(f"   ❌ {msg}")
                    all_output_parts.append(msg)
                    return "\n".join(all_output_parts), 1

                choice = data.get("choices", [{}])[0]
                message = choice.get("message", {})
                finish_reason = choice.get("finish_reason", "")

                # Append assistant message to conversation
                messages.append(message)

                # Handle text content
                content = message.get("content", "")
                if content:
                    print(f"   💬 Assistant: {content[:200]}{'...' if len(content) > 200 else ''}")
                    all_output_parts.append(content)

                    # Check for capability/deployment markers in text
                    if "CAPABILITY_REQUEST:" in content or "DEPLOYMENT_REQUEST:" in content:
                        print(f"   ⚡ Marker detected in assistant text, stopping loop")
                        break

                # Handle tool calls
                tool_calls = message.get("tool_calls", [])
                if not tool_calls:
                    # No tool calls and finish_reason is stop — agent is done
                    if finish_reason == "stop":
                        print(f"   ✅ Agent finished (stop)")
                    break

                # Execute each tool call
                for tc in tool_calls:
                    func = tc.get("function", {})
                    tool_name = func.get("name", "")
                    try:
                        tool_args = json.loads(func.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        tool_args = {}

                    print(f"   🔧 Tool: {tool_name}({json.dumps(tool_args)[:120]})")
                    tool_result = execute_tool(tool_name, tool_args)
                    print(f"   📤 Result: {tool_result[:200]}{'...' if len(tool_result) > 200 else ''}")

                    all_output_parts.append(f"[Tool:{tool_name}] {tool_result}")

                    # Check for ModuleNotFoundError in exec results
                    if tool_name == "exec" and ("ModuleNotFoundError" in tool_result or "ImportError" in tool_result):
                        all_output_parts.append(tool_result)

                    # Add tool result to conversation
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", f"call_{turn}_{tool_name}"),
                        "content": tool_result[:10000],
                    })

        combined = "\n".join(all_output_parts)
        return combined, 0

    except Exception as e:
        error = f"Agent loop error: {e}\n{traceback.format_exc()}"
        print(f"   ❌ {error}")
        all_output_parts.append(error)
        return "\n".join(all_output_parts), 1


# ---------------------------------------------------------------------------
# Capability / deployment detection  (identical to openclaw-wrapper.py)
# ---------------------------------------------------------------------------

def parse_capability_request(output: str) -> Optional[Tuple[str, List[str], str]]:
    """Parse output for CAPABILITY_REQUEST markers or ModuleNotFoundError."""
    normalised = output.replace("\\n", "\n").replace("\\r", "\r")

    all_packages: List[str] = []
    all_reasons: List[str] = []
    cap_type_found: Optional[str] = None

    for m in re.finditer(r"CAPABILITY_REQUEST:(\w+):([^:\n]+):(.+)", normalised):
        packages_raw = m.group(2).strip()
        if re.fullmatch(r"<[^>]+>", packages_raw):
            continue
        cap_type_found = m.group(1)
        for p in packages_raw.split(","):
            p = p.strip()
            if p and p not in all_packages:
                all_packages.append(p)
        reason = m.group(3).strip().rstrip('"\',}] ')
        if reason and reason not in all_reasons:
            all_reasons.append(reason)

    if all_packages and cap_type_found:
        return (cap_type_found, all_packages, "; ".join(all_reasons) if all_reasons else "Required for task execution")

    # Fallback: ModuleNotFoundError
    matches = re.findall(
        r"(?:ModuleNotFoundError|ImportError):.*?no module named ['\"]?([a-zA-Z0-9_]+)",
        normalised, re.IGNORECASE,
    )
    if not matches:
        matches = re.findall(r"no module named ['\"]([^'\"]+)['\"]", normalised, re.IGNORECASE)
    if matches:
        packages = list(dict.fromkeys(m.split(".")[0] for m in matches))
        return ("python_packages", packages, "ModuleNotFoundError detected")

    # pip install failures
    for pattern in [
        r"pip3?\s+install\s+([a-zA-Z0-9_-]+).*(?:error|denied|externally.managed|not allowed)",
        r"(?:error|denied|permission).*pip3?\s+install\s+([a-zA-Z0-9_-]+)",
    ]:
        match = re.search(pattern, normalised, re.IGNORECASE)
        if match:
            return ("python_packages", [match.group(1)], "pip install failure detected")

    return None


def parse_deployment_request(output: str) -> Optional[Dict[str, Any]]:
    """Parse output for DEPLOYMENT_REQUEST markers."""
    match = re.search(r"DEPLOYMENT_REQUEST:([^:]+):(\d+):(.+)", output)
    if match:
        entrypoint = match.group(3).strip().rstrip(".,;\\]})")
        while entrypoint and entrypoint[-1] in ('"', "'"):
            q = entrypoint[-1]
            if entrypoint.count(q) % 2 == 1:
                entrypoint = entrypoint[:-1]
            else:
                break
        if entrypoint.startswith('"') and entrypoint.endswith('"'):
            entrypoint = entrypoint[1:-1]
        return {
            "name": match.group(1).strip(),
            "port": int(match.group(2)),
            "entrypoint": entrypoint,
        }
    return None


# ---------------------------------------------------------------------------
# Workspace deliverables  (identical to openclaw-wrapper.py)
# ---------------------------------------------------------------------------

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".7z",
    ".whl", ".so", ".dll", ".pyc", ".pyo",
    ".mp3", ".mp4", ".wav", ".sqlite", ".db",
}


def _is_binary_file(fpath: str) -> bool:
    _, ext = os.path.splitext(fpath)
    if ext.lower() in BINARY_EXTENSIONS:
        return True
    try:
        with open(fpath, "rb") as f:
            chunk = f.read(8192)
        return b"\x00" in chunk
    except Exception:
        return True


def collect_workspace_files() -> Dict[str, str]:
    """Scan /workspace for deliverable files."""
    import base64
    workspace = "/workspace"
    SKIP_DIRS = {".git", "node_modules", ".openclaw", "__pycache__", ".cache", ".npm"}
    SKIP_FILES = {"result.json", "AGENTS.md", "SOUL.md", "TOOLS.md",
                  "IDENTITY.md", "USER.md", "HEARTBEAT.md", "BOOTSTRAP.md",
                  "package-lock.json"}
    MAX_FILE_SIZE = 500_000
    MAX_TOTAL = 2_000_000
    collected: Dict[str, str] = {}
    total_size = 0

    if not os.path.isdir(workspace):
        return collected

    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in sorted(files):
            if fname in SKIP_FILES:
                continue
            fpath = os.path.join(root, fname)
            relpath = os.path.relpath(fpath, workspace)
            try:
                size = os.path.getsize(fpath)
                if size == 0 or size > MAX_FILE_SIZE:
                    continue
                estimated_size = int(size * 1.37) if _is_binary_file(fpath) else size
                if total_size + estimated_size > MAX_TOTAL:
                    continue
                if _is_binary_file(fpath):
                    with open(fpath, "rb") as f:
                        raw = f.read()
                    content = "base64:" + base64.b64encode(raw).decode("ascii")
                    total_size += len(content)
                else:
                    with open(fpath, "r", errors="replace") as f:
                        content = f.read()
                    total_size += size
                collected[relpath] = content
            except Exception as e:
                print(f"  ⚠️  Could not read {relpath}: {e}")
    return collected


# ---------------------------------------------------------------------------
# Result writing  (identical to openclaw-wrapper.py)
# ---------------------------------------------------------------------------

RESULT_START = "===OPENCLAW_RESULT_JSON_START==="
RESULT_END = "===OPENCLAW_RESULT_JSON_END==="


def write_result(result: Dict[str, Any]):
    """Write result JSON to /workspace, /tmp, AND stdout (delimited)."""
    result_json = json.dumps(result, indent=2)
    for path in ["/workspace/result.json", "/tmp/result.json"]:
        try:
            with open(path, "w") as f:
                f.write(result_json)
        except Exception:
            pass
    print(f"\n{RESULT_START}")
    print(result_json)
    print(RESULT_END)


# ---------------------------------------------------------------------------
# Main  (same flow as openclaw-wrapper.py — only invoke step differs)
# ---------------------------------------------------------------------------

IMAGE_BANNERS = {
    "nanobot": ("⚡", "NANOBOT"),
    "zeroclaw": ("🦀", "ZEROCLAW"),
    "picoclaw": ("🐚", "PICOCLAW"),
}


def main():
    icon, name = IMAGE_BANNERS.get(IMAGE_TYPE, ("🤖", IMAGE_TYPE.upper()))

    print("=" * 80)
    print(f"{icon} {name} AGENT ADAPTER  (TaskForge-native)")
    print("=" * 80)
    print(f"📋 Task ID:       {TASK_ID}")
    print(f"🔄 Iteration:     {ITERATION}")
    print(f"🤖 Model:         {LLM_MODEL}")
    print(f"🌐 Control Plane: {CONTROL_PLANE_URL}")
    print(f"🔀 LLM Router:    {LLM_ROUTER_URL}")
    print(f"📦 Image Type:    {IMAGE_TYPE}")
    print("=" * 80)

    # Fetch task
    print("\n📥 Fetching task from control plane...")
    task = fetch_task()
    prompt = ""
    if task:
        prompt = task.get("description", "") or task.get("prompt", "")
        print(f"✅ Task fetched: {prompt[:150]}...")

    if not prompt:
        prompt = os.getenv("TASK_DESCRIPTION", "")
        if prompt:
            print(f"📝 Using TASK_DESCRIPTION env var: {prompt[:150]}...")

    if not prompt:
        print("❌ ERROR: No task description available", file=sys.stderr)
        write_result({"completed": False, "error": "No description in task and no TASK_DESCRIPTION env"})
        sys.exit(1)

    # Handle continuation / follow-up
    follow_up = os.getenv("FOLLOW_UP", "").strip()
    if follow_up:
        print(f"\n♻️  CONTINUATION — Follow-up instructions: {follow_up[:200]}")
        existing_files = []
        for root, dirs, files in os.walk("/workspace"):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), "/workspace")
                if not rel.startswith(".") and rel != "result.json":
                    existing_files.append(rel)
        files_context = ", ".join(existing_files[:30]) if existing_files else "none"
        prompt = (
            f"CONTINUATION: The previous run already produced these files "
            f"in /workspace: [{files_context}]. "
            f"Your job now is to IMPROVE the existing code based on these follow-up instructions:\n\n"
            f"{follow_up}\n\n"
            f"--- Original task description for reference ---\n{prompt}"
        )

    # Setup workspace context
    setup_workspace_context()

    # Invoke native agent loop
    print(f"\n🚀 Invoking {name} native agent loop...")
    output, exit_code = invoke_native_agent(prompt)

    print("\n" + "=" * 80)
    print(f"📊 {name} OUTPUT")
    print("=" * 80)
    print(output[:5000])
    if len(output) > 5000:
        print(f"... ({len(output)} total chars)")
    print("=" * 80)
    print(f"📤 Exit code: {exit_code}")

    # Build result
    result: Dict[str, Any] = {
        "completed": False,
        "capability_requested": False,
        "output": output[:50000],
        "agent_logs": output[:50000],
    }

    # Check for capability requests
    cap = parse_capability_request(output)
    if cap:
        cap_type, packages, cap_reason = cap
        print(f"\n🔐 Capability needed: {cap_type} → {packages}")
        print(f"   └─ Reason: {cap_reason}")

        if cap_type in ("tool_install", "python_packages", "pip_package"):
            actually_missing = []
            for pkg in packages:
                import_name = pkg.replace("-", "_")
                try:
                    result_check = subprocess.run(
                        ["python3", "-c", f"import {import_name}"],
                        capture_output=True, text=True, timeout=10
                    )
                    if result_check.returncode == 0:
                        print(f"   ✅ {pkg} is already installed, skipping")
                    else:
                        actually_missing.append(pkg)
                        print(f"   ❌ {pkg} is NOT installed")
                except Exception:
                    actually_missing.append(pkg)

            if not actually_missing:
                print(f"\n✅ All requested packages already installed")
                cap = None
            else:
                packages = actually_missing

    if cap:
        cap_type, packages, cap_reason = cap
        if request_capability(cap_type, packages, justification=cap_reason):
            print("✅ Capability requested — image rebuild required")
            result["capability_requested"] = True
            result["capability"] = {
                "type": "pip_package" if cap_type == "python_packages" else cap_type,
                "resource": ",".join(packages),
                "justification": cap_reason,
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
        result["completed"] = True
        result["deployment_requested"] = True
        result["deployment"] = deploy
        deliverables = collect_workspace_files()
        if deliverables:
            result["deliverables"] = deliverables
            result["deployment"]["files"] = deliverables
        result["message"] = f"Deployment requested: {deploy['name']}"
        write_result(result)
        sys.exit(0)

    # Detect LLM-level errors
    LLM_ERROR_MARKERS = ["MALFORMED_FUNCTION_CALL", "Unhandled stop reason", "function_call_filter"]
    if any(marker in output for marker in LLM_ERROR_MARKERS):
        print(f"\n⚠️  LLM error detected, marking as NOT completed")
        result["completed"] = False
        result["error"] = f"LLM error: {output[:500]}"
        result["agent_failed"] = False
        write_result(result)
        sys.exit(0)

    # Success / failure
    if exit_code == 0:
        result["completed"] = True
        result["message"] = "Task completed successfully"
        print("\n✅ Task completed successfully")
    else:
        result["error"] = output[:1000]
        print(f"\n❌ Task failed")

    # Collect deliverables
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
