"""
DAGs Router — CRUD and lifecycle for Master DAGs.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import (
    MasterDAG,
    DAGNode,
    Task,
    DAGStatus,
    NodeStatus,
    SkillSelectionEvent,
    DAGNodeStateSnapshot,
    DAGNodeAuditEvent,
    DAGNodeOutput,
)
from schemas import (
    DAGCreate,
    DAGManualCreate,
    DAGResponse,
    DAGDetail,
    DAGNodeResponse,
    DAGRevise,
    DAGRefine,
    DAGNodeEnhanceRequest,
    DAGNodePatch,
    DAGNodeStateSnapshotCreate,
    DAGNodeStateSnapshotResponse,
    DAGNodeAuditEventCreate,
    DAGNodeAuditEventResponse,
    DAGNodeOutputResponse,
    NodeAcceptanceResponse,
    WorkspaceManifestResponse,
)
from dag_validator import validate_dag
from planner import plan_dag
from routers.openai_dag import MODEL_CONFIGS
from temporal_client import start_dag_workflow
import uuid
import logging
import json
import httpx
from datetime import datetime
from copy import deepcopy

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory default model config (persists until container restart)
_dag_model_defaults: dict[str, str] = {
    "planning_model": "gemini-flash-lite-latest",
    "agent_model": "gemini-flash-lite-latest",
}


def _gen_dag_id() -> str:
    return f"dag-{uuid.uuid4().hex[:8]}"


def _gen_task_id() -> str:
    return f"task-{uuid.uuid4().hex[:8]}"


def _gen_workspace_id(dag_id: str) -> str:
    return f"workspace-{dag_id}"


def _normalize_node_skill_fields(node_def: dict, node_config: dict) -> tuple[str | None, str | None, str | None]:
    """Normalize planner-emitted skill fields.

    Some plans may place a v2 skill id (prefix skv2-) into legacy skill_id,
    which points to the v1 skills table and causes FK failures.
    This helper remaps such values to selected_skill_v2_id.
    """
    skill_id = node_def.get("skill_id")
    selected_skill_v2_id = node_config.pop("selected_skill_v2_id", None) or None
    skill_selection_reason = node_config.pop("skill_selection_reason", None) or None

    if isinstance(skill_id, str) and skill_id.startswith("skv2-"):
        if not selected_skill_v2_id:
            selected_skill_v2_id = skill_id
        skill_id = None
        if not skill_selection_reason:
            skill_selection_reason = (
                "Normalized planner output: moved v2 skill id from skill_id "
                "to selected_skill_v2_id."
            )

    return skill_id, selected_skill_v2_id, skill_selection_reason


@router.post("", response_model=DAGDetail, status_code=status.HTTP_201_CREATED)
async def create_dag(data: DAGCreate, db: AsyncSession = Depends(get_db)):
    """Create a new DAG from an objective using the LLM Planner."""
    dag_id = _gen_dag_id()
    workspace_id = _gen_workspace_id(dag_id)

    # Resolve model config: llm_model can be a MODEL_CONFIGS key or a raw model name
    cfg = MODEL_CONFIGS.get(data.llm_model or "")
    if cfg:
        planning_model = cfg["planning_model"]
        agent_model = cfg["agent_model"]
    elif data.llm_model:
        planning_model = data.llm_model
        agent_model = planning_model
    else:
        # Use stored defaults
        planning_model = _dag_model_defaults["planning_model"]
        agent_model = _dag_model_defaults["agent_model"]

    # Create DAG record in PLANNING state
    dag = MasterDAG(
        id=dag_id,
        objective=data.objective,
        status=DAGStatus.PLANNING,
        dag_json={},
        workspace_id=workspace_id,
        llm_model=agent_model,
    )
    db.add(dag)
    await db.commit()

    # Run the planner
    try:
        dag_json = await plan_dag(data.objective, planning_model, db, base_image=data.base_image, skill_ids=data.skill_ids, agent_model=agent_model)
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
        node_config = dict(node_def.get("config") or {})
        skill_id, selected_skill_v2_id, skill_selection_reason = _normalize_node_skill_fields(node_def, node_config)
        node_def["skill_id"] = skill_id
        if selected_skill_v2_id:
            node_def.setdefault("config", {})["selected_skill_v2_id"] = selected_skill_v2_id
        if skill_selection_reason:
            node_def.setdefault("config", {})["skill_selection_reason"] = skill_selection_reason
        node = DAGNode(
            dag_id=dag_id,
            node_id=node_def["node_id"],
            skill_id=skill_id,
            skill_step_index=node_def.get("skill_step_index"),
            description=node_def.get("description"),
            status=NodeStatus.PENDING,
            depends_on=node_def.get("depends_on", []),
            config=node_config,
            input_mapping=node_def.get("input_mapping", {}),
            selected_skill_v2_id=selected_skill_v2_id,
            skill_selection_reason=skill_selection_reason,
        )
        db.add(node)
        nodes.append(node)

        # Emit selection event for tracking
        if selected_skill_v2_id:
            event = SkillSelectionEvent(
                skill_id=selected_skill_v2_id,
                dag_id=dag_id,
                node_id=node_def["node_id"],
                selection_reason=skill_selection_reason,
            )
            db.add(event)

    await db.commit()
    await db.refresh(dag)
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
        node_config = dict(node_def.config or {})
        skill_id = node_def.skill_id
        selected_skill_v2_id = node_config.pop("selected_skill_v2_id", None) or None
        skill_selection_reason = node_config.pop("skill_selection_reason", None) or None
        if isinstance(skill_id, str) and skill_id.startswith("skv2-"):
            if not selected_skill_v2_id:
                selected_skill_v2_id = skill_id
            skill_id = None
            if not skill_selection_reason:
                skill_selection_reason = (
                    "Normalized manual input: moved v2 skill id from skill_id "
                    "to selected_skill_v2_id."
                )

        node = DAGNode(
            dag_id=dag_id,
            node_id=node_def.node_id,
            skill_id=skill_id,
            skill_step_index=node_def.skill_step_index,
            description=node_def.description,
            status=NodeStatus.PENDING,
            depends_on=node_def.depends_on,
            config=node_config,
            input_mapping=node_def.input_mapping,
            selected_skill_v2_id=selected_skill_v2_id,
            skill_selection_reason=skill_selection_reason,
        )
        db.add(node)
        nodes.append(node)

    await db.commit()
    await db.refresh(dag)
    return _build_dag_detail(dag, nodes)


@router.get("/models")
async def list_dag_models():
    """List available model configurations for DAG planning + execution."""
    return [
        {
            "id": model_id,
            "name": cfg["name"],
            "description": cfg.get("description", ""),
            "planning_model": cfg["planning_model"],
            "agent_model": cfg["agent_model"],
        }
        for model_id, cfg in MODEL_CONFIGS.items()
    ]


@router.get("/model-defaults")
async def get_model_defaults():
    """Return the current default planning + agent model."""
    return dict(_dag_model_defaults)


@router.post("/model-defaults")
async def set_model_defaults(body: dict):
    """Update the default planning and/or agent model."""
    changed = []
    if "planning_model" in body and body["planning_model"]:
        _dag_model_defaults["planning_model"] = body["planning_model"]
        changed.append(f"planning_model={body['planning_model']}")
    if "agent_model" in body and body["agent_model"]:
        _dag_model_defaults["agent_model"] = body["agent_model"]
        changed.append(f"agent_model={body['agent_model']}")
    logger.info(f"DAG model defaults updated: {', '.join(changed)}")
    return dict(_dag_model_defaults)


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


@router.post("/{dag_id}/nodes/{node_id}/state-snapshots", response_model=DAGNodeStateSnapshotResponse, status_code=status.HTTP_201_CREATED)
async def create_node_state_snapshot(
    dag_id: str,
    node_id: str,
    payload: DAGNodeStateSnapshotCreate,
    db: AsyncSession = Depends(get_db),
):
    """Persist a node execution state snapshot for provenance and continuity."""
    await _get_dag_or_404(dag_id, db)
    result = await db.execute(select(DAGNode).where(DAGNode.dag_id == dag_id, DAGNode.node_id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found in DAG {dag_id}")

    snapshot = DAGNodeStateSnapshot(
        dag_id=dag_id,
        node_id=node_id,
        task_id=payload.task_id,
        phase=payload.phase,
        status=payload.status,
        wave=payload.wave,
        attempt=payload.attempt,
        input_context=payload.input_context,
        output_context=payload.output_context,
        completion_state=payload.completion_state,
        acquisition_log=payload.acquisition_log,
        acceptance_result=payload.acceptance_result,
        pending_items=payload.pending_items,
    )
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)
    return snapshot


@router.get("/{dag_id}/nodes/{node_id}/state/latest", response_model=DAGNodeStateSnapshotResponse | None)
async def get_latest_node_state_snapshot(dag_id: str, node_id: str, db: AsyncSession = Depends(get_db)):
    """Get the latest execution state snapshot for a DAG node."""
    await _get_dag_or_404(dag_id, db)
    result = await db.execute(
        select(DAGNodeStateSnapshot)
        .where(DAGNodeStateSnapshot.dag_id == dag_id, DAGNodeStateSnapshot.node_id == node_id)
        .order_by(DAGNodeStateSnapshot.created_at.desc(), DAGNodeStateSnapshot.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


@router.get("/{dag_id}/nodes/{node_id}/state-snapshots", response_model=list[DAGNodeStateSnapshotResponse])
async def list_node_state_snapshots(dag_id: str, node_id: str, limit: int = 50, db: AsyncSession = Depends(get_db)):
    """List execution state snapshots for a DAG node (newest first)."""
    await _get_dag_or_404(dag_id, db)
    limit = max(1, min(limit, 200))
    result = await db.execute(
        select(DAGNodeStateSnapshot)
        .where(DAGNodeStateSnapshot.dag_id == dag_id, DAGNodeStateSnapshot.node_id == node_id)
        .order_by(DAGNodeStateSnapshot.created_at.desc(), DAGNodeStateSnapshot.id.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


@router.post("/{dag_id}/nodes/{node_id}/audit-events", response_model=DAGNodeAuditEventResponse, status_code=status.HTTP_201_CREATED)
async def create_node_audit_event(
    dag_id: str,
    node_id: str,
    payload: DAGNodeAuditEventCreate,
    db: AsyncSession = Depends(get_db),
):
    """Persist a structured audit event for a DAG node."""
    await _get_dag_or_404(dag_id, db)
    result = await db.execute(select(DAGNode).where(DAGNode.dag_id == dag_id, DAGNode.node_id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found in DAG {dag_id}")

    event = DAGNodeAuditEvent(
        dag_id=dag_id,
        node_id=node_id,
        task_id=payload.task_id,
        event_type=payload.event_type,
        severity=payload.severity,
        message=payload.message,
        event_data=payload.event_data,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


@router.get("/{dag_id}/nodes/{node_id}/audit-events", response_model=list[DAGNodeAuditEventResponse])
async def list_node_audit_events(dag_id: str, node_id: str, limit: int = 100, db: AsyncSession = Depends(get_db)):
    """List audit events for a DAG node (newest first)."""
    await _get_dag_or_404(dag_id, db)
    limit = max(1, min(limit, 500))
    result = await db.execute(
        select(DAGNodeAuditEvent)
        .where(DAGNodeAuditEvent.dag_id == dag_id, DAGNodeAuditEvent.node_id == node_id)
        .order_by(DAGNodeAuditEvent.created_at.desc(), DAGNodeAuditEvent.id.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


@router.get("/{dag_id}/audit-log", response_model=list[DAGNodeAuditEventResponse])
async def list_dag_audit_log(dag_id: str, limit: int = 200, db: AsyncSession = Depends(get_db)):
    """List DAG-wide audit events across all nodes (newest first)."""
    await _get_dag_or_404(dag_id, db)
    limit = max(1, min(limit, 1000))
    result = await db.execute(
        select(DAGNodeAuditEvent)
        .where(DAGNodeAuditEvent.dag_id == dag_id)
        .order_by(DAGNodeAuditEvent.created_at.desc(), DAGNodeAuditEvent.id.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


# ── Structured Node Output Endpoints ──────────────────────────────────────────

@router.post("/{dag_id}/nodes/{node_id}/output", status_code=status.HTTP_201_CREATED)
async def create_node_structured_output(
    dag_id: str,
    node_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """Persist a structured node output with acceptance and skill compliance data."""
    from models import DAGNodeOutput
    from schemas import DAGNodeOutputResponse

    dag = await _get_dag_or_404(dag_id, db)
    node = await db.execute(
        select(DAGNode).where(DAGNode.dag_id == dag_id, DAGNode.node_id == node_id)
    )
    node = node.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found in DAG {dag_id}")

    output = DAGNodeOutput(
        dag_id=dag_id,
        node_id=node_id,
        task_id=payload.get("task_id"),
        status=payload.get("status", "completed"),
        objective=payload.get("objective"),
        success_criteria=payload.get("success_criteria", []),
        criteria_met=payload.get("criteria_met", {}),
        acceptance_verdict=payload.get("acceptance_verdict"),
        acceptance_score=payload.get("acceptance_score", 0),
        skill_id=payload.get("skill_id"),
        skill_followed=payload.get("skill_followed"),
        skill_instruction_sections_used=payload.get("skill_instruction_sections_used", []),
        deliverables_count=payload.get("deliverables_count", 0),
        deliverables_keys=payload.get("deliverables_keys", []),
        acquisition_log=payload.get("acquisition_log", []),
        llm_interaction_count=payload.get("llm_interaction_count", 0),
        output_text=payload.get("output_text"),
        error_text=payload.get("error_text"),
        workspace_step_path=payload.get("workspace_step_path"),
    )
    db.add(output)
    await db.commit()
    await db.refresh(output)
    return output


@router.get("/{dag_id}/nodes/{node_id}/acceptance", response_model=dict)
async def get_node_acceptance(dag_id: str, node_id: str, db: AsyncSession = Depends(get_db)):
    """Get structured acceptance data for a DAG node."""
    from models import DAGNodeOutput
    from schemas import NodeAcceptanceResponse

    dag = await _get_dag_or_404(dag_id, db)

    # Get the latest structured output
    result = await db.execute(
        select(DAGNodeOutput)
        .where(DAGNodeOutput.dag_id == dag_id, DAGNodeOutput.node_id == node_id)
        .order_by(DAGNodeOutput.created_at.desc())
        .limit(1)
    )
    output = result.scalar_one_or_none()
    if output:
        return {
            "node_id": node_id,
            "status": output.status,
            "acceptance_verdict": output.acceptance_verdict,
            "acceptance_score": output.acceptance_score,
            "success_criteria": output.success_criteria,
            "criteria_met": output.criteria_met,
            "skill_id": output.skill_id,
            "skill_followed": output.skill_followed,
            "deliverables_keys": output.deliverables_keys,
            "workspace_step_path": output.workspace_step_path,
        }

    # Fallback: derive from node output_data if no structured output exists
    node_result = await db.execute(
        select(DAGNode).where(DAGNode.dag_id == dag_id, DAGNode.node_id == node_id)
    )
    node = node_result.scalar_one_or_none()
    if node and node.output_data:
        gate_result = node.output_data.get("gate_result", {})
        return {
            "node_id": node_id,
            "status": node.status.value if node.status else "unknown",
            "acceptance_verdict": "pass" if gate_result.get("valid") else "fail",
            "acceptance_score": gate_result.get("external_assessment", {}).get("score", 0),
            "success_criteria": node.config.get("success_criteria", []) if node.config else [],
            "criteria_met": {},
            "skill_id": node.selected_skill_v2_id,
            "skill_followed": None,
            "deliverables_keys": list((node.output_data or {}).get("deliverables", {}).keys()),
            "workspace_step_path": None,
        }

    return {
        "node_id": node_id,
        "status": "unknown",
        "acceptance_verdict": None,
        "acceptance_score": 0,
        "success_criteria": [],
        "criteria_met": {},
        "skill_id": None,
        "skill_followed": None,
        "deliverables_keys": [],
        "workspace_step_path": None,
    }


@router.get("/{dag_id}/workspace/manifest", response_model=dict)
async def get_dag_workspace_manifest(dag_id: str, db: AsyncSession = Depends(get_db)):
    """Get workspace file-to-node mapping for a DAG."""
    from models import DAGNodeOutput

    dag = await _get_dag_or_404(dag_id, db)

    # Get all node outputs for this DAG
    result = await db.execute(
        select(DAGNodeOutput)
        .where(DAGNodeOutput.dag_id == dag_id)
        .order_by(DAGNodeOutput.node_id)
    )
    outputs = result.scalars().all()

    step_manifest: dict[str, list[str]] = {}
    for output in outputs:
        if output.deliverables_keys:
            step_manifest[output.node_id] = output.deliverables_keys

    return {
        "workspace_id": dag.workspace_id,
        "step_manifest": step_manifest,
        "total_files": sum(len(v) for v in step_manifest.values()),
        "steps_with_deliverables": list(step_manifest.keys()),
    }


@router.patch("/{dag_id}")
async def patch_dag(dag_id: str, payload: dict, db: AsyncSession = Depends(get_db)):
    """Update DAG fields (status, completed_at)."""
    dag = await _get_dag_or_404(dag_id, db)
    if "status" in payload:
        dag.status = DAGStatus(payload["status"])
    if "completed_at" in payload:
        # Handle ISO format with timezone (e.g., "2026-07-16T21:02:20.123456+00:00")
        # Convert to offset-naive UTC for DB storage
        completed_at_str = payload["completed_at"]
        try:
            dt = datetime.fromisoformat(completed_at_str.replace('Z', '+00:00'))
            # If offset-aware, convert to UTC and strip timezone
            if dt.tzinfo is not None:
                dt = dt.utctimetuple()
                dt = datetime(*dt[:6])
            dag.completed_at = dt
        except ValueError:
            # Fallback: parse without timezone, assume UTC
            dag.completed_at = datetime.fromisoformat(completed_at_str.split('.')[0])
    await db.commit()
    return {"ok": True}


@router.patch("/{dag_id}/nodes/{node_id}")
async def patch_node(dag_id: str, node_id: str, payload: DAGNodePatch, db: AsyncSession = Depends(get_db)):
    """Update DAG node runtime fields and pre-run editable fields."""
    dag = await _get_dag_or_404(dag_id, db)
    result = await db.execute(
        select(DAGNode).where(DAGNode.dag_id == dag_id, DAGNode.node_id == node_id)
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    patch = payload.model_dump(exclude_unset=True)
    runtime_keys = {"status", "output_data", "task_id", "container_id"}
    editable_keys = set(patch.keys()) - runtime_keys

    # Runtime updates are allowed while running; user-editable graph changes are not.
    if editable_keys:
        _ensure_dag_editable(dag)

    if "status" in patch:
        node.status = NodeStatus(patch["status"])
        if node.status == NodeStatus.RUNNING and not node.started_at:
            node.started_at = datetime.utcnow()
        elif node.status in (NodeStatus.COMPLETED, NodeStatus.FAILED, NodeStatus.SKIPPED):
            node.completed_at = datetime.utcnow()
    if "output_data" in patch:
        node.output_data = patch["output_data"]
    if "task_id" in patch:
        node.task_id = patch["task_id"]
    if "container_id" in patch:
        node.container_id = patch["container_id"]

    # Editable fields (pre-run only)
    if "skill_id" in patch:
        node.skill_id = patch["skill_id"]
    if "skill_step_index" in patch:
        node.skill_step_index = patch["skill_step_index"]
    if "selected_skill_v2_id" in patch:
        node.selected_skill_v2_id = patch["selected_skill_v2_id"]
    if "skill_selection_reason" in patch:
        node.skill_selection_reason = patch["skill_selection_reason"]
    if "description" in patch:
        node.description = patch["description"]
    if "depends_on" in patch and patch["depends_on"] is not None:
        node.depends_on = _dedupe_node_ids(patch["depends_on"])
    if "input_mapping" in patch and patch["input_mapping"] is not None:
        node.input_mapping = patch["input_mapping"]
    if "config" in patch and patch["config"] is not None:
        merged_config = dict(node.config or {})
        merged_config.update(patch["config"])
        node.config = merged_config

    if editable_keys:
        dag_json = deepcopy(dag.dag_json or {})
        dag_nodes = dag_json.get("nodes", [])
        dag_node = next((n for n in dag_nodes if n.get("node_id") == node_id), None)
        if dag_node is None:
            raise HTTPException(status_code=500, detail=f"Node '{node_id}' missing from dag_json")

        if "skill_id" in patch:
            dag_node["skill_id"] = patch["skill_id"]
        if "skill_step_index" in patch:
            dag_node["skill_step_index"] = patch["skill_step_index"]
        if "description" in patch:
            dag_node["description"] = patch["description"]
        if "depends_on" in patch and patch["depends_on"] is not None:
            dag_node["depends_on"] = _dedupe_node_ids(patch["depends_on"])
        if "input_mapping" in patch and patch["input_mapping"] is not None:
            dag_node["input_mapping"] = patch["input_mapping"]

        dag_node_config = dict(dag_node.get("config") or {})
        if "config" in patch and patch["config"] is not None:
            dag_node_config.update(patch["config"])
        if "selected_skill_v2_id" in patch:
            dag_node_config["selected_skill_v2_id"] = patch["selected_skill_v2_id"]
        if "skill_selection_reason" in patch:
            dag_node_config["skill_selection_reason"] = patch["skill_selection_reason"]
        dag_node["config"] = dag_node_config

        is_valid, errors = validate_dag(dag_json)
        if not is_valid:
            await db.rollback()
            raise HTTPException(status_code=422, detail={"errors": errors})

        dag.dag_json = dag_json

    await db.commit()
    return {"ok": True}


@router.delete("/{dag_id}/nodes/{node_id}", response_model=DAGDetail)
async def delete_node(dag_id: str, node_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a node from a pre-run DAG and auto-rewire predecessor/successor dependencies."""
    dag = await _get_dag_or_404(dag_id, db)
    _ensure_dag_editable(dag)

    nodes_result = await db.execute(select(DAGNode).where(DAGNode.dag_id == dag_id))
    all_nodes = list(nodes_result.scalars().all())
    node = next((n for n in all_nodes if n.node_id == node_id), None)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")
    if len(all_nodes) <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last node from a DAG")

    predecessors = _dedupe_node_ids(node.depends_on or [])
    successors = [n for n in all_nodes if node_id in (n.depends_on or []) and n.node_id != node_id]

    for succ in successors:
        new_deps = [dep for dep in (succ.depends_on or []) if dep != node_id]
        for pred in predecessors:
            if pred != succ.node_id and pred not in new_deps:
                new_deps.append(pred)
        succ.depends_on = new_deps
        succ.input_mapping = _rewrite_input_mapping_for_removed_node(
            succ.input_mapping,
            node_id,
            predecessors,
        )

    dag_json = deepcopy(dag.dag_json or {})
    dag_json_nodes = []
    for dag_node in dag_json.get("nodes", []):
        if dag_node.get("node_id") == node_id:
            continue
        had_removed_dep = node_id in (dag_node.get("depends_on") or [])
        deps = [dep for dep in (dag_node.get("depends_on") or []) if dep != node_id]
        for pred in predecessors:
            if pred != dag_node.get("node_id") and pred not in deps and had_removed_dep:
                deps.append(pred)
        dag_node["depends_on"] = deps
        dag_node["input_mapping"] = _rewrite_input_mapping_for_removed_node(
            dag_node.get("input_mapping") or {},
            node_id,
            predecessors,
        )
        dag_json_nodes.append(dag_node)
    dag_json["nodes"] = dag_json_nodes

    # Remove all explicit edges that referenced the deleted node.
    filtered_edges = []
    for edge in dag_json.get("edges", []):
        edge_from = edge.get("from_node", edge.get("from"))
        edge_to = edge.get("to_node", edge.get("to"))
        if edge_from == node_id or edge_to == node_id:
            continue
        filtered_edges.append(edge)
    dag_json["edges"] = filtered_edges

    is_valid, errors = validate_dag(dag_json)
    if not is_valid:
        await db.rollback()
        raise HTTPException(status_code=422, detail={"errors": errors})

    dag.dag_json = dag_json
    await db.delete(node)
    await db.commit()
    await db.refresh(dag)

    nodes_result = await db.execute(select(DAGNode).where(DAGNode.dag_id == dag_id))
    remaining_nodes = list(nodes_result.scalars().all())
    return _build_dag_detail(dag, remaining_nodes)


@router.post("/{dag_id}/nodes/{node_id}/enhance", response_model=DAGDetail)
async def enhance_node(
    dag_id: str,
    node_id: str,
    body: DAGNodeEnhanceRequest,
    db: AsyncSession = Depends(get_db),
):
    """Enhance one node by rewriting it or splitting it into granular sub-steps."""
    dag = await _get_dag_or_404(dag_id, db)
    _ensure_dag_editable(dag)

    nodes_result = await db.execute(select(DAGNode).where(DAGNode.dag_id == dag_id))
    all_nodes = list(nodes_result.scalars().all())
    node = next((n for n in all_nodes if n.node_id == node_id), None)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    guidance = (body.guidance or "").strip()
    llm_model = body.llm_model or dag.llm_model or _dag_model_defaults["planning_model"]
    proposal: dict = {}
    try:
        proposal = await _generate_node_enhancement_proposal(
            dag,
            node,
            body.mode,
            guidance,
            llm_model,
            body.split_count,
        )
    except Exception as e:
        logger.warning(f"Node enhancement proposal failed for {dag_id}/{node_id}: {e}")

    dag_json = deepcopy(dag.dag_json or {})
    dag_nodes = dag_json.get("nodes", [])
    dag_node = next((n for n in dag_nodes if n.get("node_id") == node_id), None)
    if dag_node is None:
        raise HTTPException(status_code=500, detail=f"Node '{node_id}' missing from dag_json")

    if body.mode == "split":
        proposal_steps = proposal.get("steps") if isinstance(proposal, dict) else None
        if not isinstance(proposal_steps, list) or len(proposal_steps) < 2:
            proposal_steps = [
                {
                    "title": "analysis",
                    "description": (node.description or "") + "\nBreak requirements into explicit deliverables.",
                },
                {
                    "title": "execution",
                    "description": (node.description or "") + "\nImplement and validate all required outputs.",
                },
            ]

        split_steps = proposal_steps[: body.split_count]
        existing_ids = {n.node_id for n in all_nodes}
        split_ids: list[str] = []
        counter = 1
        for _ in split_steps:
            candidate = f"{node_id}-{counter}"
            while candidate in existing_ids or candidate in split_ids:
                counter += 1
                candidate = f"{node_id}-{counter}"
            split_ids.append(candidate)
            counter += 1

        predecessors = _dedupe_node_ids(node.depends_on or [])
        successors = [n for n in all_nodes if node_id in (n.depends_on or []) and n.node_id != node_id]
        base_config = dict(node.config or {})

        for i, step in enumerate(split_steps):
            step_id = split_ids[i]
            step_description = str((step or {}).get("description") or (node.description or "")).strip()
            created = DAGNode(
                dag_id=dag_id,
                node_id=step_id,
                skill_id=node.skill_id,
                skill_step_index=node.skill_step_index,
                description=step_description,
                status=NodeStatus.PENDING,
                depends_on=predecessors if i == 0 else [split_ids[i - 1]],
                config=dict(base_config),
                input_mapping=dict(node.input_mapping or {}) if i == 0 else {},
                selected_skill_v2_id=node.selected_skill_v2_id,
                skill_selection_reason=node.skill_selection_reason,
            )
            db.add(created)

        tail_id = split_ids[-1]
        for succ in successors:
            new_deps = [dep for dep in (succ.depends_on or []) if dep != node_id]
            if tail_id not in new_deps:
                new_deps.append(tail_id)
            succ.depends_on = _dedupe_node_ids(new_deps)
            succ.input_mapping = _rewrite_input_mapping_for_removed_node(
                succ.input_mapping,
                node_id,
                [tail_id],
            )

        rewritten_nodes = []
        for existing in dag_json.get("nodes", []):
            if existing.get("node_id") == node_id:
                continue
            if node_id in (existing.get("depends_on") or []):
                deps = [dep for dep in (existing.get("depends_on") or []) if dep != node_id]
                if tail_id not in deps:
                    deps.append(tail_id)
                existing["depends_on"] = _dedupe_node_ids(deps)
                existing["input_mapping"] = _rewrite_input_mapping_for_removed_node(
                    existing.get("input_mapping") or {},
                    node_id,
                    [tail_id],
                )
            rewritten_nodes.append(existing)

        for i, step in enumerate(split_steps):
            rewritten_nodes.append(
                {
                    "node_id": split_ids[i],
                    "skill_id": node.skill_id,
                    "skill_step_index": node.skill_step_index,
                    "description": str((step or {}).get("description") or (node.description or "")).strip(),
                    "depends_on": predecessors if i == 0 else [split_ids[i - 1]],
                    "config": dict(base_config),
                    "input_mapping": dict(node.input_mapping or {}) if i == 0 else {},
                }
            )
        dag_json["nodes"] = rewritten_nodes

        rewritten_edges = []
        seen_edges = set()
        for edge in dag_json.get("edges", []):
            edge_from_key = "from_node" if "from_node" in edge else "from"
            edge_to_key = "to_node" if "to_node" in edge else "to"
            edge_from = edge.get(edge_from_key)
            edge_to = edge.get(edge_to_key)

            if edge_to == node_id:
                edge[edge_to_key] = split_ids[0]
            if edge_from == node_id:
                edge[edge_from_key] = tail_id

            normalized = (edge.get(edge_from_key), edge.get(edge_to_key), edge.get("condition"), edge.get("edge_type"))
            if normalized in seen_edges:
                continue
            seen_edges.add(normalized)
            rewritten_edges.append(edge)
        dag_json["edges"] = rewritten_edges

        await db.delete(node)
    else:
        rewritten_desc = str((proposal or {}).get("description") or "").strip()
        if not rewritten_desc:
            rewritten_desc = (node.description or "").strip()
            if guidance:
                rewritten_desc = f"{rewritten_desc}\n\nRefinement guidance: {guidance}".strip()

        success_criteria = proposal.get("success_criteria") if isinstance(proposal, dict) else None
        if not isinstance(success_criteria, list):
            success_criteria = []

        node.description = rewritten_desc
        merged_cfg = dict(node.config or {})
        if success_criteria:
            merged_cfg["success_criteria"] = [str(x)[:280] for x in success_criteria[:12]]
        node.config = merged_cfg

        dag_node["description"] = rewritten_desc
        dag_node_cfg = dict(dag_node.get("config") or {})
        if success_criteria:
            dag_node_cfg["success_criteria"] = [str(x)[:280] for x in success_criteria[:12]]
        dag_node["config"] = dag_node_cfg

    is_valid, errors = validate_dag(dag_json)
    if not is_valid:
        await db.rollback()
        raise HTTPException(status_code=422, detail={"errors": errors})

    dag.dag_json = dag_json
    await db.commit()
    await db.refresh(dag)

    nodes_result = await db.execute(select(DAGNode).where(DAGNode.dag_id == dag_id))
    updated_nodes = list(nodes_result.scalars().all())
    return _build_dag_detail(dag, updated_nodes)


@router.post("/{dag_id}/retry-from/{node_id}", response_model=DAGDetail)
async def retry_dag_from_node(dag_id: str, node_id: str, db: AsyncSession = Depends(get_db)):
    """Resume execution from one failed node onward."""
    dag = await _get_dag_or_404(dag_id, db)
    if dag.status != DAGStatus.FAILED:
        raise HTTPException(status_code=400, detail="Retry-from-node is only available for failed DAGs")

    nodes_result = await db.execute(select(DAGNode).where(DAGNode.dag_id == dag_id))
    all_nodes = list(nodes_result.scalars().all())
    target = next((n for n in all_nodes if n.node_id == node_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    rerun = {node_id}
    rerun.update(_compute_descendants(node_id, all_nodes))

    for node in all_nodes:
        if node.node_id in rerun:
            node.status = NodeStatus.PENDING
            node.output_data = None
            node.task_id = None
            node.container_id = None
            node.started_at = None
            node.completed_at = None
        elif node.status != NodeStatus.COMPLETED:
            node.status = NodeStatus.SKIPPED

    dag.status = DAGStatus.READY
    dag.completed_at = None

    await db.commit()
    await db.refresh(dag)
    return await _start_dag(dag, db)


@router.post("/{dag_id}/refine", response_model=DAGDetail)
async def refine_dag(dag_id: str, body: DAGRefine, db: AsyncSession = Depends(get_db)):
    """Refine a pre-run DAG in-place by re-running planning with additional instructions."""
    dag = await _get_dag_or_404(dag_id, db)
    _ensure_dag_editable(dag)

    planning_model = body.llm_model or dag.llm_model or _dag_model_defaults["planning_model"]
    base_image = _extract_dag_base_image(dag.dag_json)
    refinement_objective = (
        f"{dag.objective}\n\n"
        f"--- DAG REFINEMENT INSTRUCTIONS ---\n"
        f"{body.instructions}\n\n"
        f"Update the DAG plan accordingly while preserving valid dependencies and practical execution order."
    )

    try:
        dag_json = await plan_dag(refinement_objective, planning_model, db, base_image=base_image, agent_model=dag.llm_model)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    is_valid, errors = validate_dag(dag_json)
    if not is_valid:
        raise HTTPException(status_code=422, detail={"errors": errors})

    # Replace all existing nodes with the refined plan's nodes, preserving DAG identity.
    existing_nodes_result = await db.execute(select(DAGNode).where(DAGNode.dag_id == dag_id))
    for old_node in existing_nodes_result.scalars().all():
        await db.delete(old_node)

    nodes = []
    for node_def in dag_json.get("nodes", []):
        node_config = dict(node_def.get("config") or {})
        skill_id, selected_skill_v2_id, skill_selection_reason = _normalize_node_skill_fields(node_def, node_config)
        node_def["skill_id"] = skill_id
        if selected_skill_v2_id:
            node_def.setdefault("config", {})["selected_skill_v2_id"] = selected_skill_v2_id
        if skill_selection_reason:
            node_def.setdefault("config", {})["skill_selection_reason"] = skill_selection_reason
        node = DAGNode(
            dag_id=dag_id,
            node_id=node_def["node_id"],
            skill_id=skill_id,
            skill_step_index=node_def.get("skill_step_index"),
            description=node_def.get("description"),
            status=NodeStatus.PENDING,
            depends_on=node_def.get("depends_on", []),
            config=node_config,
            input_mapping=node_def.get("input_mapping", {}),
            selected_skill_v2_id=selected_skill_v2_id,
            skill_selection_reason=skill_selection_reason,
        )
        db.add(node)
        nodes.append(node)

        if selected_skill_v2_id:
            event = SkillSelectionEvent(
                skill_id=selected_skill_v2_id,
                dag_id=dag_id,
                node_id=node_def["node_id"],
                selection_reason=skill_selection_reason,
            )
            db.add(event)

    dag.dag_json = dag_json
    dag.status = DAGStatus.READY
    dag.workflow_id = None
    dag.workflow_run_id = None
    dag.started_at = None
    dag.completed_at = None
    await db.commit()
    await db.refresh(dag)
    return _build_dag_detail(dag, nodes)


@router.post("/{dag_id}/revise", response_model=DAGDetail, status_code=status.HTTP_201_CREATED)
async def revise_dag(dag_id: str, body: DAGRevise, db: AsyncSession = Depends(get_db)):
    """Create a new DAG based on revision comments for an existing DAG.

    Takes the original DAG's objective, appends the user's revision
    comments as context, and calls the planner to design a fresh DAG
    that addresses the requested changes.
    """
    old_dag = await _get_dag_or_404(dag_id, db)

    if old_dag.status not in (DAGStatus.COMPLETED, DAGStatus.FAILED, DAGStatus.CANCELLED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"DAG must be completed, failed, or cancelled to revise (current: {old_dag.status.value})",
        )

    # Build a revision objective that gives the planner full context
    revision_objective = (
        f"{old_dag.objective}\n\n"
        f"--- REVISION CONTEXT ---\n"
        f"A previous attempt (DAG {dag_id}) was made to solve this objective. "
        f"The user has reviewed the result and requests the following changes:\n\n"
        f"{body.comments}\n\n"
        f"Design a new DAG that addresses these revision comments. "
        f"Reuse working parts of the original approach where appropriate, "
        f"but make the requested changes."
    )

    # Find the most recent agent image from the old DAG's nodes
    old_base_image = None
    old_nodes_result = await db.execute(
        select(DAGNode).where(DAGNode.dag_id == dag_id)
    )
    old_nodes = list(old_nodes_result.scalars().all())

    # Check tasks for current_image, preferring the most recently completed node
    candidates = [n for n in old_nodes if n.task_id]
    candidates.sort(key=lambda n: n.completed_at or datetime.min, reverse=True)
    for node in candidates:
        task_result = await db.execute(select(Task).where(Task.id == node.task_id))
        task = task_result.scalar_one_or_none()
        if task and task.current_image:
            old_base_image = task.current_image
            logger.info(f"Revision base image from node '{node.node_id}': {old_base_image}")
            break

    # Fall back to config base_image from DAG JSON if no task image found
    if not old_base_image and old_dag.dag_json and isinstance(old_dag.dag_json, dict):
        for node_def in old_dag.dag_json.get("nodes", []):
            cfg = node_def.get("config", {})
            if cfg.get("base_image"):
                old_base_image = cfg["base_image"]
                break

    llm_model = body.llm_model or old_dag.llm_model or "gemini-flash-lite-latest"

    # Create a new DAG in PLANNING state
    # Reuse the old DAG's workspace so the agent has access to previous outputs
    new_dag_id = _gen_dag_id()
    workspace_id = old_dag.workspace_id

    new_dag = MasterDAG(
        id=new_dag_id,
        objective=revision_objective,
        status=DAGStatus.PLANNING,
        dag_json={},
        workspace_id=workspace_id,
        llm_model=llm_model,
    )
    db.add(new_dag)
    await db.commit()

    # Run the planner
    try:
        dag_json = await plan_dag(
            revision_objective, llm_model, db, base_image=old_base_image
        )
    except ValueError as e:
        new_dag.status = DAGStatus.FAILED
        new_dag.dag_json = {"error": str(e)}
        await db.commit()
        raise HTTPException(status_code=422, detail=str(e))

    # Store DAG JSON and create node records
    new_dag.dag_json = dag_json
    new_dag.status = DAGStatus.READY

    nodes = []
    for node_def in dag_json.get("nodes", []):
        node_config = dict(node_def.get("config") or {})
        skill_id, selected_skill_v2_id, skill_selection_reason = _normalize_node_skill_fields(node_def, node_config)
        node_def["skill_id"] = skill_id
        if selected_skill_v2_id:
            node_def.setdefault("config", {})["selected_skill_v2_id"] = selected_skill_v2_id
        if skill_selection_reason:
            node_def.setdefault("config", {})["skill_selection_reason"] = skill_selection_reason
        node = DAGNode(
            dag_id=new_dag_id,
            node_id=node_def["node_id"],
            skill_id=skill_id,
            skill_step_index=node_def.get("skill_step_index"),
            description=node_def.get("description"),
            status=NodeStatus.PENDING,
            depends_on=node_def.get("depends_on", []),
            config=node_config,
            input_mapping=node_def.get("input_mapping", {}),
            selected_skill_v2_id=selected_skill_v2_id,
            skill_selection_reason=skill_selection_reason,
        )
        db.add(node)
        nodes.append(node)

        if selected_skill_v2_id:
            event = SkillSelectionEvent(
                skill_id=selected_skill_v2_id,
                dag_id=new_dag_id,
                node_id=node_def["node_id"],
                selection_reason=skill_selection_reason,
            )
            db.add(event)

    await db.commit()
    await db.refresh(new_dag)

    # Auto-start the revision DAG
    return await _start_dag(new_dag, db)


# ── Helpers ─────────────────────────────────────────────

def _ensure_dag_editable(dag: MasterDAG) -> None:
    editable_statuses = {DAGStatus.READY, DAGStatus.FAILED}
    if dag.status not in editable_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"DAG is in '{dag.status.value}' state, must be 'ready' or 'failed' for this operation",
        )


def _dedupe_node_ids(node_ids: list[str]) -> list[str]:
    seen = set()
    ordered = []
    for nid in node_ids:
        if nid in seen:
            continue
        seen.add(nid)
        ordered.append(nid)
    return ordered


def _rewrite_input_mapping_for_removed_node(
    mapping: dict | None,
    removed_node_id: str,
    replacement_node_ids: list[str],
) -> dict:
    """Repoint or remove input mappings that reference a removed node."""
    updated = dict(mapping or {})
    replacement = replacement_node_ids[0] if replacement_node_ids else None

    for key, source in list(updated.items()):
        if isinstance(source, dict):
            source_from = str(source.get("from") or "")
            if source_from == removed_node_id:
                if replacement:
                    copied = dict(source)
                    copied["from"] = replacement
                    updated[key] = copied
                else:
                    updated.pop(key, None)
            continue

        if isinstance(source, str) and (source == removed_node_id or source.startswith(f"{removed_node_id}.")):
            if replacement:
                suffix = source[len(removed_node_id):]
                updated[key] = f"{replacement}{suffix}"
            else:
                updated.pop(key, None)

    return updated


def _compute_descendants(target_node_id: str, all_nodes: list[DAGNode]) -> set[str]:
    """Compute transitive descendants from depends_on relations."""
    children_map: dict[str, list[str]] = {}
    for node in all_nodes:
        for dep in node.depends_on or []:
            children_map.setdefault(dep, []).append(node.node_id)

    descendants: set[str] = set()
    stack = list(children_map.get(target_node_id, []))
    while stack:
        current = stack.pop()
        if current in descendants:
            continue
        descendants.add(current)
        stack.extend(children_map.get(current, []))
    return descendants


async def _generate_node_enhancement_proposal(
    dag: MasterDAG,
    node: DAGNode,
    mode: str,
    guidance: str,
    llm_model: str,
    split_count: int,
) -> dict:
    """Generate an enhancement proposal through the existing LLM router."""
    control_plane_url = "http://control-plane:8000"
    schema_hint = (
        "Return strict JSON only with keys: steps. "
        "steps must be an array of objects with keys title and description."
        if mode == "split"
        else "Return strict JSON only with keys: description and success_criteria. "
        "success_criteria must be an array of short strings."
    )

    prompt = (
        "You are improving one failed DAG step. Keep the plan pragmatic and compact.\n"
        f"DAG Objective:\n{dag.objective}\n\n"
        f"Node ID: {node.node_id}\n"
        f"Current description:\n{node.description or ''}\n\n"
        f"Mode: {mode}\n"
        f"Requested split_count: {split_count}\n"
        f"User guidance:\n{guidance or 'None'}\n\n"
        f"{schema_hint}"
    )

    req = {
        "model": llm_model,
        "messages": [
            {"role": "system", "content": "You output strict JSON only."},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "temperature": 0.2,
    }

    async with httpx.AsyncClient(timeout=40.0) as client:
        resp = await client.post(f"{control_plane_url}/api/llm/v1/chat/completions", json=req)
        resp.raise_for_status()
        data = resp.json()
        content = ((((data.get("choices") or [{}])[0].get("message") or {}).get("content")) or "{}").strip()
        if "```" in content:
            content = content.replace("```json", "").replace("```", "").strip()
        start = content.find("{")
        end = content.rfind("}")
        payload = content[start:end + 1] if start != -1 and end != -1 else content
        return json.loads(payload)


def _extract_dag_base_image(dag_json: dict | None) -> str | None:
    if not dag_json:
        return None
    for node_def in dag_json.get("nodes", []):
        cfg = node_def.get("config", {})
        if cfg.get("base_image"):
            return cfg["base_image"]
    return None

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
                "selected_skill_v2_id": n.selected_skill_v2_id,
                "skill_selection_reason": n.skill_selection_reason,
                "deliverables_keys": (n.output_data or {}).get("deliverables_keys"),
            }
            for n in nodes
        ],
    }
