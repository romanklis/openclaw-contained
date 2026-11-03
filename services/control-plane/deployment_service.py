"""Deployment helpers shared by DAG finalization, exploration finalize, and the
deployment API.

Exploration deploy nodes write a ``deployment.json`` package. The platform must
promote that package even when the agent forgets to emit a DEPLOYMENT_REQUEST
marker (exp-ca99e842 cycle 4). Completed cycles create a pending deployment;
"Looks good, finalize" approves, builds, and starts it.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from explorations_deploy import (
    derive_app_path,
    normalize_uvicorn_entrypoint,
    resolve_task_deploy_image,
)
from models import (
    DAGNode,
    Deployment,
    DeploymentStatus,
    ExplorationCycle,
    MasterDAG,
    Task,
    TaskOutput,
)

logger = logging.getLogger(__name__)

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "temporal:7233")
ACTIVE_DEPLOYMENT_STATUSES = {
    DeploymentStatus.PENDING_APPROVAL,
    DeploymentStatus.APPROVED,
    DeploymentStatus.BUILDING,
    DeploymentStatus.BUILT,
    DeploymentStatus.RUNNING,
}


def _status(value: Any) -> str:
    return str(getattr(value, "value", value) or "").lower()


def _workspace_root(task: Optional[Task]) -> str:
    return f"/workspaces/{task.workspace_id}" if task and task.workspace_id else ""


def _parse_package_text(text: str) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if not payload.get("entrypoint") or not payload.get("port"):
        return None
    return payload


def _read_package_file(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return _parse_package_text(handle.read())
    except Exception:
        return None


async def _package_for_node(
    task: Task,
    node: DAGNode,
    db: AsyncSession,
) -> Optional[Dict[str, Any]]:
    root = _workspace_root(task)
    if root:
        package = _read_package_file(os.path.join(root, node.node_id, "deployment.json"))
        if package:
            return package

    result = await db.execute(
        select(TaskOutput)
        .where(TaskOutput.task_id == task.id)
        .order_by(TaskOutput.iteration.desc(), TaskOutput.created_at.desc())
    )
    for output in result.scalars().all():
        deliverables = output.deliverables
        if not isinstance(deliverables, dict):
            continue
        for name, value in deliverables.items():
            if not str(name).endswith("deployment.json"):
                continue
            package = _parse_package_text(value) if isinstance(value, str) else None
            if package:
                return package
    return None


async def _existing_deployment(task_id: str, db: AsyncSession) -> Optional[Deployment]:
    result = await db.execute(
        select(Deployment)
        .where(Deployment.task_id == task_id)
        .order_by(Deployment.created_at.desc())
    )
    return result.scalars().first()


async def ensure_cycle_deployment(
    dag: MasterDAG,
    db: AsyncSession,
) -> List[Deployment]:
    """Create pending deployments for a completed exploration cycle.

    Idempotent per deploy-node task: a second call returns the existing
    deployment instead of creating a duplicate.
    """
    if not dag or not getattr(dag, "exploration_session_id", None):
        return []
    if _status(dag.status) != "completed":
        return []

    cycle_result = await db.execute(
        select(ExplorationCycle)
        .where(ExplorationCycle.dag_id == dag.id)
        .order_by(ExplorationCycle.cycle_number.desc())
    )
    cycle = cycle_result.scalars().first()
    cycle_number = cycle.cycle_number if cycle else None

    nodes_result = await db.execute(select(DAGNode).where(DAGNode.dag_id == dag.id))
    nodes = list(nodes_result.scalars().all())
    deploy_nodes = [
        node
        for node in nodes
        if node.task_id and bool((node.config or {}).get("deploy_authorized"))
    ]
    if not deploy_nodes:
        return []

    created: List[Deployment] = []
    for node in deploy_nodes:
        task = await db.get(Task, node.task_id)
        if not task:
            continue
        existing = await _existing_deployment(task.id, db)
        if existing:
            created.append(existing)
            continue

        package = await _package_for_node(task, node, db)
        if not package:
            continue

        entrypoint = normalize_uvicorn_entrypoint(
            str(package.get("entrypoint") or ""),
            _workspace_root(task),
        )
        if not entrypoint:
            continue
        port = int(package.get("port") or 8000)
        image = (
            str(task.current_image or "").strip()
            or str((node.config or {}).get("last_built_image") or "").strip()
        )
        app = (
            str(package.get("app") or "")
            or str(package.get("node") or "")
            or derive_app_path(package).split("/")[0]
            or "app"
        )
        name = (
            f"{app} — Cycle {cycle_number}"
            if cycle_number is not None
            else f"{app} deployment"
        )

        deployment = Deployment(
            id=f"deploy-{uuid.uuid4().hex[:8]}",
            name=name,
            task_id=task.id,
            entrypoint=entrypoint,
            port=port,
            agent_image=image or None,
            trial_required=True,
            status=DeploymentStatus.PENDING_APPROVAL,
        )
        db.add(deployment)
        await db.flush()
        created.append(deployment)
        logger.info(
            "Created pending exploration deployment %s for %s (cycle %s)",
            deployment.id,
            task.id,
            cycle_number,
        )

    if created:
        await db.commit()
        for deployment in created:
            await db.refresh(deployment)
    return created


async def active_deployments_for_dag(
    dag_id: str,
    db: AsyncSession,
) -> List[Deployment]:
    nodes_result = await db.execute(select(DAGNode).where(DAGNode.dag_id == dag_id))
    tasks = [
        node.task_id
        for node in nodes_result.scalars().all()
        if node.task_id and bool((node.config or {}).get("deploy_authorized"))
    ]
    if not tasks:
        return []
    result = await db.execute(
        select(Deployment)
        .where(
            Deployment.task_id.in_(tasks),
            Deployment.status.in_(ACTIVE_DEPLOYMENT_STATUSES),
        )
        .order_by(Deployment.created_at.desc())
    )
    return list(result.scalars().all())


async def pending_deployments_for_dag(
    dag_id: str,
    db: AsyncSession,
) -> List[Deployment]:
    nodes_result = await db.execute(select(DAGNode).where(DAGNode.dag_id == dag_id))
    tasks = [
        node.task_id
        for node in nodes_result.scalars().all()
        if node.task_id and bool((node.config or {}).get("deploy_authorized"))
    ]
    if not tasks:
        return []
    result = await db.execute(
        select(Deployment)
        .where(
            Deployment.task_id.in_(tasks),
            Deployment.status == DeploymentStatus.PENDING_APPROVAL,
        )
        .order_by(Deployment.created_at.desc())
    )
    return list(result.scalars().all())


async def approve_deployment_record(
    deployment: Deployment,
    db: AsyncSession,
    auto_start: bool = False,
) -> Deployment:
    """Approve a pending deployment and start its build/trial workflow."""
    if deployment.status != DeploymentStatus.PENDING_APPROVAL:
        return deployment

    task = await db.get(Task, deployment.task_id)
    root = _workspace_root(task)
    entrypoint = normalize_uvicorn_entrypoint(deployment.entrypoint or "", root)
    if entrypoint != (deployment.entrypoint or ""):
        deployment.entrypoint = entrypoint
    image = (
        str(task.current_image or "").strip()
        if task
        else ""
    ) or str(deployment.agent_image or "").strip()
    if image:
        deployment.agent_image = image
    resolved_image = await resolve_task_deploy_image(
        deployment.task_id,
        db,
        deployment.agent_image,
        entrypoint=deployment.entrypoint or "",
    )
    if resolved_image:
        deployment.agent_image = resolved_image

    deployment.status = DeploymentStatus.APPROVED
    deployment.approved_at = datetime.utcnow()
    await db.commit()
    await db.refresh(deployment)

    try:
        from temporalio.client import Client

        client = await Client.connect(TEMPORAL_HOST)
        payload = {
            "task_id": deployment.task_id,
            "name": deployment.name,
            "entrypoint": deployment.entrypoint,
            "port": deployment.port,
            "agent_image": deployment.agent_image,
            "trial_required": bool(deployment.trial_required),
            "auto_start": bool(auto_start),
        }
        await client.start_workflow(
            "DeploymentBuildWorkflow",
            args=[deployment.id, payload],
            id=f"deploy-build-{deployment.id}",
            task_queue="openclaw-tasks",
        )
        logger.info(
            "Started deployment build workflow for %s (auto_start=%s)",
            deployment.id,
            auto_start,
        )
    except Exception as exc:
        logger.error("Failed to start deployment build for %s: %s", deployment.id, exc)
        deployment.status = DeploymentStatus.FAILED
        deployment.error = f"Failed to start build: {exc}"
        await db.commit()
        await db.refresh(deployment)

    return deployment
