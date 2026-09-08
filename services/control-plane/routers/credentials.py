"""Credential approval requests surfaced to the Approvals page.

The credential gateway (services/credential-gateway) creates requests here on
first credentialed use and polls GET /{id} until a human reviews them.
"""
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import CredentialRequest, CredentialGrantLog, Task
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/credential-requests", tags=["credential-requests"])


async def _signal_task_workflows(task_id: str, approved: bool, db: AsyncSession) -> None:
    """Resume any paused AgentTaskWorkflow waiting on this credential decision
    (mirrors the capability 'approve_capability' signal)."""
    candidates: List[str] = []
    try:
        trow = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if trow and trow.workflow_id:
            candidates.append(trow.workflow_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("credential signal: task lookup failed: %s", exc)
    candidates.append(f"agent-task-{task_id}")
    try:
        from temporal_client import get_temporal_client
        client = await get_temporal_client()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"⚠️ Credential review: could not connect to Temporal: {exc}")
        return
    for wf_id in dict.fromkeys(candidates):
        try:
            handle = client.get_workflow_handle(wf_id)
            await handle.signal("approve_capability", approved)
            logger.info(f"🔐 Sent credential decision signal to workflow {wf_id}: approved={approved}")
        except Exception as sig_err:  # noqa: BLE001
            logger.warning(f"Could not signal workflow {wf_id}: {sig_err}")


def _serialize(r: CredentialRequest) -> Dict[str, Any]:
    return {
        "id": r.id,
        "agent_session": r.agent_session,
        "credential_name": r.credential_name,
        "origin": r.origin,
        "method": r.method,
        "reason": r.reason or "",
        "status": r.status,
        "ttl_minutes": r.ttl_minutes,
        "allowed_methods": r.allowed_methods or ["GET", "POST"],
        "requested_at": r.requested_at.isoformat() if r.requested_at else None,
        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
        "reviewed_by": r.reviewed_by,
    }


@router.get("")
async def list_credential_requests(
    status_filter: Optional[str] = None,
    agent_session: Optional[str] = None,
    credential_name: Optional[str] = None,
    origin: Optional[str] = None,
    method: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    q = select(CredentialRequest).order_by(CredentialRequest.requested_at.desc())
    if status_filter:
        q = q.where(CredentialRequest.status == status_filter)
    if agent_session:
        q = q.where(CredentialRequest.agent_session == agent_session)
    if credential_name:
        q = q.where(CredentialRequest.credential_name == credential_name)
    if origin:
        q = q.where(CredentialRequest.origin == origin)
    if method:
        q = q.where(CredentialRequest.method == method.upper())
    rows = (await db.execute(q)).scalars().all()
    return [_serialize(r) for r in rows]


@router.get("/{request_id}")
async def get_credential_request(request_id: int, db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    r = await db.get(CredentialRequest, request_id)
    if not r:
        raise HTTPException(status_code=404, detail="credential request not found")
    return _serialize(r)


@router.post("")
async def create_credential_request(
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    agent = str(payload.get("agent_session") or "")
    credential = str(payload.get("credential_name") or "")
    origin = str(payload.get("origin") or "")
    method = str(payload.get("method") or "GET").upper()
    reason = str(payload.get("reason") or "")
    if not agent or not credential or not origin:
        raise HTTPException(status_code=400, detail="agent_session, credential_name and origin are required")

    # Reuse an existing pending request for the same scope instead of spamming.
    q = select(CredentialRequest).where(
        CredentialRequest.status == "pending",
        CredentialRequest.agent_session == agent,
        CredentialRequest.credential_name == credential,
        CredentialRequest.origin == origin,
        CredentialRequest.method == method,
    ).order_by(CredentialRequest.requested_at.asc())
    existing = (await db.execute(q)).scalars().first()
    if existing:
        await db.commit()
        return _serialize(existing)

    r = CredentialRequest(
        agent_session=agent,
        credential_name=credential,
        origin=origin,
        method=method,
        reason=reason,
        status="pending",
        ttl_minutes=int(payload.get("ttl_minutes") or 5),
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    logger.info(f"🔐 Credential request #{r.id}: {credential} @ {origin} by {agent} ({method})")
    return _serialize(r)


@router.post("/{request_id}/review")
async def review_credential_request(
    request_id: int,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    r = await db.get(CredentialRequest, request_id)
    if not r:
        raise HTTPException(status_code=404, detail="credential request not found")
    decision = str(payload.get("decision") or "")
    if decision not in ("approved", "denied"):
        raise HTTPException(status_code=400, detail="decision must be 'approved' or 'denied'")

    r.status = decision
    r.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    r.reviewed_by = str(payload.get("reviewed_by") or "web-ui")
    if decision == "approved":
        if payload.get("ttl_minutes") is not None:
            r.ttl_minutes = int(payload["ttl_minutes"])
        if payload.get("allowed_methods"):
            r.allowed_methods = payload["allowed_methods"]
        db.add(CredentialGrantLog(
            request_id=r.id,
            agent_session=r.agent_session,
            credential_name=r.credential_name,
            origin=r.origin,
            method=r.method,
            ttl_minutes=r.ttl_minutes,
            reviewed_by=r.reviewed_by,
        ))
    await db.commit()
    await db.refresh(r)

    # Resume a paused AgentTaskWorkflow if this decision concerns one.
    if r.agent_session and r.agent_session.startswith("task:"):
        await _signal_task_workflows(r.agent_session.split(":", 1)[1], decision == "approved", db)

    return _serialize(r)
