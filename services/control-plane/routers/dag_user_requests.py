"""
DAG user-request router — decision & data-input steps.

A `decision` or `input` DAG node pauses the workflow and creates a pending
`DagUserRequest`. The user answers it from the UI; the answer is stored and the
`dag-node-{dag_id}-{node_id}` workflow is signalled with `user_input` to resume.
"""
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
from typing import Optional

from database import get_db
from models import DagUserRequest
from temporal_client import get_temporal_client

router = APIRouter()


def _serialize(r: DagUserRequest) -> dict:
    return {
        "id": r.id,
        "dag_id": r.dag_id,
        "node_id": r.node_id,
        "task_id": r.task_id,
        "kind": r.kind,
        "prompt": r.prompt,
        "payload": r.payload or {},
        "status": r.status,
        "answer": r.answer,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "answered_at": r.answered_at.isoformat() if r.answered_at else None,
    }


@router.get("/user-requests")
async def list_all_user_requests(status: Optional[str] = "pending", limit: int = 100, db: AsyncSession = Depends(get_db)):
    """List user requests across all DAGs (used by the approvals page)."""
    q = select(DagUserRequest).order_by(DagUserRequest.created_at.desc()).limit(limit)
    if status:
        q = q.where(DagUserRequest.status == status)
    result = await db.execute(q)
    return [_serialize(r) for r in result.scalars().all()]


@router.post("/{dag_id}/user-requests", status_code=201)
async def create_user_request(dag_id: str, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    """Create a pending interactive step request (worker-driven)."""
    node_id = str(body.get("node_id") or "").strip()
    kind = str(body.get("kind") or "").lower()
    if not node_id or kind not in ("decision", "input"):
        raise HTTPException(status_code=422, detail="node_id and kind (decision|input) are required")
    req = DagUserRequest(
        dag_id=dag_id,
        node_id=node_id,
        task_id=body.get("task_id"),
        kind=kind,
        prompt=str(body.get("prompt") or ""),
        payload=body.get("payload") or {},
        status="pending",
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)
    return _serialize(req)


@router.get("/{dag_id}/user-requests")
async def list_user_requests(dag_id: str, status: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """List user requests for a DAG (optionally filtered by status)."""
    q = select(DagUserRequest).where(DagUserRequest.dag_id == dag_id)
    if status:
        q = q.where(DagUserRequest.status == status)
    result = await db.execute(q.order_by(DagUserRequest.created_at.desc()))
    return [_serialize(r) for r in result.scalars().all()]


@router.post("/{dag_id}/user-requests/{request_id}/answer")
async def answer_user_request(dag_id: str, request_id: int, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    """Record the user's answer and signal the DAG node workflow to resume."""
    req = await db.get(DagUserRequest, request_id)
    if not req or req.dag_id != dag_id:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.status == "answered":
        raise HTTPException(status_code=409, detail="Request already answered")
    answer = body.get("answer")
    if answer is None:
        raise HTTPException(status_code=422, detail="answer required")

    req.status = "answered"
    req.answer = answer
    req.answered_by = body.get("answered_by")
    req.answered_at = datetime.utcnow()
    await db.commit()

    # Signal the DAG node workflow so it resumes from its wait.
    signal_error = None
    try:
        client = await get_temporal_client()
        handle = client.get_workflow_handle(f"dag-node-{dag_id}-{req.node_id}")
        await handle.signal("user_input", answer)
    except Exception as exc:
        signal_error = str(exc)[:300]

    result = _serialize(req)
    if signal_error:
        result["signal_error"] = signal_error
    return result
