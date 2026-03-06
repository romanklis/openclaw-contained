"""
OpenClaw API Gateway — OpenAI-Compatible Chat Completions
=========================================================

Translates stateless OpenAI-format chat requests into stateful
Temporal Workflow operations (create task / continue task).

Streaming flow
--------------
1.  Parse the incoming request; resolve / create a conversation session.
2.  NEW conversation  → POST /api/tasks        → start AgentTaskWorkflow
    EXISTING session  → POST /api/tasks/{id}/continue → add iteration
3.  Poll task outputs and stream them as Server-Sent Events in the exact
    OpenAI chunk format that any compatible client (Open WebUI, LibreChat,
    Chainlit, curl …) understands.
4.  When the Temporal workflow reaches a terminal state (completed/failed),
    the SSE stream is closed and the session is updated.

Session management
------------------
Conversation ID resolution order:
  1. ``X-Conversation-ID`` HTTP header (explicit client control)
  2. Deterministic hash of  model + system-prompt + first-user-message
     → reconnecting after a browser refresh reuses the same workflow.

The session record is stored in Redis (if available) or an in-memory dict:
  {
    "task_id": "task-xxxxxxxx",
    "status":  "completed"       # last known; always re-verified from CP
  }
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator, List, Optional, Set

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from config import settings
from control_plane_client import ControlPlaneClient
from schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    ModelCard,
    ModelList,
    NonStreamChoice,
    Usage,
)
from session_manager import SessionStore

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(name)-20s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("api-gateway")

# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

session_store = SessionStore(redis_url=settings.REDIS_URL)
cp = ControlPlaneClient(settings.CONTROL_PLANE_URL)

TERMINAL_STATUSES: Set[str] = {"completed", "failed", "cancelled"}


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _sse(
    completion_id: str,
    model: str,
    content: Optional[str] = None,
    role: Optional[str] = None,
    finish_reason: Optional[str] = None,
) -> str:
    """Build a single ``data: {...}\\n\\n`` SSE line in OpenAI chunk format."""
    delta: dict = {}
    if role is not None:
        delta["role"] = role
    if content is not None:
        delta["content"] = content

    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
                "logprobs": None,
            }
        ],
    }
    return f"data: {json.dumps(payload)}\n\n"


def _sse_done() -> str:
    return "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Fast-path: detect lightweight LLM-only requests from Open WebUI
# ---------------------------------------------------------------------------

# Open WebUI automatically sends "meta-requests" after each conversation turn
# for title generation, tag generation, follow-up suggestions, etc.  These are
# simple LLM prompts that should NOT spin up a full TaskForge task (Temporal
# workflow → Docker container → agent runtime).  Instead we proxy them directly
# through the control-plane's LLM router — response time drops from 30s to ~2s.

_FAST_PATH_PATTERNS = [
    "generate 1-3 broad tags",
    "generate a concise, 3-5 word title",
    "suggest 3-5 relevant follow-up questions",
    "analyze the chat history to determine the necessity",
    "generate search query",
    "### task:",
]


def _is_fast_path_request(messages: List[ChatMessage]) -> bool:
    """Return True if this looks like an Open WebUI internal meta-request
    that can be answered by a direct LLM call (no agent container needed)."""
    if not messages:
        return False
    # Check the last user message (or the only message)
    last_user = next((m for m in reversed(messages) if m.role == "user"), None)
    if not last_user:
        return False
    text = last_user.text().lower().strip()

    # Quick structural check: Open WebUI meta-requests contain ### Task:
    # headers and ### Output: / ### Chat History: sections.
    if "### task:" not in text and "### guidelines:" not in text:
        return False

    for pattern in _FAST_PATH_PATTERNS:
        if pattern in text:
            return True
    return False


async def _fast_path_streaming(
    body: ChatCompletionRequest,
    llm_model: str,
) -> AsyncGenerator[str, None]:
    """Stream an LLM response directly from the control-plane router (no task)."""
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    # Build the payload for the LLM router
    payload = {
        "model": llm_model,
        "messages": [
            {"role": m.role, "content": m.text()} for m in body.messages
        ],
        "stream": True,
    }
    if body.temperature is not None:
        payload["temperature"] = body.temperature

    try:
        async for line in cp.llm_chat_completions_stream(payload):
            line = line.strip()
            if not line:
                continue
            # The LLM router already returns OpenAI-format SSE lines.
            # Forward them as-is (they include "data: " prefix).
            if line.startswith("data: "):
                yield line + "\n\n"
            elif line == "[DONE]":
                yield "data: [DONE]\n\n"
            else:
                # Some routers emit bare JSON — wrap it
                yield f"data: {line}\n\n"
    except Exception as exc:
        logger.error("Fast-path streaming error: %s", exc)
        yield _sse(completion_id, llm_model, content=f"\n❌ LLM error: {exc}")
        yield _sse(completion_id, llm_model, finish_reason="stop")
        yield _sse_done()


async def _fast_path_non_streaming(
    body: ChatCompletionRequest,
    llm_model: str,
) -> ChatCompletionResponse:
    """Call the LLM router directly and return a full response (no task)."""
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    payload = {
        "model": llm_model,
        "messages": [
            {"role": m.role, "content": m.text()} for m in body.messages
        ],
        "stream": False,
    }
    if body.temperature is not None:
        payload["temperature"] = body.temperature

    resp = await cp.llm_chat_completions(payload, stream=False)
    data = resp.json()

    # Extract the response text from the standard OpenAI format
    content = ""
    choices = data.get("choices", [])
    if choices:
        content = choices[0].get("message", {}).get("content", "")

    return ChatCompletionResponse(
        id=completion_id,
        model=body.model,
        choices=[NonStreamChoice(message=ChatMessage(role="assistant", content=content))],
        usage=Usage(
            prompt_tokens=data.get("usage", {}).get("prompt_tokens", 0),
            completion_tokens=data.get("usage", {}).get("completion_tokens", 0),
            total_tokens=data.get("usage", {}).get("total_tokens", 0),
        ),
    )


def _extract_agent_text(output: dict) -> str:
    """Extract the actual agent response text from a TaskOutput dict.

    The OpenClaw agent produces a JSON payload stored in the ``output``
    field (string) of the task-output record:

        {"payloads": [{"text": "ACTUAL_RESPONSE", ...}], "meta": {...}}

    The ``llm_response_preview`` field is NOT the real reply — it's a
    short status label set by the wrapper (e.g. "Task completed successfully").

    Extraction priority:
      1. ``output`` field  → parse JSON → ``payloads[*].text``
      2. ``raw_result.output`` → same structure (copy kept by temporal)
      3. ``llm_response_preview``  → last-resort fallback
    """
    _decoder = json.JSONDecoder()

    # ── 1. Try the top-level "output" field (JSON string with payloads) ───
    for source in (output.get("output"), (output.get("raw_result") or {}).get("output")):
        if not source or not isinstance(source, str):
            continue
        stripped = source.strip()
        if not stripped or stripped[0] != "{":
            continue
        try:
            # Use raw_decode to tolerate trailing non-JSON (e.g. shell errors
            # appended by the agent wrapper after the JSON payload).
            parsed, _end = _decoder.raw_decode(stripped)
            if isinstance(parsed, dict):
                texts = [
                    p.get("text", "")
                    for p in parsed.get("payloads", [])
                    if p.get("text")
                ]
                if texts:
                    return "\n".join(texts).strip()
        except (json.JSONDecodeError, TypeError):
            pass

    # ── 2. Fallback: llm_response_preview ─────────────────────────────────
    preview = (output.get("llm_response_preview") or "").strip()
    # Ignore generic status messages that don't carry real content
    _GENERIC = {"task completed successfully", "task failed", "task cancelled"}
    if preview and preview.lower() not in _GENERIC:
        return preview

    return ""


def _format_iteration(output: dict, task_id: str = "unknown") -> str:
    """Convert a TaskOutput dict into readable Markdown text for streaming."""
    parts: list[str] = []
    iteration = output.get("iteration", "?")
    duration_ms = output.get("duration_ms")
    model_used = output.get("model_used", "")
    image_used = output.get("image_used", "")

    # ── Header ──────────────────────────────────────────────────────────────
    header = f"\n\n---\n**⚙️ Iteration {iteration}**"
    if duration_ms:
        header += f" · ⏱ {duration_ms / 1000:.1f}s"
    if model_used:
        header += f" · 🤖 `{model_used}`"
    if image_used:
        short_image = image_used.split("/")[-1] if "/" in image_used else image_used
        header += f" · 🐳 `{short_image}`"
    parts.append(header + "\n\n")

    # ── Main content ─────────────────────────────────────────────────────────
    content = _extract_agent_text(output)

    if content:
        parts.append(content)
    elif output.get("error"):
        parts.append(f"⚠️  **Error:** {output['error'][:400]}")
    else:
        parts.append("*(iteration completed — no text output)*")

    # ── Deliverables ─────────────────────────────────────────────────────────
    deliverables = output.get("deliverables") or {}
    if deliverables and isinstance(deliverables, dict):
        file_lines: list[str] = []
        for fname in deliverables.keys():
            # Build an absolute gateway download URL so Open WebUI can offer a real link
            base = settings.GATEWAY_PUBLIC_URL.rstrip("/")
            dl_url = f"{base}/v1/files/{task_id}/{iteration}/{fname}"
            file_lines.append(f"  - 📄 [{fname}]({dl_url})")
        parts.append(f"\n\n📦 **Deliverables:**\n" + "\n".join(file_lines))

    # ── Capability gate ──────────────────────────────────────────────────────
    if output.get("capability_requested") in ("true", True):
        raw = output.get("raw_result") or {}
        cap = raw.get("capability", {}) if isinstance(raw, dict) else {}
        cap_resource = cap.get("resource", "unknown")
        cap_type = cap.get("type", "tool_install")
        cap_reason = cap.get("justification", "")

        # Package badges
        pkgs = [p.strip() for p in cap_resource.split(",") if p.strip()]
        pkg_badges = " ".join(f"`{p}`" for p in pkgs) if pkgs else f"`{cap_resource}`"
        type_label = cap_type.replace("_", " ").title()

        parts.append(f"\n\n---")
        parts.append(f"\n🔒 **Capability Request: {type_label}**")
        parts.append(f"\n📦 Packages: {pkg_badges}")
        if cap_reason:
            # Truncate long reasons
            reason_short = cap_reason[:300] + ("…" if len(cap_reason) > 300 else "")
            parts.append(f"\n💬 *{reason_short}*")

        # Direct link to the approval page in the dashboard
        dashboard = settings.DASHBOARD_URL.rstrip("/")
        approve_url = f"{dashboard}/approvals?task_id={task_id}"
        parts.append(
            f"\n\n👉 **[Review & Approve in Dashboard]({approve_url})**"
        )
        parts.append(
            "\n\n⏳ *Waiting for approval — the agent will resume automatically "
            "once approved and the new image is built.*"
        )

    # ── Deployment request ───────────────────────────────────────────────────
    raw = output.get("raw_result") or {}
    if isinstance(raw, dict) and raw.get("deployment_requested"):
        dep = raw.get("deployment") or {}
        dep_name = dep.get("name", "unnamed")
        dep_port = dep.get("port", "?")
        dep_entry = dep.get("entrypoint", "?")
        dep_files = dep.get("files", {})

        parts.append(f"\n\n🚀 **Deployment Requested: `{dep_name}`**")
        parts.append(f"\n  - **Port:** {dep_port}")
        parts.append(f"\n  - **Entrypoint:** `{dep_entry}`")
        if dep_files:
            file_list = ", ".join(f"`{f}`" for f in dep_files.keys())
            parts.append(f"\n  - **Files:** {file_list}")
        parts.append(
            "\n\n📋 *The deployment is pending approval in the TaskForge dashboard. "
            "Approve it to build and run the application.*"
        )

    parts.append("\n")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Turn-by-turn formatting
# ---------------------------------------------------------------------------

_TOOL_ICONS = {
    "exec": "⚡", "bash": "⚡", "execute": "⚡", "run": "⚡",
    "write": "📝", "write_file": "📝", "writefile": "📝", "create_file": "📝",
    "read": "📖", "read_file": "📖", "readfile": "📖",
    "edit": "✏️", "edit_file": "✏️", "patch": "✏️",
    "browser": "🌐", "browse": "🌐", "web": "🌐",
    "process": "🔄", "nodes": "🧩", "canvas": "🎨",
}


def _format_iteration_summary(output: dict, task_id: str = "unknown") -> str:
    """Compact summary appended after turn-by-turn streaming.

    Shows duration, deliverables, capability / deployment requests —
    but NOT the header or full agent text (those were already streamed).
    """
    parts: list[str] = []
    duration_ms = output.get("duration_ms")

    if duration_ms:
        parts.append(f"\n✅ *Iteration done in {duration_ms / 1000:.1f}s*\n")

    # Agent's final text (short preview only — turns already showed the work)
    content = _extract_agent_text(output)
    if content and len(content) > 20:
        preview = content[:200].replace("\n", " ")
        parts.append(f"\n💬 *{preview}*\n")

    # Deliverables
    deliverables = output.get("deliverables") or {}
    if deliverables and isinstance(deliverables, dict):
        iteration = output.get("iteration", "?")
        base = settings.GATEWAY_PUBLIC_URL.rstrip("/")
        file_lines = [
            f"  - 📄 [{fname}]({base}/v1/files/{task_id}/{iteration}/{fname})"
            for fname in deliverables.keys()
        ]
        parts.append(f"\n📦 **Deliverables:**\n" + "\n".join(file_lines))

    # Capability request
    if output.get("capability_requested") in ("true", True):
        raw = output.get("raw_result") or {}
        cap = raw.get("capability", {}) if isinstance(raw, dict) else {}
        cap_resource = cap.get("resource", "unknown")
        cap_type = cap.get("type", "tool_install")
        cap_reason = cap.get("justification", "")

        pkgs = [p.strip() for p in cap_resource.split(",") if p.strip()]
        pkg_badges = " ".join(f"`{p}`" for p in pkgs) if pkgs else f"`{cap_resource}`"
        type_label = cap_type.replace("_", " ").title()

        parts.append(f"\n---")
        parts.append(f"\n🔒 **Capability Request: {type_label}**")
        parts.append(f"\n📦 Packages: {pkg_badges}")
        if cap_reason:
            reason_short = cap_reason[:300] + ("…" if len(cap_reason) > 300 else "")
            parts.append(f"\n💬 *{reason_short}*")

        dashboard = settings.DASHBOARD_URL.rstrip("/")
        approve_url = f"{dashboard}/approvals?task_id={task_id}"
        parts.append(f"\n\n👉 **[Review & Approve in Dashboard]({approve_url})**")
        parts.append(
            "\n\n⏳ *Waiting for approval — the agent will resume automatically "
            "once approved and the new image is built.*"
        )

    # Deployment request
    raw = output.get("raw_result") or {}
    if isinstance(raw, dict) and raw.get("deployment_requested"):
        dep = raw.get("deployment") or {}
        dep_name = dep.get("name", "unnamed")
        dep_port = dep.get("port", "?")
        dep_entry = dep.get("entrypoint", "?")
        dep_files = dep.get("files", {})

        parts.append(f"\n\n🚀 **Deployment Requested: `{dep_name}`**")
        parts.append(f"\n  - **Port:** {dep_port}")
        parts.append(f"\n  - **Entrypoint:** `{dep_entry}`")
        if dep_files:
            file_list = ", ".join(f"`{f}`" for f in dep_files.keys())
            parts.append(f"\n  - **Files:** {file_list}")

    parts.append("\n")
    return "".join(parts)


def _format_turn_line(turn_data: dict, turn_number: int) -> str:
    """Format a single LLM turn into a compact progress line for the chat stream.

    Examples of output:
      ⚡ exec — `pip install flask`
      📝 write — `app.py` (1,240 chars)
      🌐 browser — navigating to localhost:5000
      💬 Agent: "I've created the application…"
    """
    resp = turn_data.get("response", {})
    tool_calls = resp.get("tool_calls", [])
    content = (resp.get("content") or "").strip()

    if tool_calls:
        lines = []
        for tc in tool_calls:
            name = tc.get("name", "?")
            icon = _TOOL_ICONS.get(name.lower(), "🔧")
            args = tc.get("arguments", {})
            detail = ""

            if isinstance(args, dict):
                name_lc = name.lower()
                if name_lc in ("exec", "bash", "execute", "run"):
                    cmd = str(args.get("command", args.get("cmd", "")))
                    if cmd:
                        # Show first line, truncated
                        first_line = cmd.split("\n")[0][:80]
                        detail = f"`{first_line}`"
                elif name_lc in ("write", "write_file", "writefile", "create_file"):
                    fpath = args.get("file_path", args.get("path", ""))
                    size = len(args.get("content", args.get("file_text", "")))
                    fname = fpath.split("/")[-1] if "/" in fpath else fpath
                    detail = f"`{fname}`" + (f" ({size:,} chars)" if size else "")
                elif name_lc in ("read", "read_file", "readfile"):
                    fpath = args.get("file_path", args.get("path", ""))
                    fname = fpath.split("/")[-1] if "/" in fpath else fpath
                    detail = f"`{fname}`"
                elif name_lc in ("edit", "edit_file", "patch"):
                    fpath = args.get("file_path", args.get("path", ""))
                    fname = fpath.split("/")[-1] if "/" in fpath else fpath
                    detail = f"`{fname}`"
                elif name_lc in ("browser", "browse"):
                    url = args.get("url", args.get("address", ""))
                    if url:
                        detail = url[:60]
                else:
                    # Generic: show tool name and key arg
                    first_key = next(iter(args), None)
                    if first_key:
                        val = str(args[first_key])[:50]
                        detail = f"{first_key}={val}"

            line = f"{icon} **{name}**"
            if detail:
                line += f" — {detail}"
            lines.append(line)

        return " · ".join(lines) + "\n"

    elif content:
        # LLM text response (no tool call) — show a short preview
        # This is often CAPABILITY_REQUEST or final answer
        preview = content[:150].replace("\n", " ")
        if "CAPABILITY_REQUEST" in preview:
            return ""  # will be handled by the capability lifecycle
        return f"💬 *{preview}*\n"

    return ""


# ---------------------------------------------------------------------------
# Core streaming generator
# ---------------------------------------------------------------------------


async def stream_task_execution(
    task_id: str,
    completion_id: str,
    model: str,
    conv_id: str,
    skip_iterations: Optional[Set[int]] = None,
) -> AsyncGenerator[str, None]:
    """
    Poll the control-plane for task outputs and stream them as OpenAI SSE.

    Yields chunks until:
    - the task reaches a terminal state (completed / failed / cancelled), or
    - the hard deadline (STREAM_TIMEOUT_SECONDS) is hit.

    ``skip_iterations`` — iteration numbers that were already streamed
    in a previous turn (e.g. when the user sends a follow-up).  These
    will not be emitted again.

    Updates the session store with the final task status before returning.
    """
    seen_iterations: Set[int] = set(skip_iterations or set())
    deadline = time.monotonic() + settings.STREAM_TIMEOUT_SECONDS
    waiting_emitted = False
    consecutive_errors = 0

    # Track capability request lifecycle for status updates
    _cap_pending = False         # True while waiting for approval + build
    _cap_approved_emitted = False
    _cap_building_emitted = False
    _cap_last_status: str | None = None

    # Turn-by-turn tracking
    _turns_seen = 0              # Number of LLM turns already streamed
    _iter_header_emitted = False # Whether we emitted the current iteration header
    _current_iter: int | None = None  # Track which iteration we're showing turns for

    # OpenAI protocol requires the *first* delta to carry the role.
    yield _sse(completion_id, model, role="assistant", content="")
    yield _sse(completion_id, model, content=f"🚀 Task `{task_id}` is running…\n")

    while time.monotonic() < deadline:
        # ── Fetch task status ──────────────────────────────────────────────
        try:
            task = await cp.get_task(task_id)
            consecutive_errors = 0
        except httpx.HTTPStatusError as exc:
            yield _sse(
                completion_id,
                model,
                content=f"\n❌ Control-plane error {exc.response.status_code} — aborting stream.\n",
                finish_reason="stop",
            )
            yield _sse_done()
            return
        except Exception as exc:
            consecutive_errors += 1
            if consecutive_errors >= 5:
                yield _sse(
                    completion_id,
                    model,
                    content=f"\n❌ Control-plane unreachable ({exc}) — aborting stream.\n",
                    finish_reason="stop",
                )
                yield _sse_done()
                return
            await asyncio.sleep(settings.POLL_INTERVAL_SECONDS * 2)
            continue

        status: str = task.get("status", "unknown")

        # ── Fetch new iteration outputs ────────────────────────────────────
        try:
            outputs_resp = await cp.get_task_outputs(task_id)
            outputs: list = outputs_resp.get("outputs", [])
        except Exception:
            outputs = []

        new_outputs = [
            o for o in outputs if o.get("iteration") not in seen_iterations
        ]

        # ── Poll LLM turns for live progress ───────────────────────────────
        # This gives the user real-time insight into what the agent is doing
        # (which tools it's calling, what files it's writing, etc.)
        if status == "running" and not _cap_pending:
            try:
                ix_resp = await cp.get_llm_interactions(task_id, since=_turns_seen)
                new_turns = ix_resp.get("interactions", [])
                if new_turns:
                    # Figure out what iteration we're in
                    current_iter_num = max(
                        (o.get("iteration", 0) for o in outputs), default=0
                    ) + (1 if not new_outputs else 0)
                    # Determine if this is a new iteration — need header
                    if current_iter_num != _current_iter:
                        _current_iter = current_iter_num
                        _iter_header_emitted = False

                    if not _iter_header_emitted:
                        _iter_header_emitted = True
                        # Find the image/model from the task record
                        task_model = task.get("llm_model", model)
                        yield _sse(
                            completion_id, model,
                            content=f"\n---\n**⚙️ Iteration {_current_iter}** · 🤖 `{task_model}`\n\n",
                        )
                        waiting_emitted = False

                    for turn in new_turns:
                        _turns_seen += 1
                        line = _format_turn_line(turn, _turns_seen)
                        if line:
                            yield _sse(completion_id, model, content=line)
                            await asyncio.sleep(0.01)
            except Exception:
                pass  # non-critical

        if new_outputs:
            waiting_emitted = False
            _turns_seen = 0  # Reset turns for next iteration
            _iter_header_emitted = False
        elif status == "running" and not waiting_emitted and not _cap_pending and _turns_seen == 0:
            yield _sse(completion_id, model, content="\n⏳ Agent is working…\n")
            waiting_emitted = True

        # ── Stream each new iteration ──────────────────────────────────────
        for output in sorted(new_outputs, key=lambda o: o.get("iteration", 0)):
            iter_num = output.get("iteration", 0)
            seen_iterations.add(iter_num)

            # If we already streamed turn-by-turn for this iteration,
            # show only the compact summary (duration, deliverables, cap request).
            # Otherwise show the full format (e.g. reconnecting to an ongoing task).
            if _iter_header_emitted and _current_iter == iter_num:
                text = _format_iteration_summary(output, task_id=task_id)
            else:
                text = _format_iteration(output, task_id=task_id)

            if text:
                chunk_size = 256
                for i in range(0, len(text), chunk_size):
                    yield _sse(completion_id, model, content=text[i : i + chunk_size])
                    await asyncio.sleep(0.015)

            # Detect if this iteration triggered a capability request
            if output.get("capability_requested") in ("true", True):
                _cap_pending = True
                _cap_approved_emitted = False
                _cap_building_emitted = False
                _cap_last_status = "pending"

        # ── Capability lifecycle updates ───────────────────────────────────
        # While a capability request is pending, poll for status changes and
        # surface approval / build progress to the user in real-time.
        if _cap_pending and status == "running":
            try:
                cap_reqs = await cp.get_capability_requests(task_id=task_id)
                # Find the latest pending or recently-decided request
                latest = None
                for cr in cap_reqs:
                    if latest is None or cr.get("id", 0) > latest.get("id", 0):
                        latest = cr
                if latest:
                    cap_status = latest.get("status", "pending")

                    if cap_status in ("approved", "modified") and not _cap_approved_emitted:
                        _cap_approved_emitted = True
                        _cap_last_status = cap_status
                        pkgs = latest.get("resource_name", "")
                        yield _sse(
                            completion_id, model,
                            content=f"\n✅ **Capability approved** — `{pkgs}`\n"
                                    f"🔨 Building new agent image with the requested packages…\n",
                        )
                        waiting_emitted = False

                    elif cap_status == "denied" and _cap_last_status != "denied":
                        _cap_last_status = "denied"
                        _cap_pending = False
                        yield _sse(
                            completion_id, model,
                            content="\n❌ **Capability denied** — the agent will try to continue without it.\n",
                        )
                        waiting_emitted = False

                    # If approved, check if the workflow has moved past the
                    # wait (i.e. a new iteration appeared → _cap_pending will
                    # be reset by the new_outputs block above).  Also detect
                    # image build completion by checking if a new output
                    # appeared with a different image tag.
                    if _cap_approved_emitted and not _cap_building_emitted:
                        _cap_building_emitted = True
                        yield _sse(
                            completion_id, model,
                            content="⏳ *Image build in progress — this may take 1-2 minutes…*\n",
                        )

            except Exception:
                pass  # Non-critical — don't break the stream

            # If we see a new iteration output after the capability was approved,
            # the build completed and the agent resumed — announce it.
            if new_outputs and _cap_approved_emitted:
                latest_output = max(new_outputs, key=lambda o: o.get("iteration", 0))
                new_image = latest_output.get("image_used", "")
                if new_image:
                    short = new_image.split("/")[-1] if "/" in new_image else new_image
                    yield _sse(
                        completion_id, model,
                        content=f"✅ **New image built:** `{short}` — agent resumed\n",
                    )
                _cap_pending = False
                _cap_approved_emitted = False
                _cap_building_emitted = False
                waiting_emitted = False

        # ── Check for terminal state ───────────────────────────────────────
        if status in TERMINAL_STATUSES:
            # ── Deployment status summary ──────────────────────────────────
            try:
                deployments = await cp.get_task_deployments(task_id)
                if deployments:
                    dep_lines = ["\n\n---\n🚀 **Deployments**\n"]
                    for dep in (deployments if isinstance(deployments, list) else []):
                        d_name = dep.get("name", "unnamed")
                        d_status = dep.get("status", "unknown")
                        d_id = dep.get("id", "?")
                        d_port = dep.get("host_port") or dep.get("port", "?")
                        d_url = dep.get("url")

                        status_icon = {
                            "pending_approval": "⏳",
                            "approved": "👍",
                            "built": "📦",
                            "running": "🟢",
                            "stopped": "🔴",
                            "failed": "❌",
                        }.get(d_status, "❓")

                        line = f"| {status_icon} `{d_name}` | status: **{d_status}** | id: `{d_id}`"
                        if d_url:
                            line += f" | [Open App]({d_url})"
                        elif d_status in ("built", "running") and d_port:
                            line += f" | port: {d_port}"
                        dep_lines.append(line + " |")

                    if any(
                        d.get("status") == "pending_approval"
                        for d in (deployments if isinstance(deployments, list) else [])
                    ):
                        dep_lines.append(
                            "\n\n📋 *One or more deployments are **pending approval** "
                            "in the TaskForge dashboard.*"
                        )

                    dep_text = "\n".join(dep_lines)
                    for i in range(0, len(dep_text), 256):
                        yield _sse(completion_id, model, content=dep_text[i : i + 256])
                        await asyncio.sleep(0.015)
            except Exception as exc:
                logger.debug("Could not fetch deployments for %s: %s", task_id, exc)

            icon = "✅" if status == "completed" else "❌"
            yield _sse(
                completion_id,
                model,
                content=f"\n\n{icon} Task **{status}**.",
            )
            # Termination chunk (empty delta + finish_reason)
            yield _sse(completion_id, model, finish_reason="stop")
            yield _sse_done()

            # Persist final status so the next request calls /continue correctly
            await session_store.set(
                conv_id,
                {"task_id": task_id, "status": status},
                settings.SESSION_TTL_SECONDS,
            )
            return

        await asyncio.sleep(settings.POLL_INTERVAL_SECONDS)

    # ── Timeout ───────────────────────────────────────────────────────────────
    yield _sse(
        completion_id,
        model,
        content="\n\n⏱ Stream timeout reached.  The task may still be running — "
        "check the TaskForge dashboard.",
        finish_reason="stop",
    )
    yield _sse_done()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🟢  OpenClaw API Gateway starting")
    logger.info("    Control-Plane : %s", settings.CONTROL_PLANE_URL)
    logger.info("    Redis         : %s", settings.REDIS_URL or "(none — in-memory)")
    logger.info("    Default LLM   : %s", settings.DEFAULT_LLM_MODEL)
    yield
    logger.info("🔴  OpenClaw API Gateway shutting down")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="OpenClaw API Gateway",
    description=(
        "OpenAI-Compatible API Gateway for TaskForge / OpenClaw.  "
        "POST /v1/chat/completions to start or continue a task."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Conversation-ID", "X-Task-ID"],
)


# ---------------------------------------------------------------------------
# Health / Info
# ---------------------------------------------------------------------------


@app.get("/health", tags=["meta"])
async def health():
    cp_ok = await cp.health()
    return {
        "status": "healthy",
        "service": "api-gateway",
        "version": "1.0.0",
        "control_plane": "up" if cp_ok else "degraded",
    }


@app.get("/v1/models", tags=["openai-compat"])
async def list_models():
    """Return available model identifiers (OpenAI /v1/models).

    Fetches the real LLM model catalogue from the control-plane's
    ``/api/llm/models`` endpoint so Open WebUI (or any OpenAI-compatible
    client) shows the actual models the user can choose from —
    gemini-*, claude-*, gpt-*, ollama models, etc.

    Two "meta-models" are always appended at the end of the list:

    * **taskforge-iterator** — selects the default LLM and runs in
      multi-turn (iterate) mode.
    * **taskforge-oneshot** — single-shot task execution.

    These are *not* real LLM model names; the gateway recognises them and
    falls back to ``DEFAULT_LLM_MODEL`` for the actual inference call.
    """
    cards: list[ModelCard] = []
    try:
        models = await cp.get_llm_models()
        for m in models:
            mid = m.get("id", "")
            provider = m.get("provider", "openclaw")
            if mid:
                cards.append(ModelCard(id=mid, owned_by=provider))
    except Exception as exc:
        logger.warning("Could not fetch models from control-plane: %s", exc)

    # Always include a fallback entry so the UI has at least one option
    if not cards:
        cards.append(ModelCard(id=settings.DEFAULT_LLM_MODEL, owned_by="openclaw"))

    # ── Meta-models (always present) ─────────────────────────────────────
    cards.append(ModelCard(id="taskforge-iterator", owned_by="openclaw-gateway"))
    cards.append(ModelCard(id="taskforge-oneshot", owned_by="openclaw-gateway"))

    return ModelList(data=cards)


# ---------------------------------------------------------------------------
# File download — serves deliverable files from task outputs
# ---------------------------------------------------------------------------


@app.get("/v1/files/{task_id}/{iteration:int}/{filename:path}", tags=["files"])
async def download_file(task_id: str, iteration: int, filename: str):
    """Serve a deliverable file from a task output.

    URL: ``/v1/files/{task_id}/{iteration}/{filename}``

    Looks up the specific task + iteration directly, decodes base64 binary
    files, and returns the raw content with proper MIME type.
    """
    import base64
    from fastapi.responses import Response

    try:
        outputs_resp = await cp.get_task_outputs(task_id)
    except Exception:
        raise HTTPException(status_code=502, detail="Cannot reach control-plane")

    for o in outputs_resp.get("outputs", []):
        if o.get("iteration") != iteration:
            continue
        deliverables = o.get("deliverables") or {}
        if filename not in deliverables:
            raise HTTPException(status_code=404, detail=f"File '{filename}' not in deliverables")
        content = deliverables[filename]
        if isinstance(content, str) and content.startswith("base64:"):
            raw = base64.b64decode(content[7:])
            return Response(
                content=raw,
                media_type=_guess_mime(filename),
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        else:
            return Response(
                content=content.encode("utf-8") if isinstance(content, str) else content,
                media_type=_guess_mime(filename),
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

    raise HTTPException(status_code=404, detail=f"Iteration {iteration} not found for task {task_id}")


def _guess_mime(filename: str) -> str:
    """Return a reasonable MIME type for common deliverable file extensions."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "pdf": "application/pdf",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "svg": "image/svg+xml",
        "html": "text/html",
        "htm": "text/html",
        "css": "text/css",
        "js": "application/javascript",
        "json": "application/json",
        "csv": "text/csv",
        "txt": "text/plain",
        "py": "text/x-python",
        "md": "text/markdown",
        "zip": "application/zip",
        "tar": "application/x-tar",
        "gz": "application/gzip",
    }.get(ext, "application/octet-stream")


# ---------------------------------------------------------------------------
# Chat Completions  ← the main endpoint
# ---------------------------------------------------------------------------


@app.post("/v1/chat/completions", tags=["openai-compat"])
async def chat_completions(
    body: ChatCompletionRequest,
    request: Request,
    x_conversation_id: Optional[str] = Header(None, alias="X-Conversation-ID"),
):
    """
    OpenAI-compatible POST /v1/chat/completions.

    **Session resolution order:**
    1. ``X-Conversation-ID`` header (explicit — use for testing or custom clients).
    2. Derived from  ``model + system_prompt + first_user_message`` (deterministic —
       a browser refresh will automatically reconnect to the same workflow).

    **Action selection:**
    - NEW session  →  creates a TaskForge task and starts an AgentTaskWorkflow.
    - EXISTING session, task COMPLETED/FAILED →  calls ``/continue`` with the
      latest user message as the follow-up instruction.
    - EXISTING session, task RUNNING  →  streams remaining output (rare edge-case
      where a previous stream was interrupted mid-flight).

    **Response:**
    - ``stream: true`` (default) — Server-Sent Events in OpenAI chunk format.
    - ``stream: false``          — waits for task completion, returns full JSON.
    """
    messages: List[ChatMessage] = body.messages
    if not messages:
        raise HTTPException(status_code=400, detail="`messages` cannot be empty")

    # ── Resolve conversation ID ────────────────────────────────────────────
    conv_id: str = x_conversation_id or SessionStore.derive_id(
        [m.model_dump() for m in messages], model=body.model
    )

    # ── Extract the instruction (last user message) ────────────────────────
    last_user = next((m for m in reversed(messages) if m.role == "user"), None)
    if not last_user:
        raise HTTPException(status_code=400, detail="No user message found in `messages`")
    instruction: str = last_user.text()

    # ── Internal LLM model selection ──────────────────────────────────────
    # Priority: explicit llm_model extension field  →  standard model field
    # (what the user picked in the UI dropdown)  →  env default.
    # Meta-names like "taskforge-iterator" are not real LLM models;
    # they indicate the gateway *mode*, so we skip them.
    _META_MODELS = {"taskforge-iterator", "taskforge-oneshot"}
    if body.llm_model:
        llm_model = body.llm_model
    elif body.model and body.model not in _META_MODELS:
        llm_model = body.model
    else:
        llm_model = settings.DEFAULT_LLM_MODEL

    # ── Fast-path: lightweight LLM-only requests ──────────────────────────
    # Open WebUI sends auto-generated meta-requests after every conversation
    # (title generation, tag generation, follow-up suggestions, etc.).
    # These do NOT need a full TaskForge task → Temporal workflow → Docker
    # container.  We proxy them straight through the LLM router in ~2s
    # instead of ~30+s.
    if _is_fast_path_request(messages):
        logger.info("⚡ Fast-path LLM request detected — proxying to LLM router (model=%s)", llm_model)
        # Always use non-streaming for meta-requests (titles, tags, follow-ups).
        # These are tiny responses where streaming adds no value and the
        # chunked-encoding from upstream proxies can cause TransferEncodingError.
        return await _fast_path_non_streaming(body, llm_model)

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    # ── Look up existing session ───────────────────────────────────────────
    session = await session_store.get(conv_id)
    task_id: Optional[str] = None
    needs_new_task = True

    # Iterations already streamed in previous turns — will be skipped.
    prior_iterations: Set[int] = set()

    if session:
        task_id = session.get("task_id")
        logger.info("[%s] Existing session → task=%s", conv_id, task_id)

        # Always verify actual status from the control-plane (session is a cache)
        try:
            task_meta = await cp.get_task(task_id)
            actual_status: str = task_meta.get("status", "unknown")
        except Exception as exc:
            logger.warning("[%s] Could not fetch task %s: %s", conv_id, task_id, exc)
            actual_status = session.get("status", "unknown")

        # Snapshot existing iterations so we don't re-stream them
        try:
            existing_outputs = await cp.get_task_outputs(task_id)
            prior_iterations = {
                o.get("iteration") for o in existing_outputs.get("outputs", [])
                if o.get("iteration") is not None
            }
            logger.info("[%s] Prior iterations to skip: %s", conv_id, prior_iterations)
        except Exception:
            pass

        if actual_status == "running":
            # A previous stream was interrupted — just resume streaming
            logger.info("[%s] Task %s still RUNNING — resuming stream", conv_id, task_id)
            needs_new_task = False

        elif actual_status in TERMINAL_STATUSES:
            # Happy path: continue the task with the new instruction
            logger.info(
                "[%s] Continuing task %s  [%s] instruction: %s…",
                conv_id,
                task_id,
                actual_status,
                instruction[:80],
            )
            try:
                await cp.continue_task(task_id, follow_up=instruction, llm_model=llm_model)
                await session_store.set(
                    conv_id,
                    {"task_id": task_id, "status": "running"},
                    settings.SESSION_TTL_SECONDS,
                )
                needs_new_task = False
            except httpx.HTTPStatusError as exc:
                err = exc.response.text[:300]
                raise HTTPException(
                    status_code=502,
                    detail=f"Control-plane rejected /continue: {exc.response.status_code} — {err}",
                )

        else:
            logger.warning(
                "[%s] Unexpected task status '%s' — creating new task",
                conv_id,
                actual_status,
            )
            needs_new_task = True

    # ── Create brand-new task ──────────────────────────────────────────────
    if needs_new_task:
        # Build a compact task name from first user message
        system_ctx = next((m.text() for m in messages if m.role == "system"), "")
        task_name = f"{system_ctx[:40]} · {instruction[:60]}" if system_ctx else instruction[:80]

        # Full description = entire conversation so far
        desc_parts: list[str] = []
        for m in messages:
            if m.role == "system":
                desc_parts.append(f"[SYSTEM]\n{m.text()}")
            elif m.role == "user":
                desc_parts.append(f"[USER]\n{m.text()}")
        description = "\n\n".join(desc_parts) or instruction

        logger.info("[%s] Creating new task: %s…", conv_id, task_name[:60])
        try:
            task_data = await cp.create_task(
                name=task_name[:100],
                description=description,
                llm_model=llm_model,
            )
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Control-plane rejected task creation: {exc.response.status_code} — {exc.response.text[:300]}",
            )

        task_id = task_data["id"]
        await session_store.set(
            conv_id,
            {"task_id": task_id, "status": "running"},
            settings.SESSION_TTL_SECONDS,
        )
        logger.info("[%s] Created task %s", conv_id, task_id)

    # ── Non-streaming path ─────────────────────────────────────────────────
    if body.stream is False:
        collected: list[str] = []
        seen_iters: Set[int] = set(prior_iterations)
        deadline = time.monotonic() + settings.STREAM_TIMEOUT_SECONDS

        while time.monotonic() < deadline:
            try:
                t = await cp.get_task(task_id)
                st = t.get("status", "")
                outputs_r = await cp.get_task_outputs(task_id)
                for o in sorted(
                    outputs_r.get("outputs", []), key=lambda x: x.get("iteration", 0)
                ):
                    if o.get("iteration") not in seen_iters:
                        seen_iters.add(o["iteration"])
                        collected.append(_format_iteration(o, task_id=task_id))
                if st in TERMINAL_STATUSES:
                    icon = "✅" if st == "completed" else "❌"
                    collected.append(f"\n\n{icon} Task **{st}**.")
                    await session_store.set(
                        conv_id,
                        {"task_id": task_id, "status": st},
                        settings.SESSION_TTL_SECONDS,
                    )
                    break
            except Exception:
                pass
            await asyncio.sleep(settings.POLL_INTERVAL_SECONDS)

        full_text = "".join(collected) or "*(task submitted — no output yet)*"
        return ChatCompletionResponse(
            id=completion_id,
            model=body.model,
            choices=[
                NonStreamChoice(
                    message=ChatMessage(role="assistant", content=full_text)
                )
            ],
            usage=Usage(),
        )

    # ── Streaming path (default) ───────────────────────────────────────────
    _task_id = task_id  # capture for the inner async generator
    _prior = prior_iterations  # capture for closure

    async def generate():
        async for chunk in stream_task_execution(
            _task_id, completion_id, body.model, conv_id,
            skip_iterations=_prior,
        ):
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx proxy buffering
            "X-Conversation-ID": conv_id,
            "X-Task-ID": task_id,
        },
    )


# ---------------------------------------------------------------------------
# Session management endpoints (debug / admin)
# ---------------------------------------------------------------------------


@app.get("/v1/sessions/{conversation_id}", tags=["sessions"])
async def get_session(conversation_id: str):
    """Inspect the session record for a given conversation ID."""
    session = await session_store.get(conversation_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"conversation_id": conversation_id, **session}


@app.delete("/v1/sessions/{conversation_id}", tags=["sessions"])
async def delete_session(conversation_id: str):
    """Reset a conversation — the next message will create a brand-new task."""
    await session_store.delete(conversation_id)
    return {"status": "deleted", "conversation_id": conversation_id}


@app.get("/v1/sessions", tags=["sessions"])
async def list_sessions():
    """List all in-memory sessions (dev/debug only — not available with Redis)."""
    return {
        "sessions": [
            {"conversation_id": k, **v}
            for k, v in session_store._store.items()
        ]
    }
