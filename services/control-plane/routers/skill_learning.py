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
    AgentImage, TaskOutput, Task, DAGNode, Skill, DeepReview,
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
    node_id: Optional[str] = None
    dag_id: Optional[str] = None
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
                    "Write each item in 'steps' as a single pseudo-code statement (imperative, with explicit "
                    "tool calls, conditions IF/ELSE, loops, and verification checks) — not prose paragraphs. "
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
    prompt_parts = [
        f"Task: {task.name}",
        f"Description: {task.description or ''}",
    ]
    if data.node_id:
        prompt_parts.append(f"DAG Node: {data.node_id}")
    if data.dag_id:
        prompt_parts.append(f"Parent DAG: {data.dag_id}")
    prompt_parts.append(f"\nExecution logs (tool calls and outputs):\n{log_summary}")
    prompt = "\n".join(prompt_parts)

    demo = SkillDemo(
        id=_demo_id(),
        image_id=image_id,
        prompt=prompt,
        source_task_id=data.task_id,
        artifacts={
            "source_task_id": data.task_id,
            "source_node_id": data.node_id,
            "source_dag_id": data.dag_id,
        },
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


# ---------------------------------------------------------------------------
# Skill analysis / learning (redesigned flow)
# ---------------------------------------------------------------------------

class SkillAnalysisRequest(BaseModel):
    task_id: str
    node_id: Optional[str] = None
    dag_id: Optional[str] = None
    created_by: Optional[str] = None


class SkillAnalysisResponse(BaseModel):
    task_id: str
    image_id: str
    image_tag: str
    skill_used_id: Optional[str]
    skill_used_name: Optional[str]
    learning_potential: bool
    assessment: str
    warnings: List[str]
    suggested_improvements: List[str]
    extracted_skills: List[dict]
    skill_id: Optional[str]  # created draft skill (if any)


def _resolve_image_id(task: Task, node: Optional[object], db: AsyncSession) -> Optional[str]:
    """Resolve the AgentImage.id for a task.

    Priority:
      1. task.agent_profile (if it matches an AgentImage id)
      2. DAG node config.base_image
      3. Extract the tag suffix from task.current_image (e.g. browser_v4)
    """
    import re

    # 1. agent_profile
    if task.agent_profile:
        return task.agent_profile

    # 2. node config.base_image
    if node and getattr(node, "config", None) and node.config.get("base_image"):
        return node.config["base_image"]

    # 3. current_image -> last segment after ':' stripped of registry prefix
    if task.current_image:
        # e.g. localhost:5000/openclaw-agent:browser_v4  OR  registry:5000/openclaw-agent:dag-...-task-...
        # last colon segment
        tag = task.current_image.rsplit(":", 1)[-1] if ":" in task.current_image else task.current_image
        # strip registry prefix before '/'
        return tag.split("/")[-1]
    return None


async def _resolve_or_create_image(task: Task, node: Optional[object], db: AsyncSession) -> Optional[AgentImage]:
    """Find an AgentImage by id; if the resolved id doesn't exist, fall back / create a minimal row."""
    import re
    resolved = _resolve_image_id(task, node, db)
    if not resolved:
        resolved = "openclaw"
    # try exact
    img = await db.get(AgentImage, resolved)
    if img:
        return img
    # try matching by name or tag
    result = await db.execute(
        select(AgentImage).where(
            (AgentImage.name == resolved) | (AgentImage.tag == resolved) | (AgentImage.tag.like(f"%:{resolved}"))
        )
    )
    img = result.scalars().first()
    if img:
        return img
    # normalize to base (strip _vN)
    base = re.sub(r"_v\d+$", "", resolved)
    if base != resolved:
        result = await db.execute(select(AgentImage).where(AgentImage.id == base))
        img = result.scalars().first()
        if img:
            return img
    # fallback: openclaw
    return await db.get(AgentImage, "openclaw")


def _build_execution_summary(task: Task, outputs: List[TaskOutput], node: Optional[object] = None,
                             image_id: Optional[str] = None) -> dict:
    """Build a rich summary of the task execution in the format that yields accurate
    integrity audits: task_metadata + per-iteration full deliverables + command_issued turns."""
    iterations = []
    for out in outputs:
        deliverables = out.deliverables or {}
        turns = []
        final_output = ""
        if out.raw_result and isinstance(out.raw_result, dict):
            agent_logs = out.raw_result.get("agent_logs", "")
            turns = _extract_turns_from_agent_logs(agent_logs)
            final_output = out.raw_result.get("output", "")
        iter_data = {
            "iteration": out.iteration,
            "status": "completed" if out.completed == "true" else "running" if out.completed == "false" else str(out.completed),
            "deliverables_produced": list(deliverables.keys()) if deliverables else [],
            "deliverables": deliverables,          # full file contents (keyed by filename)
            "turns": turns,
            "turn_count": len(turns),
            "error": out.error,
            "output": final_output[:3000] if final_output else "",
        }
        iterations.append(iter_data)

    # Determine base_image / node_id for metadata
    base_image = task.current_image
    node_id = None
    if node:
        node_id = getattr(node, "node_id", None) or None
        if getattr(node, "config", None) and node.config.get("base_image"):
            base_image = node.config["base_image"]
    if image_id and (not base_image or ":" not in base_image):
        base_image = image_id

    completed_iters = sum(1 for i in iterations if i["status"] == "completed")
    failed_iters = sum(1 for i in iterations if i.get("error"))

    return {
        "task_metadata": {
            "task_id": task.id,
            "task_name": task.name,
            "description": task.description or "",
            "model": task.llm_model,
            "status": task.status.value if hasattr(task.status, "value") else str(task.status),
            "base_image": base_image,
            "dag_id": task.dag_id,
            "node_id": node_id,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "total_iterations": len(iterations),
            "completed_iterations": completed_iters,
            "failed_iterations": failed_iters,
        },
        "iterations": iterations,
    }




def _detect_integrity_signals(summary: dict) -> List[str]:
    """Rule-based pre-scan of the summary for concrete integrity signals.

    Returns a list of concrete findings (strings) that are passed to the LLM as
    explicit hints, so critical issues (e.g. placeholder/synthetic data) are never
    missed even if the model is lenient.
    """
    import re
    signals: List[str] = []

    placeholders = re.compile(r"\b(?:N/A|TBD|TBA|TODO|placeholder|dummy|sample|example|Lorem ipsum|\bnull\b|\[\])\b", re.IGNORECASE)
    empty_vals = re.compile(r'\b(?:total_count|count|records|rows|results)\s*[:=]\s*0\b', re.IGNORECASE)

    for it in summary.get("iterations", []):
        deliverables = it.get("deliverables", {}) or {}
        status = it.get("status", "")
        error = it.get("error")

        # 1. Placeholder markers in deliverable contents (only flag DATA files,
        #    not source code where N/A is legitimately handled in logic).
        #    .html is excluded: markup naturally contains N/A/null and is not
        #    tabular data — it is validated by the agent's claims + presence.
        data_exts = (".json", ".csv", ".tsv", ".txt", ".md", ".xml", ".yaml", ".yml")
        for fname, content in deliverables.items():
            if not isinstance(content, str):
                continue
            lower_name = fname.lower()
            is_code = not lower_name.endswith(data_exts)
            if is_code:
                continue  # skip source files (e.g. .py) — N/A may be legit logic
            matches = set(m.group(0) for m in placeholders.finditer(content))
            if matches:
                signals.append(
                    f"[{fname}] deliverable contains placeholder/synthetic markers: {', '.join(sorted(matches))}. "
                    f"Likely fabricated/mocked data."
                )
            if empty_vals.search(content):
                signals.append(
                    f"[{fname}] deliverable reports zero/empty count or empty results, which contradicts a successful fetch."
                )

        # 2. Task marked completed but error present
        if status == "completed" and error:
            signals.append(f"Iteration {it.get('iteration')} is marked completed but has an error: {error}")

        # 3. Deliverables but no turns (no actual work evidence)
        if deliverables and not it.get("turns"):
            signals.append(f"Iteration {it.get('iteration')} lists deliverables but has no recorded tool turns — no evidence of how they were produced.")

        # 4. Output claims success while zero items
        output = it.get("output", "") or ""
        for fname, content in deliverables.items():
            if not isinstance(content, str):
                continue
            if ("success" in output.lower() or "completed" in output.lower()) and empty_vals.search(content):
                signals.append(
                    f"Final output claims success for iteration {it.get('iteration')} but [{fname}] shows empty/zero results."
                )

    return signals


def _extract_turns_from_agent_logs(agent_logs: str) -> List[dict]:
    """Parse turn-by-turn tool usage from agent_logs text."""
    import re
    turns = []
    turn_pattern = r'── Turn (\d+)/\d+ ──\n(.*?)(?=\n── Turn \d+/\d+ ──|\n===|$)'
    for match in re.finditer(turn_pattern, agent_logs, re.DOTALL):
        turn_num = int(match.group(1))
        content = match.group(2)
        tool_calls = []
        tool_matches = re.findall(r'🔧 Tool: (\w+)\((\{.*?\})\)', content)
        for tool_name, args_str in tool_matches:
            try:
                args = json.loads(args_str)
                tool_calls.append({"tool": tool_name, "arguments": args})
            except Exception:
                tool_calls.append({"tool": tool_name, "arguments": args_str[:300]})
        output = ""
        rm = re.search(r'📤 Result: (.*?)(?=\n\n|\n──|$)', content, re.DOTALL)
        if rm:
            output = rm.group(1).strip()
        am = re.search(r'💬 Assistant: (.*?)(?=\n\n|\n──|$)', content, re.DOTALL)
        if am:
            output = am.group(1).strip()
        turns.append({
            "turn": turn_num,
            "command_issued": tool_calls,
            "output": output[:1500],
        })
    return turns






def _normalize_review_payload(parsed: dict) -> dict:
    """Normalize a parsed deep-review/analysis payload.

    Some models wrap the result in an `analysis`, `result`, or `data` object,
    or use different key spellings. Unwrap and standardize keys.
    """
    if not isinstance(parsed, dict):
        return {}
    # Recursively unwrap nested wrapper objects (models sometimes return
    # {"status":..., "analysis": {"overall_assessment":...}} envelopes).
    wrappers = ("analysis", "result", "data", "review", "response", "assessment", "output", "content", "payload")
    for _ in range(6):
        unwrapped = False
        for wrapper in wrappers:
            if wrapper in parsed and isinstance(parsed[wrapper], dict):
                inner = parsed[wrapper]
                # merge inner into outer, keeping outer keys that exist
                parsed = {**inner, **{k: v for k, v in parsed.items() if k != wrapper}}
                unwrapped = True
                break
        if not unwrapped:
            break
    # Standardize key aliases
    alias = {
        "verdict": ("verdict", "outcome", "result_status", "verdict_status"),
        "score": ("score", "quality_score", "integrity_score", "rating"),
        "summary": ("summary", "assessment", "overall", "analysis_summary"),
        "issues": ("issues", "findings", "problems", "concerns"),
        "positives": ("positives", "strengths", "good", "accomplishments"),
        "suggested_improvements": ("suggested_improvements", "improvements", "recommendations"),
        "warnings": ("warnings", "alerts"),
        "skills": ("skills", "extracted_skills", "new_skills"),
        "learning_potential": ("learning_potential", "has_skill_potential", "learnable"),
    }
    out: dict = {}
    for target, candidates in alias.items():
        for key in candidates:
            if key in parsed and parsed[key] is not None:
                out[target] = parsed[key]
                break
    # Convenience aliases so consumers reading assessment or summary both work
    if "summary" in out and "assessment" not in out:
        out["assessment"] = out["summary"]
    if "assessment" in out and "summary" not in out:
        out["summary"] = out["assessment"]
    return out




VALID_VERDICTS = {"clean", "issues_found", "needs_attention", "completed_with_issues"}
# Map alternate verdict wordings the LLM may use -> canonical values.
_VERDICT_ALIASES = {
    "pass": "clean",
    "ok": "clean",
    "succeeded": "clean",
    "success": "clean",
    "no_issues": "clean",
    "good": "clean",
    "fail": "issues_found",
    "failed": "issues_found",
    "failed_with_issues": "issues_found",
    "issues": "issues_found",
    "issues_detected": "issues_found",
    "problems": "issues_found",
    "needs_revision": "needs_attention",
    "needs_improvement": "needs_attention",
    "review_needed": "needs_attention",
    "attention_needed": "needs_attention",
    "inconclusive": "needs_attention",
    "unknown": "needs_attention",
    "completed_with_issues": "issues_found",
}


def _normalize_verdict(value) -> str:
    """Validate/normalize a verdict to one of the known values; empty otherwise."""
    if not isinstance(value, str):
        return ""
    v = value.strip().lower().replace(" ", "_").replace("-", "_")
    if v in VALID_VERDICTS:
        return v
    if v in _VERDICT_ALIASES:
        return _VERDICT_ALIASES[v]
    # fuzzy: contains a known verdict keyword
    if "clean" in v:
        return "clean"
    if "issue" in v or "fail" in v:
        return "issues_found"
    if "attention" in v or "revision" in v or "review" in v:
        return "needs_attention"
    return ""


def _normalize_score(value) -> int:
    """Coerce a model score to an int 0-100 (round floats, clamp)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, int(round(f))))


def _derive_verdict_from_payload(payload: dict) -> str:
    """Synthesize a verdict from issues/score when the model omits a verdict.

    Returns a canonical verdict string, or "" if the payload has no usable
    signals (so callers know it's a wrong-shaped response, not a review).
    """
    if payload.get("issues"):
        return "issues_found"
    score = _normalize_score(payload.get("score"))
    if score:
        if score >= 85:
            return "clean"
        if score >= 50:
            return "needs_attention"
        return "issues_found"
    if payload.get("positives"):
        return "needs_attention"
    return ""


def _extract_json_object(text: str):
    """Robustly extract the first JSON object from an LLM response.

    Handles markdown code fences, leading/trailing prose, and nested braces
    inside string values (e.g. URLs). Returns parsed object or None.
    """
    import json as _json
    if not text:
        return None

    # Strip markdown code fences if present
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
                        return _json.loads(candidate)
                    except Exception:
                        # Invalid JSON — try next '{'
                        break
        start = text.find("{", start + 1)
    return None


def _build_skill_analysis_system_prompt() -> str:
    return (
        "You are a senior skill-extraction and code-review analyst for an autonomous agent platform. "
        "You examine an agent's execution log for a task and decide whether it reveals a reusable/improveable "
        "procedural skill. You must also critically assess whether the agent completed the task CORRECTLY, "
        "flagging any hallucinations, fabrication of data, or generation of synthetic/mock content in code or "
        "output files (this is a serious quality problem we must prevent).\n\n"
        "Respond with ONLY valid JSON, no surrounding text, in this exact shape:\n"
        "{\n"
        '  "learning_potential": <bool>,\n'
        '  "assessment": "<string: overall quality + whether a skill can be learned>",\n'
        '  "warnings": ["<string: hallucination/synthetic-data/quality concerns>", ...],\n'
        '  "suggested_improvements": ["<string: how to improve the skill to avoid these issues>", ...],\n'
        '  "skills": [\n'
        "    {\n"
        '      "name": "<skill name>",\n'
        '      "description": "<one-line description>",\n'
        '      "instructions": "<pseudo-code: an explicit, step-by-step algorithm an agent can follow verbatim>",\n'
        '      "tags": ["<tag>", ...]\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "WRITE INSTRUCTIONS AS PSEUDO-CODE, not prose. This is critical: the skill is executed by another "
        "AI agent, so instructions must be an unambiguous, structured algorithm.\n"
        "Format requirements for \"instructions\":\n"
        "- One statement per line (plain text, no JSON nesting).\n"
        "- Use indentation to denote nesting/block scope.\n"
        "- Name every exact source, URL/API endpoint, and tool call (e.g. web_search(query=..., source='google trends')).\n"
        "- Make control flow explicit: IF/ELSE, FOR EACH, WHILE, RETRY / ON FAILURE.\n"
        "- Include an explicit verification step for any data fetched: what to check and what counts as fabricated "
        "or placeholder (e.g. 'N/A', 'TBD', empty results), and how to react (retry, fail, or mark an issue).\n"
        "- End with an explicit RETURN of the final deliverable/result.\n"
        "Example pseudo-code style:\n"
        "  INPUT: product_category\n"
        "  trends = fetch_google_trends(product_category, region='CH', timeframe='last_90_days')\n"
        "  IF trends is empty OR trends has placeholder values THEN\n"
        "    retry(fetch_google_trends) UP_TO 3 TIMES\n"
        "  END IF\n"
        "  FOR EACH trend IN trends DO\n"
        "    validate(trend.interest > 0 AND trend.source == 'google')\n"
        "  END FOR\n"
        "  RETURN report(items=trends)\n"
        "If no reusable skill is evident, set learning_potential=false and skills=[]. "
        "If the agent generated synthetic/fabricated data or mocked outputs, surface it clearly in warnings."
    )


async def _call_skill_analysis_llm(summary: dict, skill_used: Optional[dict], task: Task) -> dict:
    """Call the LLM to analyze the execution and propose skill(s).

    Uses a single, explicitly configured model (no silent cross-model fallback).
    """
    import httpx
    model = _deep_review_model()
    user_content = json.dumps(
        {
            "task": {
                "task_id": summary.get("task_metadata", {}).get("task_id"),
                "name": summary.get("task_metadata", {}).get("task_name"),
                "description": summary.get("task_metadata", {}).get("description"),
                "model": summary.get("task_metadata", {}).get("model"),
                "base_image": summary.get("task_metadata", {}).get("base_image"),
                "dag_id": summary.get("task_metadata", {}).get("dag_id"),
                "node_id": summary.get("task_metadata", {}).get("node_id"),
            },
            "skill_used": skill_used,
            "execution_summary": summary,
        },
        default=str,
    )
    async with httpx.AsyncClient(timeout=180) as client:
        for strict in (False, True):
            if strict:
                system = "Output ONLY a single valid JSON object. No markdown, no code fences, no commentary, no trailing text. Begin with { and end with }."
                user = f"Return your analysis as valid JSON only.\n\n{user_content}"
            else:
                system = _build_skill_analysis_system_prompt()
                user = user_content
            payload = {
                "model": model,
                "max_tokens": 30000,
                "thinking": {"type": "disabled"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
            try:
                resp = await client.post("http://localhost:8000/api/llm/v1/chat/completions", json=payload)
            except httpx.HTTPError as exc:
                logger.warning("Skill analysis LLM request failed (%s): %s", model, exc)
                continue
            if resp.status_code != 200:
                logger.warning("Skill analysis LLM returned %s for %s: %s", resp.status_code, model, resp.text[:500])
                break
            content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = _extract_json_object(content)
            if parsed is not None:
                normalized = _normalize_review_payload(parsed)
                normalized["model"] = model
                return normalized
            logger.warning("Could not parse skill analysis JSON for %s (strict=%s)", model, strict)
    return {"learning_potential": False, "assessment": "Could not parse LLM output", "warnings": [], "suggested_improvements": [], "skills": [], "model": model}


@router.post("/analyze", response_model=SkillAnalysisResponse)
async def analyze_task_for_skill(data: SkillAnalysisRequest, db: AsyncSession = Depends(get_db)):
    """Analyze a completed task's execution log to assess skill-learning potential.

    Returns an assessment report + optionally creates draft SkillV2 nodes (for review)
    associated with the task's agent image.
    """
    task = await db.get(Task, data.task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{data.task_id}' not found")

    # Resolve DAG node for skill/base_image context
    node = None
    if task.dag_id and data.node_id:
        from models import DAGNode
        nr = await db.execute(
            select(DAGNode).where(DAGNode.dag_id == task.dag_id, DAGNode.node_id == data.node_id)
        )
        node = nr.scalars().first()

    # Resolve image
    img = await _resolve_or_create_image(task, node, db)
    image_id = img.id if img else "openclaw"
    image_tag = img.tag if img else ""

    # Skill used (from DAG node)
    skill_used = None
    if node:
        if getattr(node, "selected_skill_v2_id", None):
            sv = await db.get(SkillV2, node.selected_skill_v2_id)
            if sv:
                skill_used = {"id": sv.id, "name": sv.name, "instructions": sv.instructions}
        elif getattr(node, "skill_id", None):
            from models import Skill
            sk = await db.get(Skill, node.skill_id)
            if sk:
                skill_used = {"id": sk.id, "name": sk.name, "instructions": getattr(sk, "instructions", None)}

    # Build execution summary
    result = await db.execute(
        select(TaskOutput).where(TaskOutput.task_id == data.task_id).order_by(TaskOutput.iteration)
    )
    outputs = list(result.scalars().all())
    if not outputs:
        raise HTTPException(status_code=404, detail=f"No outputs found for task '{data.task_id}'")
    summary = _build_execution_summary(task, outputs, node=node, image_id=image_id)

    # Call LLM
    analysis = await _call_skill_analysis_llm(summary, skill_used, task)

    learning_potential = bool(analysis.get("learning_potential"))
    warnings = analysis.get("warnings", []) or []
    suggestions = analysis.get("suggested_improvements", []) or []
    assessment = analysis.get("assessment", "")
    skills_data = analysis.get("skills", []) or []

    # Create draft SkillV2 nodes for each extracted skill (associated with image)
    extracted_skills = []
    created_skill_id = None
    for sk in skills_data:
        name = (sk.get("name") or "").strip()
        if not name:
            continue
        instructions = (sk.get("instructions") or "").strip()
        skill_node = SkillV2(
            id=_skv2_id(),
            image_id=image_id,
            name=name,
            description=sk.get("description", "") or "",
            instructions=instructions,
            tags=sk.get("tags", []) or [],
            source_type=SkillV2Source.AUDIT_MINE,
            status=SkillV2Status.DRAFT,
            evidence_task_ids=[data.task_id],
            created_by=data.created_by,
        )
        db.add(skill_node)
        await db.flush()
        extracted_skills.append({
            "id": skill_node.id,
            "name": name,
            "description": skill_node.description,
            "instructions": skill_node.instructions,
            "tags": skill_node.tags,
            "status": skill_node.status.value,
            "image_id": image_id,
        })
        if not created_skill_id:
            created_skill_id = skill_node.id

    await db.commit()

    return SkillAnalysisResponse(
        task_id=data.task_id,
        image_id=image_id,
        image_tag=image_tag,
        skill_used_id=skill_used.get("id") if skill_used else None,
        skill_used_name=skill_used.get("name") if skill_used else None,
        learning_potential=learning_potential,
        assessment=assessment,
        warnings=warnings,
        suggested_improvements=suggestions,
        extracted_skills=extracted_skills,
        skill_id=created_skill_id,
    )


# ---------------------------------------------------------------------------
# Deep Review — rigorous audit for hallucinations / synthetic data / shortcuts
# ---------------------------------------------------------------------------

class DeepReviewRequest(BaseModel):
    task_id: str
    node_id: Optional[str] = None
    dag_id: Optional[str] = None
    created_by: Optional[str] = None
    include_skill: bool = True  # include the skill used in the audit context


class DeepReviewIssue(BaseModel):
    severity: str          # high / medium / low
    category: str          # hallucination / synthetic_data / shortcut / mismatch / quality
    finding: str           # what was found
    evidence: str          # excerpt / location from the log
    recommendation: str    # how to fix / prevent


class DeepReviewResponse(BaseModel):
    task_id: str
    node_id: Optional[str] = None
    image_id: str
    image_tag: str
    skill_used_id: Optional[str]
    skill_used_name: Optional[str]
    verdict: str           # clean / issues_found / needs_attention
    score: int             # 0-100 quality score (higher = better)
    summary: str
    issues: List[DeepReviewIssue]
    positives: List[str]
    id: Optional[int] = None
    model: Optional[str] = None       # which model generated this review
    created_at: Optional[datetime] = None




def _deep_review_model() -> str:
    """Return the configured Deep Review / skill-learning model from DAG defaults."""
    try:
        from routers.dags import _dag_model_defaults
        return _dag_model_defaults.get("deep_review_model") or _dag_model_defaults.get("planning_model") or "gemini-flash-lite-latest"
    except Exception:
        return "gemini-flash-lite-latest"




def _trim_execution_summary_for_llm(summary: dict, max_deliverable_chars: int = 4000,
                                    max_turn_output_chars: int = 400,
                                    max_iter_output_chars: int = 800) -> dict:
    """Return a context-safe copy of the execution summary for LLM review.

    Large deliverable file contents and long turn outputs are truncated to keep
    the prompt within the model's context window. The structure is preserved so
    parsing/analysis still works. Full deliverables remain available for the
    rule-based integrity scan (which runs separately).
    """
    def _trim_deliverables(deliverables):
        if not isinstance(deliverables, dict):
            return deliverables
        out = {}
        for k, v in deliverables.items():
            if not isinstance(v, str):
                out[k] = v
                continue
            # HTML deliverables: keep the filename in context but exclude the raw
            # markup (it is huge and full of N/A/null false positives).
            if k.lower().endswith(".html"):
                out[k] = f"[HTML deliverable, {len(v)} bytes — markup excluded from review]"
            elif len(v) > max_deliverable_chars:
                out[k] = v[:max_deliverable_chars] + "\n...[truncated]..."
            else:
                out[k] = v
        return out

    def _trim_iter(it):
        trimmed = dict(it)
        if "deliverables" in it:
            trimmed["deliverables"] = _trim_deliverables(it["deliverables"])
        if "output" in it and isinstance(it["output"], str) and len(it["output"]) > max_iter_output_chars:
            trimmed["output"] = it["output"][:max_iter_output_chars] + "\n...[truncated]..."
        if "turns" in it and isinstance(it["turns"], list):
            new_turns = []
            for t in it["turns"]:
                if isinstance(t, dict) and isinstance(t.get("output"), str) and len(t["output"]) > max_turn_output_chars:
                    nt = dict(t)
                    nt["output"] = t["output"][:max_turn_output_chars] + "\n...[truncated]..."
                    new_turns.append(nt)
                else:
                    new_turns.append(t)
            trimmed["turns"] = new_turns
        return trimmed

    out = dict(summary)
    if "iterations" in summary and isinstance(summary["iterations"], list):
        out["iterations"] = [_trim_iter(it) for it in summary["iterations"]]
    return out


def _build_deep_review_system_prompt() -> str:
    return (
        "You are a rigorous AI-execution auditor for an autonomous agent platform. "
        "You are given the full execution log summary of an agent task and must audit it "
        "for integrity issues. Be skeptical and thorough. Look specifically for:\n"
        "1. HALLUCINATIONS: claims, URLs, file contents, or data that were never actually "
        "   fetched/verified — the agent describing things as done without evidence.\n"
        "2. SYNTHETIC DATA / MOCKING: the agent generating fabricated/synthetic/mock data "
        "   (e.g. sample rows, placeholder values, invented API responses) instead of real "
        "   fetched data, especially inside code files or deliverables.\n"
        "3. SHORTCUTS: skipping steps, hardcoding expected answers, not actually calling tools, "
        "   circumventing validation, silently degrading to fallback without justification.\n"
        "4. MISMATCH: claims in the final output that don't match the tool results, or "
        "   deliverables that don't match the task requirements.\n"
        "5. QUALITY: dead code, ignored errors, brittle behavior, missing error handling.\n"
        "6. DATA QUALITY: check if the data used in the processing is complete and without errors.\n"
        "MANDATORY CHECKS — you MUST actively hunt for these common failure signals and report them as issues:\n"
        '   - Placeholder/synthetic values in deliverables, e.g. "N/A", "TBD", "example", "sample", dummy data, or empty fields.\n'
        '   - The agent reporting success/"completed" while tool outputs show empty results, 0 items, or failed fetches.\n'
        "   - Claims in the final output/summary that are not supported by the actual tool results or deliverable contents.\n"
        "   - Scripts that rely on brittle selectors and silently produce empty output yet are reported as working.\n"
        "   - Repeated script rewrites with no evidence of verification against real responses.\n"
        "Do NOT give a clean verdict if any of the above is present. Verify the deliverable CONTENTS against the claimed result before judging clean.\n\n"
        "Respond with ONLY valid JSON, no surrounding text, in this exact shape:\n"
        "{\n"
        '  "verdict": "<clean | issues_found | needs_attention>",\n'
        '  "score": <int 0-100>,\n'
        '  "summary": "<2-3 sentence overall assessment>",\n'
        '  "issues": [\n'
        "    {\n"
        '      "severity": "<high | medium | low>",\n'
        '      "category": "<hallucination | synthetic_data | shortcut | mismatch | quality>",\n'
        '      "finding": "<what was found>",\n'
        '      "evidence": "<excerpt or location from the log>",\n'
        '      "recommendation": "<how to fix / prevent>"\n'
        "    }\n"
        "  ],\n"
        '  "positives": ["<what was done correctly>", ...]\n'
        "}\n"
        "If the execution is clean, set verdict=clean, score>=85, and issues=[]. "
        "Be honest and specific — do not invent issues, but do not be lenient either."
    )


def _build_deep_review_payload(model: str, user_content: dict, strict: bool) -> dict:
    if strict:
        system = "Output ONLY a single valid JSON object. No markdown, no code fences, no commentary, no trailing text. Begin with { and end with }."
        user = f"Return your analysis as valid JSON only.\n\n{json.dumps(user_content, default=str)}"
    else:
        system = _build_deep_review_system_prompt()
        user = json.dumps(user_content, default=str)
    return {
        "model": model,
        "max_tokens": 30000,  # generous: reasoning models burn tokens on CoT before the final JSON
        "thinking": {"type": "disabled"},  # structured JSON: skip chain-of-thought entirely
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }


async def _call_deep_review_llm(summary: dict, skill_used: Optional[dict], task: Task, include_skill: bool = True) -> dict:
    """Call the LLM for a rigorous audit of hallucinations / synthetic data / shortcuts.

    Uses a single, explicitly configured review model (no silent cross-model
    fallback) so the result is traceable to the model that generated it.
    """
    import httpx
    model = _deep_review_model()
    # Send a context-safe copy of the summary (truncated to fit the model's
    # context window). Structure preserved for parsing. Full deliverables are
    # still scanned by the rule-based integrity detector separately.
    user_content = _trim_execution_summary_for_llm(summary)
    if include_skill and skill_used:
        user_content["skill_used"] = skill_used

    async with httpx.AsyncClient(timeout=240) as client:
        for strict in (False, True):
            payload = _build_deep_review_payload(model, user_content, strict=strict)
            try:
                resp = await client.post("http://localhost:8000/api/llm/v1/chat/completions", json=payload)
            except httpx.HTTPError as exc:
                logger.warning("Deep review LLM request failed (%s): %s", model, exc)
                continue
            if resp.status_code != 200:
                logger.warning("Deep review LLM returned %s for %s: %s", resp.status_code, model, resp.text[:500])
                break
            content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = _extract_json_object(content)
            if parsed is not None:
                normalized = _normalize_review_payload(parsed)
                # Prefer an explicit verdict; otherwise derive one from
                # issues/score so wrapped-but-valid audits work.
                verdict = _normalize_verdict(normalized.get("verdict"))
                if not verdict:
                    verdict = _derive_verdict_from_payload(normalized)
                    if verdict:
                        normalized["verdict"] = verdict
                if verdict:
                    normalized["model"] = model
                    return normalized
            logger.warning("Deep review: could not parse output for %s (strict=%s); retrying", model, strict)
    return {"verdict": "needs_attention", "score": 0, "summary": "Could not parse LLM output", "issues": [], "positives": [], "model": model}


@router.post("/deep-review", response_model=DeepReviewResponse)
async def deep_review_task(data: DeepReviewRequest, db: AsyncSession = Depends(get_db)):
    """Run an in-depth integrity audit of a task's execution for hallucinations,
    synthetic data generation, shortcuts, and quality shortcuts.

    Returns a structured verdict + list of issues with evidence, scoped to the
    task's agent image.
    """
    task = await db.get(Task, data.task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{data.task_id}' not found")

    node = None
    if task.dag_id and data.node_id:
        nr = await db.execute(
            select(DAGNode).where(DAGNode.dag_id == task.dag_id, DAGNode.node_id == data.node_id)
        )
        node = nr.scalars().first()

    img = await _resolve_or_create_image(task, node, db)
    image_id = img.id if img else "openclaw"
    image_tag = img.tag if img else ""

    skill_used = None
    if node:
        if getattr(node, "selected_skill_v2_id", None):
            sv = await db.get(SkillV2, node.selected_skill_v2_id)
            if sv:
                skill_used = {"id": sv.id, "name": sv.name, "instructions": sv.instructions}
        elif getattr(node, "skill_id", None):
            sk = await db.get(Skill, node.skill_id)
            if sk:
                skill_used = {"id": sk.id, "name": sk.name, "instructions": getattr(sk, "instructions", None)}

    result = await db.execute(
        select(TaskOutput).where(TaskOutput.task_id == data.task_id).order_by(TaskOutput.iteration)
    )
    outputs = list(result.scalars().all())
    if not outputs:
        raise HTTPException(status_code=404, detail=f"No outputs found for task '{data.task_id}'")
    summary = _build_execution_summary(task, outputs, node=node, image_id=image_id)

    review = await _call_deep_review_llm(summary, skill_used, task, include_skill=data.include_skill)

    # Hard-integrity safety net: placeholder/synthetic markers in DATA files are an
    # unambiguous fabrication signal (e.g. description_snippet "N/A", empty results
    # reported as success). These are concrete facts, so surface them as high-severity
    # issues and prevent a "clean" verdict. This only triggers on hard evidence, not on
    # the LLM's opinion — the LLM remains the primary judge.
    llm_issues = [DeepReviewIssue(**i) for i in (review.get("issues") or []) if isinstance(i, dict)]
    hard_signals = _detect_integrity_signals(summary)
    merged_issues = list(llm_issues)
    if hard_signals:
        for sig in hard_signals:
            merged_issues.append(DeepReviewIssue(
                severity="high",
                category="synthetic_data",
                finding=sig,
                evidence="rule-based scan of deliverable data files",
                recommendation="Verify deliverable contents contain real fetched data (not placeholders); add validation before reporting success.",
            ))

    if hard_signals:
        verdict = "issues_found"
        score = min(_normalize_score(review.get("score")), 55) if review.get("score") else 50
    else:
        verdict = _normalize_verdict(review.get("verdict"))
        if not verdict:
            # Model returned a malformed/wrong-shaped payload (e.g. echoed task
            # status instead of a verdict). Fall back gracefully instead of
            # surfacing the raw value (e.g. "completed").
            verdict = "needs_attention" if (review.get("issues") or []) else "clean"
        score = _normalize_score(review.get("score"))

    review_model = review.get("model") or _deep_review_model()

    response = DeepReviewResponse(
        task_id=data.task_id,
        node_id=data.node_id,
        image_id=image_id,
        image_tag=image_tag,
        skill_used_id=skill_used.get("id") if skill_used else None,
        skill_used_name=skill_used.get("name") if skill_used else None,
        verdict=verdict,
        score=score,
        summary=review.get("summary", ""),
        issues=merged_issues,
        positives=[str(p) for p in (review.get("positives") or [])],
        model=review_model,
    )

    # Persist so each step's review is retained and switchable in the UI.
    record = DeepReview(
        dag_id=data.dag_id or task.dag_id,
        node_id=data.node_id or task.node_id or "",
        task_id=data.task_id,
        image_id=image_id,
        image_tag=image_tag,
        skill_used_id=response.skill_used_id,
        skill_used_name=response.skill_used_name,
        model=review_model,
        include_skill=data.include_skill,
        verdict=verdict,
        score=score,
        summary=response.summary,
        issues=[i.model_dump() for i in merged_issues],
        positives=response.positives,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    response.id = record.id
    response.created_at = record.created_at
    return response


@router.get("/deep-review", response_model=List[DeepReviewResponse])
async def list_deep_reviews(
    dag_id: Optional[str] = Query(None),
    node_id: Optional[str] = Query(None),
    task_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Return persisted deep-review results so the UI can show each step's review."""
    stmt = select(DeepReview).order_by(DeepReview.created_at.desc())
    if dag_id:
        stmt = stmt.where(DeepReview.dag_id == dag_id)
    if node_id:
        stmt = stmt.where(DeepReview.node_id == node_id)
    if task_id:
        stmt = stmt.where(DeepReview.task_id == task_id)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [
        DeepReviewResponse(
            task_id=r.task_id,
            node_id=r.node_id,
            image_id=r.image_id or "openclaw",
            image_tag=r.image_tag or "",
            skill_used_id=r.skill_used_id,
            skill_used_name=r.skill_used_name,
            verdict=r.verdict,
            score=r.score,
            summary=r.summary or "",
            issues=[DeepReviewIssue(**i) for i in (r.issues or []) if isinstance(i, dict)],
            positives=list(r.positives or []),
            id=r.id,
            model=r.model,
            created_at=r.created_at,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Skill correction from deep review — fix the skill so issues are prevented
# ---------------------------------------------------------------------------

class SkillCorrectRequest(BaseModel):
    task_id: str
    node_id: Optional[str] = None
    dag_id: Optional[str] = None
    skill_id: Optional[str] = None   # optional explicit skill to correct
    created_by: Optional[str] = None


class CorrectedSkill(BaseModel):
    skill_id: str            # id of the corrected skill (new node)
    parent_id: Optional[str] # original skill this corrects
    image_id: str
    name: str
    description: str
    instructions: str
    tags: List[str]
    status: str
    addressed_issues: List[str]  # issues this correction addresses
    assessment: str


class SkillCorrectResponse(BaseModel):
    task_id: str
    image_id: str
    skill_id: str              # original skill id (parent)
    skill_name: str            # original skill name
    corrected: List[CorrectedSkill]
    unchanged: bool


def _build_correction_context(summary: dict, skill: dict, issues: List[dict]) -> dict:
    """Build a compact context for skill correction.

    The correction task only needs the skill, the concrete issues, and a brief
    execution summary — NOT the full deliverable file contents (which can overflow
    the model context window). Deliverable filenames + sizes are kept for reference.
    """
    def _trim_iter(it: dict) -> dict:
        deliverables = it.get("deliverables") or {}
        return {
            "iteration": it.get("iteration"),
            "status": it.get("status"),
            "error": it.get("error"),
            "deliverables_produced": it.get("deliverables_produced") or list(deliverables.keys()),
            "deliverable_sizes": {
                k: len(v) if isinstance(v, str) else None
                for k, v in (deliverables or {}).items()
            },
            "turn_count": it.get("turn_count"),
            "turns": [
                {
                    "turn": t.get("turn"),
                    "command_issued": t.get("command_issued"),
                    "output": (t.get("output") or "")[:600],
                }
                for t in (it.get("turns") or [])
            ],
            "output": (it.get("output") or "")[:1000],
        }

    return {
        "task": {
            "task_id": summary.get("task_metadata", {}).get("task_id"),
            "name": summary.get("task_metadata", {}).get("task_name"),
            "description": summary.get("task_metadata", {}).get("description"),
            "base_image": summary.get("task_metadata", {}).get("base_image"),
        },
        "skill_used": {"id": skill.get("id"), "name": skill.get("name"), "instructions": (skill.get("instructions") or "")[:2000]},
        "issues": issues,
        "execution_summary": {
            "task_metadata": summary.get("task_metadata"),
            "iterations": [_trim_iter(it) for it in (summary.get("iterations") or [])],
        },
    }


async def _correct_skill_llm(summary: dict, skill: dict, issues: List[dict], task: Task) -> dict:
    """Ask the LLM to produce a corrected version of the skill that prevents the issues."""
    import httpx
    user_context = _build_correction_context(summary, skill, issues)
    payload = {
        "model": _deep_review_model(),
        "max_tokens": 30000,
        "thinking": {"type": "disabled"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a senior skill-quality engineer. You are given a skill and an integrity-audit "
                    "report listing concrete issues found when the agent executed that skill (hallucinations, "
                    "synthetic/mock data generation, shortcuts, quality problems). You must produce an IMPROVED "
                    "version of the skill whose instructions PREVENT these issues from recurring.\n"
                    "Write the corrected instructions as PSEUDO-CODE, not prose — the skill is executed by "
                    "another AI agent, so instructions must be an explicit, structured algorithm it can follow "
                    "verbatim. Requirements:\n"
                    "- One statement per line (plain text, no JSON nesting); use indentation for block scope.\n"
                    "- Name every exact source, URL/API endpoint, and tool call explicitly.\n"
                    "- Make control flow explicit: IF/ELSE, FOR EACH, WHILE, RETRY / ON FAILURE.\n"
                    "- Include mandatory verification steps: validate fetched data is real, check for "
                    "placeholders/N-A, verify row counts, confirm non-empty results before declaring success; "
                    "spell out how to react (retry, fail, or mark an issue) when a check fails.\n"
                    "- Add explicit 'do NOT' rules against the specific failure modes found (no mock/synthetic "
                    "data, no fabricating URLs/descriptions, no declaring success on empty results).\n"
                    "- End with an explicit RETURN of the final deliverable/result.\n"
                    "Keep the working, correct parts of the original skill. Respond with ONLY valid JSON, no "
                    "surrounding text, in this exact shape:\n"
                    "{\n"
                    '  "name": "<corrected skill name>",\n'
                    '  "description": "<one-line description>",\n'
                    '  "instructions": "<full corrected instructions as pseudo-code>",\n'
                    '  "tags": ["<tag>", ...],\n'
                    '  "addressed_issues": ["<issue this correction addresses>", ...]\n'
                    "}\n"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(user_context, default=str),
            },
        ],
    }
    async with httpx.AsyncClient(timeout=240) as client:
        for attempt in range(2):
            resp = await client.post("http://localhost:8000/api/llm/v1/chat/completions", json=payload)
            if resp.status_code != 200:
                logger.warning("Skill correction LLM returned %s: %s", resp.status_code, resp.text[:500])
                return None
            content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = _extract_json_object(content)
            if parsed is not None:
                norm = _normalize_review_payload(parsed) or parsed
                if norm.get("instructions"):
                    return norm
            logger.warning("Skill correction: could not parse/validate output on attempt %s; retrying", attempt + 1)
            payload = {
                **payload,
                "messages": [
                    {"role": "system", "content": "Output ONLY a single valid JSON object with the corrected skill. No markdown, no commentary, no trailing text."},
                    {"role": "user", "content": f"Return the corrected skill as valid JSON only.\n\n{json.dumps(user_context, default=str)}"},
                ],
            }
    logger.warning("Skill correction failed after retries")
    return None


@router.post("/correct", response_model=SkillCorrectResponse)
async def correct_skill_from_review(data: SkillCorrectRequest, db: AsyncSession = Depends(get_db)):
    """Correct a skill based on deep-review findings so the issues are prevented.

    Runs the deep review on the task, then produces an improved SkillV2 node
    (child of the original) whose instructions explicitly prevent the found issues.
    """
    task = await db.get(Task, data.task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{data.task_id}' not found")

    node = None
    if task.dag_id and data.node_id:
        from models import DAGNode
        nr = await db.execute(select(DAGNode).where(DAGNode.dag_id == task.dag_id, DAGNode.node_id == data.node_id))
        node = nr.scalars().first()

    img = await _resolve_or_create_image(task, node, db)
    image_id = img.id if img else "openclaw"

    # Resolve the skill to correct (explicit skill_id, or the skill used on the node)
    skill = None
    skill_id = data.skill_id
    if node and not skill_id:
        if getattr(node, "selected_skill_v2_id", None):
            skill_id = node.selected_skill_v2_id
        elif getattr(node, "skill_id", None):
            skill_id = node.skill_id
    if skill_id:
        sv = await db.get(SkillV2, skill_id)
        if sv:
            skill = {"id": sv.id, "name": sv.name, "instructions": sv.instructions}

    if not skill:
        raise HTTPException(status_code=404, detail="No skill to correct — provide skill_id or a node with a skill assigned")

    # Build summary
    result = await db.execute(select(TaskOutput).where(TaskOutput.task_id == data.task_id).order_by(TaskOutput.iteration))
    outputs = list(result.scalars().all())
    if not outputs:
        raise HTTPException(status_code=404, detail=f"No outputs found for task '{data.task_id}'")
    summary = _build_execution_summary(task, outputs, node=node, image_id=image_id)

    # Run deep review to get the issues
    review = await _call_deep_review_llm(summary, skill, task, include_skill=False)
    llm_issues = review.get("issues", []) or []
    hard_signals = _detect_integrity_signals(summary)
    issues_for_correction = []
    for i in llm_issues:
        if isinstance(i, dict):
            issues_for_correction.append({
                "severity": i.get("severity", "medium"),
                "category": i.get("category", "quality"),
                "finding": i.get("finding", ""),
                "recommendation": i.get("recommendation", ""),
            })
    for sig in hard_signals:
        issues_for_correction.append({
            "severity": "high",
            "category": "synthetic_data",
            "finding": sig,
            "recommendation": "Verify deliverable contents contain real fetched data; add validation before reporting success.",
        })

    # If no issues, nothing to correct
    if not issues_for_correction:
        return SkillCorrectResponse(
            task_id=data.task_id, image_id=image_id, skill_id=skill["id"],
            skill_name=skill["name"], corrected=[], unchanged=True,
        )

    corrected_data = await _correct_skill_llm(summary, skill, issues_for_correction, task)
    if not corrected_data:
        raise HTTPException(status_code=500, detail="Failed to generate corrected skill")

    # Create corrected skill node (child of original)
    corrected_node = SkillV2(
        id=_skv2_id(),
        image_id=image_id,
        parent_id=skill["id"],
        name=(corrected_data.get("name") or skill["name"]).strip(),
        description=corrected_data.get("description", "") or skill.get("name", ""),
        instructions=(corrected_data.get("instructions") or "").strip(),
        tags=corrected_data.get("tags", []) or [],
        source_type=SkillV2Source.AUDIT_MINE,
        status=SkillV2Status.DRAFT,
        evidence_task_ids=[data.task_id],
        created_by=data.created_by,
    )
    db.add(corrected_node)
    await db.commit()
    await db.refresh(corrected_node)

    corrected_skill = CorrectedSkill(
        skill_id=corrected_node.id,
        parent_id=skill["id"],
        image_id=image_id,
        name=corrected_node.name,
        description=corrected_node.description,
        instructions=corrected_node.instructions,
        tags=corrected_node.tags,
        status=corrected_node.status.value,
        addressed_issues=corrected_data.get("addressed_issues", []) or [i["finding"] for i in issues_for_correction],
        assessment=review.get("summary", ""),
    )

    return SkillCorrectResponse(
        task_id=data.task_id, image_id=image_id, skill_id=skill["id"],
        skill_name=skill["name"], corrected=[corrected_skill], unchanged=False,
    )
