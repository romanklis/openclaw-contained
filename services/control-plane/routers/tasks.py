"""
Task management router
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import uuid
from datetime import datetime

from database import get_db
from models import Task, TaskStatus, Policy
from schemas import TaskCreate, TaskResponse, TaskDetail
from temporal_client import start_task_workflow

router = APIRouter()


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    task_data: TaskCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new task"""
    
    # Generate task ID
    task_id = f"task-{str(uuid.uuid4())[:8]}"
    workspace_id = f"workspace-{str(uuid.uuid4())[:8]}"
    
    # Resolve aliased fields
    description = task_data.effective_description
    llm_model = task_data.effective_model

    # Create task first (without policy reference)
    task = Task(
        id=task_id,
        name=task_data.name,
        description=description,
        workspace_id=workspace_id,
        status=TaskStatus.CREATED,
        current_policy_id=None,
        llm_model=llm_model,
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

    # Auto-start the workflow
    try:
        workflow_id = await start_task_workflow(task_id, llm_model)
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
