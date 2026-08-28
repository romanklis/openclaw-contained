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
    dag_id: str = None,
    status_filter: RequestStatus = None,
    db: AsyncSession = Depends(get_db)
):
    """List capability requests.

    - task_id: filter to a single task
    - dag_id: include requests from ALL tasks belonging to this DAG
    - status_filter: filter by request status
    """
    query = select(CapabilityRequest)

    if dag_id:
        # Find all task IDs in this DAG
        dag_tasks = await db.execute(
            select(Task.id).where(
                Task.dag_id == dag_id,
            )
        )
        dag_task_ids = [row[0] for row in dag_tasks.all()]
        if dag_task_ids:
            query = query.where(CapabilityRequest.task_id.in_(dag_task_ids))
        else:
            return []
    elif task_id:
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
    
    # Signal Temporal workflow(s)
    approved = decision.decision == "approved"

    # Resolve the task + DAG context to determine candidate workflows.
    # Capability signals must reach the task's live AgentTaskWorkflow; during a
    # DAG corrective retry that runs under "agent-task-{dag}-{node}-assessment-retry",
    # which is not the persisted task.workflow_id. Signal ALL candidates so the
    # live workflow always receives the decision.
    result = await db.execute(
        select(Task).where(Task.id == capability_request.task_id)
    )
    task = result.scalar_one_or_none()

    candidates: List[str] = []
    if task:
        if task.workflow_id:
            candidates.append(task.workflow_id)
        if task.dag_id and task.node_id:
            candidates.append(f"agent-task-{task.dag_id}-{task.node_id}")
            candidates.append(f"agent-task-{task.dag_id}-{task.node_id}-assessment-retry")

    temporal_client = None
    try:
        temporal_client = await get_temporal_client()
    except Exception as exc:
        logger.error(f"Could not connect to Temporal to send signal: {exc}")

    if temporal_client and candidates:
        for wf_id in dict.fromkeys(candidates):  # de-dupe, preserve order
            try:
                handle = temporal_client.get_workflow_handle(wf_id)
                await handle.signal("approve_capability", approved)
                logger.info(f"Sent capability signal to workflow {wf_id}: approved={approved}")
            except Exception as sig_err:
                logger.warning(f"Could not signal workflow {wf_id}: {sig_err}")
    elif not task:
        logger.warning(f"No task found for capability request {capability_request.id}")
    else:
        logger.warning(f"No workflow candidates for task {capability_request.task_id}")

    # If this task belongs to a DAG, also signal ALL sibling node workflows so
    # they ALL rebuild their images with the newly approved capability.
    if approved and task and task.dag_id and temporal_client:
        siblings_result = await db.execute(
            select(Task).where(
                Task.dag_id == task.dag_id,
                Task.id != task.id,
            )
        )
        siblings = siblings_result.scalars().all()
        for sibling in siblings:
            try:
                # Create a matching capability request for each sibling
                # so their AgentTaskWorkflow can find it
                sib_cap = CapabilityRequest(
                    task_id=sibling.id,
                    capability_type=capability_request.capability_type,
                    resource_name=capability_request.resource_name,
                    justification=f"Propagated from sibling {task.id}: {capability_request.justification}",
                    details=capability_request.details,
                    status=RequestStatus.APPROVED,
                    reviewed_at=datetime.utcnow(),
                    reviewed_by="system (task-force-propagation)",
                    decision_notes=f"Auto-approved: same Task Force as {task.id}",
                )
                db.add(sib_cap)

                if sibling.workflow_id:
                    sib_handle = temporal_client.get_workflow_handle(sibling.workflow_id)
                    await sib_handle.signal("approve_capability", True)
                    logger.info(
                        f"Propagated cap approval to sibling {sibling.id} "
                        f"(workflow {sibling.workflow_id})"
                    )
            except Exception as sib_err:
                logger.warning(
                    f"Could not propagate to sibling {sibling.id}: {sib_err}"
                )
        await db.commit()

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


@router.post("/requests/dismiss-pending")
async def dismiss_pending_capabilities(
    task_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Bulk-dismiss all pending capability requests for a task.

    Called by the temporal worker after a capability has been processed
    to prevent stale pending requests from interfering with subsequent
    container runs.
    """
    result = await db.execute(
        select(CapabilityRequest).where(
            CapabilityRequest.task_id == task_id,
            CapabilityRequest.status == RequestStatus.PENDING,
        )
    )
    pending = result.scalars().all()
    count = 0
    for cap in pending:
        cap.status = RequestStatus.APPROVED
        cap.decision_notes = "auto-dismissed after processing"
        cap.reviewed_at = datetime.utcnow()
        cap.reviewed_by = "system"
        count += 1
    await db.commit()
    logger.info(f"Dismissed {count} pending capability request(s) for {task_id}")
    return {"dismissed": count}


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
