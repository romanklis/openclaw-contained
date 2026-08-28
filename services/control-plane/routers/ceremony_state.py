"""
Ceremony State router — Artifacts, Verdicts & Agent State Exchange

Provides API-tracked ceremony artifacts, immutable verdicts, and 
structured agent-to-agent state exchange.  Replaces filesystem-based
ceremony files with DB-backed records for full traceability.

Endpoints:
    POST   /api/task-forces/{tf_id}/artifacts          — create artifact
    GET    /api/task-forces/{tf_id}/artifacts          — list artifacts
    GET    /api/task-forces/{tf_id}/artifacts/{id}     — get single artifact
    GET    /api/task-forces/{tf_id}/verdict            — get latest verdict
    POST   /api/tasks/{task_id}/verdict                — submit verdict (immutable)
    POST   /api/task-forces/{tf_id}/state              — post state exchange
    GET    /api/task-forces/{tf_id}/state              — list state exchanges
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import List, Optional
from datetime import datetime

from database import get_db
from models import (
    CeremonyArtifact, ArtifactKind,
    AgentStateExchange,
    TaskForce, Task,
)
from schemas import (
    CeremonyArtifactCreate, CeremonyArtifactResponse,
    VerdictSubmit, VerdictResponse,
    AgentStateExchangeCreate, AgentStateExchangeResponse,
)

import logging

logger = logging.getLogger(__name__)

router = APIRouter()


# =========================================================================
# Ceremony Artifacts
# =========================================================================

@router.post(
    "/task-forces/{tf_id}/artifacts",
    response_model=CeremonyArtifactResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["ceremony-state"],
)
async def create_artifact(
    tf_id: str,
    data: CeremonyArtifactCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create an immutable ceremony artifact.

    Once created, artifacts cannot be modified.  If a new version is
    needed (e.g. rework cycle produces new review brief), create a new
    artifact and the old one will be automatically superseded.
    """
    # Validate task force exists
    result = await db.execute(select(TaskForce).where(TaskForce.id == tf_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Task Force not found.")

    # For verdict artifacts, validate the verdict value
    if data.kind == ArtifactKind.VERDICT:
        if data.verdict not in ("pass", "fail"):
            raise HTTPException(400, "Verdict artifacts must have verdict='pass' or 'fail'.")

    artifact = CeremonyArtifact(
        task_force_id=tf_id,
        ceremony_id=data.ceremony_id,
        task_id=data.task_id,
        kind=data.kind.value,
        filename=data.filename,
        title=data.title,
        content=data.content,
        metadata_json=data.metadata,
        verdict=data.verdict,
        rework_cycle=data.rework_cycle,
    )
    db.add(artifact)
    await db.flush()

    # Supersede previous artifacts of the same kind + ceremony
    if data.ceremony_id is not None:
        prev_result = await db.execute(
            select(CeremonyArtifact).where(
                CeremonyArtifact.task_force_id == tf_id,
                CeremonyArtifact.ceremony_id == data.ceremony_id,
                CeremonyArtifact.kind == data.kind.value,
                CeremonyArtifact.id != artifact.id,
                CeremonyArtifact.superseded_by.is_(None),
            )
        )
        for prev in prev_result.scalars().all():
            prev.superseded_by = artifact.id

    await db.commit()
    await db.refresh(artifact)

    logger.info(
        f"📝 Artifact created: #{artifact.id} kind={data.kind.value} "
        f"tf={tf_id} verdict={data.verdict}"
    )
    return artifact


@router.get(
    "/task-forces/{tf_id}/artifacts",
    response_model=List[CeremonyArtifactResponse],
    tags=["ceremony-state"],
)
async def list_artifacts(
    tf_id: str,
    kind: Optional[str] = Query(None, description="Filter by artifact kind"),
    ceremony_id: Optional[int] = Query(None, description="Filter by ceremony ID"),
    active_only: bool = Query(True, description="Exclude superseded artifacts"),
    db: AsyncSession = Depends(get_db),
):
    """List ceremony artifacts for a task force."""
    q = select(CeremonyArtifact).where(
        CeremonyArtifact.task_force_id == tf_id
    )
    if kind:
        q = q.where(CeremonyArtifact.kind == kind)
    if ceremony_id is not None:
        q = q.where(CeremonyArtifact.ceremony_id == ceremony_id)
    if active_only:
        q = q.where(CeremonyArtifact.superseded_by.is_(None))
    q = q.order_by(CeremonyArtifact.created_at.desc())

    result = await db.execute(q)
    return result.scalars().all()


@router.get(
    "/task-forces/{tf_id}/artifacts/{artifact_id}",
    response_model=CeremonyArtifactResponse,
    tags=["ceremony-state"],
)
async def get_artifact(
    tf_id: str,
    artifact_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get a single ceremony artifact."""
    result = await db.execute(
        select(CeremonyArtifact).where(
            CeremonyArtifact.id == artifact_id,
            CeremonyArtifact.task_force_id == tf_id,
        )
    )
    artifact = result.scalar_one_or_none()
    if not artifact:
        raise HTTPException(404, "Artifact not found.")
    return artifact


# =========================================================================
# Verdicts — convenience wrapper over verdict-kind artifacts
# =========================================================================

@router.post(
    "/tasks/{task_id}/verdict",
    response_model=VerdictResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["ceremony-state"],
)
async def submit_verdict(
    task_id: str,
    data: VerdictSubmit,
    db: AsyncSession = Depends(get_db),
):
    """Submit an immutable verdict for a task.

    Once a PASS verdict is submitted, no further verdicts can be
    submitted for the same task + rework_cycle combination.  This
    prevents agents from accidentally overwriting a verdict in
    subsequent iterations.
    """
    # Validate task exists and get task_force_id
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(404, "Task not found.")
    if not task.task_force_id:
        raise HTTPException(400, "Task is not part of a Task Force.")

    verdict_lower = data.verdict.strip().lower()
    if verdict_lower not in ("pass", "fail"):
        raise HTTPException(400, "Verdict must be 'pass' or 'fail'.")

    # Check for existing PASS verdict at this rework cycle — immutability guard
    existing = await db.execute(
        select(CeremonyArtifact).where(
            CeremonyArtifact.task_force_id == task.task_force_id,
            CeremonyArtifact.task_id == task_id,
            CeremonyArtifact.kind == ArtifactKind.VERDICT.value,
            CeremonyArtifact.verdict == "pass",
            CeremonyArtifact.rework_cycle == data.rework_cycle,
            CeremonyArtifact.superseded_by.is_(None),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A PASS verdict already exists for this task and rework cycle. "
            "Verdicts are immutable once submitted.",
        )

    # Build content from summary + files
    content_parts = [f"# Verdict: {verdict_lower.upper()}\n"]
    if data.summary:
        content_parts.append(f"\n{data.summary}\n")
    if data.files_reviewed:
        content_parts.append("\n## Files Reviewed\n")
        for f in data.files_reviewed:
            content_parts.append(f"- {f}\n")

    artifact = CeremonyArtifact(
        task_force_id=task.task_force_id,
        ceremony_id=data.ceremony_id,
        task_id=task_id,
        kind=ArtifactKind.VERDICT.value,
        filename="REVIEW_VERDICT.md",
        title=f"Verdict: {verdict_lower.upper()}",
        content="".join(content_parts),
        metadata_json={
            "files_reviewed": data.files_reviewed,
            "summary": data.summary,
        },
        verdict=verdict_lower,
        rework_cycle=data.rework_cycle,
    )
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)

    logger.info(
        f"⚖️ Verdict submitted: #{artifact.id} {verdict_lower.upper()} "
        f"task={task_id} tf={task.task_force_id} cycle={data.rework_cycle}"
    )

    return VerdictResponse(
        id=artifact.id,
        task_force_id=task.task_force_id,
        task_id=task_id,
        verdict=verdict_lower,
        summary=data.summary,
        files_reviewed=data.files_reviewed,
        rework_cycle=data.rework_cycle,
        created_at=artifact.created_at,
    )


@router.get(
    "/task-forces/{tf_id}/verdict",
    response_model=VerdictResponse,
    tags=["ceremony-state"],
)
async def get_latest_verdict(
    tf_id: str,
    rework_cycle: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get the latest active verdict for a task force.

    Optionally filter by rework_cycle.  Returns the most recent
    non-superseded verdict artifact.
    """
    q = select(CeremonyArtifact).where(
        CeremonyArtifact.task_force_id == tf_id,
        CeremonyArtifact.kind == ArtifactKind.VERDICT.value,
        CeremonyArtifact.superseded_by.is_(None),
    )
    if rework_cycle is not None:
        q = q.where(CeremonyArtifact.rework_cycle == rework_cycle)
    q = q.order_by(desc(CeremonyArtifact.created_at))

    result = await db.execute(q)
    artifact = result.scalars().first()
    if not artifact:
        raise HTTPException(404, "No verdict found.")

    return VerdictResponse(
        id=artifact.id,
        task_force_id=artifact.task_force_id,
        task_id=artifact.task_id or "",
        verdict=artifact.verdict or "unknown",
        summary=(artifact.metadata_json or {}).get("summary"),
        files_reviewed=(artifact.metadata_json or {}).get("files_reviewed"),
        rework_cycle=artifact.rework_cycle or 0,
        created_at=artifact.created_at,
    )


# =========================================================================
# Agent State Exchange
# =========================================================================

@router.post(
    "/task-forces/{tf_id}/state",
    response_model=AgentStateExchangeResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["ceremony-state"],
)
async def post_state_exchange(
    tf_id: str,
    data: AgentStateExchangeCreate,
    db: AsyncSession = Depends(get_db),
):
    """Post a state exchange message to the task force channel.

    Used by agents to announce status updates, decisions, handoffs,
    and feedback.  All messages are append-only and fully tracked.
    """
    # Validate task force
    result = await db.execute(select(TaskForce).where(TaskForce.id == tf_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Task Force not found.")

    exchange = AgentStateExchange(
        task_force_id=tf_id,
        from_task_id=data.from_task_id,
        to_task_id=data.to_task_id,
        state_type=data.state_type,
        subject=data.subject,
        body=data.body,
        state_data=data.state_data,
    )
    db.add(exchange)
    await db.commit()
    await db.refresh(exchange)

    logger.info(
        f"💬 State exchange: #{exchange.id} type={data.state_type} "
        f"from={data.from_task_id} tf={tf_id}"
    )
    return exchange


@router.get(
    "/task-forces/{tf_id}/state",
    response_model=List[AgentStateExchangeResponse],
    tags=["ceremony-state"],
)
async def list_state_exchanges(
    tf_id: str,
    state_type: Optional[str] = Query(None, description="Filter by state type"),
    from_task_id: Optional[str] = Query(None),
    to_task_id: Optional[str] = Query(None),
    since_id: Optional[int] = Query(None, description="Only return exchanges after this ID"),
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
):
    """List state exchanges for a task force, with optional filters."""
    q = select(AgentStateExchange).where(
        AgentStateExchange.task_force_id == tf_id
    )
    if state_type:
        q = q.where(AgentStateExchange.state_type == state_type)
    if from_task_id:
        q = q.where(AgentStateExchange.from_task_id == from_task_id)
    if to_task_id:
        q = q.where(
            (AgentStateExchange.to_task_id == to_task_id)
            | (AgentStateExchange.to_task_id.is_(None))  # include broadcasts
        )
    if since_id is not None:
        q = q.where(AgentStateExchange.id > since_id)
    q = q.order_by(AgentStateExchange.created_at.asc()).limit(limit)

    result = await db.execute(q)
    return result.scalars().all()
