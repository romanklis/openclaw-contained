"""
Deployment management router
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from datetime import datetime
import uuid
import logging
import os

logger = logging.getLogger(__name__)

from database import get_db
from models import Deployment, DeploymentStatus, Task
from schemas import (
    DeploymentRequestCreate,
    DeploymentResponse,
    DeploymentDecision,
)

router = APIRouter()

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "temporal:7233")


# ---- CRUD ----

@router.post("", response_model=DeploymentResponse, status_code=status.HTTP_201_CREATED)
async def create_deployment(
    data: DeploymentRequestCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a deployment record (called by the worker after agent requests deployment)."""
    # Verify task exists
    result = await db.execute(select(Task).where(Task.id == data.task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {data.task_id} not found")

    deployment = Deployment(
        id=f"deploy-{str(uuid.uuid4())[:8]}",
        name=data.name,
        task_id=data.task_id,
        entrypoint=data.entrypoint,
        port=data.port,
        status=DeploymentStatus.PENDING_APPROVAL,
    )
    db.add(deployment)
    await db.commit()
    await db.refresh(deployment)

    logger.info(f"Deployment created: {deployment.id} for task {data.task_id}")
    return deployment


@router.get("", response_model=List[DeploymentResponse])
async def list_deployments(
    task_id: Optional[str] = None,
    status_filter: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """List deployments, optionally filtered by task or status."""
    query = select(Deployment).order_by(Deployment.created_at.desc())
    if task_id:
        query = query.where(Deployment.task_id == task_id)
    if status_filter:
        query = query.where(Deployment.status == status_filter)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{deployment_id}", response_model=DeploymentResponse)
async def get_deployment(
    deployment_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get deployment details."""
    result = await db.execute(
        select(Deployment).where(Deployment.id == deployment_id)
    )
    deployment = result.scalar_one_or_none()
    if not deployment:
        raise HTTPException(status_code=404, detail=f"Deployment {deployment_id} not found")
    return deployment


@router.patch("/{deployment_id}", response_model=DeploymentResponse)
async def update_deployment(
    deployment_id: str,
    updates: dict,
    db: AsyncSession = Depends(get_db),
):
    """Patch deployment fields (used internally by worker / image-builder)."""
    result = await db.execute(
        select(Deployment).where(Deployment.id == deployment_id)
    )
    deployment = result.scalar_one_or_none()
    if not deployment:
        raise HTTPException(status_code=404, detail=f"Deployment {deployment_id} not found")

    allowed = {"image_tag", "status", "container_id", "host_port", "url", "error", "entrypoint", "port"}
    for key, value in updates.items():
        if key in allowed:
            if key == "status":
                value = DeploymentStatus(value)
            setattr(deployment, key, value)
            # Set timestamp fields
            if key == "status":
                if value == DeploymentStatus.BUILT:
                    deployment.built_at = datetime.utcnow()
                elif value == DeploymentStatus.RUNNING:
                    deployment.started_at = datetime.utcnow()
                elif value == DeploymentStatus.STOPPED:
                    deployment.stopped_at = datetime.utcnow()

    await db.commit()
    await db.refresh(deployment)
    return deployment


# ---- Approval ----

@router.post("/{deployment_id}/approve", response_model=DeploymentResponse)
async def approve_deployment(
    deployment_id: str,
    decision: DeploymentDecision,
    db: AsyncSession = Depends(get_db),
):
    """Approve or deny a deployment. If approved, triggers image build."""
    result = await db.execute(
        select(Deployment).where(Deployment.id == deployment_id)
    )
    deployment = result.scalar_one_or_none()
    if not deployment:
        raise HTTPException(status_code=404, detail=f"Deployment {deployment_id} not found")

    if deployment.status != DeploymentStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=400,
            detail=f"Deployment is not pending approval (status: {deployment.status})",
        )

    if not decision.approved:
        deployment.status = DeploymentStatus.FAILED
        deployment.error = decision.notes or "Deployment denied by user"
        await db.commit()
        await db.refresh(deployment)
        return deployment

    deployment.status = DeploymentStatus.APPROVED
    deployment.approved_at = datetime.utcnow()
    await db.commit()
    await db.refresh(deployment)

    # Trigger deployment image build via Temporal
    try:
        from temporalio.client import Client

        temporal_client = await Client.connect(TEMPORAL_HOST)
        # Start a one-shot workflow for building the deployment image
        workflow_id = f"deploy-build-{deployment_id}"
        await temporal_client.start_workflow(
            "DeploymentBuildWorkflow",
            deployment_id,
            id=workflow_id,
            task_queue="openclaw-tasks",
        )
        logger.info(f"Started deployment build workflow: {workflow_id}")
    except Exception as e:
        logger.error(f"Failed to start deployment build workflow: {e}")
        deployment.status = DeploymentStatus.FAILED
        deployment.error = f"Failed to start build: {e}"
        await db.commit()
        await db.refresh(deployment)

    return deployment


# ---- Start / Stop ----

@router.post("/{deployment_id}/start", response_model=DeploymentResponse)
async def start_deployment(
    deployment_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Start a built deployment (run its container)."""
    result = await db.execute(
        select(Deployment).where(Deployment.id == deployment_id)
    )
    deployment = result.scalar_one_or_none()
    if not deployment:
        raise HTTPException(status_code=404, detail=f"Deployment {deployment_id} not found")

    if deployment.status not in (DeploymentStatus.BUILT, DeploymentStatus.STOPPED):
        raise HTTPException(
            status_code=400,
            detail=f"Deployment must be built or stopped to start (status: {deployment.status})",
        )

    # Start via Temporal activity
    try:
        from temporalio.client import Client

        temporal_client = await Client.connect(TEMPORAL_HOST)
        workflow_id = f"deploy-run-{deployment_id}-{str(uuid.uuid4())[:4]}"
        await temporal_client.start_workflow(
            "DeploymentRunWorkflow",
            args=[deployment_id, "start"],
            id=workflow_id,
            task_queue="openclaw-tasks",
        )
        logger.info(f"Started deployment run workflow: {workflow_id}")
    except Exception as e:
        logger.error(f"Failed to start deployment: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start: {e}")

    # Reload to get updated state
    await db.refresh(deployment)
    return deployment


@router.post("/{deployment_id}/stop", response_model=DeploymentResponse)
async def stop_deployment(
    deployment_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Stop a running deployment."""
    result = await db.execute(
        select(Deployment).where(Deployment.id == deployment_id)
    )
    deployment = result.scalar_one_or_none()
    if not deployment:
        raise HTTPException(status_code=404, detail=f"Deployment {deployment_id} not found")

    if deployment.status != DeploymentStatus.RUNNING:
        raise HTTPException(
            status_code=400,
            detail=f"Deployment must be running to stop (status: {deployment.status})",
        )

    # Stop via Temporal activity
    try:
        from temporalio.client import Client

        temporal_client = await Client.connect(TEMPORAL_HOST)
        workflow_id = f"deploy-stop-{deployment_id}-{str(uuid.uuid4())[:4]}"
        await temporal_client.start_workflow(
            "DeploymentRunWorkflow",
            args=[deployment_id, "stop"],
            id=workflow_id,
            task_queue="openclaw-tasks",
        )
        logger.info(f"Started deployment stop workflow: {workflow_id}")
    except Exception as e:
        logger.error(f"Failed to stop deployment: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to stop: {e}")

    await db.refresh(deployment)
    return deployment
