"""
Skill Learning System v2 — Demo ingestion, audit mining, human review, tree browsing.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update as sa_update, func as sa_func
from sqlalchemy.orm import selectinload
from database import get_db
from models import (
    SkillV2, SkillV2Status, SkillV2Source,
    SkillDemo, SkillReview, SkillSelectionEvent,
    AgentImage, TaskOutput, Task,
)
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field
import uuid
import logging
import json

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas (inline — avoids touching the shared schemas.py for now)
# ---------------------------------------------------------------------------

class SkillV2Create(BaseModel):
    image_id: str
    name: str
    description: str = ""
    instructions: str = ""
    parent_id: Optional[str] = None
    tags: List[str] = []
    source_type: str = "manual"


class SkillV2Update(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    instructions: Optional[str] = None
    tags: Optional[List[str]] = None
    status: Optional[str] = None


class SkillV2Response(BaseModel):
    id: str
    image_id: str
    name: str
    description: str
    instructions: str
    status: str
    source_type: str
    parent_id: Optional[str]
    confidence_score: int
    usage_count: int
    success_count: int
    reviewer_score: Optional[int]
    tags: List[str]
    evidence_task_ids: List[str]
    created_at: datetime
    updated_at: Optional[datetime]
    created_by: Optional[str]

    class Config:
        from_attributes = True


class DemoIngestRequest(BaseModel):
    image_id: str
    prompt: str = Field(..., description="Description of what the user demonstrated")
    source_task_id: Optional[str] = Field(None, description="Task ID this demo was captured from")
    artifacts: dict = Field(default_factory=dict)
    created_by: Optional[str] = None


class DemoResponse(BaseModel):
    id: str
    image_id: str
    skill_id: Optional[str]
    prompt: str
    extracted_procedure: Optional[dict]
    source_task_id: Optional[str]
    status: str
    created_at: datetime
    created_by: Optional[str]

    class Config:
        from_attributes = True


class ReviewRequest(BaseModel):
    decision: str = Field(..., description="approve / reject / request_changes")
    rating: Optional[int] = Field(None, ge=1, le=5)
    notes: Optional[str] = None
    edited_instructions: Optional[str] = None
    reviewed_by: Optional[str] = None


class ReviewResponse(BaseModel):
    id: int
    skill_id: str
    decision: str
    rating: Optional[int]
    notes: Optional[str]
    edited_instructions: Optional[str]
    reviewed_by: Optional[str]
    reviewed_at: datetime

    class Config:
        from_attributes = True


class AuditMineRequest(BaseModel):
    task_id: str
    created_by: Optional[str] = None


class SelectionEventResponse(BaseModel):
    id: int
    skill_id: str
    dag_id: Optional[str]
    node_id: Optional[str]
    task_id: Optional[str]
    selection_reason: Optional[str]
    alternatives_considered: List[str]
    followed: Optional[bool]
    outcome: Optional[str]
    feedback_notes: Optional[str]
    selected_at: datetime
    resolved_at: Optional[datetime]

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skv2_id() -> str:
    return f"skv2-{uuid.uuid4().hex[:8]}"


def _demo_id() -> str:
    return f"demo-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Skills v2 — CRUD
# ---------------------------------------------------------------------------

@router.post("/skills", response_model=SkillV2Response, status_code=status.HTTP_201_CREATED)
async def create_skill_v2(data: SkillV2Create, db: AsyncSession = Depends(get_db)):
    """Create a new v2 skill node manually."""
    # Verify image exists
    img = await db.get(AgentImage, data.image_id)
    if not img:
        raise HTTPException(status_code=404, detail=f"AgentImage '{data.image_id}' not found")

    if data.parent_id:
        parent = await db.get(SkillV2, data.parent_id)
        if not parent:
            raise HTTPException(status_code=404, detail=f"Parent skill '{data.parent_id}' not found")

    try:
        src = SkillV2Source(data.source_type)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid source_type '{data.source_type}'")

    skill = SkillV2(
        id=_skv2_id(),
        image_id=data.image_id,
        name=data.name,
        description=data.description,
        instructions=data.instructions,
        parent_id=data.parent_id,
        tags=data.tags,
        source_type=src,
        status=SkillV2Status.DRAFT,
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return skill


@router.get("/skills", response_model=List[SkillV2Response])
async def list_skills_v2(
    image_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    tag: Optional[str] = Query(None),
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """List v2 skills, optionally filtered by image, status, or tag."""
    q = select(SkillV2).offset(skip).limit(limit)
    if image_id:
        q = q.where(SkillV2.image_id == image_id)
    if status_filter:
        try:
            q = q.where(SkillV2.status == SkillV2Status(status_filter))
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid status '{status_filter}'")
    result = await db.execute(q)
    skills = list(result.scalars().all())
    if tag:
        skills = [s for s in skills if tag in (s.tags or [])]
    return skills


@router.get("/skills/{skill_id}", response_model=SkillV2Response)
async def get_skill_v2(skill_id: str, db: AsyncSession = Depends(get_db)):
    skill = await db.get(SkillV2, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
    return skill


@router.put("/skills/{skill_id}", response_model=SkillV2Response)
async def update_skill_v2(skill_id: str, data: SkillV2Update, db: AsyncSession = Depends(get_db)):
    skill = await db.get(SkillV2, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")

    if data.name is not None:
        skill.name = data.name
    if data.description is not None:
        skill.description = data.description
    if data.instructions is not None:
        skill.instructions = data.instructions
    if data.tags is not None:
        skill.tags = data.tags
    if data.status is not None:
        try:
            skill.status = SkillV2Status(data.status)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid status '{data.status}'")

    await db.commit()
    await db.refresh(skill)
    return skill


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_skill_v2(skill_id: str, db: AsyncSession = Depends(get_db)):
    """Archive (soft-delete) a v2 skill."""
    skill = await db.get(SkillV2, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
    skill.status = SkillV2Status.ARCHIVED
    await db.commit()


# ---------------------------------------------------------------------------
# Demo ingestion
# ---------------------------------------------------------------------------

@router.post("/demos", response_model=DemoResponse, status_code=status.HTTP_201_CREATED)
async def ingest_demo(data: DemoIngestRequest, db: AsyncSession = Depends(get_db)):
    """Ingest a user demonstration and trigger LLM extraction."""
    img = await db.get(AgentImage, data.image_id)
    if not img:
        raise HTTPException(status_code=404, detail=f"AgentImage '{data.image_id}' not found")

    demo = SkillDemo(
        id=_demo_id(),
        image_id=data.image_id,
        prompt=data.prompt,
        source_task_id=data.source_task_id,
        artifacts=data.artifacts,
        created_by=data.created_by,
        status="pending",
    )
    db.add(demo)
    await db.commit()
    await db.refresh(demo)

    # Kick off async LLM extraction (best-effort; errors don't fail the response)
    try:
        await _extract_procedure_from_demo(demo, db)
    except Exception as exc:
        logger.warning("Demo extraction failed for %s: %s", demo.id, exc)

    return demo


async def _extract_procedure_from_demo(demo: SkillDemo, db: AsyncSession):
    """Use the control-plane LLM router to extract a structured procedure from the demo prompt."""
    import httpx
    payload = {
        "model": "gemma3:4b",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a skill extraction assistant. "
                    "Given a user demonstration description, extract a structured reusable procedure "
                    "as a JSON object with keys: name (string), steps (list of strings), "
                    "tools_used (list of strings), preconditions (list of strings), "
                    "postconditions (list of strings). "
                    "Respond with ONLY the JSON object, no surrounding text."
                ),
            },
            {"role": "user", "content": demo.prompt},
        ],
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post("http://localhost:8000/api/llm/chat", json=payload)
        if resp.status_code == 200:
            content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            # Attempt to parse JSON from the response
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                extracted = json.loads(content[start:end])
                demo.extracted_procedure = extracted
                demo.status = "extracted"
                await db.commit()


@router.get("/demos", response_model=List[DemoResponse])
async def list_demos(
    image_id: Optional[str] = Query(None),
    demo_status: Optional[str] = Query(None, alias="status"),
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    q = select(SkillDemo).offset(skip).limit(limit).order_by(SkillDemo.created_at.desc())
    if image_id:
        q = q.where(SkillDemo.image_id == image_id)
    if demo_status:
        q = q.where(SkillDemo.status == demo_status)
    result = await db.execute(q)
    return list(result.scalars().all())


@router.get("/demos/{demo_id}", response_model=DemoResponse)
async def get_demo(demo_id: str, db: AsyncSession = Depends(get_db)):
    demo = await db.get(SkillDemo, demo_id)
    if not demo:
        raise HTTPException(status_code=404, detail=f"Demo '{demo_id}' not found")
    return demo


@router.post("/demos/{demo_id}/promote", response_model=SkillV2Response, status_code=status.HTTP_201_CREATED)
async def promote_demo_to_skill(
    demo_id: str,
    name: Optional[str] = Body(None),
    parent_id: Optional[str] = Body(None),
    db: AsyncSession = Depends(get_db),
):
    """Promote an extracted demo into a draft SkillV2 node ready for review."""
    demo = await db.get(SkillDemo, demo_id)
    if not demo:
        raise HTTPException(status_code=404, detail=f"Demo '{demo_id}' not found")
    if demo.status == "linked":
        raise HTTPException(status_code=409, detail="Demo is already linked to a skill")

    proc = demo.extracted_procedure or {}
    skill_name = name or proc.get("name") or f"Skill from {demo_id}"
    steps = proc.get("steps", [])
    instructions = "\n".join(f"- {s}" for s in steps) if steps else demo.prompt

    skill = SkillV2(
        id=_skv2_id(),
        image_id=demo.image_id,
        name=skill_name,
        description=demo.prompt[:200],
        instructions=instructions,
        parent_id=parent_id,
        source_type=SkillV2Source.DEMO,
        status=SkillV2Status.DRAFT,
        evidence_task_ids=[demo.source_task_id] if demo.source_task_id else [],
        created_by=demo.created_by,
    )
    db.add(skill)
    await db.flush()

    demo.skill_id = skill.id
    demo.status = "linked"
    await db.commit()
    await db.refresh(skill)
    return skill


# ---------------------------------------------------------------------------
# Audit log mining
# ---------------------------------------------------------------------------

@router.post("/mine", response_model=DemoResponse, status_code=status.HTTP_201_CREATED)
async def mine_task_audit(data: AuditMineRequest, db: AsyncSession = Depends(get_db)):
    """Extract a skill candidate by mining the audit outputs of a completed task."""
    task = await db.get(Task, data.task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{data.task_id}' not found")

    # Collect agent_logs from task outputs
    result = await db.execute(
        select(TaskOutput).where(TaskOutput.task_id == data.task_id).order_by(TaskOutput.iteration)
    )
    outputs = list(result.scalars().all())
    if not outputs:
        raise HTTPException(status_code=404, detail=f"No outputs found for task '{data.task_id}'")

    # Build summary of tool usage from logs
    log_summary_parts = []
    for out in outputs:
        if out.agent_logs:
            log_summary_parts.append(f"[iteration {out.iteration}]\n{out.agent_logs[:2000]}")
    log_summary = "\n---\n".join(log_summary_parts[:5])  # cap at 5 iterations

    image_id = task.agent_profile or "openclaw"
    prompt = (
        f"Task: {task.name}\n"
        f"Description: {task.description or ''}\n\n"
        f"Execution logs (tool calls and outputs):\n{log_summary}"
    )

    demo = SkillDemo(
        id=_demo_id(),
        image_id=image_id,
        prompt=prompt,
        source_task_id=data.task_id,
        created_by=data.created_by,
        status="pending",
    )
    db.add(demo)
    await db.commit()
    await db.refresh(demo)

    try:
        await _extract_procedure_from_demo(demo, db)
    except Exception as exc:
        logger.warning("Audit mine extraction failed for task %s: %s", data.task_id, exc)

    return demo


# ---------------------------------------------------------------------------
# Human review
# ---------------------------------------------------------------------------

@router.post("/skills/{skill_id}/review", response_model=SkillV2Response)
async def review_skill(skill_id: str, data: ReviewRequest, db: AsyncSession = Depends(get_db)):
    """Approve, reject, or request changes on a draft skill."""
    skill = await db.get(SkillV2, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")

    if data.decision not in ("approve", "reject", "request_changes"):
        raise HTTPException(status_code=422, detail="decision must be approve / reject / request_changes")

    review = SkillReview(
        skill_id=skill_id,
        decision=data.decision,
        rating=data.rating,
        notes=data.notes,
        edited_instructions=data.edited_instructions,
        reviewed_by=data.reviewed_by,
    )
    db.add(review)

    if data.decision == "approve":
        skill.status = SkillV2Status.ACTIVE
        if data.edited_instructions:
            skill.instructions = data.edited_instructions
        if data.rating:
            skill.reviewer_score = data.rating
            # Boost confidence: approved with high rating → higher confidence
            skill.confidence_score = min(100, skill.confidence_score + data.rating * 10)
    elif data.decision == "reject":
        skill.status = SkillV2Status.ARCHIVED

    await db.commit()
    await db.refresh(skill)
    return skill


@router.get("/skills/{skill_id}/reviews", response_model=List[ReviewResponse])
async def get_skill_reviews(skill_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SkillReview).where(SkillReview.skill_id == skill_id).order_by(SkillReview.reviewed_at.desc())
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Review queue — list all draft skills awaiting human review
# ---------------------------------------------------------------------------

@router.get("/review-queue", response_model=List[SkillV2Response])
async def get_review_queue(
    image_id: Optional[str] = Query(None),
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Return all draft skills pending human review."""
    q = (
        select(SkillV2)
        .where(SkillV2.status == SkillV2Status.DRAFT)
        .offset(skip)
        .limit(limit)
        .order_by(SkillV2.created_at.desc())
    )
    if image_id:
        q = q.where(SkillV2.image_id == image_id)
    result = await db.execute(q)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Skill tree — image-scoped tree view
# ---------------------------------------------------------------------------

@router.get("/tree/{image_id}", response_model=List[SkillV2Response])
async def get_skill_tree(
    image_id: str,
    active_only: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    """Return all v2 skill nodes for an image, optionally only active ones."""
    q = select(SkillV2).where(SkillV2.image_id == image_id)
    if active_only:
        q = q.where(SkillV2.status == SkillV2Status.ACTIVE)
    q = q.order_by(SkillV2.confidence_score.desc(), SkillV2.name)
    result = await db.execute(q)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Selection events — record and query planner choices
# ---------------------------------------------------------------------------

class RecordSelectionRequest(BaseModel):
    skill_id: str
    dag_id: Optional[str] = None
    node_id: Optional[str] = None
    task_id: Optional[str] = None
    selection_reason: Optional[str] = None
    alternatives_considered: List[str] = []


@router.post("/selection-events", response_model=SelectionEventResponse, status_code=status.HTTP_201_CREATED)
async def record_selection_event(data: RecordSelectionRequest, db: AsyncSession = Depends(get_db)):
    skill = await db.get(SkillV2, data.skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{data.skill_id}' not found")

    event = SkillSelectionEvent(
        skill_id=data.skill_id,
        dag_id=data.dag_id,
        node_id=data.node_id,
        task_id=data.task_id,
        selection_reason=data.selection_reason,
        alternatives_considered=data.alternatives_considered,
    )
    db.add(event)

    # Increment usage counter
    skill.usage_count = (skill.usage_count or 0) + 1
    await db.commit()
    await db.refresh(event)
    return event


class ResolveSelectionRequest(BaseModel):
    followed: bool
    outcome: str  # success / failure / partial
    feedback_notes: Optional[str] = None


@router.post("/selection-events/{event_id}/resolve", response_model=SelectionEventResponse)
async def resolve_selection_event(
    event_id: int,
    data: ResolveSelectionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Record post-execution outcome for a skill selection event."""
    event = await db.get(SkillSelectionEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    event.followed = data.followed
    event.outcome = data.outcome
    event.feedback_notes = data.feedback_notes
    event.resolved_at = datetime.utcnow()

    # Update skill quality signals
    skill = await db.get(SkillV2, event.skill_id)
    if skill and data.outcome == "success":
        skill.success_count = (skill.success_count or 0) + 1
        # Nudge confidence upward on success (capped at 100)
        skill.confidence_score = min(100, (skill.confidence_score or 0) + 2)
    elif skill and data.outcome == "failure":
        skill.confidence_score = max(0, (skill.confidence_score or 0) - 5)

    await db.commit()
    await db.refresh(event)
    return event


# ---------------------------------------------------------------------------
# Archive all legacy (v1) skills — hard reset endpoint
# ---------------------------------------------------------------------------

@router.post("/archive-legacy", status_code=200)
async def archive_legacy_skills(db: AsyncSession = Depends(get_db)):
    """Archive all v1 Skill rows (hard reset). Idempotent."""
    from models import Skill
    result = await db.execute(select(Skill))
    skills = list(result.scalars().all())
    archived = 0
    for s in skills:
        if not hasattr(s, "_archived") or not s.tags or "archived" not in s.tags:
            s.tags = list(s.tags or []) + ["archived"]
            archived += 1
    await db.commit()
    return {"archived": archived, "message": f"Marked {archived} legacy v1 skills as archived"}
