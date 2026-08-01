"""
Task management router
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Any, Dict, Optional
import uuid
from datetime import datetime
import httpx
import os
import logging

from database import get_db
from models import (
    Task, TaskStatus, Policy, TaskOutput,
)
from schemas import TaskCreate, TaskResponse, TaskDetail, TaskContinue
from temporal_client import start_task_workflow, continue_task_workflow

router = APIRouter()


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    task_data: TaskCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new task."""
    profile = task_data.agent_profile or ""

    # ── Normal single-agent path ─────────────────────────────────────
    
    # Generate task ID
    task_id = f"task-{str(uuid.uuid4())[:8]}"
    workspace_id = task_data.workspace_id or f"workspace-{str(uuid.uuid4())[:8]}"
    
    # Resolve aliased fields
    description = task_data.effective_description
    llm_model = task_data.effective_model
    base_image_tag = task_data.effective_base_image_tag

    # Create task first (without policy reference)
    task = Task(
        id=task_id,
        name=task_data.name,
        description=description,
        workspace_id=workspace_id,
        status=TaskStatus.CREATED,
        current_policy_id=None,
        llm_model=llm_model,
        current_image=base_image_tag,
        agent_profile=task_data.agent_profile,
        dag_id=task_data.dag_id,
        node_id=task_data.node_id,
    )
    
    db.add(task)
    await db.flush()
    
    # Now create initial policy with task_id foreign key
    initial_policy = Policy(
        task_id=task_id,
        version=1,
        tools_allowed=task_data.initial_policy.get("tools_allowed", []) if task_data.initial_policy else [],
        network_rules=task_data.initial_policy.get("network_rules", {}) if task_data.initial_policy else {},
        filesystem_rules=task_data.initial_policy.get("filesystem_rules", {
            "read": ["/workspace"],
            "write": ["/workspace/output"]
        }) if task_data.initial_policy else {},
        database_rules=task_data.initial_policy.get("database_rules", {}) if task_data.initial_policy else {},
        resource_limits=task_data.initial_policy.get("resource_limits", {
            "max_cpu": "2",
            "max_memory": "4Gi",
            "timeout": "1h"
        }) if task_data.initial_policy else {}
    )
    
    db.add(initial_policy)
    await db.flush()
    
    # Update task with policy reference
    task.current_policy_id = initial_policy.id
    
    await db.commit()
    await db.refresh(task)

    # Auto-start the workflow (unless explicitly disabled)
    if task_data.auto_start:
        try:
            workflow_id = await start_task_workflow(task_id, llm_model, base_image_tag)
            task.status = TaskStatus.RUNNING
            task.workflow_id = workflow_id
            task.started_at = datetime.utcnow()
            await db.commit()
            await db.refresh(task)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to auto-start task {task_id}: {e}")
            # Task is still created, user can start manually

    return task


@router.get("", response_model=List[TaskResponse])
async def list_tasks(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db)
):
    """List all tasks"""
    result = await db.execute(
        select(Task)
        .offset(skip)
        .limit(limit)
        .order_by(Task.created_at.desc())
    )
    tasks = result.scalars().all()
    return tasks


@router.get("/{task_id}", response_model=TaskDetail)
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get task details"""
    result = await db.execute(
        select(Task).where(Task.id == task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found"
        )
    
    return task


@router.post("/{task_id}/start")
async def start_task(
    task_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Start task execution"""
    result = await db.execute(
        select(Task).where(Task.id == task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found"
        )
    
    if task.status != TaskStatus.CREATED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task must be in CREATED status to start (current: {task.status})"
        )
    
    # Start Temporal workflow
    workflow_id = await start_task_workflow(task_id, task.llm_model or "gemma3:4b")
    
    # Update task
    task.status = TaskStatus.RUNNING
    task.workflow_id = workflow_id
    task.started_at = datetime.utcnow()
    
    await db.commit()
    
    return {
        "task_id": task_id,
        "workflow_id": workflow_id,
        "status": "started"
    }


@router.post("/{task_id}/pause")
async def pause_task(
    task_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Pause task execution"""
    result = await db.execute(
        select(Task).where(Task.id == task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found"
        )
    
    if task.status != TaskStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task must be in RUNNING status to pause"
        )
    
    # TODO: Signal Temporal workflow to pause
    
    task.status = TaskStatus.PAUSED
    await db.commit()
    
    return {"task_id": task_id, "status": "paused"}


@router.post("/{task_id}/resume")
async def resume_task(
    task_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Resume task execution"""
    result = await db.execute(
        select(Task).where(Task.id == task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found"
        )
    
    if task.status != TaskStatus.PAUSED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task must be in PAUSED status to resume"
        )
    
    # TODO: Signal Temporal workflow to resume
    
    task.status = TaskStatus.RUNNING
    await db.commit()
    
    return {"task_id": task_id, "status": "resumed"}


@router.post("/{task_id}/continue")
async def continue_task(
    task_id: str,
    body: TaskContinue,
    db: AsyncSession = Depends(get_db)
):
    """Continue iterating on a completed or failed task.

    Starts a new Temporal workflow that picks up from the last image
    and passes follow-up instructions to the agent.
    """
    result = await db.execute(
        select(Task).where(Task.id == task_id)
    )
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found"
        )

    if task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task must be completed or failed to continue (current: {task.status})"
        )

    # Determine the latest image used by looking at the last task output
    latest_image = task.current_image or ""
    if not latest_image:
        outputs_result = await db.execute(
            select(TaskOutput)
            .where(TaskOutput.task_id == task_id)
            .order_by(TaskOutput.iteration.desc())
            .limit(1)
        )
        last_output = outputs_result.scalar_one_or_none()
        if last_output and last_output.image_used:
            latest_image = last_output.image_used

    # Count existing continuations to generate unique workflow ID
    existing_workflows = task.workflow_id or ""
    cont_count = existing_workflows.count("-cont-") + 1 if "-cont-" in existing_workflows else 1

    llm_model = body.llm_model or task.llm_model or "gemma3:4b"

    # Append follow-up to description for context
    separator = "\n\n--- Follow-up Instructions ---\n"
    task.description = (task.description or "") + separator + body.follow_up

    # Start continuation workflow
    try:
        workflow_id = await continue_task_workflow(
            task_id=task_id,
            llm_model=llm_model,
            current_image=latest_image,
            follow_up=body.follow_up,
            continuation_number=cont_count,
        )
        task.status = TaskStatus.RUNNING
        task.workflow_id = workflow_id
        task.completed_at = None
        await db.commit()
        await db.refresh(task)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to continue task {task_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start continuation workflow: {e}"
        )

    return {
        "task_id": task_id,
        "workflow_id": workflow_id,
        "status": "running",
        "follow_up": body.follow_up,
        "current_image": latest_image,
        "continuation": cont_count,
    }


@router.post("/{task_id}/complete")
async def complete_task(
    task_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Mark task as completed"""
    result = await db.execute(
        select(Task).where(Task.id == task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found"
        )
    
    task.status = TaskStatus.COMPLETED
    task.completed_at = datetime.utcnow()
    await db.commit()
    
    return {"task_id": task_id, "status": "completed"}


@router.patch("/{task_id}/image")
async def update_task_image(
    task_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db)
):
    """Update the current image for a task after a capability build.

    Called by the temporal-worker after successfully building a new image
    with additional packages.  This ensures continuation workflows pick
    up the rebuilt image (with packages) instead of the bare base image.
    """
    result = await db.execute(
        select(Task).where(Task.id == task_id)
    )
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found"
        )

    new_image = body.get("current_image", "")
    if not new_image:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="current_image is required"
        )

    task.current_image = new_image
    await db.commit()

    return {"task_id": task_id, "current_image": new_image}


@router.patch("/{task_id}")
async def update_task(
    task_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Partially update mutable task fields (workflow_id, status, etc).

    Used by the temporal-worker to persist the Temporal workflow_id
    immediately after launching a member sub-task workflow, so that
    capability approval signals can be routed correctly.
    """
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Task {task_id} not found")

    if "workflow_id" in body:
        task.workflow_id = body["workflow_id"]
    if "current_image" in body:
        task.current_image = body["current_image"]

    await db.commit()
    return {"task_id": task_id, "updated": list(body.keys())}


@router.patch("/{task_id}/status")
async def update_task_status(
    task_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db)
):
    """Update task status (called by temporal worker for real-time state sync)."""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    new_status = body.get("status")
    if not new_status:
        raise HTTPException(status_code=400, detail="status is required")

    try:
        task.status = TaskStatus(new_status)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status: {new_status}")

    await db.commit()
    await db.refresh(task)
    return {"task_id": task_id, "status": task.status.value}


@router.post("/{task_id}/fail")
async def fail_task(
    task_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Mark task as failed"""
    result = await db.execute(
        select(Task).where(Task.id == task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found"
        )
    
    task.status = TaskStatus.FAILED
    task.completed_at = datetime.utcnow()
    await db.commit()
    
    return {"task_id": task_id, "status": "failed"}


@router.get("/{task_id}/logs")
async def get_task_logs(
    task_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get task execution logs"""
    # TODO: Implement log retrieval
    return {"task_id": task_id, "logs": []}


@router.get("/{task_id}/subtasks")
async def get_subtasks(
    task_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return sibling tasks belonging to the same DAG as this task."""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Task {task_id} not found")
    if not task.dag_id:
        return {"subtasks": []}

    sub_result = await db.execute(
        select(Task)
        .where(Task.dag_id == task.dag_id, Task.id != task_id)
        .order_by(Task.created_at)
    )
    subtasks = sub_result.scalars().all()

    # Fetch pending capability requests for all sub-task IDs
    subtask_ids = [s.id for s in subtasks]
    cap_reqs_by_task: dict = {}
    if subtask_ids:
        from models import CapabilityRequest
        cap_result = await db.execute(
            select(CapabilityRequest)
            .where(CapabilityRequest.task_id.in_(subtask_ids))
            .order_by(CapabilityRequest.requested_at.desc())
        )
        for cr in cap_result.scalars().all():
            cap_reqs_by_task.setdefault(cr.task_id, []).append({
                "id": cr.id,
                "type": cr.capability_type,
                "resource": cr.resource_name,
                "justification": cr.justification,
                "status": cr.status.value if hasattr(cr.status, 'value') else str(cr.status),
                "requested_at": cr.requested_at.isoformat() if cr.requested_at else None,
            })

    return {
        "dag_id": task.dag_id,
        "subtasks": [
            {
                "id": s.id,
                "name": s.name,
                "status": s.status.value if s.status else "unknown",
                "node_id": s.node_id,
                "agent_profile": s.agent_profile,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "capability_requests": cap_reqs_by_task.get(s.id, []),
                "has_pending_approval": any(
                    cr["status"] == "pending" for cr in cap_reqs_by_task.get(s.id, [])
                ),
            }
            for s in subtasks
        ],
    }


@router.get("/{task_id}/audit-logs/export")
async def export_audit_logs(task_id: str, db: AsyncSession = Depends(get_db)):
    """Export complete audit logs as structured JSON for skill extraction.
    
    Includes both:
    - TaskOutput data (persistent, stored in DB): iterations, deliverables, agent_logs
    - Temporal audit-turns (when available): per-LLM-turn tool calls, responses, tokens
    """
    import httpx
    import os
    
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    # Get all iterations from TaskOutput (persistent DB storage)
    result = await db.execute(
        select(TaskOutput).where(TaskOutput.task_id == task_id).order_by(TaskOutput.iteration)
    )
    outputs = list(result.scalars().all())

    # Build base export from TaskOutput
    export_data = {
        "task_id": task_id,
        "task_name": task.name,
        "task_description": task.description,
        "agent_profile": task.agent_profile,
        "base_image": task.current_image,
        "llm_model": task.llm_model,
        "status": task.status.value if task.status else "unknown",
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "dag_id": task.dag_id,
        "node_id": task.node_id,
        "iterations": [],
        "temporal_audit_turns": None,  # Will be populated if available
    }

    for out in outputs:
        export_data["iterations"].append({
            "iteration": out.iteration,
            "completed": out.completed,
            "agent_logs": out.agent_logs,
            "output": out.output,
            "error": out.error,
            "llm_response_preview": out.llm_response_preview,
            "model_used": out.model_used,
            "image_used": out.image_used,
            "duration_ms": out.duration_ms,
            "deliverables": out.deliverables,
            "raw_result": out.raw_result,
            "created_at": out.created_at.isoformat() if out.created_at else None,
        })

    # Try to fetch detailed Temporal audit-turns (per-LLM-turn data)
    temporal_data = await _fetch_temporal_audit_turns(task_id, task, db)
    if temporal_data:
        export_data["temporal_audit_turns"] = temporal_data
    else:
        # Fallback: extract turns from agent_logs when Temporal history is unavailable
        fallback_turns = []
        for out in outputs:
            if out.raw_result and isinstance(out.raw_result, dict):
                agent_logs = out.raw_result.get("agent_logs", "")
                extracted = _extract_turns_from_logs(agent_logs)
                deduped = _deduplicate_turns(extracted)
                if deduped:
                    fallback_turns.append({
                        "iteration": out.iteration,
                        "task_id": task_id,
                        "turns": deduped,
                        "turn_count": len(deduped),
                    })
        if fallback_turns:
            export_data["temporal_audit_turns"] = {
                "iterations": fallback_turns,
                "total_iterations": len(fallback_turns),
                "total_turns": sum(it["turn_count"] for it in fallback_turns),
                "source": "agent_logs_fallback",
            }

    return export_data


async def _fetch_temporal_audit_turns(task_id: str, task: Task, db: AsyncSession) -> Optional[Dict[str, Any]]:
    """Fetch detailed per-turn audit data from Temporal workflow history.
    
    This mirrors the logic in tasks_extended.py:get_audit_turns.
    It queries the specific task's workflow(s) and finds AgentStepWorkflow
    children to extract turn data from record_agent_turn activities.
    """
    import httpx
    import os
    
    temporal_http = os.getenv("TEMPORAL_HTTP_URL", "http://temporal-ui:8080")
    namespace = "default"

    # Collect all task IDs relevant for this audit query
    relevant_task_ids = [task_id]
    if task.dag_id:
        sub_result = await db.execute(
            select(Task.id)
            .where(Task.dag_id == task.dag_id, Task.id != task_id)
        )
        relevant_task_ids.extend([row[0] for row in sub_result.all()])

    # Pre-fetch workflow_ids for all relevant tasks
    task_workflow_ids: Dict[str, str] = {task_id: task.workflow_id or ""}
    if len(relevant_task_ids) > 1:
        wf_result = await db.execute(
            select(Task.id, Task.workflow_id)
            .where(Task.id.in_(relevant_task_ids))
        )
        for row in wf_result.all():
            task_workflow_ids[row[0]] = row[1] or ""

    all_iterations_data: List[Dict[str, Any]] = []

    for current_task_id in relevant_task_ids:
        # Find all child workflows for this current_task_id
        # They are named: agent-step-{task_id}-iter-{N}
        # Also check continuation workflows: task-workflow-{task_id}-cont-{N}
        workflow_ids_to_check = []

        # Primary workflow (standalone tasks)
        primary_wf_id = f"task-workflow-{current_task_id}"
        workflow_ids_to_check.append(primary_wf_id)

        # DAG task workflow (agent-task-{dag_id}-{node_id})
        actual_wf_id = task_workflow_ids.get(current_task_id, "")
        if actual_wf_id and actual_wf_id != primary_wf_id:
            workflow_ids_to_check.append(actual_wf_id)

        # Find continuations (cont-1, cont-2, etc.)
        for cont_num in range(1, 20):
            cont_wf_id = f"task-workflow-{current_task_id}-cont-{cont_num}"
            workflow_ids_to_check.append(cont_wf_id)

        # For each parent workflow, find the child AgentStepWorkflow IDs
        child_workflows: List[Dict[str, Any]] = []

        for parent_wf_id in workflow_ids_to_check:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        f"{temporal_http}/api/v1/namespaces/{namespace}/workflows/{parent_wf_id}/history",
                        params={"maximumPageSize": 500},
                    )
                    if resp.status_code != 200:
                        continue
                    data = resp.json()

                for event in data.get("history", {}).get("events", []) or data.get("events", []):
                    event_type = event.get("eventType", "")
                    if event_type == "EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED":
                        attrs = event.get("startChildWorkflowExecutionInitiatedEventAttributes", {})
                        child_wf_id = attrs.get("workflowId", "")
                        child_wf_type = attrs.get("workflowType", {}).get("name", "")
                        if child_wf_type == "AgentStepWorkflow" and child_wf_id:
                            # Extract iteration from input payloads
                            input_payloads = _decode_temporal_payloads(attrs.get("input", {}))
                            iteration = input_payloads[1] if len(input_payloads) > 1 else 0
                            child_workflows.append({
                                "workflow_id": child_wf_id,
                                "iteration": iteration,
                                "parent": parent_wf_id,
                                "task_id": current_task_id,
                                "created_at": event.get("eventTime", ""),
                            })
            except Exception as e:
                logger.debug(f"Could not fetch history for {parent_wf_id}: {e}")
                continue

        # Now fetch turns from each child workflow
        for child in child_workflows:
            raw_events = await _fetch_child_workflow_turns(child["workflow_id"])

            turns = []
            container_info = {}
            for ev in raw_events:
                if ev.get("activity_type") == "start_agent_container":
                    raw_ci = ev.get("result") or {}
                    if raw_ci:
                        container_info = {
                            "container_id": raw_ci.get("container_id", ""),
                            "image": raw_ci.get("image") or raw_ci.get("agent_image", ""),
                            "status": raw_ci.get("status", "completed"),
                            "sandbox_mode": raw_ci.get("sandbox_mode", ""),
                            "workspace_dir": raw_ci.get("workspace_dir", ""),
                        }
                elif ev.get("activity_type") == "collect_agent_result":
                    if container_info:
                        container_info["status"] = "completed"
                elif ev.get("data"):
                    turns.append(ev)

            all_iterations_data.append({
                "iteration": child["iteration"],
                "workflow_id": child["workflow_id"],
                "parent_workflow": child["parent"],
                "task_id": child["task_id"],
                "container": container_info,
                "turns": turns,
                "turn_count": len(turns),
                "created_at": child.get("created_at", ""),
            })

    # Sort all iterations by creation time, then by iteration number
    all_iterations_data.sort(key=lambda x: (x.get("created_at", ""), x["iteration"]))

    # Compute totals across all aggregated audit turns
    total_turns = sum(it["turn_count"] for it in all_iterations_data)
    total_input_tokens = 0
    total_output_tokens = 0
    for it in all_iterations_data:
        for t in it["turns"]:
            usage = t.get("data", {}).get("response", {}).get("usage", {})
            total_input_tokens += usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
            total_output_tokens += usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)

    if not all_iterations_data:
        return None

    return {
        "iterations": all_iterations_data,
        "total_iterations": len(all_iterations_data),
        "total_turns": total_turns,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
    }


async def _fetch_child_workflow_turns(workflow_id: str, run_id: str = "") -> List[Dict[str, Any]]:
    """Fetch record_agent_turn activity results from a Temporal child workflow.

    Each AgentStepWorkflow child invokes record_agent_turn activities.
    We walk the event history and extract the input payloads (which contain
    the full turn data: provider, tokens, tool_calls, etc.) and the
    output payloads.
    """
    import httpx
    from config import settings

    temporal_http = os.getenv("TEMPORAL_HTTP_URL", "http://temporal-ui:8080")
    namespace = "default"

    # Build URL — if we have a run_id, include it
    url = f"{temporal_http}/api/v1/namespaces/{namespace}/workflows/{workflow_id}/history"
    params = {"maximumPageSize": 500}

    turns: List[Dict[str, Any]] = []
    # Temporary maps: scheduled_event_id → activity_type, started_event_id → scheduled_id
    scheduled_map: Dict[int, Dict] = {}

    try:
        next_token = ""
        while True:
            req_params = {**params}
            if next_token:
                req_params["nextPageToken"] = next_token

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, params=req_params)
                if resp.status_code != 200:
                    logger.warning(f"Temporal history API returned {resp.status_code} for {workflow_id}")
                    break
                data = resp.json()

            for event in data.get("history", {}).get("events", []) or data.get("events", []):
                event_type = event.get("eventType", "")
                attrs = None

                # Track scheduled activities (name + input)
                if event_type == "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED":
                    attrs = event.get("activityTaskScheduledEventAttributes", {})
                    activity_type = attrs.get("activityType", {}).get("name", "")
                    event_id = int(event.get("eventId", 0))
                    scheduled_map[event_id] = {
                        "type": activity_type,
                        "input": attrs.get("input", {}),
                    }

                # Track completed activities (output)
                elif event_type == "EVENT_TYPE_ACTIVITY_TASK_COMPLETED":
                    attrs = event.get("activityTaskCompletedEventAttributes", {})
                    scheduled_id = int(attrs.get("scheduledEventId", 0))
                    sched = scheduled_map.get(scheduled_id, {})

                    if sched.get("type") in ("record_agent_turn",):
                        # Extract input payloads
                        turn_data = _decode_temporal_payloads(sched.get("input", {}))
                        result_data = _decode_temporal_payloads(attrs.get("result", {}))
                        
                        # turn_data is [task_id, iteration, turn_number, turn_payload]
                        turn_payload = turn_data[3] if len(turn_data) > 3 else {}
                        turn_result = result_data[0] if result_data else {}
                        
                        turns.append({
                            "activity_type": "record_agent_turn",
                            "data": turn_payload,
                            "result": turn_result,
                            "turn_number": turn_data[2] if len(turn_data) > 2 else 0,
                            "iteration": turn_data[1] if len(turn_data) > 1 else 0,
                        })

                    elif sched.get("type") in ("start_agent_container", "collect_agent_result", "poll_agent_turns"):
                        # Also include these as structural events
                        result_data = _decode_temporal_payloads(attrs.get("result", {}))
                        turns.append({
                            "activity_type": sched["type"],
                            "result": result_data[0] if result_data else {},
                        })

            next_token = data.get("nextPageToken", "")
            if not next_token:
                break

    except Exception as e:
        logging.getLogger(__name__).warning(f"Failed to fetch child workflow history for {workflow_id}: {e}")

    return turns


def _decode_temporal_payloads(payloads: Dict[str, Any]) -> List[Any]:
    """Decode Temporal protobuf payloads to Python objects."""
    import base64
    import json
    
    if not payloads or "payloads" not in payloads:
        return []
    
    results = []
    for p in payloads.get("payloads", []):
        data = p.get("data", "")
        if data:
            try:
                decoded = base64.b64decode(data).decode("utf-8")
                try:
                    results.append(json.loads(decoded))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    results.append(decoded)
            except Exception:
                results.append(None)
        else:
            results.append(None)
    return results
@router.get("/{task_id}/audit-logs/summary")
async def export_audit_summary(task_id: str, db: AsyncSession = Depends(get_db)):
    """Export a compact summary of task execution for skill learning/review.
    
    Provides deduplicated iteration data with:
    - Task metadata (id, description, model, status, iterations count)
    - Per-iteration: status, deliverables, condensed turns (tool calls + outputs)
    - Deduplicated repetitive content (e.g., repeated link listings)
    - Optional source code files attached for skill extraction
    """
    import json
    import os
    
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    # Get all iterations from TaskOutput
    result = await db.execute(
        select(TaskOutput).where(TaskOutput.task_id == task_id).order_by(TaskOutput.iteration)
    )
    outputs = list(result.scalars().all())

    # Build iteration summaries with deduplication
    iterations = []
    seen_outputs = set()  # For deduplication
    
    for out in outputs:
        # Parse raw_result for turn data if available
        turns = []
        deliverables = out.deliverables or {}
        
        if out.raw_result and isinstance(out.raw_result, dict):
            agent_logs = out.raw_result.get("agent_logs", "")
            # Extract turn data from agent_logs
            turns = _extract_turns_from_logs(agent_logs)
            
            # Deduplicate repetitive outputs (like repeated link listings)
            deduped_turns = _deduplicate_turns(turns)
            turns = deduped_turns

        iteration_summary = {
            "iteration": out.iteration,
            "status": "completed" if out.completed == "true" else "running" if out.completed == "false" else "unknown",
            "deliverables_produced": list(deliverables.keys()) if deliverables else [],
            "deliverables": deliverables,
            "turns": turns,
            "turn_count": len(turns),
            "error": out.error,
            "output": out.output,
        }
        iterations.append(iteration_summary)

    # Determine overall task status
    completed_iterations = sum(1 for i in iterations if i["status"] == "completed")
    failed_iterations = sum(1 for i in iterations if i.get("error"))
    
    if failed_iterations > 0:
        overall_status = "failed"
    elif completed_iterations == len(iterations) and len(iterations) > 0:
        overall_status = "completed"
    else:
        overall_status = task.status.value if task.status else "unknown"

    return {
        "task_metadata": {
            "task_id": task_id,
            "task_name": task.name,
            "description": task.description,
            "model": task.llm_model,
            "status": overall_status,
            "base_image": task.current_image,
            "dag_id": task.dag_id,
            "node_id": task.node_id,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "total_iterations": len(iterations),
            "completed_iterations": completed_iterations,
            "failed_iterations": failed_iterations,
        },
        "iterations": iterations,
    }


def _extract_turns_from_logs(agent_logs: str) -> List[Dict[str, Any]]:
    """Extract structured turn data from agent_logs text."""
    import re
    
    turns = []
    
    # Pattern to match turn sections in agent logs
    # Matches: ── Turn N/30 ── followed by tool calls and results
    turn_pattern = r'── Turn (\d+)/\d+ ──\n(.*?)(?=\n── Turn \d+/\d+ ──|\n===|$)'
    
    for match in re.finditer(turn_pattern, agent_logs, re.DOTALL):
        turn_num = int(match.group(1))
        turn_content = match.group(2)
        
        # Extract tool calls
        tool_calls = []
        # Pattern: 🔧 Tool: name({args})
        tool_matches = re.findall(r'🔧 Tool: (\w+)\((\{.*?\})\)', turn_content)
        for tool_name, args_str in tool_matches:
            try:
                import json
                args = json.loads(args_str)
                tool_calls.append({"tool": tool_name, "arguments": args})
            except:
                tool_calls.append({"tool": tool_name, "arguments": args_str[:200]})
        
        # Extract output/result
        output = ""
        # Pattern: 📤 Result: <text>
        result_match = re.search(r'📤 Result: (.*?)(?=\n\n|\n──|$)', turn_content, re.DOTALL)
        if result_match:
            output = result_match.group(1).strip()
        
        # Also check for assistant message
        assistant_match = re.search(r'💬 Assistant: (.*?)(?=\n\n|\n──|$)', turn_content, re.DOTALL)
        if assistant_match:
            output = assistant_match.group(1).strip()
        
        turns.append({
            "turn": turn_num,
            "command_issued": tool_calls,
            "output": output[:2000] if output else "",  # Limit output size
        })
    
    return turns


def _deduplicate_turns(turns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove duplicate/repetitive turn outputs (e.g., repeated link listings)."""
    if not turns:
        return []
    
    deduped = []
    seen_outputs = set()
    
    for turn in turns:
        output = turn.get("output", "")
        # Normalize output for comparison: remove common prefixes, whitespace
        normalized = _normalize_output_for_dedup(output)
        # Create a signature for the output (first 200 chars)
        sig = normalized[:200].strip()
        
        if sig and sig in seen_outputs:
            # Skip duplicate, but keep turn number for reference
            continue
        elif sig:
            seen_outputs.add(sig)
        
        deduped.append(turn)
    
    return deduped


def _normalize_output_for_dedup(output: str) -> str:
    """Normalize output for deduplication comparison."""
    import re
    if not output:
        return ""
    # Remove common repetitive prefixes
    prefixes_to_remove = [
        "Total links on homepage: ",
        "Total links on homepage: 325",
        "Total links on homepage:",
        "Sitemaps listed: ",
    ]
    normalized = output
    for prefix in prefixes_to_remove:
        normalized = normalized.replace(prefix, "")
    # Collapse whitespace
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized
