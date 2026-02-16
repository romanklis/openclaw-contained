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
