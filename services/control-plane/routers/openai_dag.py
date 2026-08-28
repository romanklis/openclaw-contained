"""
OpenAI-compatible DAG facade for Open WebUI integration.

Exposes TaskForge DAG orchestration as a standard OpenAI chat completions
API so Open WebUI (or any OpenAI-compatible client) can:
  - GET  /api/dag-ui/v1/models         → list available DAG "models"
  - POST /api/dag-ui/v1/chat/completions → create + stream DAG execution

The user's message becomes the DAG objective. The response streams back
planning status, node progress, and final results as SSE chunks.
"""
import asyncio
import base64
import json
import mimetypes
import os
import time
import uuid
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Any, Union

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from models import MasterDAG, DAGNode, DAGStatus, NodeStatus, Skill, TaskOutput, CapabilityRequest, RequestStatus
from planner import plan_dag, is_gemini_lite_model
from temporal_client import start_dag_workflow
from config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

DAG_MODEL_ID = "taskforge-dag"  # default / backward-compat
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "http://localhost:3000")

# ── Model configurations ─────────────────────────────────────────
# Each entry maps a selectable Open WebUI "model" to a planning LLM
# (used to decompose the objective into a DAG) and an agent LLM
# (injected into every DAG node for task execution).
MODEL_CONFIGS: dict[str, dict] = {
    "taskforge-dag": {
        "name": "TaskForge — Default",
        "description": "Uses the planning & agent models configured in the LLM Router page.",
        "planning_model": "gemini-flash-lite-latest",
        "agent_model": "gemini-flash-lite-latest",
    },
    "taskforge-gemini-flash": {
        "name": "TaskForge — Gemini Flash",
        "description": "Balanced. Gemini 2.0 Flash for planning and agents.",
        "planning_model": "gemini-2.0-flash",
        "agent_model": "gemini-2.0-flash",
    },
    "taskforge-gemma-27b": {
        "name": "TaskForge — Gemma 27B",
        "description": "Google Gemma 3 27B agents, Gemini Lite planning.",
        "planning_model": "gemini-flash-lite-latest",
        "agent_model": "gemma-3-27b-it",
    },
    "taskforge-gemma-12b": {
        "name": "TaskForge — Gemma 12B",
        "description": "Google Gemma 3 12B agents, Gemini Lite planning.",
        "planning_model": "gemini-flash-lite-latest",
        "agent_model": "gemma-3-12b-it",
    },
    "taskforge-gemma-4b": {
        "name": "TaskForge — Gemma 4B",
        "description": "Google Gemma 3 4B agents, Gemini Lite planning.",
        "planning_model": "gemini-flash-lite-latest",
        "agent_model": "gemma-3-4b-it",
    },
}

DEFAULT_MODEL_CONFIG = MODEL_CONFIGS[DAG_MODEL_ID]


def get_dag_model_defaults() -> dict[str, str]:
    """Return the current default planning + agent model.

    Imports _dag_model_defaults from dags router at call-time to avoid
    circular import issues and to always get the latest values.
    """
    from routers.dags import _dag_model_defaults
    return dict(_dag_model_defaults)

# ── Schemas (OpenAI-compatible) ──────────────────────────────────

class _ContentPart(BaseModel):
    type: str  # "text", "image_url", "file"
    text: Optional[str] = None
    image_url: Optional[dict] = None  # {"url": "data:...;base64,..."}
    file_url: Optional[dict] = None   # {"url": "data:...;base64,..."}

class _ChatMessage(BaseModel):
    role: str
    content: Optional[Union[str, List[_ContentPart]]] = None

class _ChatRequest(BaseModel):
    model: str = DAG_MODEL_ID
    messages: List[_ChatMessage] = []
    stream: bool = True
    # TaskForge extensions (optional)
    skill_ids: Optional[List[str]] = None
    base_image: Optional[str] = None
    auto_start: bool = True


# ── Helpers ──────────────────────────────────────────────────────

def _gen_id(prefix: str = "dag") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _sse_chunk(content: str, model: str = DAG_MODEL_ID, finish_reason: Optional[str] = None) -> str:
    """Format a single SSE chunk in OpenAI streaming format."""
    delta = {"role": "assistant", "content": content} if content else {}
    chunk = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": finish_reason,
        }],
    }
    return f"data: {json.dumps(chunk)}\n\n"


def _sse_done() -> str:
    return "data: [DONE]\n\n"


def _status_emoji(status: str) -> str:
    return {
        "pending": "⏳", "running": "🔄", "completed": "✅",
        "failed": "❌", "pending_approval": "⏸️", "skipped": "⏭️",
    }.get(status, "❓")


# Map common MIME types to file extensions
_MIME_TO_EXT = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "text/html": ".html",
    "text/markdown": ".md",
    "application/json": ".json",
    "application/xml": ".xml",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "application/zip": ".zip",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


def _extract_message_content(msg: _ChatMessage) -> tuple[str, list[tuple[str, bytes, str]]]:
    """Extract text and files from a message.
    
    Returns (text, [(filename, data_bytes, mime_type), ...])
    """
    if msg.content is None:
        return "", []
    
    if isinstance(msg.content, str):
        return msg.content, []
    
    # content is a list of parts (OpenAI vision/multipart format)
    text_parts = []
    files = []
    
    for part in msg.content:
        if part.type == "text" and part.text:
            text_parts.append(part.text)
        elif part.type == "image_url" and part.image_url:
            url = part.image_url.get("url", "")
            file_info = _decode_data_uri(url)
            if file_info:
                files.append(file_info)
        elif part.type == "file" and part.file_url:
            url = part.file_url.get("url", "")
            file_info = _decode_data_uri(url)
            if file_info:
                files.append(file_info)
    
    return "\n".join(text_parts), files


def _decode_data_uri(uri: str) -> Optional[tuple[str, bytes, str]]:
    """Decode a data URI (data:mime;base64,...) into (filename, bytes, mime)."""
    if not uri.startswith("data:"):
        return None
    
    try:
        header, b64_data = uri.split(",", 1)
        # header is like "data:application/pdf;base64"
        mime = header.split(":", 1)[1].split(";")[0]
        data = base64.b64decode(b64_data)
        ext = _MIME_TO_EXT.get(mime) or mimetypes.guess_extension(mime) or ".bin"
        filename = f"upload-{uuid.uuid4().hex[:8]}{ext}"
        return filename, data, mime
    except Exception as e:
        logger.warning(f"Failed to decode data URI: {e}")
        return None


def _save_files_to_workspace(workspace_id: str, files: list[tuple[str, bytes, str]]) -> list[str]:
    """Save decoded files to the workspace directory. Returns list of saved filenames."""
    if not files:
        return []
    
    ws_path = Path(settings.WORKSPACE_ROOT) / workspace_id
    ws_path.mkdir(parents=True, exist_ok=True)
    
    saved = []
    for filename, data, mime in files:
        filepath = ws_path / filename
        filepath.write_bytes(data)
        saved.append(filename)
        logger.info(f"Saved uploaded file: {filepath} ({len(data)} bytes, {mime})")
    
    return saved


def _save_input_prompt(workspace_id: str, objective: str) -> None:
    """Save the full input prompt/objective to the workspace as input_prompt.md."""
    ws_path = Path(settings.WORKSPACE_ROOT) / workspace_id
    ws_path.mkdir(parents=True, exist_ok=True)
    prompt_file = ws_path / "input_prompt.md"
    prompt_file.write_text(objective, encoding="utf-8")
    logger.info(f"Saved input prompt: {prompt_file} ({len(objective)} chars)")


def _save_attached_context(workspace_id: str, context_parts: list[str]) -> str:
    """Save RAG-extracted context (e.g. PDF text from Open WebUI) to attached_context.md.
    
    Returns the filename for reference in the objective.
    """
    ws_path = Path(settings.WORKSPACE_ROOT) / workspace_id
    ws_path.mkdir(parents=True, exist_ok=True)
    context_file = ws_path / "attached_context.md"
    content = "\n\n---\n\n".join(context_parts)
    context_file.write_text(content, encoding="utf-8")
    logger.info(f"Saved attached context: {context_file} ({len(content)} chars)")
    return "attached_context.md"


# Patterns that indicate Open WebUI auxiliary requests (not real user tasks)
_AUXILIARY_PATTERNS = [
    "generate a concise, 3-5 word title",
    "generate 1-3 broad tags categorizing",
    "analyze the chat history to determine the necessity of generating search queries",
    "### task:\ngenerate",
    '"queries":',
    '"title":',
    '"tags":',
]


def _is_auxiliary_request(text: str) -> bool:
    """Detect Open WebUI internal requests (title, tag, search query generation)."""
    lower = text.lower()[:500]  # Only check beginning
    return any(pattern.lower() in lower for pattern in _AUXILIARY_PATTERNS[:3])


def _handle_auxiliary_request(text: str, stream: bool):
    """Return a quick response for auxiliary requests without creating a DAG."""
    lower = text.lower()[:500]
    if "title" in lower:
        content = '{"title": "TaskForge DAG Request"}'
    elif "tags" in lower or "tag" in lower:
        content = '{"tags": ["AI", "Automation"]}'
    elif "search" in lower or "queries" in lower:
        content = '{"queries": []}'
    else:
        content = '{"result": "ok"}'

    if stream:
        async def _aux_stream():
            yield _sse_chunk(content)
            yield _sse_chunk("", finish_reason="stop")
            yield _sse_done()
        return StreamingResponse(
            _aux_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    else:
        return _non_streaming_response(content)


# ── Routes ───────────────────────────────────────────────────────

@router.get("/v1/models")
async def list_models(db: AsyncSession = Depends(get_db)):
    """OpenAI-compatible models list.

    Lists every MODEL_CONFIGS entry.  Skills are appended as sub-variants
    of the default model (e.g. ``taskforge-dag:web-search``).
    """
    now = int(time.time())
    models = []

    for model_id, cfg in MODEL_CONFIGS.items():
        models.append({
            "id": model_id,
            "object": "model",
            "created": now,
            "owned_by": "taskforge",
            "name": cfg["name"],
            "description": cfg.get("description", ""),
        })

    # Add skill-specific model entries (under the default model)
    result = await db.execute(select(Skill))
    skills = list(result.scalars().all())
    for skill in skills:
        models.append({
            "id": f"{DAG_MODEL_ID}:{skill.name}",
            "object": "model",
            "created": now,
            "owned_by": "taskforge",
            "name": f"TaskForge — {skill.name}",
        })

    return {"object": "list", "data": models}


@router.post("/v1/chat/completions")
async def dag_chat_completions(req: _ChatRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """OpenAI-compatible chat completions — creates a DAG from user message.
    
    The last user message becomes the DAG objective.
    If stream=true (default), streams planning + execution progress as SSE.
    If stream=false, blocks until DAG completes and returns final result.
    """
    # Log the raw request body for debugging file attachments
    try:
        raw_body = await request.body()
        raw_json = json.loads(raw_body)
        # Log message structure (truncate large base64 payloads)
        for i, msg in enumerate(raw_json.get("messages", [])):
            content = msg.get("content")
            role = msg.get("role", "unknown")
            if isinstance(content, list):
                for j, part in enumerate(content):
                    part_type = part.get("type", "unknown")
                    if part_type == "text":
                        logger.info(f"[DAG-UI] msg[{i}] role={role} part[{j}]: type=text, len={len(part.get('text', ''))}")
                    else:
                        # Log structure but truncate data URIs
                        part_summary = json.dumps(part, default=str)
                        if len(part_summary) > 500:
                            part_summary = part_summary[:500] + "...[truncated]"
                        logger.info(f"[DAG-UI] msg[{i}] role={role} part[{j}]: {part_summary}")
            elif isinstance(content, str):
                logger.info(f"[DAG-UI] msg[{i}] role={role}: type=string, len={len(content)}, preview={content[:100]}")
            else:
                logger.info(f"[DAG-UI] msg[{i}] role={role}: content type={type(content).__name__}, keys={list(msg.keys())}")
    except Exception as e:
        logger.warning(f"[DAG-UI] Failed to log request: {e}")

    # Detect and handle Open WebUI auxiliary requests (title, tags, search queries)
    # These are NOT real user requests — respond quickly without creating a DAG
    raw_text = ""
    for msg in req.messages:
        if msg.content:
            t, _ = _extract_message_content(msg)
            raw_text += t + "\n"

    if _is_auxiliary_request(raw_text):
        logger.info(f"[DAG-UI] Detected auxiliary request, returning quick response")
        return _handle_auxiliary_request(raw_text, req.stream)

    # Extract objective from last user message, and gather system/context messages
    # (Open WebUI injects RAG-extracted file content into system messages)
    objective = ""
    context_parts: list[str] = []
    uploaded_files: list[tuple[str, bytes, str]] = []

    # Collect context from system messages (contains RAG-extracted PDF content etc)
    for msg in req.messages:
        if msg.role == "system" and msg.content:
            text, files = _extract_message_content(msg)
            if text.strip():
                context_parts.append(text.strip())
            uploaded_files.extend(files)

    # Extract objective from last user message
    for msg in reversed(req.messages):
        if msg.role == "user" and msg.content:
            text, files = _extract_message_content(msg)
            if text.strip():
                objective = text.strip()
                uploaded_files.extend(files)
                break

    if not objective:
        return _non_streaming_response("Please provide an objective for the DAG.")

    # Context will be saved to workspace file instead of being injected into objective
    # (handled in _stream_dag_execution / _blocking_dag_execution)

    # Check if a skill-specific model was selected (e.g. "taskforge-dag:web-search")
    skill_ids = req.skill_ids or []
    model_name = req.model or DAG_MODEL_ID
    if ":" in model_name:
        skill_name = model_name.split(":", 1)[1]
        result = await db.execute(select(Skill).where(Skill.name == skill_name))
        skill = result.scalar_one_or_none()
        if skill and skill.id not in skill_ids:
            skill_ids.append(skill.id)
        # Skill-specific variants use the default model config
        model_name = DAG_MODEL_ID

    # Resolve planning vs agent model from the selected config
    # For the default model (taskforge-dag) and skill variants, use the
    # user-configured defaults from the LLM Router page.
    if model_name == DAG_MODEL_ID:
        defaults = get_dag_model_defaults()
        planning_model = defaults["planning_model"]
        agent_model = defaults["agent_model"]
    else:
        cfg = MODEL_CONFIGS.get(model_name, DEFAULT_MODEL_CONFIG)
        planning_model = cfg["planning_model"]
        agent_model = cfg["agent_model"]
    logger.info(f"[DAG-UI] Model config: {model_name} → planning={planning_model}, agent={agent_model}")

    if req.stream:
        return StreamingResponse(
            _stream_dag_execution(objective, planning_model, agent_model, skill_ids, req.base_image, uploaded_files, context_parts, db),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        return await _blocking_dag_execution(objective, planning_model, agent_model, skill_ids, req.base_image, uploaded_files, context_parts, db)


async def _stream_dag_execution(
    objective: str,
    planning_model: str,
    agent_model: str,
    skill_ids: list[str],
    base_image: Optional[str],
    uploaded_files: list[tuple[str, bytes, str]],
    context_parts: list[str],
    db: AsyncSession,
):
    """Generator that creates a DAG, starts it, and streams progress as SSE."""

    yield _sse_chunk(f"🎯 **Objective:** {objective}\n\n")

    # Plan
    dag_id = _gen_id("dag")
    workspace_id = f"workspace-{dag_id}"

    # Always save the full input prompt to workspace so agents can reference it
    _save_input_prompt(workspace_id, objective)

    # Save RAG-extracted context (e.g. PDF text) as a separate workspace file
    if context_parts:
        ctx_filename = _save_attached_context(workspace_id, context_parts)
        yield _sse_chunk(f"📄 **Attached context saved** to `{ctx_filename}`\n\n")
        objective += f"\n\nAttached reference material has been saved to the workspace as '{ctx_filename}'. Read it for context."

    # Save uploaded files to workspace
    if uploaded_files:
        saved = _save_files_to_workspace(workspace_id, uploaded_files)
        yield _sse_chunk(f"📎 **{len(saved)} file(s) uploaded:** {', '.join(saved)}\n\n")
        objective += f"\n\nThe following files have been uploaded to the workspace: {', '.join(saved)}"

    yield _sse_chunk(f"📋 **Planning DAG** (planner: `{planning_model}`, agents: `{agent_model}`)...\n\n")

    dag = MasterDAG(
        id=dag_id,
        objective=objective,
        status=DAGStatus.PLANNING,
        dag_json={},
        workspace_id=workspace_id,
        llm_model=agent_model,
    )
    db.add(dag)
    await db.commit()

    try:
        dag_json = await plan_dag(
            objective, planning_model, db,
            base_image=base_image,
            skill_ids=skill_ids if skill_ids else None,
            agent_model=agent_model,
        )
    except ValueError as e:
        dag.status = DAGStatus.FAILED
        dag.dag_json = {"error": str(e)}
        await db.commit()
        yield _sse_chunk(f"\n❌ **Planning failed:** {e}\n")
        yield _sse_chunk("", finish_reason="stop")
        yield _sse_done()
        return

    # Store nodes
    dag.dag_json = dag_json
    dag.status = DAGStatus.READY

    nodes_data = dag_json.get("nodes", [])
    for node_def in nodes_data:
        node = DAGNode(
            dag_id=dag_id,
            node_id=node_def["node_id"],
            skill_id=node_def.get("skill_id"),
            skill_step_index=node_def.get("skill_step_index"),
            description=node_def.get("description"),
            status=NodeStatus.PENDING,
            depends_on=node_def.get("depends_on", []),
            config=node_def.get("config", {}),
            input_mapping=node_def.get("input_mapping", {}),
        )
        db.add(node)

    await db.commit()

    # Show plan
    yield _sse_chunk(f"✅ **Plan ready** — {len(nodes_data)} task(s):\n\n")
    for i, nd in enumerate(nodes_data, 1):
        yield _sse_chunk(f"  {i}. **{nd['node_id']}** — {nd.get('description', '')[:100]}\n")
    yield _sse_chunk("\n")

    # Start DAG
    yield _sse_chunk("🚀 **Starting DAG execution...**\n\n")
    try:
        workflow_id = await start_dag_workflow(dag.id)
        dag.workflow_id = workflow_id
        dag.status = DAGStatus.RUNNING
        dag.started_at = datetime.utcnow()
        await db.commit()
    except Exception as e:
        yield _sse_chunk(f"\n❌ **Failed to start:** {e}\n")
        yield _sse_chunk("", finish_reason="stop")
        yield _sse_done()
        return

    # Poll for progress
    seen_status = {}
    seen_iterations = {}   # {task_id: max_iteration_seen}
    seen_capabilities = set()  # capability request IDs already reported
    max_polls = 600
    poll = 0

    while poll < max_polls:
        poll += 1
        await asyncio.sleep(2)

        # Refresh DAG state
        db.expire_all()
        result = await db.execute(select(MasterDAG).where(MasterDAG.id == dag_id))
        dag_now = result.scalar_one_or_none()
        if not dag_now:
            break

        nodes_result = await db.execute(select(DAGNode).where(DAGNode.dag_id == dag_id))
        nodes_now = list(nodes_result.scalars().all())

        # Report node status changes
        for node in nodes_now:
            prev = seen_status.get(node.node_id)
            if node.status != prev:
                seen_status[node.node_id] = node.status
                status_val = node.status.value if hasattr(node.status, 'value') else str(node.status)
                if status_val == "running" and prev is None or (prev and (prev.value if hasattr(prev, 'value') else str(prev)) == "pending"):
                    base_img = node.config.get("base_image", "openclaw")
                    dag_img = node.config.get("dag_image")
                    if dag_img:
                        dag_img_tag = dag_img.split(":")[-1] if ":" in dag_img else dag_img
                        img_info = f"{base_img} (built: {dag_img_tag})"
                    else:
                        img_info = base_img
                    skill_info = node.selected_skill_v2_id or node.skill_id or "inline/custom"
                    yield _sse_chunk(f"🔨 **{node.node_id}** — building agent image (image: '{img_info}', skill: '{skill_info}')...\n")
                elif status_val == "completed":
                    base_img = node.config.get("base_image", "openclaw")
                    dag_img = node.config.get("dag_image")
                    if dag_img:
                        dag_img_tag = dag_img.split(":")[-1] if ":" in dag_img else dag_img
                        img_info = f"{base_img} (built: {dag_img_tag})"
                    else:
                        img_info = base_img
                    skill_info = node.selected_skill_v2_id or node.skill_id or "inline/custom"
                    yield _sse_chunk(f"✅ **{node.node_id}** — done (image: '{img_info}', skill: '{skill_info}')\n")
                elif status_val == "failed":
                    base_img = node.config.get("base_image", "openclaw")
                    dag_img = node.config.get("dag_image")
                    if dag_img:
                        dag_img_tag = dag_img.split(":")[-1] if ":" in dag_img else dag_img
                        img_info = f"{base_img} (built: {dag_img_tag})"
                    else:
                        img_info = base_img
                    skill_info = node.selected_skill_v2_id or node.skill_id or "inline/custom"
                    yield _sse_chunk(f"❌ **{node.node_id}** — failed (image: '{img_info}', skill: '{skill_info}')\n")
                elif status_val == "pending_approval":
                    yield _sse_chunk(f"⏸️ **{node.node_id}** — waiting for approval\n")

        # Report per-task iteration progress for running nodes
        running_task_ids = [n.task_id for n in nodes_now if n.task_id and n.status in (NodeStatus.RUNNING,)]
        if running_task_ids:
            for task_id in running_task_ids:
                outputs_result = await db.execute(
                    select(TaskOutput)
                    .where(TaskOutput.task_id == task_id)
                    .order_by(TaskOutput.iteration.asc())
                )
                outputs = list(outputs_result.scalars().all())
                prev_max = seen_iterations.get(task_id, -1)

                # If this is a running node and we get the first iteration,
                # update the status message from "building" to "working"
                if prev_max == -1 and len(outputs) > 0:
                    node_name = task_id
                    node_obj = None
                    for n in nodes_now:
                        if n.task_id == task_id:
                            node_name = n.node_id
                            node_obj = n
                            break
                    if node_obj:
                        base_img = node_obj.config.get("base_image", "openclaw")
                        dag_img = node_obj.config.get("dag_image")
                        if dag_img:
                            dag_img_tag = dag_img.split(":")[-1] if ":" in dag_img else dag_img
                            img_info = f"{base_img} (built: {dag_img_tag})"
                        else:
                            img_info = base_img
                        skill_info = node_obj.selected_skill_v2_id or node_obj.skill_id or "inline/custom"
                        yield _sse_chunk(f"🔄 **{node_name}** — agent is working (image: '{img_info}', skill: '{skill_info}')...\n")
                    else:
                        yield _sse_chunk(f"🔄 **{node_name}** — agent is working...\n")

                for out in outputs:
                    if out.iteration > prev_max:
                        seen_iterations[task_id] = out.iteration
                        # Find the node name for this task
                        node_name = task_id
                        for n in nodes_now:
                            if n.task_id == task_id:
                                node_name = n.node_id
                                break
                        # Show brief progress only for completed iterations
                        if out.completed == "true" and out.llm_response_preview:
                            preview = out.llm_response_preview[:150].replace("\n", " ")
                            yield _sse_chunk(f"  > {preview}\n")

        # Report capability requests (pending approvals)
        task_ids_with_tasks = [n.task_id for n in nodes_now if n.task_id]
        if task_ids_with_tasks:
            try:
                cap_result = await db.execute(
                    select(CapabilityRequest).where(
                        CapabilityRequest.task_id.in_(task_ids_with_tasks)
                    )
                )
                cap_requests = list(cap_result.scalars().all())
            except Exception as e:
                logger.warning(f"[DAG-UI] Failed to query capability requests: {e}")
                cap_requests = []
        else:
            cap_requests = []

        for cap in cap_requests:
            if cap.id not in seen_capabilities:
                seen_capabilities.add(cap.id)
                # Find node name
                node_name = cap.task_id
                for n in nodes_now:
                    if n.task_id == cap.task_id:
                        node_name = n.node_id
                        break
                status_emoji = {"PENDING": "🟡", "APPROVED": "✅", "DENIED": "🚫"}.get(cap.status.value if hasattr(cap.status, 'value') else str(cap.status), "❓")
                cap_type = cap.capability_type.value if hasattr(cap.capability_type, 'value') else str(cap.capability_type)
                yield _sse_chunk(
                    f"\n{status_emoji} **Capability Request** — {node_name} wants `{cap_type}` access to `{cap.resource_name}`\n"
                    f"  {cap.justification[:200]}\n"
                )
                if cap.status == RequestStatus.PENDING:
                    yield _sse_chunk(f"  ⏳ [Approve / Deny]({FRONTEND_BASE_URL}/dags/{dag_id})\n\n")
                seen_status[f"_cap_{cap.id}"] = cap.status.value if hasattr(cap.status, 'value') else str(cap.status)
            else:
                # Check if status changed (approved/denied)
                prev_status = seen_status.get(f"_cap_{cap.id}")
                curr_status = cap.status.value if hasattr(cap.status, 'value') else str(cap.status)
                if prev_status and prev_status != curr_status:
                    status_emoji = {"APPROVED": "✅", "DENIED": "🚫"}.get(curr_status, "❓")
                    yield _sse_chunk(f"{status_emoji} Capability `{cap.resource_name}` → **{curr_status}**\n")
                    seen_status[f"_cap_{cap.id}"] = curr_status

        # Check if DAG is done
        if dag_now.status in (DAGStatus.COMPLETED, DAGStatus.FAILED, DAGStatus.CANCELLED):
            yield _sse_chunk("\n---\n\n")

            if dag_now.status == DAGStatus.COMPLETED:
                yield _sse_chunk(f"✅ **DAG `{dag_id}` completed successfully!**\n\n")

                # Collect deliverables from task outputs
                yield _sse_chunk("📦 **Results:**\n\n")
                for node in nodes_now:
                    if not node.task_id:
                        continue
                    # Fetch latest task output for this node's task
                    out_result = await db.execute(
                        select(TaskOutput)
                        .where(TaskOutput.task_id == node.task_id)
                        .order_by(TaskOutput.iteration.desc())
                        .limit(1)
                    )
                    last_output = out_result.scalar_one_or_none()
                    if last_output:
                        if last_output.deliverables and isinstance(last_output.deliverables, dict):
                            filenames = list(last_output.deliverables.keys())
                            if filenames:
                                yield _sse_chunk(f"  **{node.node_id}:** {', '.join(filenames)}\n")
                        if last_output.output:
                            preview = last_output.output[:500].replace("\n", "\n  > ")
                            yield _sse_chunk(f"\n  > {preview}\n\n")

            elif dag_now.status == DAGStatus.FAILED:
                yield _sse_chunk(f"❌ **DAG `{dag_id}` failed.**\n\n")
                for node in nodes_now:
                    if node.status == NodeStatus.FAILED and node.task_id:
                        out_result = await db.execute(
                            select(TaskOutput)
                            .where(TaskOutput.task_id == node.task_id)
                            .order_by(TaskOutput.iteration.desc())
                            .limit(1)
                        )
                        last_output = out_result.scalar_one_or_none()
                        if last_output and last_output.error:
                            yield _sse_chunk(f"  **{node.node_id}:** {last_output.error[:300]}\n")

            else:
                yield _sse_chunk(f"🚫 **DAG `{dag_id}` was cancelled.**\n")

            yield _sse_chunk(f"\n🔗 [View details]({FRONTEND_BASE_URL}/dags/{dag_id})\n")
            break

    else:
        yield _sse_chunk(f"\n⏰ **Polling timeout** — DAG is still running. [Check status]({FRONTEND_BASE_URL}/dags/{dag_id})\n")

    yield _sse_chunk("", finish_reason="stop")
    yield _sse_done()


async def _blocking_dag_execution(
    objective: str,
    planning_model: str,
    agent_model: str,
    skill_ids: list[str],
    base_image: Optional[str],
    uploaded_files: list[tuple[str, bytes, str]],
    context_parts: list[str],
    db: AsyncSession,
) -> dict:
    """Non-streaming: create, start, wait for completion, return result."""
    dag_id = _gen_id("dag")
    workspace_id = f"workspace-{dag_id}"

    # Always save the full input prompt to workspace so agents can reference it
    _save_input_prompt(workspace_id, objective)

    # Save RAG-extracted context as a separate workspace file
    if context_parts:
        ctx_filename = _save_attached_context(workspace_id, context_parts)
        objective += f"\n\nAttached reference material has been saved to the workspace as '{ctx_filename}'. Read it for context."

    # Save uploaded files to workspace
    if uploaded_files:
        saved = _save_files_to_workspace(workspace_id, uploaded_files)
        objective += f"\n\nThe following files have been uploaded to the workspace: {', '.join(saved)}"

    dag = MasterDAG(
        id=dag_id,
        objective=objective,
        status=DAGStatus.PLANNING,
        dag_json={},
        workspace_id=workspace_id,
        llm_model=agent_model,
    )
    db.add(dag)
    await db.commit()

    try:
        dag_json = await plan_dag(objective, planning_model, db, base_image=base_image, skill_ids=skill_ids if skill_ids else None, agent_model=agent_model)
    except ValueError as e:
        return _non_streaming_response(f"Planning failed: {e}")

    dag.dag_json = dag_json
    dag.status = DAGStatus.READY
    for node_def in dag_json.get("nodes", []):
        db.add(DAGNode(
            dag_id=dag_id,
            node_id=node_def["node_id"],
            skill_id=node_def.get("skill_id"),
            description=node_def.get("description"),
            status=NodeStatus.PENDING,
            depends_on=node_def.get("depends_on", []),
            config=node_def.get("config", {}),
        ))
    await db.commit()

    try:
        workflow_id = await start_dag_workflow(dag.id)
        dag.workflow_id = workflow_id
        dag.status = DAGStatus.RUNNING
        dag.started_at = datetime.utcnow()
        await db.commit()
    except Exception as e:
        return _non_streaming_response(f"Failed to start DAG: {e}")

    # Poll until done (max 10 min)
    for _ in range(200):
        await asyncio.sleep(3)
        db.expire_all()
        result = await db.execute(select(MasterDAG).where(MasterDAG.id == dag_id))
        dag_now = result.scalar_one_or_none()
        if dag_now and dag_now.status in (DAGStatus.COMPLETED, DAGStatus.FAILED, DAGStatus.CANCELLED):
            nodes_result = await db.execute(select(DAGNode).where(DAGNode.dag_id == dag_id))
            nodes = list(nodes_result.scalars().all())
            summary = f"DAG {dag_id}: {dag_now.status}\n\n"
            for n in nodes:
                summary += f"- {n.node_id}: {n.status}\n"
                if n.output_data and n.output_data.get("response"):
                    summary += f"  {n.output_data['response'][:300]}\n"
            return _non_streaming_response(summary)

    return _non_streaming_response(f"DAG {dag_id} is still running. Check {FRONTEND_BASE_URL}/dags/{dag_id}")


def _non_streaming_response(content: str) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": DAG_MODEL_ID,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
