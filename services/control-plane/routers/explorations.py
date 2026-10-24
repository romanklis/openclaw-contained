"""Exploration session APIs and streaming chat."""
import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session, get_db
from explorations import (
    MAX_EXPLORATION_CYCLES,
    build_chat_context,
    create_exploration_session,
    finish_session,
    plan_next_cycle,
    reconcile_session,
)
from models import (
    DAGNode,
    DagUserRequest,
    ExplorationCycle,
    ExplorationMessage,
    ExplorationSession,
    MasterDAG,
)
from schemas import ExplorationChatRequest, ExplorationCreateRequest, ExplorationFeedbackRequest

logger = logging.getLogger(__name__)
router = APIRouter()

LLM_ROUTER_URL = "http://localhost:8000/api/llm/v1/chat/completions"

PROPOSE_TOOL = {
    "type": "function",
    "function": {
        "name": "propose_exploration_cycle",
        "description": (
            "Prepare the next ready DAG cycle for this exploration. This only "
            "plans a workflow; it never starts it. Use only in feedback mode."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "description": "Optional focus for the next cycle.",
                }
            },
            "required": [],
        },
    },
}


def _session_dict(session: ExplorationSession) -> dict[str, Any]:
    return {
        "id": session.id,
        "workflow_group_id": session.workflow_group_id,
        "title": session.title,
        "objective": session.objective,
        "status": session.status,
        "model": session.model,
        "project_id": session.project_id,
        "current_cycle": session.current_cycle,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        "last_activity_at": session.last_activity_at.isoformat() if session.last_activity_at else None,
        "max_cycles": MAX_EXPLORATION_CYCLES,
    }


def _cycle_dict(cycle: ExplorationCycle) -> dict[str, Any]:
    return {
        "id": cycle.id,
        "cycle_number": cycle.cycle_number,
        "dag_id": cycle.dag_id,
        "status": cycle.status,
        "feedback": cycle.feedback,
        "finalized_at": cycle.finalized_at.isoformat() if cycle.finalized_at else None,
        "created_at": cycle.created_at.isoformat() if cycle.created_at else None,
    }


def _message_dict(message: ExplorationMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "session_id": message.session_id,
        "cycle_id": message.cycle_id,
        "role": message.role,
        "kind": message.kind,
        "content": message.content,
        "metadata": message.metadata_json or {},
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def _request_dict(request: DagUserRequest) -> dict[str, Any]:
    return {
        "id": request.id,
        "dag_id": request.dag_id,
        "node_id": request.node_id,
        "task_id": request.task_id,
        "kind": request.kind,
        "prompt": request.prompt,
        "payload": request.payload or {},
        "status": request.status,
        "answer": request.answer,
        "created_at": request.created_at.isoformat() if request.created_at else None,
    }


async def _session_or_404(session_id: str, db: AsyncSession) -> ExplorationSession:
    result = await db.execute(select(ExplorationSession).where(ExplorationSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Exploration session not found")
    return session


@router.post("")
async def create_session(
    objective: str = Form(...),
    model: Optional[str] = Form(None),
    project_id: Optional[str] = Form(None),
    skill_ids: Optional[str] = Form(None),
    files: list[UploadFile] = File(default=[]),
    db: AsyncSession = Depends(get_db),
):
    parsed_skills = []
    if skill_ids:
        try:
            parsed_skills = json.loads(skill_ids)
        except Exception:
            parsed_skills = []
    session, dag, cycle = await create_exploration_session(
        objective,
        model,
        project_id or None,
        db,
        files=files,
        skill_ids=parsed_skills if isinstance(parsed_skills, list) else [],
    )
    await reconcile_session(session, db)
    return {
        "session": _session_dict(session),
        "cycle": _cycle_dict(cycle),
        "dag_id": dag.id,
    }


@router.get("")
async def list_sessions(
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=300),
    db: AsyncSession = Depends(get_db),
):
    query = select(ExplorationSession)
    if project_id == "__general__":
        query = query.where(ExplorationSession.project_id.is_(None))
    elif project_id:
        query = query.where(ExplorationSession.project_id == project_id)
    if status:
        query = query.where(ExplorationSession.status == status)
    result = await db.execute(query.order_by(ExplorationSession.last_activity_at.desc()).limit(limit))
    sessions = list(result.scalars().all())

    items = []
    for session in sessions:
        cycles_result = await db.execute(
            select(ExplorationCycle)
            .where(ExplorationCycle.session_id == session.id)
            .order_by(ExplorationCycle.cycle_number.desc())
        )
        cycles = list(cycles_result.scalars().all())
        latest_cycle = cycles[0] if cycles else None
        items.append({
            **_session_dict(session),
            "cycle_count": len(cycles),
            "latest_cycle": _cycle_dict(latest_cycle) if latest_cycle else None,
        })
    return items


async def _build_progress(session: ExplorationSession, db: AsyncSession) -> dict[str, Any] | None:
    from routers.llm import get_task_interaction_summary

    cycles_result = await db.execute(
        select(ExplorationCycle)
        .where(ExplorationCycle.session_id == session.id)
        .order_by(ExplorationCycle.cycle_number.desc())
        .limit(1)
    )
    cycle = cycles_result.scalars().first()
    if not cycle:
        return None
    dag_result = await db.execute(select(MasterDAG).where(MasterDAG.id == cycle.dag_id))
    dag = dag_result.scalar_one_or_none()
    if not dag:
        return None
    nodes_result = await db.execute(
        select(DAGNode).where(DAGNode.dag_id == dag.id)
    )
    nodes = list(nodes_result.scalars().all())
    by_id = {node.node_id: node for node in nodes}
    order = [
        str(item.get("node_id"))
        for item in (dag.dag_json or {}).get("nodes", [])
        if item.get("node_id")
    ]
    ordered = [by_id[node_id] for node_id in order if node_id in by_id]
    ordered.extend(node for node in nodes if node.node_id not in set(order))

    counts = {"completed": 0, "failed": 0, "running": 0, "pending": 0}
    progress_nodes = []
    for node in ordered:
        status = str(getattr(node.status, "value", node.status) or "pending")
        if status in counts:
            counts[status] += 1
        summary = {"count": 0, "latest_turn": None, "latest_activity": None}
        if node.task_id and status in ("running", "waiting_input"):
            summary = get_task_interaction_summary(node.task_id)
        progress_nodes.append({
            "node_id": node.node_id,
            "description": (node.description or node.node_id)[:160],
            "status": status,
            "task_id": node.task_id,
            "turns": summary.get("count") if status == "running" else None,
            "latest_activity": summary.get("latest_activity") if status == "running" else None,
        })

    elapsed = None
    if dag.started_at:
        end = dag.completed_at or datetime.utcnow()
        try:
            elapsed = max(0, int((end - dag.started_at).total_seconds()))
        except Exception:
            elapsed = None

    return {
        "session_status": session.status,
        "cycle_number": cycle.cycle_number,
        "dag_id": dag.id,
        "dag_status": str(getattr(dag.status, "value", dag.status) or "unknown"),
        "elapsed_seconds": elapsed,
        "total_nodes": len(ordered),
        "completed_nodes": counts["completed"],
        "failed_nodes": counts["failed"],
        "running_nodes": counts["running"],
        "pending_nodes": counts["pending"],
        "active_node_ids": [
            node["node_id"] for node in progress_nodes if node["status"] == "running"
        ],
        "nodes": progress_nodes,
    }


@router.get("/{session_id}/progress")
async def get_progress(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await _session_or_404(session_id, db)
    await reconcile_session(session, db)
    progress = await _build_progress(session, db)
    return {"session_status": session.status, "progress": progress}


@router.get("/{session_id}")
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await _session_or_404(session_id, db)
    await reconcile_session(session, db)

    cycles_result = await db.execute(
        select(ExplorationCycle)
        .where(ExplorationCycle.session_id == session.id)
        .order_by(ExplorationCycle.cycle_number.asc())
    )
    cycles = list(cycles_result.scalars().all())
    messages_result = await db.execute(
        select(ExplorationMessage)
        .where(ExplorationMessage.session_id == session.id)
        .order_by(ExplorationMessage.created_at.asc())
    )
    messages = list(messages_result.scalars().all())

    active_dag_id = cycles[-1].dag_id if cycles else None
    requests: list[DagUserRequest] = []
    if active_dag_id:
        req_result = await db.execute(
            select(DagUserRequest)
            .where(DagUserRequest.dag_id == active_dag_id)
            .order_by(DagUserRequest.created_at.desc())
        )
        requests = list(req_result.scalars().all())

    return {
        "session": _session_dict(session),
        "cycles": [_cycle_dict(c) for c in cycles],
        "messages": [_message_dict(m) for m in messages],
        "pending_requests": [_request_dict(r) for r in requests if r.status == "pending"],
        "requests": [_request_dict(r) for r in requests],
    }


@router.patch("/{session_id}")
async def update_session(session_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    session = await _session_or_404(session_id, db)
    if "model" in body:
        session.model = str(body["model"]) if body["model"] else None
    session.last_activity_at = datetime.utcnow()
    await db.commit()
    return _session_dict(session)


@router.post("/{session_id}/finish")
async def finish(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await _session_or_404(session_id, db)
    if session.status in ("running", "awaiting_input"):
        raise HTTPException(status_code=400, detail="Cancel the running cycle before finishing")
    await finish_session(session, db)
    return _session_dict(session)


@router.post("/{session_id}/feedback")
async def feedback(session_id: str, body: ExplorationFeedbackRequest, db: AsyncSession = Depends(get_db)):
    session = await _session_or_404(session_id, db)
    await reconcile_session(session, db)
    try:
        dag, cycle = await plan_next_cycle(session, body.feedback, db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        session.status = "error"
        await db.commit()
        raise HTTPException(status_code=500, detail=f"Exploration planning failed: {exc}")
    return {
        "session": _session_dict(session),
        "cycle": _cycle_dict(cycle),
        "dag_id": dag.id,
    }


@router.post("/{session_id}/plan")
async def plan_next(session_id: str, body: Optional[dict] = None, db: AsyncSession = Depends(get_db)):
    session = await _session_or_404(session_id, db)
    await reconcile_session(session, db)
    feedback_text = str((body or {}).get("feedback") or "Continue the exploration with the next useful step.")
    try:
        dag, cycle = await plan_next_cycle(session, feedback_text, db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"session": _session_dict(session), "cycle": _cycle_dict(cycle), "dag_id": dag.id}


@router.post("/{session_id}/cycles/{dag_id}/start")
async def start_cycle(session_id: str, dag_id: str, db: AsyncSession = Depends(get_db)):
    session = await _session_or_404(session_id, db)
    if session.status not in ("awaiting_run", "awaiting_feedback"):
        raise HTTPException(status_code=400, detail="Exploration is not waiting to run a cycle")
    dag_result = await db.execute(
        select(MasterDAG).where(
            MasterDAG.id == dag_id,
            MasterDAG.exploration_session_id == session.id,
        )
    )
    dag = dag_result.scalar_one_or_none()
    if not dag:
        raise HTTPException(status_code=404, detail="Cycle DAG not found for this exploration")
    if str(getattr(dag.status, "value", dag.status)) != "ready":
        raise HTTPException(status_code=400, detail="Cycle DAG is not ready to start")

    from routers.dags import start_dag
    await start_dag(dag_id, db)

    cycle_result = await db.execute(
        select(ExplorationCycle).where(
            ExplorationCycle.session_id == session.id,
            ExplorationCycle.dag_id == dag_id,
        )
    )
    cycle = cycle_result.scalar_one_or_none()
    if cycle:
        cycle.status = "running"
        await db.execute(
            delete(ExplorationMessage).where(
                ExplorationMessage.cycle_id == cycle.id,
                ExplorationMessage.kind == "cycle_result",
            )
        )

    session.status = "running"
    session.last_activity_at = datetime.utcnow()
    db.add(ExplorationMessage(
        session_id=session.id,
        cycle_id=None,
        role="system",
        kind="status",
        content=f"Cycle {session.current_cycle} started.",
        metadata_json={"dag_id": dag_id, "status": "running"},
    ))
    await db.commit()
    return {"started": True, "dag_id": dag_id}


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=True)}\n\n"


async def _chat_stream(
    session_id: str,
    message: str,
    mode: str,
    model_override: Optional[str] = None,
):
    async with async_session() as db:
        session = await _session_or_404(session_id, db)
        await reconcile_session(session, db)

        if mode == "feedback" and session.status not in ("awaiting_feedback", "error"):
            yield _sse({"type": "error", "message": "Exploration is not waiting for feedback."})
            return

        context = await build_chat_context(session, db)
        context.append({"role": "user", "content": message})

        user_message = ExplorationMessage(
            session_id=session.id,
            role="user",
            kind="text",
            content=message,
            metadata_json={"mode": mode},
        )
        db.add(user_message)
        session.last_activity_at = datetime.utcnow()
        await db.commit()
        model = model_override or session.model or "deepseek-v4-flash"

        payload: dict[str, Any] = {
            "model": model,
            "messages": context,
            "stream": True,
            "temperature": 0.6,
        }
        if mode == "feedback":
            payload["tools"] = [PROPOSE_TOOL]
            payload["tool_choice"] = "auto"

        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, str]] = {}
        error_text: Optional[str] = None

        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                async with client.stream("POST", LLM_ROUTER_URL, json=payload) as resp:
                    if resp.status_code != 200:
                        text = await resp.aread()
                        raise RuntimeError(text.decode("utf-8", errors="replace")[:400])
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data or data == "[DONE]":
                            continue
                        try:
                            parsed = json.loads(data)
                        except Exception:
                            continue
                        choices = parsed.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        text = delta.get("content")
                        if isinstance(text, str) and text:
                            content_parts.append(text)
                            yield _sse({"type": "text_delta", "text": text})
                        for tc in delta.get("tool_calls") or []:
                            index = int(tc.get("index", 0))
                            current = tool_calls.setdefault(index, {"name": "", "arguments": ""})
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                current["name"] += fn["name"]
                            if fn.get("arguments"):
                                current["arguments"] += fn["arguments"]
        except Exception as exc:
            error_text = str(exc)
            logger.warning("Exploration chat stream failed: %s", exc)
            yield _sse({"type": "error", "message": error_text[:500]})

        assistant_text = "".join(content_parts).strip()

        if mode == "feedback" and not error_text:
            yield _sse({"type": "status", "message": "Planning the next cycle…"})
            focus = message
            for call in tool_calls.values():
                if call.get("name") == "propose_exploration_cycle" and call.get("arguments"):
                    try:
                        args = json.loads(call["arguments"])
                        focus = str(args.get("focus") or focus)
                    except Exception:
                        pass
            try:
                await db.refresh(session)
                dag, cycle = await plan_next_cycle(session, focus, db)
                proposal_messages = await db.execute(
                    select(ExplorationMessage)
                    .where(
                        ExplorationMessage.cycle_id == cycle.id,
                        ExplorationMessage.kind == "cycle_proposal",
                    )
                    .order_by(ExplorationMessage.created_at.desc())
                    .limit(1)
                )
                proposal = proposal_messages.scalars().first()
                yield _sse({
                    "type": "proposal",
                    "cycle": _cycle_dict(cycle),
                    "dag_id": dag.id,
                    "message": _message_dict(proposal) if proposal else None,
                })
            except Exception as exc:
                logger.warning("Next-cycle planning failed: %s", exc)
                session.status = "error"
                await db.commit()
                yield _sse({"type": "error", "message": f"Planning failed: {exc}"})

        if assistant_text:
            db.add(ExplorationMessage(
                session_id=session.id,
                role="assistant",
                kind="text",
                content=assistant_text,
            ))
            session.last_activity_at = datetime.utcnow()
            await db.commit()

        yield _sse({"type": "done"})


@router.post("/{session_id}/chat")
async def chat(session_id: str, body: ExplorationChatRequest, db: AsyncSession = Depends(get_db)):
    await _session_or_404(session_id, db)
    if not body.message.strip():
        raise HTTPException(status_code=422, detail="message required")
    return StreamingResponse(
        _chat_stream(session_id, body.message.strip(), body.mode),
        media_type="text/event-stream",
    )
