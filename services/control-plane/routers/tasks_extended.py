"""
Extended task endpoints for execution tracking
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from pathlib import Path
import os
from typing import List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pathlib import Path
from typing import List, Dict, Any, Optional
import os
import json
import logging

from database import get_db
from models import Task, CapabilityRequest, TaskStatus, TaskOutput, TaskMessage
from schemas import TaskOutputCreate, TaskOutputResponse, TaskMessageCreate, TaskMessageResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tasks", tags=["tasks-extended"])

AGENT_IMAGES_DIR = Path(os.getenv("AGENT_IMAGES_DIR", "/app/agent-images"))


@router.get("/{task_id}/dockerfiles")
async def get_task_dockerfiles(task_id: str) -> Dict[str, Any]:
    """Get all Dockerfiles for a task"""
    task_dir = AGENT_IMAGES_DIR / task_id
    
    if not task_dir.exists():
        return {
            "task_id": task_id,
            "dockerfiles": [],
            "message": "No custom images built yet (using base image)"
        }
    
    dockerfiles = []
    
    # Find all Dockerfile versions
    for dockerfile in sorted(task_dir.glob("Dockerfile*")):
        try:
            content = dockerfile.read_text()
            
            # Extract version from filename
            if dockerfile.name == "Dockerfile":
                version = "latest"
            else:
                version = dockerfile.name.replace("Dockerfile.", "")
            
            dockerfiles.append({
                "version": version,
                "filename": dockerfile.name,
                "content": content,
                "size": len(content),
                "lines": len(content.splitlines())
            })
        except Exception as e:
            logger.error(f"Error reading {dockerfile}: {e}")
    
    return {
        "task_id": task_id,
        "directory": str(task_dir),
        "dockerfiles": dockerfiles,
        "count": len(dockerfiles)
    }


@router.get("/{task_id}/execution-timeline")
async def get_execution_timeline(
    task_id: str,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Get complete execution timeline for a task"""
    
    # Get task
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Get all capability requests for this task
    requests_result = await db.execute(
        select(CapabilityRequest)
        .where(CapabilityRequest.task_id == task_id)
        .order_by(CapabilityRequest.requested_at)
    )
    capability_requests = requests_result.scalars().all()
    
    # Build timeline
    timeline = []
    
    # Task creation
    timeline.append({
        "timestamp": task.created_at.isoformat(),
        "event": "task_created",
        "description": "Task created",
        "data": {
            "task_id": task_id,
            "description": task.description,
            "status": task.status
        }
    })
    
    # Task started
    if task.started_at:
        timeline.append({
            "timestamp": task.started_at.isoformat(),
            "event": "task_started",
            "description": "Task execution started",
            "data": {
                "workflow_id": task.workflow_id,
                "image": "openclaw-agent:base"
            }
        })
    
    # Capability requests
    for req in capability_requests:
        timeline.append({
            "timestamp": req.requested_at.isoformat(),
            "event": "capability_requested",
            "description": f"Requested {req.capability_type}: {req.resource_name}",
            "data": {
                "request_id": req.id,
                "type": req.capability_type,
                "resource": req.resource_name,
                "justification": req.justification,
                "status": req.status
            }
        })
        
        if req.decided_at:
            timeline.append({
                "timestamp": req.decided_at.isoformat(),
                "event": "capability_decided",
                "description": f"Capability {req.status}: {req.resource_name}",
                "data": {
                    "request_id": req.id,
                    "decision": req.status,
                    "decided_by": req.decided_by,
                    "notes": req.decision_notes
                }
            })
    
    # Task completion
    if task.completed_at:
        timeline.append({
            "timestamp": task.completed_at.isoformat(),
            "event": "task_completed",
            "description": "Task execution completed",
            "data": {
                "status": task.status
            }
        })
    
    # Get Dockerfiles info
    dockerfiles_info = await get_task_dockerfiles(task_id)
    
    return {
        "task_id": task_id,
        "task": {
            "id": task.id,
            "description": task.description,
            "status": task.status,
            "created_at": task.created_at.isoformat(),
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "workflow_id": task.workflow_id
        },
        "timeline": sorted(timeline, key=lambda x: x["timestamp"]),
        "capability_requests": [
            {
                "id": req.id,
                "type": req.capability_type,
                "resource": req.resource_name,
                "justification": req.justification,
                "status": req.status,
                "requested_at": req.requested_at.isoformat(),
                "decided_at": req.decided_at.isoformat() if req.decided_at else None,
                "decided_by": req.decided_by
            }
            for req in capability_requests
        ],
        "dockerfiles": dockerfiles_info["dockerfiles"],
        "image_versions": len(dockerfiles_info["dockerfiles"]),
        "current_image": dockerfiles_info["dockerfiles"][-1]["version"] if dockerfiles_info["dockerfiles"] else "base"
    }


# =========================================================================
# Task Outputs — iteration results from agent execution
# =========================================================================

@router.post("/{task_id}/outputs")
async def create_task_output(
    task_id: str,
    output_data: TaskOutputCreate,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Store an iteration output (called by the worker after each agent step)"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Extract deliverables from raw_result if not provided directly
    deliverables = output_data.deliverables
    if not deliverables and output_data.raw_result:
        deliverables = output_data.raw_result.get("deliverables")

    output = TaskOutput(
        task_id=task_id,
        iteration=output_data.iteration,
        completed=output_data.completed,
        capability_requested=output_data.capability_requested,
        agent_logs=output_data.agent_logs,
        output=output_data.output,
        error=output_data.error,
        llm_response_preview=output_data.llm_response_preview,
        model_used=output_data.model_used,
        image_used=output_data.image_used,
        duration_ms=output_data.duration_ms,
        deliverables=deliverables,
        raw_result=output_data.raw_result,
    )
    db.add(output)

    # Also store the agent output as a conversation message for the chat view
    agent_text = ""
    if output_data.raw_result:
        # Extract the LLM's actual text response from OpenClaw payloads
        payloads = output_data.raw_result.get("output", "")
        if isinstance(payloads, str) and payloads:
            # Try to extract actual response from openclaw JSON output
            import json as _json
            try:
                parsed = _json.loads(payloads) if payloads.strip().startswith("{") else None
                if parsed and parsed.get("payloads"):
                    texts = [p.get("text", "") for p in parsed["payloads"] if p.get("text")]
                    agent_text = "\n".join(texts)
            except Exception:
                pass
        if not agent_text:
            agent_text = output_data.llm_response_preview or ""
    if not agent_text and output_data.error:
        agent_text = f"⚠️ Error: {output_data.error}"

    # Build deliverables summary for the conversation
    deliverable_summary = ""
    if deliverables and isinstance(deliverables, dict):
        file_list = "\n".join(f"  📄 {fname}" for fname in deliverables.keys())
        deliverable_summary = f"\n\n📦 **Deliverables created:**\n{file_list}"

    if not agent_text:
        if deliverable_summary:
            agent_text = f"Iteration {output_data.iteration} completed.{deliverable_summary}"
        else:
            agent_text = f"Iteration {output_data.iteration} completed."
    elif deliverable_summary:
        agent_text += deliverable_summary

    if agent_text:
        msg = TaskMessage(
            task_id=task_id,
            role="agent",
            content=agent_text,
            msg_metadata={
                "iteration": output_data.iteration,
                "model": output_data.model_used,
                "completed": output_data.completed,
            }
        )
        db.add(msg)

    await db.commit()
    await db.refresh(output)
    return {"id": output.id, "status": "stored"}


@router.get("/{task_id}/outputs")
async def get_task_outputs(
    task_id: str,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Get all iteration outputs for a task"""
    result = await db.execute(
        select(TaskOutput)
        .where(TaskOutput.task_id == task_id)
        .order_by(TaskOutput.iteration)
    )
    outputs = result.scalars().all()

    return {
        "task_id": task_id,
        "count": len(outputs),
        "outputs": [
            {
                "id": o.id,
                "iteration": o.iteration,
                "completed": o.completed,
                "capability_requested": o.capability_requested,
                "agent_logs": o.agent_logs,
                "output": o.output,
                "error": o.error,
                "llm_response_preview": o.llm_response_preview,
                "model_used": o.model_used,
                "image_used": o.image_used,
                "duration_ms": o.duration_ms,
                "deliverables": o.deliverables,
                "raw_result": o.raw_result,
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }
            for o in outputs
        ],
    }


# =========================================================================
# Task Messages — conversation between agent and user
# =========================================================================

@router.get("/{task_id}/messages")
async def get_task_messages(
    task_id: str,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Get all conversation messages for a task"""
    result = await db.execute(
        select(TaskMessage)
        .where(TaskMessage.task_id == task_id)
        .order_by(TaskMessage.created_at)
    )
    messages = result.scalars().all()

    return {
        "task_id": task_id,
        "count": len(messages),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "metadata": m.msg_metadata,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
    }


@router.post("/{task_id}/messages")
async def post_task_message(
    task_id: str,
    msg: TaskMessageCreate,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Post a user message to a task conversation.
    
    The message is stored and will be picked up by the next agent iteration.
    """
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    message = TaskMessage(
        task_id=task_id,
        role=msg.role or "user",
        content=msg.content,
        msg_metadata=msg.metadata,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)

    return {
        "id": message.id,
        "task_id": task_id,
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


@router.get("/{task_id}/current-state")
async def get_task_current_state(
    task_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get current execution state of a task"""
    result = await db.execute(
        select(Task).where(Task.id == task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Get current Dockerfile
    dockerfiles = await get_task_dockerfiles(task_id)
    current_dockerfile = dockerfiles["dockerfiles"][-1] if dockerfiles["dockerfiles"] else None
    current_image = f"localhost:5000/openclaw-agent:task-{task_id}-v{len(dockerfiles['dockerfiles'])}" if dockerfiles["dockerfiles"] else "localhost:5000/openclaw-agent:base"
    
    # Get latest capability requests
    cap_result = await db.execute(
        select(CapabilityRequest)
        .where(CapabilityRequest.task_id == task_id)
        .order_by(CapabilityRequest.requested_at.desc())
        .limit(5)
    )
    latest_requests = cap_result.scalars().all()
    
    return {
        "task_id": task_id,
        "status": task.status.value,
        "current_iteration": 0,  # TODO: Track in database
        "max_iterations": 15,  # TODO: Get from task config
        "current_image": current_image,
        "current_image_version": len(dockerfiles["dockerfiles"]),
        "current_dockerfile": current_dockerfile,
        "pending_approvals": len([r for r in latest_requests if r.status == "pending"]),
        "total_capabilities": len(latest_requests),
        "workflow_running": task.status == TaskStatus.RUNNING,
        "last_activity": task.updated_at.isoformat() if task.updated_at else task.created_at.isoformat()
    }


# ---------------------------------------------------------------------------
# Audit turns — fetches per-turn data from Temporal child workflows
# ---------------------------------------------------------------------------

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
                            "turn_number": turn_data[2] if len(turn_data) > 2 else 0,
                            "iteration": turn_data[1] if len(turn_data) > 1 else 0,
                            "data": turn_payload,
                            "result": turn_result,
                        })

                    elif sched.get("type") in ("start_agent_container", "collect_agent_result", "poll_agent_turns"):
                        # Also include these as structural events
                        result_data = _decode_temporal_payloads(attrs.get("result", {}))
                        turns.append({
                            "turn_number": 0,
                            "iteration": 0,
                            "activity_type": sched["type"],
                            "result": result_data[0] if result_data else {},
                        })

            next_token = data.get("nextPageToken", "")
            if not next_token:
                break

    except Exception as e:
        logger.warning(f"Failed to fetch child workflow history for {workflow_id}: {e}")

    return turns


def _decode_temporal_payloads(payload_container: Dict) -> List[Any]:
    """Decode Temporal payloads from the HTTP API format.

    Temporal's HTTP API returns payloads as:
    {"payloads": [{"metadata": {"encoding": "..."}, "data": "<base64>"}]}
    """
    import base64

    payloads = payload_container.get("payloads", [])
    results = []
    for p in payloads:
        data_b64 = p.get("data", "")
        if not data_b64:
            results.append(None)
            continue
        try:
            raw = base64.b64decode(data_b64)
            # Try JSON parse first
            try:
                results.append(json.loads(raw))
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Might be a plain string or number
                results.append(raw.decode("utf-8", errors="replace"))
        except Exception:
            results.append(None)
    return results


@router.get("/{task_id}/audit-turns")
async def get_audit_turns(
    task_id: str,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Get per-turn audit data for a task.

    Queries Temporal for all AgentStepWorkflow child workflows associated
    with this task and extracts the record_agent_turn activities from each.
    Returns a structured list of iterations with their individual LLM turns.
    """
    import httpx

    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    temporal_http = os.getenv("TEMPORAL_HTTP_URL", "http://temporal-ui:8080")
    namespace = "default"

    # Find all child workflows for this task
    # They are named: agent-step-{task_id}-iter-{N}
    # Also check continuation workflows: task-workflow-{task_id}-cont-{N}
    workflow_ids_to_check = []

    # Primary workflow
    primary_wf_id = f"task-workflow-{task_id}"
    workflow_ids_to_check.append(primary_wf_id)

    # Find continuations (cont-1, cont-2, etc.)
    for cont_num in range(1, 20):
        cont_wf_id = f"task-workflow-{task_id}-cont-{cont_num}"
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
                        })
        except Exception as e:
            logger.debug(f"Could not fetch history for {parent_wf_id}: {e}")
            continue

    # Now fetch turns from each child workflow
    iterations_data: List[Dict[str, Any]] = []

    for child in child_workflows:
        raw_events = await _fetch_child_workflow_turns(child["workflow_id"])

        # Separate turn activities from structural activities
        turns = []
        container_info = {}
        for ev in raw_events:
            if ev.get("activity_type") == "start_agent_container":
                container_info = ev.get("result", {})
            elif ev.get("activity_type") == "collect_agent_result":
                # Could enrich with final result data
                pass
            elif ev.get("data"):
                turns.append(ev)

        iterations_data.append({
            "iteration": child["iteration"],
            "workflow_id": child["workflow_id"],
            "parent_workflow": child["parent"],
            "container": container_info,
            "turns": turns,
            "turn_count": len(turns),
        })

    # Sort by iteration
    iterations_data.sort(key=lambda x: x["iteration"])

    # Compute totals
    total_turns = sum(it["turn_count"] for it in iterations_data)
    total_input_tokens = 0
    total_output_tokens = 0
    for it in iterations_data:
        for t in it["turns"]:
            usage = t.get("data", {}).get("response", {}).get("usage", {})
            total_input_tokens += usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
            total_output_tokens += usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)

    return {
        "task_id": task_id,
        "iterations": iterations_data,
        "total_iterations": len(iterations_data),
        "total_turns": total_turns,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
    }
