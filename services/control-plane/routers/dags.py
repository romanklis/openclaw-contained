"""
DAGs Router — CRUD and lifecycle for Master DAGs.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import MasterDAG, DAGNode, Task, DAGStatus, NodeStatus, TaskStatus
from schemas import (
    DAGCreate, DAGManualCreate, DAGResponse, DAGDetail, DAGNodeResponse,
)
from dag_validator import validate_dag
from planner import plan_dag
from temporal_client import start_dag_workflow
import uuid
import logging
from datetime import datetime

logger = logging.getLogger(__name__)
router = APIRouter()


def _gen_dag_id() -> str:
    return f"dag-{uuid.uuid4().hex[:8]}"


def _gen_task_id() -> str:
    return f"task-{uuid.uuid4().hex[:8]}"


def _gen_workspace_id(dag_id: str) -> str:
    return f"workspace-{dag_id}"


@router.post("", response_model=DAGDetail, status_code=status.HTTP_201_CREATED)
async def create_dag(data: DAGCreate, db: AsyncSession = Depends(get_db)):
    """Create a new DAG from an objective using the LLM Planner."""
    dag_id = _gen_dag_id()
    workspace_id = _gen_workspace_id(dag_id)
    llm_model = data.llm_model or "gemma3:4b"

    # Create DAG record in PLANNING state
    dag = MasterDAG(
        id=dag_id,
        objective=data.objective,
        status=DAGStatus.PLANNING,
        dag_json={},
        workspace_id=workspace_id,
        llm_model=llm_model,
    )
    db.add(dag)
    await db.commit()

    # Run the planner
    try:
        dag_json = await plan_dag(data.objective, llm_model, db)
    except ValueError as e:
        dag.status = DAGStatus.FAILED
        dag.dag_json = {"error": str(e)}
        await db.commit()
        raise HTTPException(status_code=422, detail=str(e))

    # Store DAG JSON and create node records
    dag.dag_json = dag_json
    dag.status = DAGStatus.READY

    nodes = []
    for node_def in dag_json.get("nodes", []):
        node = DAGNode(
            dag_id=dag_id,
            node_id=node_def["node_id"],
            skill_id=node_def.get("skill_id"),
            skill_step_index=node_def.get("skill_step_index"),
            description=node_def.get("description"),
            status=NodeStatus.PENDING,
            depends_on=node_def.get("depends_on", []),
            config=node_def.get("config", {}),
            input_mapping=node_def.get("input_mapping", {}),
        )
        db.add(node)
        nodes.append(node)

    await db.commit()
    await db.refresh(dag)

    # Auto-start if requested
    if data.auto_start:
        return await _start_dag(dag, db)

    return _build_dag_detail(dag, nodes)


@router.post("/manual", response_model=DAGDetail, status_code=status.HTTP_201_CREATED)
async def create_dag_manual(data: DAGManualCreate, db: AsyncSession = Depends(get_db)):
    """Create a DAG with an explicit node graph (skip planner)."""
    dag_id = _gen_dag_id()
    workspace_id = _gen_workspace_id(dag_id)

    dag_json = {
        "nodes": [n.model_dump() for n in data.nodes],
        "edges": [e.model_dump() for e in data.edges],
        "default_image": data.default_image,
        "default_llm": data.default_llm,
    }

    # Validate
    is_valid, errors = validate_dag(dag_json)
    if not is_valid:
        raise HTTPException(status_code=422, detail={"errors": errors})

    dag = MasterDAG(
        id=dag_id,
        objective=data.objective,
        status=DAGStatus.READY,
        dag_json=dag_json,
        workspace_id=workspace_id,
        llm_model=data.default_llm,
    )
    db.add(dag)

    nodes = []
    for node_def in data.nodes:
        node = DAGNode(
            dag_id=dag_id,
            node_id=node_def.node_id,
            skill_id=node_def.skill_id,
            skill_step_index=node_def.skill_step_index,
            description=node_def.description,
            status=NodeStatus.PENDING,
            depends_on=node_def.depends_on,
            config=node_def.config,
            input_mapping=node_def.input_mapping,
        )
        db.add(node)
        nodes.append(node)

    await db.commit()
    await db.refresh(dag)
    return _build_dag_detail(dag, nodes)


@router.get("", response_model=list[DAGResponse])
async def list_dags(skip: int = 0, limit: int = 50, db: AsyncSession = Depends(get_db)):
    """List all DAGs."""
    result = await db.execute(
        select(MasterDAG).order_by(MasterDAG.created_at.desc()).offset(skip).limit(limit)
    )
    return list(result.scalars().all())


@router.get("/{dag_id}", response_model=DAGDetail)
async def get_dag(dag_id: str, db: AsyncSession = Depends(get_db)):
    """Get a DAG with its nodes."""
    dag = await _get_dag_or_404(dag_id, db)
    nodes_result = await db.execute(select(DAGNode).where(DAGNode.dag_id == dag_id))
    nodes = list(nodes_result.scalars().all())
    return _build_dag_detail(dag, nodes)


@router.post("/{dag_id}/start", response_model=DAGDetail)
async def start_dag(dag_id: str, db: AsyncSession = Depends(get_db)):
    """Start executing a DAG via Temporal workflow."""
    dag = await _get_dag_or_404(dag_id, db)
    if dag.status not in (DAGStatus.READY, DAGStatus.FAILED):
        raise HTTPException(
            status_code=400,
            detail=f"DAG is in '{dag.status.value}' state, must be 'ready' or 'failed' to start"
        )
    return await _start_dag(dag, db)


@router.post("/{dag_id}/cancel", response_model=DAGResponse)
async def cancel_dag(dag_id: str, db: AsyncSession = Depends(get_db)):
    """Cancel a running DAG."""
    dag = await _get_dag_or_404(dag_id, db)
    if dag.status != DAGStatus.RUNNING:
        raise HTTPException(status_code=400, detail=f"DAG is not running (status: {dag.status.value})")

    dag.status = DAGStatus.CANCELLED
    dag.completed_at = datetime.utcnow()

    # Cancel pending nodes
    nodes_result = await db.execute(
        select(DAGNode).where(DAGNode.dag_id == dag_id, DAGNode.status == NodeStatus.PENDING)
    )
    for node in nodes_result.scalars().all():
        node.status = NodeStatus.SKIPPED

    await db.commit()
    await db.refresh(dag)
    return dag


@router.get("/{dag_id}/nodes", response_model=list[DAGNodeResponse])
async def get_dag_nodes(dag_id: str, db: AsyncSession = Depends(get_db)):
    """Get all nodes for a DAG."""
    await _get_dag_or_404(dag_id, db)
    result = await db.execute(select(DAGNode).where(DAGNode.dag_id == dag_id))
    return list(result.scalars().all())


@router.get("/{dag_id}/nodes/{node_id}/logs")
async def get_node_logs(dag_id: str, node_id: str, db: AsyncSession = Depends(get_db)):
    """Get execution logs for a specific DAG node."""
    await _get_dag_or_404(dag_id, db)
    result = await db.execute(
        select(DAGNode).where(DAGNode.dag_id == dag_id, DAGNode.node_id == node_id)
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found in DAG {dag_id}")

    # If node has a task_id, fetch task outputs for logs
    logs = {"node_id": node_id, "status": node.status.value, "output_data": node.output_data}
    if node.task_id:
        from models import TaskOutput
        outputs = await db.execute(
            select(TaskOutput).where(TaskOutput.task_id == node.task_id).order_by(TaskOutput.iteration)
        )
        logs["task_outputs"] = [
            {
                "iteration": o.iteration,
                "completed": o.completed,
                "agent_logs": o.agent_logs,
                "error": o.error,
            }
            for o in outputs.scalars().all()
        ]
    return logs


@router.patch("/{dag_id}")
async def patch_dag(dag_id: str, payload: dict, db: AsyncSession = Depends(get_db)):
    """Update DAG fields (status, completed_at)."""
    dag = await _get_dag_or_404(dag_id, db)
    if "status" in payload:
        dag.status = DAGStatus(payload["status"])
    if "completed_at" in payload:
        dag.completed_at = datetime.fromisoformat(payload["completed_at"])
    await db.commit()
    return {"ok": True}


@router.patch("/{dag_id}/nodes/{node_id}")
async def patch_node(dag_id: str, node_id: str, payload: dict, db: AsyncSession = Depends(get_db)):
    """Update DAG node fields (status, output_data, task_id, container_id)."""
    await _get_dag_or_404(dag_id, db)
    result = await db.execute(
        select(DAGNode).where(DAGNode.dag_id == dag_id, DAGNode.node_id == node_id)
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    if "status" in payload:
        node.status = NodeStatus(payload["status"])
        if payload["status"] == "running" and not node.started_at:
            node.started_at = datetime.utcnow()
        elif payload["status"] in ("completed", "failed", "skipped"):
            node.completed_at = datetime.utcnow()
    if "output_data" in payload:
        node.output_data = payload["output_data"]
    if "task_id" in payload:
        node.task_id = payload["task_id"]
    if "container_id" in payload:
        node.container_id = payload["container_id"]

    await db.commit()
    return {"ok": True}


# ── Helpers ─────────────────────────────────────────────

async def _get_dag_or_404(dag_id: str, db: AsyncSession) -> MasterDAG:
    result = await db.execute(select(MasterDAG).where(MasterDAG.id == dag_id))
    dag = result.scalar_one_or_none()
    if not dag:
        raise HTTPException(status_code=404, detail=f"DAG {dag_id} not found")
    return dag


async def _start_dag(dag: MasterDAG, db: AsyncSession) -> dict:
    """Start a DAG workflow and return the updated DAG detail."""
    workflow_id = await start_dag_workflow(dag.id)
    dag.workflow_id = workflow_id
    dag.status = DAGStatus.RUNNING
    dag.started_at = datetime.utcnow()
    await db.commit()
    await db.refresh(dag)

    nodes_result = await db.execute(select(DAGNode).where(DAGNode.dag_id == dag.id))
    nodes = list(nodes_result.scalars().all())
    return _build_dag_detail(dag, nodes)


def _build_dag_detail(dag: MasterDAG, nodes: list) -> dict:
    """Build DAGDetail response dict."""
    return {
        "id": dag.id,
        "objective": dag.objective,
        "status": dag.status,
        "workspace_id": dag.workspace_id,
        "llm_model": dag.llm_model,
        "workflow_id": dag.workflow_id,
        "created_by": dag.created_by,
        "created_at": dag.created_at,
        "updated_at": dag.updated_at,
        "started_at": dag.started_at,
        "completed_at": dag.completed_at,
        "dag_json": dag.dag_json,
        "nodes": [
            {
                "id": n.id,
                "dag_id": n.dag_id,
                "node_id": n.node_id,
                "skill_id": n.skill_id,
                "skill_step_index": n.skill_step_index,
                "description": n.description,
                "status": n.status,
                "depends_on": n.depends_on or [],
                "config": n.config or {},
                "input_mapping": n.input_mapping or {},
                "output_data": n.output_data,
                "task_id": n.task_id,
                "container_id": n.container_id,
                "started_at": n.started_at,
                "completed_at": n.completed_at,
            }
            for n in nodes
        ],
    }
