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
NODE_ID = os.getenv("NODE_ID", "")  # For step-scoped deliverables in DAG nodes
# Whether the step must produce at least one deliverable file before the run
# may end. Mirrors config.deliverable_gate.require_deliverables on the worker;
# when false, a bare no-tool-call message ends the run as before.
DELIVERABLES_REQUIRED = os.getenv("DELIVERABLES_REQUIRED", "true").strip().lower() in ("1", "true", "yes", "y")

# Runtime description can be overridden via OPENCLAW_RUNTIME_DESCRIPTION env var
# (set in each image's Dockerfile). Falls back to get_runtime_description().
RUNTIME_DESCRIPTION_OVERRIDE = os.getenv("OPENCLAW_RUNTIME_DESCRIPTION")

MAX_TURNS = int(os.getenv("MAX_AGENT_TURNS", "30"))
TOOL_TIMEOUT = int(os.getenv("TOOL_TIMEOUT", "60"))

# Agent context-management mode (set from the LLM Providers page via
# AGENT_CONTEXT_MODE env):
#   "none"   — replay the full history each turn (quadratic cost)
#   "linear" — trim old turns into a condensed note so growth is bounded
#   "graph"  — short-term memory (last ~5 turns) raw; older turns folded into a
#              compressed JSON context graph (context-graph-compressor approach)
AGENT_CONTEXT_MODE = os.getenv("AGENT_CONTEXT_MODE", "none")
CONTEXT_BUDGET_TOKENS = int(os.getenv("AGENT_CONTEXT_BUDGET", "24000"))
KEEP_RECENT_MESSAGES = int(os.getenv("AGENT_KEEP_RECENT_MESSAGES", "16"))
SHORT_TERM_MESSAGES = int(os.getenv("AGENT_SHORT_TERM_MESSAGES", "12"))  # ~5 turns

# Condensed instructions for compressing old turns into a context graph
# (compact mode of context-graph-compressor).
_GRAPH_COMPRESS_SYSTEM = (
    "You compress an agent conversation into a minimal, portable JSON context graph "
    "so a model can resume with the most important state at minimal token cost.\n"
    "Node types: F=fact, D=decision, P=problem, G=goal, C=code (verbatim snippets), "
    "A=assumption (inferred — add conf), X=context/open thread.\n"
    "Importance: h=high (losing breaks continuity), m=medium, l=low.\n"
    "Status (only when meaningful): active, open, resolved, deferred, blocked, abandoned.\n"
    "Relationships (top-level \"rel\"): depends_on, caused_by, resolves, supersedes, "
    "references, related_to.\n"
    "Rules: code/method names/versions exact, never paraphrase; keep negatives "
    "('decided NOT to use X'); superseded != deleted (use supersedes + abandoned); "
    "assumptions explicit with conf; status over recency; open threads are nodes; "
    "strip conversational filler.\n"
    "Return ONLY valid JSON (compact):\n"
    '{"v":2,"mode":"compact","desc":"one tight sentence: topic and current state",'
    '"n":[{"id":"n1","t":"F","i":"h","s":"summary","c":[{"id":"n1.1","t":"A","i":"m","conf":0.8,"s":"..."}]}],'
    '"rel":[{"from":"n2","to":"n5","type":"depends_on"}],'
    '"handoff":"one paragraph telling a resuming model exactly where things stand and what is active/open/deferred."}\n'
    "Target 400-800 tokens total; hard cap 1200. If the existing graph + new turns "
    "overlap, dedupe (latest wins), link changes with supersedes, renumber n1..nN."
)


def _extract_json_object(text):
    """Robustly extract the first JSON object from an LLM response."""
    if not text:
        return None
    import re
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None


# In-agent graph state: how many leading `messages` entries are already folded
# into the compressed graph summary.
_graph_state: dict = {"summary": "", "folded_upto": 2}


def _call_graph_compress(client, completions_url, api_key, segment):
    """Fold `segment` (new turns) into the compressed context graph via one LLM call."""
    try:
        existing = _graph_state.get("summary") or "(none)"
        user = (
            "Existing compressed context graph:\n" + existing + "\n\n"
            "New conversation turns to merge into the graph:\n" + segment[:20000]
        )
        resp = client.post(
            completions_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": _GRAPH_COMPRESS_SYSTEM},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.1,
                "max_tokens": 2048,
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()
        content = (data.get("choices", [{}])[0].get("message", {}).get("content", "")) or ""
        graph = _extract_json_object(content)
        return graph if graph else None
    except Exception:
        return None


def _build_graph_send_messages(messages, client, completions_url, api_key):
    """In 'graph' mode: keep the short-term window raw and fold older turns into
    a compressed context graph. Returns the messages to send (the authoritative
    `messages` list is kept for continuation)."""
    if len(messages) <= 2 + SHORT_TERM_MESSAGES:
        return messages
    start = len(messages) - SHORT_TERM_MESSAGES
    while start > 2 and start < len(messages) and messages[start].get("role") == "tool":
        start -= 1
    if start > _graph_state.get("folded_upto", 2):
        new_segment = messages[_graph_state.get("folded_upto", 2):start]
        segment_text = "\n".join(
            f"[{m.get('role')}] {(m.get('content') or '')[:600]}" for m in new_segment
        )
        graph = _call_graph_compress(client, completions_url, api_key, segment_text)
        if graph:
            _graph_state["summary"] = graph
            _graph_state["folded_upto"] = start
        else:
            # Fallback: lossy text fold (like linear) so we never grow unbounded.
            folded_text = "\n".join(
                f"[{m.get('role')}] {(m.get('content') or '')[:400]}" for m in messages[2:start]
            )
            _graph_state["summary"] = "[Earlier conversation (condensed)]:\n" + folded_text[:8000]
            _graph_state["folded_upto"] = start

    out = messages[:2]
    if _graph_state.get("summary"):
        out.append({
            "role": "user",
            "content": "Context graph (compressed memory):\n" + _graph_state["summary"] + "\n\nResume from this state.",
        })
    out.extend(messages[_graph_state.get("folded_upto", 2):])
    return out


def _estimate_tokens(messages) -> int:
    """Rough token estimate (chars / 3.5) — no tiktoken dependency."""
    total = 0
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and isinstance(b.get("text"), str):
                    total += len(b["text"])
    return int(total / 3.5)


def _trim_messages(messages):
    """LangChain-style trim: always keep system + the original task prompt +
    the most recent turns (never starting on an orphaned tool result), and fold
    the older middle into a single condensed history note. Returns the list to
    SEND (the authoritative `messages` list is kept for continuation).
    """
    if not messages or _estimate_tokens(messages) <= CONTEXT_BUDGET_TOKENS:
        return messages
    if len(messages) <= 2 + KEEP_RECENT_MESSAGES:
        return messages
    keep_head = messages[:2]
    start = len(messages) - KEEP_RECENT_MESSAGES
    while start > 2 and start < len(messages) and messages[start].get("role") == "tool":
        start -= 1
    tail = messages[start:]
    mid = messages[2:start]

    folded_parts = []
    for m in mid:
        role = m.get("role") or "?"
        c = m.get("content") or ""
        if isinstance(c, list):
            c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("text"))
        snippet = str(c)[:400].replace("\n", " ")
        folded_parts.append(f"[{role}] {snippet}")
    summary = "\n".join(folded_parts)[:8000]

    out = list(keep_head)
    if summary:
        out.append({"role": "user", "content": "[Earlier conversation (condensed)]:\n" + summary})
    out.extend(tail)
    return out


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


# ---------------------------------------------------------------------------
# Package type classification — detect pip vs npm vs apt from package names
# ---------------------------------------------------------------------------
_NPM_KNOWN_PACKAGES = {
    "agent-browser", "express", "typescript", "ts-node", "prettier", "eslint",
    "webpack", "vite", "tailwindcss", "postcss", "autoprefixer", "react",
    "react-dom", "leaflet", "react-leaflet", "recharts", "puppeteer",
    "playwright", "cypress", "axios", "lodash", "next", "nuxt",
}

_APT_PATTERNS = ("-dev", "lib", "build-essential", "cmake", "gcc", "g++",
                 "make", "pkg-config", "libssl", "libcurl", "zlib")


def _classify_package(pkg_name: str) -> str:
    """Classify a package name as pip_package, npm_package, or apt_package."""
    # Scoped npm packages (@scope/pkg)
    if pkg_name.startswith("@"):
        return "npm_package"
    # Known npm packages
    if pkg_name in _NPM_KNOWN_PACKAGES:
        return "npm_package"
    # APT patterns
    if any(pkg_name.startswith(p) or pkg_name.endswith(p) for p in _APT_PATTERNS):
        return "apt_package"
    # Check if npm is available and the package exists there
    try:
        r = subprocess.run(["npm", "view", pkg_name, "name"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            # Also check it's NOT a valid pip package (prefer pip for ambiguous names)
            p = subprocess.run(["pip3", "show", pkg_name],
                               capture_output=True, text=True, timeout=10)
            if p.returncode != 0:
                return "npm_package"
    except Exception:
        pass
    return "pip_package"


def request_capability(capability_type: str, packages: List[str], justification: str = "",
                       typed_packages: Optional[List[Dict[str, str]]] = None) -> bool:
    """Request a new capability from the control plane."""
    # The control plane API uses CapabilityType enum which only has
    # tool_install, network_access, filesystem_access, database_access.
    # All package install types (pip, npm, apt) map to tool_install.
    TYPE_MAP = {
        "python_packages": "tool_install",
        "pip_package": "tool_install",
        "npm_packages": "tool_install",
        "npm_package": "tool_install",
        "system_packages": "tool_install",
        "apt_package": "tool_install",
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
            # Build per-package details — if typed_packages was supplied by
            # the caller, use it; otherwise fall back to plain name list.
            if typed_packages:
                packages_detail = typed_packages
            else:
                packages_detail = [
                    {"name": p, "type": _classify_package(p)}
                    for p in packages
                ]

            response = client.post(
                f"{CONTROL_PLANE_URL}/api/capabilities/requests",
                json={
                    "task_id": TASK_ID,
                    "capability_type": api_type,
                    "resource_name": resource_name,
                    "justification": full_justification,
                    "details": {
                        "packages": packages_detail,
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
    # Allow override via OPENCLAW_RUNTIME_DESCRIPTION env var (set in Dockerfile)
    if RUNTIME_DESCRIPTION_OVERRIDE:
        return RUNTIME_DESCRIPTION_OVERRIDE
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
    elif IMAGE_TYPE == "browser_v2":
        return (
            "- Python 3 (Debian, standard library)\n"
            "- `httpx` (HTTP client)\n"
            "- `curl`, `git`\n"
            "- `obscura` and `obscura-worker` for stealth browser automation\n"
            "- Browser-ready runtime inherited from the browser image\n"
        )
    elif IMAGE_TYPE == "browser_v3":
        return (
            "- Python 3 (Debian, standard library)\n"
            "- `httpx` (HTTP client)\n"
            "- `curl`, `git`\n"
            "- `lightpanda` CLI at /usr/local/bin/lightpanda (fast headless browser)\n"
            "- `agent-browser` and Chromium available as fallback\n"
            "- Browser-ready runtime with shared libraries\n"
        )
    elif IMAGE_TYPE == "browser":
        return (
            "- Python 3 (Debian, standard library)\n"
            "- `httpx` (HTTP client)\n"
            "- `curl`, `git`\n"
            "- `agent-browser` for browser automation\n"
            "- Browser-ready runtime with Chromium and shared libraries\n"
        )
    else:
        return (
            "- Python 3 (standard library)\n"
            "- `httpx` (HTTP client)\n"
            "- `curl`, `git`\n"
        )


def get_image_specific_guidance() -> str:
    """Return extra workflow guidance tailored to the active image type."""
    if IMAGE_TYPE == "browser_v2":
        return (
            "## Browser V2 Workflow\n\n"
            "Use the browser_v2 toolchain deliberately:\n"
            "- Prefer `obscura fetch --stealth <url>` for dynamic or protected pages.\n"
            "- Use Obscura first to discover the real report/document URLs from investor sites.\n"
            "- Once you have the direct PDF or HTML URLs, use `curl -L -o <file>` to download the source files.\n"
            "- Prefer `curl` for binary artifacts such as PDFs. Do NOT rely on Obscura stdout as a binary downloader.\n"
            "- For HTML pages, use Obscura `--eval` or `--dump text` to extract readable content.\n"
            "- For PDF analysis, first download the PDF to `/workspace`, then use local CLI tools or Python code to extract text.\n"
            "- Unless the task explicitly requires it, do NOT use `agent-browser` on browser_v2; Obscura plus direct downloads is the default path.\n"
        )
    if IMAGE_TYPE == "browser_v3":
        return (
            "## Browser V3 Workflow (Lightpanda)\n\n"
            "Lightpanda is a fast, low-memory headless browser. Use it as your primary browsing tool.\n"
            "- Fetch and render pages: `lightpanda fetch <url>` — outputs rendered HTML to stdout.\n"
            "- Pipe output to extract links: `lightpanda fetch <url> | grep -oP 'href=\"\\K[^\"]+\\.pdf'`\n"
            "- For JavaScript-heavy sites, Lightpanda executes JS during render.\n"
            "- Once you have a direct PDF URL, download it with `curl -L -o /workspace/file.pdf <url>`.\n"
            "- If Lightpanda cannot render a page (exit non-zero or empty output), fall back to `curl -L` or `agent-browser`.\n"
            "- Always validate that the URLs returned are absolute (start with http:// or https://). Resolve relative URLs against the base page URL.\n"
        )
    if IMAGE_TYPE == "browser":
        return (
            "## Browser Workflow\n\n"
            "Use browser automation only when a normal HTTP fetch is insufficient.\n"
            "For direct PDFs or static assets, prefer `curl -L -o <file>` over browser tooling.\n"
        )
    return ""


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
    image_guidance = get_image_specific_guidance()

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
{image_guidance}

### How to request a missing package

If any of these errors occur, emit a CAPABILITY_REQUEST and **STOP immediately**:

1. Python `ModuleNotFoundError` or `ImportError`
2. CLI command "not found" (exit code 127)
3. Shared library error: `error while loading shared libraries: libXYZ.so`

Format — always include the package type prefix (`pip/`, `npm/`, `apt/`, or `auto/`):
```
CAPABILITY_REQUEST:tool_install:<type>/<package_name>:<detailed reason why this package is needed>
```

Where `<type>` is one of:
- `pip` — Python packages (e.g. pandas, flask, requests)
- `npm` — Node.js packages (e.g. express, typescript, react)
- `apt` — System/OS packages and shared libraries (e.g. libglib2.0-0, ffmpeg, gcc)
- `auto` — Use when unsure (the system will auto-detect)

For shared library errors, use `apt/`:
```
CAPABILITY_REQUEST:tool_install:apt/libglib2.0-0:Shared library libglib-2.0.so.0 required by Chrome browser to launch
CAPABILITY_REQUEST:tool_install:apt/libnss3:Shared library libnss3.so required by Chrome browser
```

Other examples:
```
CAPABILITY_REQUEST:tool_install:pip/pandas:Data analysis library required to read CSV files and compute statistical aggregations
CAPABILITY_REQUEST:tool_install:pip/flask:Web microframework needed to build the HTTP API server
CAPABILITY_REQUEST:tool_install:npm/agent-browser:Browser automation tool required to interact with dynamic web pages
CAPABILITY_REQUEST:tool_install:apt/ffmpeg:Media processing tool required to convert audio files
```

After this line, STOP. The system will rebuild your container with the package
and re-run your task automatically.

**IMPORTANT**: Do NOT work around a missing tool or library by using a fallback approach.
If a command is not found or a library is missing, you MUST request it via
CAPABILITY_REQUEST and STOP. Do NOT attempt alternative methods.

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
- Deliverables: write final outputs to your node's deliverables directory (e.g. `/workspace/<node_id>/`, given in the task prompt); intermediates/raw fetched HTML to `/tmp` or `/workspace/.cache/`
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
        "Write final deliverables to your node's deliverables directory (e.g. /workspace/<node_id>/, given in the task prompt).",
        "Intermediate/raw files (fetched HTML pages, caches, scratch) go to /tmp or /workspace/.cache/ and are NOT collected.",
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
        "3. If the skill instructions include a complete, ready-to-run script, COPY it into your deliverables directory and EXECUTE it. Only modify it if it errors or the deliverable is wrong — do NOT rewrite or re-derive a working script from scratch; re-discovering what the skill already encodes is wasteful.",
        "4. If you get ModuleNotFoundError, 'command not found', or 'error while loading shared libraries', emit: CAPABILITY_REQUEST:tool_install:<type>/<pkg>:<reason> and STOP (type is pip, npm, apt, or auto)",
        "5. For web apps, emit: DEPLOYMENT_REQUEST:<name>:<port>:<entrypoint>",
        "6. Do NOT try pip install or apt-get — they will fail",
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


# Mirrors the include/exclude rules of collect_workspace_files() so the loop can
# decide cheaply (no file-content reads) whether the step has produced a
# deliverable yet. Counts any collectible file in the node dir (or the workspace
# root fallback), skipping bookkeeping files/dirs.
_STEP_DELIVERABLE_SKIP_DIRS = {".git", "node_modules", ".openclaw", "__pycache__", ".cache", ".npm"}
_STEP_DELIVERABLE_SKIP_FILES = {
    "result.json", "AGENTS.md", "SOUL.md", "TOOLS.md", "IDENTITY.md",
    "USER.md", "HEARTBEAT.md", "BOOTSTRAP.md", "package-lock.json",
    "input_prompt.md", "attached_context.md",
}


def _step_deliverable_dirs() -> list:
    if NODE_ID:
        return [os.path.join("/workspace", NODE_ID), "/workspace"]
    return ["/workspace"]


def _step_deliverables_exist() -> bool:
    for d in _step_deliverable_dirs():
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if name in _STEP_DELIVERABLE_SKIP_FILES or name in _STEP_DELIVERABLE_SKIP_DIRS:
                continue
            full = os.path.join(d, name)
            if os.path.isfile(full):
                return True
            if os.path.isdir(full):
                for _root, _dirs, files in os.walk(full):
                    for fn in files:
                        if fn in _STEP_DELIVERABLE_SKIP_FILES:
                            continue
                        return True
    return False


def invoke_native_agent(prompt: str) -> Tuple[str, int, str]:
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
    assistant_text_parts: List[str] = []
    turn = 0

    print(f"   🔗 LLM endpoint: {completions_url}")
    print(f"   🤖 Model: {LLM_MODEL}")
    print(f"   🔄 Max turns: {MAX_TURNS}")

    try:
        with httpx.Client(timeout=120.0) as client:
            while turn < MAX_TURNS:
                turn += 1
                print(f"\n── Turn {turn}/{MAX_TURNS} ──")

                # Stagnation nudge: if a deliverable is required and none exists
                # yet, once ~60% of the budget is spent, push the agent to write
                # instead of exploring (fires even while it keeps calling tools).
                if DELIVERABLES_REQUIRED and not _step_deliverables_exist():
                    budget_floor = max(1, int(MAX_TURNS * 0.6))
                    if turn >= budget_floor and (turn - budget_floor) % 3 == 0:
                        nudge = (
                            f"⏱ You have used {turn} of {MAX_TURNS} turns and still no "
                            f"deliverable in {_step_deliverable_dirs()[0]}. STOP exploring — "
                            f"write the deliverable file(s) now, then you may finish."
                        )
                        print(f"   ⚠ Nudge: {nudge}")
                        messages.append({"role": "user", "content": nudge})

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
                            "messages": (
                                _build_graph_send_messages(messages, client, completions_url, api_key)
                                if AGENT_CONTEXT_MODE == "graph"
                                else (_trim_messages(messages) if AGENT_CONTEXT_MODE == "linear" else messages)
                            ),
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
                    assistant_text_parts.append(content)

                    # Check for capability/deployment markers in text
                    if "CAPABILITY_REQUEST:" in content or "DEPLOYMENT_REQUEST:" in content:
                        print(f"   ⚡ Marker detected in assistant text, stopping loop")
                        break

                # Handle tool calls
                tool_calls = message.get("tool_calls", [])
                if not tool_calls:
                    # A bare text message ends the run ONLY when the step already
                    # has a deliverable (or deliverables aren't required).
                    # Otherwise nudge the agent to actually write its output so a
                    # premature textual "completion" cannot fail the acceptance
                    # gate with an empty deliverables directory.
                    if not DELIVERABLES_REQUIRED or _step_deliverables_exist():
                        if finish_reason == "stop":
                            print(f"   ✅ Agent finished (stop)")
                        break
                    nudge = (
                        f"You have NOT produced the required deliverable: no file "
                        f"exists in {_step_deliverable_dirs()[0]}. Write your "
                        f"deliverable file(s) there now with the write/exec tool "
                        f"before you finish. A reply without a tool call will not "
                        f"end the task."
                    )
                    print(f"   ⚠ Nudge: {nudge}")
                    messages.append({"role": "user", "content": nudge})
                    continue

                # Execute each tool call
                for tc in tool_calls:
                    func = tc.get("function", {})
                    tool_name = func.get("name", "")
                    try:
                        raw_args = func.get("arguments", "{}")
                        # Ollama returns arguments as a dict; OpenAI/Gemini as a JSON string
                        tool_args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)
                    except (json.JSONDecodeError, TypeError):
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

        if DELIVERABLES_REQUIRED and not _step_deliverables_exist():
            warn = "[WARN] Run ended without producing any deliverable in the step directory"
            print(f"   ⚠ {warn}")
            all_output_parts.append(warn)

        combined = "\n".join(all_output_parts)
        agent_output = ""
        if assistant_text_parts:
            agent_output = json.dumps(
                {"payloads": [{"text": text} for text in assistant_text_parts]},
                ensure_ascii=False,
            )
        return combined, 0, agent_output

    except Exception as e:
        error = f"Agent loop error: {e}\n{traceback.format_exc()}"
        print(f"   ❌ {error}")
        all_output_parts.append(error)
        return "\n".join(all_output_parts), 1, ""


# ---------------------------------------------------------------------------
# Capability / deployment detection  (identical to openclaw-wrapper.py)
# ---------------------------------------------------------------------------

def _parse_typed_package(raw: str) -> Tuple[str, str]:
    """Parse a package specifier that may have a type prefix.

    Formats handled:
      - "pip/pandas"   → ("pip_package", "pandas")
      - "npm/express"  → ("npm_package", "express")
      - "apt/libssl"   → ("apt_package", "libssl")
      - "auto/foo"     → ("auto", "foo")      — will be classified later
      - "pandas"       → ("auto", "pandas")   — legacy format, classify later
    """
    _PREFIX_MAP = {
        "pip": "pip_package",
        "npm": "npm_package",
        "apt": "apt_package",
        "apk": "apt_package",
        "auto": "auto",
    }
    if "/" in raw:
        prefix, _, name = raw.partition("/")
        prefix_lower = prefix.strip().lower()
        if prefix_lower in _PREFIX_MAP and name.strip():
            return _PREFIX_MAP[prefix_lower], name.strip()
    # No recognised prefix — return as-is for heuristic classification
    return "auto", raw.strip()


def parse_capability_request(output: str) -> Optional[Tuple[str, List[str], str]]:
    """Parse output for explicit CAPABILITY_REQUEST markers ONLY.

    Returns (cap_type, packages, reason) where *packages* may contain a
    type prefix ("pip/pandas") that callers should process via
    ``_parse_typed_package``.

    Capability requests are raised ONLY when the LLM explicitly emits a
    CAPABILITY_REQUEST marker. The previous heuristic fallbacks
    (ModuleNotFoundError / pip-install failure / "command not found" /
    shared-library scans) are removed: they produced false positives that
    blocked workflows on bogus approvals.
    """
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

    return None


def _auto_detect_deployment() -> Optional[Dict[str, Any]]:
    """Scan /workspace for a runnable web app and return deployment info.

    Checks for common web app patterns (Flask, FastAPI, Express, etc.)
    and returns a deployment dict if found.
    """
    workspace = "/workspace"

    # Pattern: (file_glob, content_pattern, entrypoint_template, default_port, app_name)
    detection_rules = [
        # Python Flask
        ("app.py", r"Flask\(__name__\)", "python3 app.py", 5000, "flask-app"),
        ("run.py", r"app\.run\(", "python3 run.py", 5000, "flask-app"),
        ("main.py", r"Flask\(__name__\)|uvicorn|FastAPI", "python3 main.py", 8000, "python-app"),
        ("server.py", r"Flask|FastAPI|uvicorn", "python3 server.py", 8000, "python-app"),
        # Node.js
        ("server.js", r"listen\(|createServer", "node server.js", 3000, "node-app"),
        ("app.js", r"listen\(|createServer|express", "node app.js", 3000, "node-app"),
        ("index.js", r"listen\(|createServer", "node index.js", 3000, "node-app"),
    ]

    for filename, pattern, entrypoint, port, name in detection_rules:
        filepath = os.path.join(workspace, filename)
        if os.path.exists(filepath):
            try:
                with open(filepath, "r") as f:
                    content = f.read()
                if re.search(pattern, content):
                    # Try to detect port from file content
                    port_match = re.search(r"\.run\([^)]*port\s*=\s*(\d+)", content)
                    if not port_match:
                        port_match = re.search(r"listen\(\s*(\d+)", content)
                    if port_match:
                        port = int(port_match.group(1))
                    print(f"   📁 Found {filename} matching {pattern}")
                    return {"name": name, "port": port, "entrypoint": entrypoint}
            except Exception:
                continue

    return None


def parse_deployment_request(output: str) -> Optional[Dict[str, Any]]:
    """Parse output for DEPLOYMENT_REQUEST markers."""
    match = re.search(r"DEPLOYMENT_REQUEST:([^:]+):(\d+):(.+)", output)
    if match:
        entrypoint = match.group(3).strip()
        # Strip markdown bold markers and other trailing punctuation
        entrypoint = entrypoint.strip("*")
        entrypoint = entrypoint.rstrip(".,;\\]})")
        while entrypoint and entrypoint[-1] in ('"', "'"):
            q = entrypoint[-1]
            if entrypoint.count(q) % 2 == 1:
                entrypoint = entrypoint[:-1]
            else:
                break
        if entrypoint.startswith('"') and entrypoint.endswith('"'):
            entrypoint = entrypoint[1:-1]
        entrypoint = entrypoint.strip()
        return {
            "name": match.group(1).strip().strip("*"),
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


def _make_file_ref(fpath: str, size: int) -> dict:
    """Return a metadata reference for a deliverable too large to embed.

    Keeps DB/Temporal payloads small while keeping the step's deliverables
    non-empty, and gives downstream steps a real path into the shared
    workspace to read the file from.
    """
    import hashlib
    digest = hashlib.sha256()
    try:
        with open(fpath, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
    except Exception:
        pass
    return {"ref": "file", "path": fpath, "size": size, "sha256": digest.hexdigest()}


def _scan_workspace_tree(scan_root: str, workspace: str, SKIP_DIRS, SKIP_FILES,
                         MAX_FILE_SIZE: int, MAX_TOTAL: int, collected: Dict[str, Any], total_size: int):
    """Walk scan_root collecting files into `collected`. Returns updated total_size."""
    import base64
    for root, dirs, files in os.walk(scan_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in sorted(files):
            if fname in SKIP_FILES:
                continue
            fpath = os.path.join(root, fname)
            relpath = os.path.relpath(fpath, scan_root)
            try:
                size = os.path.getsize(fpath)
                if size == 0:
                    continue
                estimated_size = int(size * 1.37) if _is_binary_file(fpath) else size
                if size > MAX_FILE_SIZE or total_size + estimated_size > MAX_TOTAL:
                    # Too large to embed — record a file reference so the step
                    # still delivers (the file exists in the shared workspace).
                    collected[relpath] = _make_file_ref(fpath, size)
                    total_size += 160
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
    return total_size


def collect_workspace_files(node_id: str | None = None) -> Dict[str, Any]:
    """Scan the workspace for deliverable files.

    For DAG nodes (node_id set) /workspace/{node_id}/ is scanned so files written
    by PARALLEL steps sharing the workspace are NOT mixed in. If the node dir is
    empty (agent wrote outputs to the workspace root, common for older skills),
    fall back to collecting root-level files directly in /workspace so the step
    still delivers. For legacy non-DAG tasks the whole /workspace is scanned.
    """
    workspace = "/workspace"
    SKIP_DIRS = {".git", "node_modules", ".openclaw", "__pycache__", ".cache", ".npm"}
    SKIP_FILES = {"result.json", "AGENTS.md", "SOUL.md", "TOOLS.md",
                  "IDENTITY.md", "USER.md", "HEARTBEAT.md", "BOOTSTRAP.md",
                  "package-lock.json", "input_prompt.md", "attached_context.md"}
    MAX_FILE_SIZE = 500_000
    MAX_TOTAL = 2_000_000
    collected: Dict[str, Any] = {}
    total_size = 0

    scan_root = os.path.join(workspace, node_id) if node_id else workspace
    if os.path.isdir(scan_root):
        total_size = _scan_workspace_tree(scan_root, workspace, SKIP_DIRS, SKIP_FILES,
                                          MAX_FILE_SIZE, MAX_TOTAL, collected, total_size)

    # Fallback: node dir empty but the agent wrote to the workspace root.
    # Only root-level files are collected (no descent into .cache/, steps/, or
    # sibling node dirs) so parallel steps' subdirs are not pulled in.
    if node_id and not collected and os.path.isdir(workspace):
        try:
            for fname in sorted(os.listdir(workspace)):
                if fname.startswith(".") or fname in SKIP_FILES:
                    continue
                fpath = os.path.join(workspace, fname)
                if not os.path.isfile(fpath):
                    continue
                relpath = fname
                size = os.path.getsize(fpath)
                if size == 0:
                    continue
                estimated_size = int(size * 1.37) if _is_binary_file(fpath) else size
                if size > MAX_FILE_SIZE or total_size + estimated_size > MAX_TOTAL:
                    collected[relpath] = _make_file_ref(fpath, size)
                    total_size += 160
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
            print(f"  ⚠️  Fallback scan failed: {e}")
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

    # Prepend skill instructions if available (from ClawHub SKILL.md)
    skill_instructions = os.getenv("SKILL_INSTRUCTIONS", "").strip()
    if skill_instructions:
        print(f"\n📚 Skill instructions loaded ({len(skill_instructions)} chars)")
        prompt = (
            f"=== SKILL INSTRUCTIONS ===\n"
            f"Follow these setup and usage instructions for this task:\n\n"
            f"{skill_instructions}\n\n"
            f"=== TASK ===\n"
            f"{prompt}"
        )

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
    output, exit_code, agent_output = invoke_native_agent(prompt)

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
        "output": agent_output[:50000] if agent_output else output[:50000],
        "agent_logs": output[:50000],
    }

    # Check for deployment request FIRST — if the agent emitted a
    # DEPLOYMENT_REQUEST it has already resolved any issues (e.g. removed
    # gunicorn) and we should not let a stale ModuleNotFoundError from
    # earlier in the log override its final intent.
    deploy = parse_deployment_request(output)
    if deploy:
        print(f"\n🚀 Deployment requested: {deploy['name']} on port {deploy['port']}")
        result["completed"] = True
        result["deployment_requested"] = True
        result["deployment"] = deploy
        deliverables = collect_workspace_files(NODE_ID or None)
        if deliverables:
            result["deliverables"] = deliverables
            result["deployment"]["files"] = deliverables
        result["message"] = f"Deployment requested: {deploy['name']}"
        write_result(result)
        sys.exit(0)

    # Auto-detect deployment: if the task asks for deployment but the agent
    # didn't emit a DEPLOYMENT_REQUEST marker, scan workspace for a runnable
    # web app and auto-generate the deployment request.
    # Skip if agent emitted a CAPABILITY_REQUEST — deps aren't installed yet.
    has_capability_request = "CAPABILITY_REQUEST:" in output
    if exit_code == 0 and not deploy and not has_capability_request:
        task_text = (prompt + " " + (follow_up or "")).lower()
        deploy_keywords = ["deploy", "deployment", "containerize", "containerised",
                           "containerized", "serve", "service", "production"]
        if any(kw in task_text for kw in deploy_keywords):
            auto_deploy = _auto_detect_deployment()
            if auto_deploy:
                print(f"\n🤖 Auto-detected deployment: {auto_deploy['name']} on port {auto_deploy['port']}")
                result["completed"] = True
                result["deployment_requested"] = True
                result["deployment"] = auto_deploy
                deliverables = collect_workspace_files(NODE_ID or None)
                if deliverables:
                    result["deliverables"] = deliverables
                    result["deployment"]["files"] = deliverables
                result["message"] = f"Auto-detected deployment: {auto_deploy['name']}"
                write_result(result)
                sys.exit(0)

    # Check for capability requests (only if no deployment request)
    cap = parse_capability_request(output)
    # Guard against stale heuristic hits: the agent may probe imports/tools
    # early in a run (e.g. require('agent-browser')) and later complete
    # successfully using the correct CLI path. In that case, do not create
    # a capability request from fallback ModuleNotFoundError parsing.
    if cap and exit_code == 0 and cap[2] == "ModuleNotFoundError detected":
        print("\n⚠️ Ignoring stale ModuleNotFoundError capability signal because task exited successfully")
        cap = None

    if cap:
        cap_type, packages, cap_reason = cap
        print(f"\n🔐 Capability needed: {cap_type} → {packages}")
        print(f"   └─ Reason: {cap_reason}")

        # Classify each package by its manager type — honour type prefix if present
        classified = {}
        clean_names = []   # package names without the type/ prefix
        for pkg in packages:
            typed, name = _parse_typed_package(pkg)
            if typed != "auto":
                classified[name] = typed
            else:
                classified[name] = _classify_package(name)
            clean_names.append(name)
            print(f"   📦 {pkg} → {classified[name]}")

        # Only check Python imports for pip packages
        actually_missing = []
        for pkg in clean_names:
            if classified[pkg] == "pip_package":
                import_name = pkg.replace("-", "_")
                try:
                    result_check = subprocess.run(
                        ["python3", "-c", f"import {import_name}"],
                        capture_output=True, text=True, timeout=10
                    )
                    if result_check.returncode == 0:
                        print(f"   ✅ {pkg} is already installed (pip), skipping")
                        continue
                    else:
                        print(f"   ❌ {pkg} is NOT installed (pip)")
                except Exception:
                    pass
            elif classified[pkg] == "npm_package":
                # Check if npm package is available
                try:
                    result_check = subprocess.run(
                        ["which", pkg] if "/" not in pkg else ["npm", "list", "-g", pkg],
                        capture_output=True, text=True, timeout=10
                    )
                    if result_check.returncode == 0:
                        print(f"   ✅ {pkg} is already installed (npm), skipping")
                        continue
                    else:
                        print(f"   ❌ {pkg} is NOT installed (npm)")
                except Exception:
                    pass
            elif classified[pkg] == "apt_package":
                # Check if apt package is installed
                try:
                    result_check = subprocess.run(
                        ["dpkg", "-s", pkg],
                        capture_output=True, text=True, timeout=10
                    )
                    if result_check.returncode == 0:
                        print(f"   ✅ {pkg} is already installed (apt), skipping")
                        continue
                    else:
                        print(f"   ❌ {pkg} is NOT installed (apt)")
                except Exception:
                    pass
            actually_missing.append(pkg)

        if not actually_missing:
            print(f"\n✅ All requested packages already installed")
            cap = None
        else:
            clean_names = actually_missing

    if cap:
        cap_type, _raw_packages, cap_reason = cap

        # Build per-package typed list for the details payload
        typed_packages = [
            {"name": p, "type": classified.get(p, _classify_package(p))}
            for p in clean_names
        ]

        # Use the dominant type for the top-level capability request
        pkg_types = [tp["type"] for tp in typed_packages]
        dominant_type = max(set(pkg_types), key=pkg_types.count) if pkg_types else "pip_package"

        if request_capability(dominant_type, clean_names, justification=cap_reason,
                              typed_packages=typed_packages):
            print("✅ Capability requested — image rebuild required")
            result["capability_requested"] = True
            result["capability"] = {
                "type": dominant_type,
                "resource": ",".join(clean_names),
                "justification": cap_reason,
            }
            write_result(result)
            sys.exit(0)
        else:
            print("❌ Capability request failed")
            result["error"] = "Required capability denied"
            write_result(result)
            sys.exit(1)

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
    deliverables = collect_workspace_files(NODE_ID or None)
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
