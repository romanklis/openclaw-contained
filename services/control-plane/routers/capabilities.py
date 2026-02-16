"""
Capability management router
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from datetime import datetime
import uuid
import logging
import os

from temporalio.client import Client

logger = logging.getLogger(__name__)

from database import get_db
from models import CapabilityRequest, Task, RequestStatus
from schemas import (
    CapabilityRequestCreate,
    CapabilityRequestResponse,
    CapabilityDecision
)

router = APIRouter()

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "temporal:7233")


async def get_temporal_client():
    """Get Temporal client connection"""
    try:
        return await Client.connect(TEMPORAL_HOST)
    except Exception as e:
        logger.error(f"Failed to connect to Temporal: {e}")
        return None


@router.get("/requests", response_model=List[CapabilityRequestResponse])
async def list_capability_requests(
    task_id: str = None,
    status_filter: RequestStatus = None,
    db: AsyncSession = Depends(get_db)
):
    """List capability requests"""
    query = select(CapabilityRequest)
    
    if task_id:
        query = query.where(CapabilityRequest.task_id == task_id)
    
    if status_filter:
        query = query.where(CapabilityRequest.status == status_filter)
    
    query = query.order_by(CapabilityRequest.requested_at.desc())
    
    result = await db.execute(query)
    requests = result.scalars().all()
    
    return requests


@router.post("/requests", response_model=CapabilityRequestResponse)
async def create_capability_request(
    request_data: CapabilityRequestCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new capability request"""
    
    # Verify task exists
    result = await db.execute(
        select(Task).where(Task.id == request_data.task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {request_data.task_id} not found"
        )
    
    # Create request
    capability_request = CapabilityRequest(
        task_id=request_data.task_id,
        capability_type=request_data.capability_type,
        resource_name=request_data.resource_name,
        justification=request_data.justification,
        details=request_data.details,
        status=RequestStatus.PENDING
    )
    
    db.add(capability_request)
    await db.commit()
    await db.refresh(capability_request)
    
    # TODO: Trigger approval workflow notifications
    
    return capability_request


@router.post("/requests/{request_id}/review", response_model=CapabilityRequestResponse)
async def review_capability_request(
    request_id: int,
    decision: CapabilityDecision,
    db: AsyncSession = Depends(get_db)
):
    """Review a capability request with approve/deny/alternative"""
    
    # Get request
    result = await db.execute(
        select(CapabilityRequest).where(CapabilityRequest.id == request_id)
    )
    capability_request = result.scalar_one_or_none()
    
    if not capability_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {request_id} not found"
        )
    
    if capability_request.status != RequestStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Request already processed with status: {capability_request.status}"
        )
    
    # Update request based on decision
    if decision.decision == "approved":
        capability_request.status = RequestStatus.APPROVED
    elif decision.decision == "denied":
        capability_request.status = RequestStatus.DENIED
    elif decision.decision == "alternative_suggested":
        capability_request.status = RequestStatus.PENDING
        capability_request.alternative_suggestion = decision.alternative_suggestion
    
    capability_request.decision_notes = decision.comment
    capability_request.reviewed_at = datetime.utcnow()
    capability_request.reviewed_by = decision.reviewed_by or "system"
    
    await db.commit()
    await db.refresh(capability_request)
    
    # Signal Temporal workflow
    try:
        # Get task to find workflow ID
        result = await db.execute(
            select(Task).where(Task.id == capability_request.task_id)
        )
        task = result.scalar_one_or_none()
        
        if task and task.workflow_id:
            temporal_client = await get_temporal_client()
            if temporal_client:
                # Get workflow handle
                workflow_handle = temporal_client.get_workflow_handle(task.workflow_id)
                
                # Send approval signal
                approved = decision.decision == "approved"
                await workflow_handle.signal("approve_capability", approved)
                
                logger.info(f"Sent signal to workflow {task.workflow_id}: approved={approved}")
            else:
                logger.error("Could not connect to Temporal to send signal")
        else:
            logger.warning(f"No workflow found for task {capability_request.task_id}")
    except Exception as e:
        logger.error(f"Error signaling Temporal workflow: {e}", exc_info=True)
    
    return capability_request


@router.post("/approve", response_model=CapabilityRequestResponse)
async def approve_capability(
    decision: CapabilityDecision,
    db: AsyncSession = Depends(get_db)
):
    """Approve or deny a capability request (legacy endpoint)"""
    
    # Get request
    result = await db.execute(
        select(CapabilityRequest).where(CapabilityRequest.id == decision.request_id)
    )
    capability_request = result.scalar_one_or_none()
    
    if not capability_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {decision.request_id} not found"
        )
    
    if capability_request.status != RequestStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Request already processed with status: {capability_request.status}"
        )
    
    # Update request
    capability_request.status = RequestStatus.APPROVED if decision.approved else RequestStatus.DENIED
    capability_request.decision_notes = decision.notes
    capability_request.reviewed_at = datetime.utcnow()
    capability_request.reviewed_by = "system"  # TODO: Get from auth
    
    await db.commit()
    await db.refresh(capability_request)
    
    # TODO: If approved, trigger image rebuild
    if decision.approved:
        # Signal Temporal workflow to resume with new capability
        pass
    
    return capability_request


@router.get("/requests/{request_id}", response_model=CapabilityRequestResponse)
async def get_capability_request(
    request_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get capability request details"""
    result = await db.execute(
        select(CapabilityRequest).where(CapabilityRequest.id == request_id)
    )
    capability_request = result.scalar_one_or_none()
    
    if not capability_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {request_id} not found"
        )
    
    return capability_request
