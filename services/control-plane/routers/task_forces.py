"""
Task Force router — Multi-Agent Orchestration

Provides CRUD endpoints for creating, managing, and launching
Task Forces (teams of agents with defined roles and ceremonies).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List
import uuid
from datetime import datetime

from database import get_db
from models import (
    TaskForce, TaskForceStatus as TFStatus,
    TaskForceMember, TaskForceCeremony,
    Task, TaskStatus, Policy,
)
from schemas import (
    TaskForceCreate, TaskForceResponse, TaskForceDetail,
    TaskForceMemberCreate, TaskForceMemberResponse,
    TaskForceCeremonyCreate, TaskForceCeremonyResponse,
)
from temporal_client import start_task_force_workflow

router = APIRouter()


# ── CREATE ───────────────────────────────────────────────

@router.post("", response_model=TaskForceDetail, status_code=status.HTTP_201_CREATED)
async def create_task_force(
    data: TaskForceCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a Task Force with members and ceremonies.

    The Task Force is created in DRAFT status.  Call POST /start
    to launch all agent workflows.
    """
    if not data.members:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A Task Force must have at least one member.",
        )

    tf_id = f"taskforce-{str(uuid.uuid4())[:8]}"
    workspace_id = f"workspace-{str(uuid.uuid4())[:8]}"

    tf = TaskForce(
        id=tf_id,
        name=data.name,
        description=data.description,
        objective=data.objective,
        execution_environment=data.execution_environment,
        status=TFStatus.ACTIVE,
        workspace_id=workspace_id,
    )
    db.add(tf)
    await db.flush()

    # Create members
    for m in data.members:
        member = TaskForceMember(
            task_force_id=tf_id,
            agent_profile=m.agent_profile,
            role=m.role,
            responsibilities=m.responsibilities,
            llm_model=m.llm_model,
            base_image=m.base_image,
            execution_order=m.execution_order,
        )
        db.add(member)

    # Create ceremonies
    for c in data.ceremonies:
        ceremony = TaskForceCeremony(
            task_force_id=tf_id,
            name=c.name,
            ceremony_type=c.ceremony_type,
            mode=c.mode,
            sequence_order=c.sequence_order,
            participant_member_ids=c.participant_member_ids,
            description=c.description,
            trigger_condition=c.trigger_condition,
            timeout_minutes=c.timeout_minutes,
        )
        db.add(ceremony)

    await db.commit()

    # Re-fetch with relationships
    return await _get_task_force_detail(db, tf_id)


# ── AS AGENT PROFILES ────────────────────────────────────

@router.get("/as-profiles")
async def list_as_profiles(
    db: AsyncSession = Depends(get_db),
):
    """Return active Task Forces formatted as virtual agent profiles.

    The API gateway merges these with real agent profiles so Task Forces
    appear in the agent selection dropdown.
    """
    result = await db.execute(
        select(TaskForce)
        .options(selectinload(TaskForce.members))
        .where(TaskForce.status == TFStatus.ACTIVE)
        .order_by(TaskForce.created_at.desc())
    )
    task_forces = result.scalars().all()

    profiles = []
    for tf in task_forces:
        member_roles = [m.role for m in tf.members]
        member_count = len(tf.members)
        profiles.append({
            "id": tf.id,  # e.g. "taskforce-abc12345"
            "name": f"\u2693 {tf.name}",
            "description": tf.objective or tf.description or "",
            "base_image": "openclaw",  # primary image
            "llm_model": "multi-agent",
            "tags": ["task-force", f"{member_count}-agents"],
            "icon": "\u2693",
            "is_task_force": True,
            "member_count": member_count,
            "member_roles": member_roles,
            "metadata": {
                "runtime": f"Task Force ({member_count} agents)",
                "strengths": member_roles,
            },
        })

    return {"profiles": profiles}


# ── LIST ─────────────────────────────────────────────────

@router.get("", response_model=List[TaskForceResponse])
async def list_task_forces(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """List all Task Forces."""
    result = await db.execute(
        select(TaskForce)
        .offset(skip)
        .limit(limit)
        .order_by(TaskForce.created_at.desc())
    )
    return result.scalars().all()


# ── GET DETAIL ───────────────────────────────────────────

@router.get("/{tf_id}", response_model=TaskForceDetail)
async def get_task_force(
    tf_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get full Task Force detail including members and ceremonies."""
    return await _get_task_force_detail(db, tf_id)


# ── START ────────────────────────────────────────────────

@router.post("/{tf_id}/start", response_model=TaskForceDetail)
async def start_task_force(
    tf_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Launch the Task Force workflow.

    Creates individual tasks for each member and starts the
    TaskForceWorkflow in Temporal which orchestrates execution
    per the defined ceremonies.
    """
    tf = await _get_tf_or_404(db, tf_id)

    if tf.status not in (TFStatus.DRAFT, TFStatus.ACTIVE):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot start Task Force in status '{tf.status.value}'. Must be 'draft' or 'active'.",
        )

    # Eagerly load members
    result = await db.execute(
        select(TaskForceMember).where(TaskForceMember.task_force_id == tf_id)
    )
    members = result.scalars().all()

    if not members:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task Force has no members.",
        )

    # Load ceremonies so we can inform agents about the process
    ceremony_result = await db.execute(
        select(TaskForceCeremony)
        .where(TaskForceCeremony.task_force_id == tf_id)
        .order_by(TaskForceCeremony.sequence_order)
    )
    ceremonies = ceremony_result.scalars().all()

    # Determine which member is the "lead" (first Developer, otherwise first member)
    lead_roles = {"developer", "lead", "architect", "engineer", "implementer"}
    support_roles = {"tester", "reviewer", "qa", "auditor", "validator"}
    lead_member = None
    for m in members:
        if m.role and m.role.lower() in lead_roles:
            lead_member = m
            break
    if not lead_member:
        lead_member = members[0]  # Fallback: first member is lead

    # Build team roster for context
    team_roster = "\n".join(
        f"  - **{m.role}** ({m.agent_profile})"
        + (" ← YOU" if m.id == lead_member.id else "")
        for m in members
    )

    # Build ceremony schedule description
    ceremony_schedule = ""
    if ceremonies:
        ceremony_lines = []
        for c in ceremonies:
            ceremony_lines.append(
                f"  {c.sequence_order + 1}. **{c.name}** ({c.ceremony_type}) — {c.description or 'No description'}"
            )
        ceremony_schedule = (
            "## PROCESS & CEREMONIES\n"
            "The Task Force follows a structured process with defined ceremonies.\n"
            "Before you start working, check if a `CEREMONY_PLAN.md` file exists in\n"
            "the workspace — it contains the coordinated work plan. Follow it.\n\n"
            "Ceremony schedule:\n" + "\n".join(ceremony_lines) + "\n\n"
            "After each ceremony, check the workspace for any `REVIEW_BRIEF.md` or\n"
            "other ceremony artifacts that may contain feedback or instructions.\n\n"
        )

    # Create a Task for each member
    for member in members:
        task_id = f"task-{str(uuid.uuid4())[:8]}"
        workspace_id = tf.workspace_id  # Shared workspace

        llm_model = member.llm_model or "gemini-flash-latest"
        base_image_key = member.base_image or "openclaw"
        base_image_tag = f"localhost:5000/openclaw-agent:{base_image_key}"

        is_lead = (member.id == lead_member.id)
        is_support = member.role and member.role.lower() in support_roles

        # Deployment instructions based on role
        if is_lead:
            deploy_instructions = (
                "You are the **deployment lead**. When the implementation is ready "
                "and tested, YOU MUST request deployment as your FINAL action.\n"
                "Other team members will NOT handle deployment.\n"
                "**To deploy**, output this EXACT marker on its own line:\n"
                "```\n"
                "DEPLOYMENT_REQUEST:<app-name>:<port>:<entrypoint command>\n"
                "```\n"
                "Example: `DEPLOYMENT_REQUEST:my-flask-app:5000:python main.py`\n"
                "This is REQUIRED — do NOT simply finish without deploying.\n"
            )
        elif is_support:
            deploy_instructions = (
                "**DO NOT** request deployments or emit DEPLOYMENT_REQUEST markers.\n"
                "Deployment is handled by the team lead ({lead_role}). Your job is to\n"
                "review, test, and provide feedback. Write your findings to the workspace\n"
                "so the lead can see them.\n".format(lead_role=lead_member.role)
            )
        else:
            deploy_instructions = (
                "Coordinate with the team lead ({lead_role}) before requesting any\n"
                "deployment. Only deploy if you are sure no one else is handling it.\n"
                .format(lead_role=lead_member.role)
            )

        # Build a per-agent description that includes the role, responsibilities,
        # team context, ceremony schedule, and deployment rules
        agent_description = (
            f"## TASK FORCE OBJECTIVE\n{tf.objective}\n\n"
            f"## YOUR ROLE: {member.role}\n"
            f"{member.responsibilities or 'Execute tasks within your assigned role.'}\n\n"
            f"## TEAM COMPOSITION\n"
            f"You are part of **{tf.name}** with {len(members)} agents:\n"
            f"{team_roster}\n\n"
            f"{ceremony_schedule}"
            f"## COORDINATION RULES\n"
            f"- You share a workspace with other agents. Check for files from teammates.\n"
            f"- {deploy_instructions}\n"
            f"- When you finish your work, write a summary to `/workspace/DONE_{member.role.upper().replace(' ', '_')}.md`\n"
            f"  so other agents can see what you produced.\n"
            f"- Focus strictly on your role: **{member.role}**. Do not duplicate others' work.\n"
        )

        task = Task(
            id=task_id,
            name=f"[{tf.name}] {member.role}",
            description=agent_description,
            workspace_id=workspace_id,
            status=TaskStatus.CREATED,
            current_image=base_image_tag,
            llm_model=llm_model,
            agent_profile=member.agent_profile,
            task_force_id=tf_id,
            task_force_role=member.role,
        )
        db.add(task)
        await db.flush()

        # Create initial policy
        policy = Policy(
            task_id=task_id,
            version=1,
            tools_allowed=[],
            network_rules={},
            filesystem_rules={"read": ["/workspace"], "write": ["/workspace/output"]},
            database_rules={},
            resource_limits={"max_cpu": "2", "max_memory": "4Gi", "timeout": "1h"},
        )
        db.add(policy)
        await db.flush()
        task.current_policy_id = policy.id

        # Link member to its task
        member.task_id = task_id
        member.status = "created"

    # Start the Task Force workflow in Temporal
    try:
        workflow_id = await start_task_force_workflow(tf_id)
        tf.status = TFStatus.RUNNING
        tf.workflow_id = workflow_id
        tf.started_at = datetime.utcnow()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to start Task Force {tf_id}: {e}")
        # Still saved — can retry

    await db.commit()
    return await _get_task_force_detail(db, tf_id)


# ── COMPLETE ─────────────────────────────────────────────

@router.post("/{tf_id}/complete", response_model=TaskForceResponse)
async def complete_task_force(tf_id: str, db: AsyncSession = Depends(get_db)):
    """Mark a running/paused Task Force as completed."""
    tf = await _get_tf_or_404(db, tf_id)
    if tf.status not in (TFStatus.RUNNING, TFStatus.PAUSED):
        raise HTTPException(400, "Can only complete a running or paused Task Force.")
    tf.status = TFStatus.COMPLETED
    tf.completed_at = datetime.utcnow()
    await db.commit()
    await db.refresh(tf)
    return tf


# ── PAUSE / RESUME / CANCEL ─────────────────────────────

@router.post("/{tf_id}/pause", response_model=TaskForceResponse)
async def pause_task_force(tf_id: str, db: AsyncSession = Depends(get_db)):
    tf = await _get_tf_or_404(db, tf_id)
    if tf.status != TFStatus.RUNNING:
        raise HTTPException(400, "Can only pause a running Task Force.")
    tf.status = TFStatus.PAUSED
    await db.commit()
    await db.refresh(tf)
    return tf


@router.post("/{tf_id}/resume", response_model=TaskForceResponse)
async def resume_task_force(tf_id: str, db: AsyncSession = Depends(get_db)):
    tf = await _get_tf_or_404(db, tf_id)
    if tf.status != TFStatus.PAUSED:
        raise HTTPException(400, "Can only resume a paused Task Force.")
    tf.status = TFStatus.RUNNING
    await db.commit()
    await db.refresh(tf)
    return tf


@router.post("/{tf_id}/cancel", response_model=TaskForceResponse)
async def cancel_task_force(tf_id: str, db: AsyncSession = Depends(get_db)):
    tf = await _get_tf_or_404(db, tf_id)
    if tf.status in (TFStatus.COMPLETED, TFStatus.CANCELLED):
        raise HTTPException(400, f"Task Force already {tf.status.value}.")
    tf.status = TFStatus.CANCELLED
    await db.commit()
    await db.refresh(tf)
    return tf


# ── ADD / REMOVE MEMBERS ────────────────────────────────

@router.post("/{tf_id}/members", response_model=TaskForceMemberResponse, status_code=201)
async def add_member(
    tf_id: str,
    member_data: TaskForceMemberCreate,
    db: AsyncSession = Depends(get_db),
):
    """Add a member to a draft/active Task Force."""
    tf = await _get_tf_or_404(db, tf_id)
    if tf.status not in (TFStatus.DRAFT, TFStatus.ACTIVE):
        raise HTTPException(400, "Can only add members to a draft or active Task Force.")
    member = TaskForceMember(
        task_force_id=tf_id,
        agent_profile=member_data.agent_profile,
        role=member_data.role,
        responsibilities=member_data.responsibilities,
        llm_model=member_data.llm_model,
        base_image=member_data.base_image,
        execution_order=member_data.execution_order,
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return member


@router.delete("/{tf_id}/members/{member_id}", status_code=204)
async def remove_member(
    tf_id: str,
    member_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Remove a member from a draft/active Task Force."""
    tf = await _get_tf_or_404(db, tf_id)
    if tf.status not in (TFStatus.DRAFT, TFStatus.ACTIVE):
        raise HTTPException(400, "Can only remove members from a draft or active Task Force.")
    result = await db.execute(
        select(TaskForceMember).where(
            TaskForceMember.id == member_id,
            TaskForceMember.task_force_id == tf_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(404, "Member not found.")
    await db.delete(member)
    await db.commit()


# ── ADD / REMOVE CEREMONIES ─────────────────────────────

@router.post("/{tf_id}/ceremonies", response_model=TaskForceCeremonyResponse, status_code=201)
async def add_ceremony(
    tf_id: str,
    data: TaskForceCeremonyCreate,
    db: AsyncSession = Depends(get_db),
):
    """Add a ceremony to a draft/active Task Force."""
    tf = await _get_tf_or_404(db, tf_id)
    if tf.status not in (TFStatus.DRAFT, TFStatus.ACTIVE):
        raise HTTPException(400, "Can only add ceremonies to a draft or active Task Force.")
    ceremony = TaskForceCeremony(
        task_force_id=tf_id,
        name=data.name,
        ceremony_type=data.ceremony_type,
        mode=data.mode,
        sequence_order=data.sequence_order,
        participant_member_ids=data.participant_member_ids,
        description=data.description,
        trigger_condition=data.trigger_condition,
        timeout_minutes=data.timeout_minutes,
    )
    db.add(ceremony)
    await db.commit()
    await db.refresh(ceremony)
    return ceremony


@router.delete("/{tf_id}/ceremonies/{ceremony_id}", status_code=204)
async def remove_ceremony(
    tf_id: str,
    ceremony_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Remove a ceremony from a draft/active Task Force."""
    tf = await _get_tf_or_404(db, tf_id)
    if tf.status not in (TFStatus.DRAFT, TFStatus.ACTIVE):
        raise HTTPException(400, "Can only remove ceremonies from a draft or active Task Force.")
    result = await db.execute(
        select(TaskForceCeremony).where(
            TaskForceCeremony.id == ceremony_id,
            TaskForceCeremony.task_force_id == tf_id,
        )
    )
    ceremony = result.scalar_one_or_none()
    if not ceremony:
        raise HTTPException(404, "Ceremony not found.")
    await db.delete(ceremony)
    await db.commit()


# ── DELETE ───────────────────────────────────────────────

@router.delete("/{tf_id}", status_code=204)
async def delete_task_force(tf_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a draft/active Task Force and all its members/ceremonies."""
    tf = await _get_tf_or_404(db, tf_id)
    if tf.status not in (TFStatus.DRAFT, TFStatus.ACTIVE):
        raise HTTPException(400, "Can only delete a draft or active Task Force.")
    await db.delete(tf)
    await db.commit()


# ── HELPERS ──────────────────────────────────────────────

async def _get_tf_or_404(db: AsyncSession, tf_id: str) -> TaskForce:
    result = await db.execute(select(TaskForce).where(TaskForce.id == tf_id))
    tf = result.scalar_one_or_none()
    if not tf:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task Force not found.")
    return tf


async def _get_task_force_detail(db: AsyncSession, tf_id: str) -> TaskForceDetail:
    """Fetch a Task Force with all relationships for the detail response."""
    result = await db.execute(
        select(TaskForce)
        .options(
            selectinload(TaskForce.members),
            selectinload(TaskForce.ceremonies),
        )
        .where(TaskForce.id == tf_id)
    )
    tf = result.scalar_one_or_none()
    if not tf:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task Force not found.")

    from schemas import TaskForceDetail as TFD
    return TFD(
        id=tf.id,
        name=tf.name,
        description=tf.description,
        objective=tf.objective,
        execution_environment=tf.execution_environment,
        status=tf.status,
        workspace_id=tf.workspace_id,
        workflow_id=tf.workflow_id,
        created_by=tf.created_by,
        created_at=tf.created_at,
        updated_at=tf.updated_at,
        started_at=tf.started_at,
        completed_at=tf.completed_at,
        members=[
            TaskForceMemberResponse.model_validate(m) for m in tf.members
        ],
        ceremonies=[
            TaskForceCeremonyResponse.model_validate(c) for c in tf.ceremonies
        ],
    )
