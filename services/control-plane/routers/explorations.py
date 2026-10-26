"""Exploration session APIs and streaming chat."""
import asyncio
import json
import logging
from dataclasses import asdict
from datetime import datetime
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session, get_db
from explorations import (
    MAX_EXPLORATION_CYCLES,
    THINKING_BUDGET_LEVELS,
    QUALITY_BUDGET_LEVELS,
    build_chat_context,
    create_exploration_session,
    finish_session,
    pending_capability_requests,
    plan_next_cycle,
    reconcile_session,
)
from explorations_deploy import detect_cycle_deployables
from models import (
    DAGNode,
    DagUserRequest,
    ExplorationCycle,
    ExplorationMessage,
    ExplorationSession,
    MasterDAG,
    DAGStatus,
    NodeStatus,
    TaskOutput,
    AgentTurn,
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
            "Prepare the next ready DAG cycle for this exploration. Use this only "
            "when the user gives an actionable change or next-cycle instruction, "
            "not for questions, explanations, status, or finalization. This only "
            "plans a workflow; it never starts it."
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


QUEUE_FEEDBACK_TOOL = {
    "type": "function",
    "function": {
        "name": "queue_exploration_feedback",
        "description": (
            "Queue an actionable feedback/change request for the next cycle while "
            "the current cycle is running or a cycle is planned. Do not use for "
            "questions, status, explanations, or finalization."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "description": "Concise feedback for the next cycle.",
                }
            },
            "required": ["focus"],
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
        "thinking_budget": session.thinking_budget or "standard",
        "quality_budget": session.quality_budget or "standard",
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


def _approval_dict(request) -> dict[str, Any]:
    return {
        "id": request.id,
        "task_id": request.task_id,
        "capability_type": getattr(request.capability_type, "value", request.capability_type),
        "resource_name": request.resource_name,
        "justification": request.justification,
        "status": getattr(request.status, "value", request.status),
        "requested_at": request.requested_at.isoformat() if request.requested_at else None,
        "decided_at": request.decided_at.isoformat() if request.decided_at else None,
        "decision_notes": request.decision_notes,
        "details": request.details or {},
        "alternative_suggestion": request.alternative_suggestion,
    }


@router.post("")
async def create_session(
    objective: str = Form(...),
    model: Optional[str] = Form(None),
    project_id: Optional[str] = Form(None),
    thinking_budget: Optional[str] = Form("standard"),
    quality_budget: Optional[str] = Form("standard"),
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
        thinking_budget=thinking_budget or "standard",
        quality_budget=quality_budget or "standard",
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
        iteration_count = 0
        durable_turns = 0
        approval_wait_ms = 0
        if node.task_id:
            output_stats = await db.execute(
                select(
                    func.count(TaskOutput.id),
                    func.coalesce(func.sum(TaskOutput.turn_count), 0),
                    func.coalesce(func.sum(TaskOutput.approval_wait_ms), 0),
                ).where(TaskOutput.task_id == node.task_id)
            )
            iteration_count, durable_turns, approval_wait_ms = output_stats.one()
            turn_stats = await db.execute(
                select(func.count(AgentTurn.id)).where(AgentTurn.task_id == node.task_id)
            )
            durable_turns = max(int(durable_turns or 0), int(turn_stats.scalar() or 0))
        cfg = node.config or {}
        progress_nodes.append({
            "node_id": node.node_id,
            "description": (node.description or node.node_id)[:160],
            "status": status,
            "task_id": node.task_id,
            "turns": max(int(summary.get("count") or 0), durable_turns),
            "latest_activity": summary.get("latest_activity") if status == "running" else None,
            "iteration_count": int(iteration_count or 0),
            "turn_count": durable_turns,
            "turn_budget": cfg.get("max_turns"),
            "max_iterations": cfg.get("max_iterations"),
            "approval_wait_ms": int(approval_wait_ms or 0),
            "verification_mode": cfg.get("verification_mode"),
        })

    elapsed = None
    if dag.started_at:
        end = dag.completed_at or datetime.utcnow()
        try:
            elapsed = max(0, int((end - dag.started_at).total_seconds()))
        except Exception:
            elapsed = None

    approvals = [_approval_dict(request) for request in await pending_capability_requests(dag.id, db)]
    return {
        "session_status": session.status,
        "cycle_number": cycle.cycle_number,
        "dag_id": dag.id,
        "workflow_id": dag.workflow_id,
        "dag_status": str(getattr(dag.status, "value", dag.status) or "unknown"),
        "pending_approvals": approvals,
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


@router.get("/{session_id}/deployables")
async def get_deployables(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await _session_or_404(session_id, db)
    deployables = await detect_cycle_deployables(session, db)
    return {"deployables": [asdict(item) for item in deployables]}


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
    approvals: list[dict[str, Any]] = []
    if active_dag_id:
        req_result = await db.execute(
            select(DagUserRequest)
            .where(DagUserRequest.dag_id == active_dag_id)
            .order_by(DagUserRequest.created_at.desc())
        )
        requests = list(req_result.scalars().all())
        approvals = [_approval_dict(request) for request in await pending_capability_requests(active_dag_id, db)]

    return {
        "session": _session_dict(session),
        "cycles": [_cycle_dict(c) for c in cycles],
        "messages": [_message_dict(m) for m in messages],
        "pending_requests": [_request_dict(r) for r in requests if r.status == "pending"],
        "requests": [_request_dict(r) for r in requests],
        "approvals": approvals,
    }


@router.patch("/{session_id}")
async def update_session(session_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    session = await _session_or_404(session_id, db)
    if "model" in body:
        session.model = str(body["model"]) if body["model"] else None
    if "thinking_budget" in body:
        value = str(body["thinking_budget"] or "standard")
        if value not in THINKING_BUDGET_LEVELS:
            raise HTTPException(status_code=400, detail="Invalid thinking_budget")
        session.thinking_budget = value
    if "quality_budget" in body:
        value = str(body["quality_budget"] or "standard")
        if value not in QUALITY_BUDGET_LEVELS:
            raise HTTPException(status_code=400, detail="Invalid quality_budget")
        session.quality_budget = value
    session.last_activity_at = datetime.utcnow()
    await db.commit()
    await db.refresh(session)
    return _session_dict(session)


@router.post("/{session_id}/finish")
async def finish(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await _session_or_404(session_id, db)
    if session.status in ("running", "awaiting_input"):
        raise HTTPException(status_code=400, detail="Cancel the running cycle before finishing")
    await finish_session(session, db)
    await db.refresh(session)
    return _session_dict(session)


@router.post("/{session_id}/feedback")
async def feedback(session_id: str, body: ExplorationFeedbackRequest, db: AsyncSession = Depends(get_db)):
    session = await _session_or_404(session_id, db)
    await reconcile_session(session, db)
    try:
        dag, cycle = await plan_next_cycle(session, body.feedback, db, source_feedback=body.feedback)
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
        dag, cycle = await plan_next_cycle(session, feedback_text, db, source_feedback=feedback_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"session": _session_dict(session), "cycle": _cycle_dict(cycle), "dag_id": dag.id}


@router.post("/{session_id}/cycles/{dag_id}/retry")
async def retry_cycle(session_id: str, dag_id: str, db: AsyncSession = Depends(get_db)):
    """Rerun a terminal exploration cycle in place."""
    session = await _session_or_404(session_id, db)
    if session.status in ("running", "awaiting_input", "awaiting_approval"):
        raise HTTPException(status_code=400, detail="Exploration is currently running")
    if session.status not in ("awaiting_feedback", "error"):
        raise HTTPException(status_code=400, detail="Exploration is not ready to retry a cycle")

    dag_result = await db.execute(
        select(MasterDAG).where(
            MasterDAG.id == dag_id,
            MasterDAG.exploration_session_id == session.id,
        )
    )
    dag = dag_result.scalar_one_or_none()
    if not dag:
        raise HTTPException(status_code=404, detail="Cycle DAG not found for this exploration")

    dag_status = str(getattr(dag.status, "value", dag.status))
    if dag_status not in ("cancelled", "failed", "completed"):
        raise HTTPException(status_code=400, detail=f"Cycle DAG is not terminal (status: {dag_status})")

    cycle_result = await db.execute(
        select(ExplorationCycle).where(
            ExplorationCycle.session_id == session.id,
            ExplorationCycle.dag_id == dag_id,
        )
    )
    cycle = cycle_result.scalar_one_or_none()
    if not cycle:
        raise HTTPException(status_code=404, detail="Exploration cycle not found")

    nodes_result = await db.execute(select(DAGNode).where(DAGNode.dag_id == dag_id))
    for node in nodes_result.scalars().all():
        node.status = NodeStatus.PENDING
        node.output_data = None
        node.task_id = None
        node.container_id = None
        node.started_at = None
        node.completed_at = None

    dag.status = DAGStatus.READY
    dag.completed_at = None
    dag.started_at = None

    await db.execute(
        delete(ExplorationMessage).where(
            ExplorationMessage.cycle_id == cycle.id,
            ExplorationMessage.kind == "cycle_result",
        )
    )
    await db.commit()
    await db.refresh(dag)

    from routers.dags import start_dag
    await start_dag(dag_id, db)

    cycle.status = "running"
    session.status = "running"
    session.last_activity_at = datetime.utcnow()
    db.add(ExplorationMessage(
        session_id=session.id,
        cycle_id=None,
        role="system",
        kind="status",
        content=f"Cycle {cycle.cycle_number} restarted.",
        metadata_json={"dag_id": dag_id, "status": "running"},
    ))
    await db.commit()
    return {"session_id": session.id, "cycle_number": cycle.cycle_number, "dag_id": dag_id, "status": "running"}


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


QUEUEABLE_FEEDBACK_STATUSES = {
    "planning",
    "running",
    "awaiting_run",
    "awaiting_input",
    "awaiting_approval",
}


async def _queued_feedback_messages(db: AsyncSession, session_id: str) -> list[ExplorationMessage]:
    result = await db.execute(
        select(ExplorationMessage)
        .where(
            ExplorationMessage.session_id == session_id,
            ExplorationMessage.role == "user",
        )
        .order_by(ExplorationMessage.created_at.asc())
    )
    queued: list[ExplorationMessage] = []
    for message in result.scalars().all():
        metadata = message.metadata_json or {}
        if metadata.get("feedback_queue") is True and not metadata.get("feedback_consumed_cycle"):
            queued.append(message)
    return queued


async def _mark_feedback_consumed(
    db: AsyncSession,
    messages: list[ExplorationMessage],
    cycle_number: int,
) -> None:
    for message in messages:
        metadata = dict(message.metadata_json or {})
        metadata["feedback_queue"] = False
        metadata["feedback_consumed_cycle"] = cycle_number
        message.metadata_json = metadata
    if messages:
        await db.commit()


async def _chat_stream(
    session_id: str,
    message: str,
    mode: str,
    model_override: Optional[str] = None,
):
    async with async_session() as db:
        session = await _session_or_404(session_id, db)
        await reconcile_session(session, db)
        can_plan = session.status in ("awaiting_feedback", "error")
        queueable = session.status in QUEUEABLE_FEEDBACK_STATUSES

        if mode == "feedback" and not can_plan:
            if session.status in QUEUEABLE_FEEDBACK_STATUSES:
                queued_message = ExplorationMessage(
                    session_id=session.id,
                    role="user",
                    kind="text",
                    content=message,
                    metadata_json={
                        "mode": "feedback",
                        "feedback_queue": True,
                        "queued_at": datetime.utcnow().isoformat(),
                        "queued_cycle": session.current_cycle,
                    },
                )
                ack = f"Feedback queued for after Cycle {session.current_cycle}."
                db.add(queued_message)
                db.add(ExplorationMessage(
                    session_id=session.id,
                    role="assistant",
                    kind="text",
                    content=ack,
                ))
                session.last_activity_at = datetime.utcnow()
                await db.commit()
                yield _sse({"type": "text_delta", "text": ack})
                yield _sse({"type": "done"})
                return
            yield _sse({"type": "error", "message": "Exploration is not waiting for feedback."})
            return

        if mode in ("auto", "chat") and session.status == "awaiting_run":
            query = message.lower().strip()
            status_question = any(
                phrase in query
                for phrase in (
                    "is it finalized", "is it done", "is it complete",
                    "is it finished", "is it ready", "has it completed",
                    "has it finished", "what is the status", "what's the status",
                    "what score", "what is the score", "what are the deliverables",
                    "are the deliverables", "what did cycle", "why did cycle",
                    "did the cycle", "did cycle", "what did it produce",
                )
            )
            if status_question:
                cycle_number = session.current_cycle
                answer = (
                    f"No. Cycle {cycle_number} is only planned and has not run yet, "
                    "so there are no deliverables or audit score. Expand the Agent "
                    "Plan and click Run cycle; after it completes I can review the "
                    "results and finalize."
                )
                db.add(ExplorationMessage(
                    session_id=session.id,
                    role="user",
                    kind="text",
                    content=message,
                    metadata_json={"mode": mode},
                ))
                db.add(ExplorationMessage(
                    session_id=session.id,
                    role="assistant",
                    kind="text",
                    content=answer,
                ))
                session.last_activity_at = datetime.utcnow()
                await db.commit()
                yield _sse({"type": "text_delta", "text": answer})
                yield _sse({"type": "done"})
                return

        queued_feedback = await _queued_feedback_messages(db, session.id)
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
        if mode == "auto" and can_plan:
            payload["tools"] = [PROPOSE_TOOL]
            payload["tool_choice"] = "auto"
        elif mode == "auto" and queueable:
            payload["tools"] = [QUEUE_FEEDBACK_TOOL]
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
                        if isinstance(text, str) and text and mode != "feedback":
                            content_parts.append(text)
                            if mode == "chat":
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
        propose_called = any(
            call.get("name") == "propose_exploration_cycle"
            for call in tool_calls.values()
        )
        queue_called = any(
            call.get("name") == "queue_exploration_feedback"
            for call in tool_calls.values()
        )
        suppress_text = bool((propose_called or queue_called) and mode in ("auto", "feedback"))

        if queue_called and queueable and mode == "auto" and not error_text:
            metadata = dict(user_message.metadata_json or {})
            metadata.update({
                "feedback_queue": True,
                "queued_at": datetime.utcnow().isoformat(),
                "queued_cycle": session.current_cycle,
                "queued_by": "intent",
            })
            user_message.metadata_json = metadata
            ack = f"Feedback queued for after Cycle {session.current_cycle}."
            db.add(ExplorationMessage(
                session_id=session.id,
                role="assistant",
                kind="text",
                content=ack,
            ))
            session.last_activity_at = datetime.utcnow()
            await db.commit()
            yield _sse({"type": "text_delta", "text": ack})
            yield _sse({"type": "done"})
            return

        # Persist the chat answer before the proposal so the transcript reads
        # user -> assistant -> proposal, and so the legacy narration filter does
        # not hide a legitimate chat answer stored after a proposal. When the
        # model is only narrating a planning tool call, do not persist that
        # internal reasoning; the proposal card is the answer.
        if assistant_text and not suppress_text:
            db.add(ExplorationMessage(
                session_id=session.id,
                role="assistant",
                kind="text",
                content=assistant_text,
            ))
            session.last_activity_at = datetime.utcnow()
            await db.commit()
            if mode == "auto":
                yield _sse({"type": "text_delta", "text": assistant_text})

        should_plan = not error_text and (
            mode == "feedback"
            or (mode == "auto" and can_plan and propose_called)
        )

        if should_plan:
            yield _sse({"type": "status", "message": "Planning the next cycle…"})
            focus = message
            for call in tool_calls.values():
                if call.get("name") == "propose_exploration_cycle" and call.get("arguments"):
                    try:
                        args = json.loads(call["arguments"])
                        focus = str(args.get("focus") or focus)
                    except Exception:
                        pass
            if mode in ("chat", "auto"):
                recent = [
                    f"{item.get('role')}: {str(item.get('content') or '')[:600]}"
                    for item in context[1:]
                    if item.get("role") in ("user", "assistant")
                ][-6:]
                if recent:
                    focus = f"{focus}\n\nRecent conversation:\n" + "\n".join(recent)
                focus = focus[-6000:]
            if queued_feedback:
                queued_text = "\n".join(
                    f"- {item.content}" for item in queued_feedback
                )
                focus = f"{focus}\n\nQUEUED FEEDBACK FROM EARLIER:\n{queued_text}"[-8000:]
            try:
                await db.refresh(session)
                dag, cycle = await plan_next_cycle(session, focus, db, source_feedback=message)
                await _mark_feedback_consumed(db, queued_feedback, cycle.cycle_number)
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

        yield _sse({"type": "done"})


@router.post("/{session_id}/feedback/plan")
async def plan_queued_feedback(session_id: str, db: AsyncSession = Depends(get_db)):
    """Plan the next cycle from feedback queued while a cycle was running."""
    session = await _session_or_404(session_id, db)
    await reconcile_session(session, db)
    if session.status not in ("awaiting_feedback", "error"):
        raise HTTPException(status_code=400, detail="Exploration is not waiting for feedback")
    queued = await _queued_feedback_messages(db, session.id)
    if not queued:
        raise HTTPException(status_code=400, detail="No queued feedback to plan from")
    focus = "\n".join(item.content for item in queued if item.content).strip()
    if not focus:
        raise HTTPException(status_code=400, detail="Queued feedback is empty")
    try:
        await db.refresh(session)
        dag, cycle = await plan_next_cycle(session, focus, db, source_feedback=focus)
    except Exception as exc:
        logger.warning("Queued feedback planning failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Planning failed: {exc}")
    await _mark_feedback_consumed(db, queued, cycle.cycle_number)
    proposal_result = await db.execute(
        select(ExplorationMessage)
        .where(
            ExplorationMessage.cycle_id == cycle.id,
            ExplorationMessage.kind == "cycle_proposal",
        )
        .order_by(ExplorationMessage.created_at.desc())
        .limit(1)
    )
    proposal = proposal_result.scalars().first()
    return {
        "cycle": _cycle_dict(cycle),
        "dag_id": dag.id,
        "message": _message_dict(proposal) if proposal else None,
    }


@router.post("/{session_id}/chat")
async def chat(session_id: str, body: ExplorationChatRequest, db: AsyncSession = Depends(get_db)):
    await _session_or_404(session_id, db)
    if not body.message.strip():
        raise HTTPException(status_code=422, detail="message required")
    return StreamingResponse(
        _chat_stream(session_id, body.message.strip(), body.mode),
        media_type="text/event-stream",
    )
