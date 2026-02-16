"""
Policy management router
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from database import get_db
from models import Policy, Task
from schemas import PolicyResponse, PolicyCreate

router = APIRouter()


@router.get("", response_model=List[PolicyResponse])
async def list_policies(
    task_id: str = None,
    db: AsyncSession = Depends(get_db)
):
    """List policies"""
    query = select(Policy)
    
    if task_id:
        query = query.where(Policy.task_id == task_id)
    
    query = query.order_by(Policy.created_at.desc())
    
    result = await db.execute(query)
    policies = result.scalars().all()
    
    return policies


@router.get("/{policy_id}", response_model=PolicyResponse)
async def get_policy(
    policy_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get policy details"""
    result = await db.execute(
        select(Policy).where(Policy.id == policy_id)
    )
    policy = result.scalar_one_or_none()
    
    if not policy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy {policy_id} not found"
        )
    
    return policy


@router.post("", response_model=PolicyResponse)
async def create_policy(
    policy_data: PolicyCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new policy version"""
    
    # Verify task exists
    result = await db.execute(
        select(Task).where(Task.id == policy_data.task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {policy_data.task_id} not found"
        )
    
    # Get current version
    result = await db.execute(
        select(Policy)
        .where(Policy.task_id == policy_data.task_id)
        .order_by(Policy.version.desc())
        .limit(1)
    )
    latest_policy = result.scalar_one_or_none()
    
    new_version = (latest_policy.version + 1) if latest_policy else 1
    
    # Create new policy
    policy = Policy(
        task_id=policy_data.task_id,
        version=new_version,
        tools_allowed=policy_data.rules.tools_allowed,
        network_rules=policy_data.rules.network_rules,
        filesystem_rules=policy_data.rules.filesystem_rules,
        database_rules=policy_data.rules.database_rules,
        resource_limits=policy_data.rules.resource_limits
    )
    
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    
    return policy


@router.get("/task/{task_id}/current", response_model=PolicyResponse)
async def get_current_policy(
    task_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get current policy for a task"""
    result = await db.execute(
        select(Task).where(Task.id == task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found"
        )
    
    if not task.current_policy_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No policy set for task {task_id}"
        )
    
    result = await db.execute(
        select(Policy).where(Policy.id == task.current_policy_id)
    )
    policy = result.scalar_one_or_none()
    
    return policy
