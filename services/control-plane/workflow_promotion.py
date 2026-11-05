"""Promote an exploration cycle into a normal experiment workflow group."""
from __future__ import annotations

import os
import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from attachments import copy_attachments
from models import (
    DAGNode,
    DAGStatus,
    ExplorationCycle,
    ExplorationSession,
    MasterDAG,
    NodeStatus,
    WorkflowGroup,
)
from workflow_groups import ensure_workflow_group, resolve_project_id


class PromotionError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _status(value: Any) -> str:
    return str(getattr(value, "value", value) or "").lower()


def _new_dag_id() -> str:
    return f"dag-{uuid.uuid4().hex[:8]}"


async def promote_exploration_cycle(
    session: ExplorationSession,
    cycle: ExplorationCycle,
    *,
    db: AsyncSession,
    title: Optional[str] = None,
    project_id: Optional[str] = None,
    workspace_mode: str = "continue",
    force: bool = False,
) -> Dict[str, Any]:
    """Copy a completed cycle DAG into a new mode=experiment workflow group.

    The source exploration DAG is never modified. Node definitions and image
    pins are preserved; runtime state is reset. By default the promoted
    experiment continues in the source workspace.
    """
    if cycle.session_id != session.id:
        raise PromotionError(404, "Cycle does not belong to this exploration session")
    if workspace_mode not in ("continue", "fresh"):
        raise PromotionError(400, "workspace_mode must be 'continue' or 'fresh'")

    source = await db.get(MasterDAG, cycle.dag_id)
    if not source:
        raise PromotionError(404, "Cycle DAG not found")
    if _status(source.status) != "completed":
        raise PromotionError(
            400,
            f"Only completed exploration cycles can be promoted (current: {_status(source.status)})",
        )

    if not force:
        existing_result = await db.execute(
            select(MasterDAG)
            .where(
                MasterDAG.promoted_from_cycle_id == cycle.id,
                MasterDAG.archived.is_(False),
            )
            .order_by(MasterDAG.created_at.desc())
        )
        existing = existing_result.scalars().first()
        if existing:
            group = await db.get(WorkflowGroup, existing.workflow_group_id)
            return _promotion_result(existing, group, cycle, created=False)

    new_dag_id = _new_dag_id()
    target_project_id = await resolve_project_id(
        project_id if project_id is not None else session.project_id, db
    )
    target_workspace_id = (
        source.workspace_id
        if workspace_mode == "continue"
        else f"workspace-{new_dag_id}"
    )
    if workspace_mode == "fresh":
        os.makedirs(f"/workspaces/{target_workspace_id}", exist_ok=True)
        attachments = (source.dag_json or {}).get("input_attachments") or []
        if attachments:
            try:
                copy_attachments(source.workspace_id, target_workspace_id)
            except Exception as exc:
                raise PromotionError(500, f"Could not copy input attachments: {exc}")

    # A unique marker keeps the promoted group out of the exploration group's
    # objective-key bucket and away from generic regroup rules.
    marker = f"[promotion:{session.id}/{cycle.id}/{uuid.uuid4().hex[:8]}]"
    group = await ensure_workflow_group(
        f"{source.objective}\n\n{marker}",
        target_project_id,
        db,
        llm_model=source.llm_model,
        use_llm_title=False,
        mode="experiment",
        force_new=True,
    )
    group.title = (
        (title or "").strip()
        or f"{session.title} — Experiment {cycle.cycle_number}"
    )
    group.title_source = "manual"
    group.objective = source.objective

    now = datetime.utcnow()
    dag_json = deepcopy(source.dag_json or {})
    dag_json.pop("error", None)
    dag_json.pop("_llm_usage", None)
    dag_json["promotion"] = {
        "source_session_id": session.id,
        "source_cycle_id": cycle.id,
        "source_dag_id": source.id,
        "source_cycle_number": cycle.cycle_number,
        "workspace_mode": workspace_mode,
        "promoted_at": now.isoformat() + "Z",
    }

    dag = MasterDAG(
        id=new_dag_id,
        objective=(title.strip() if title else source.objective),
        status=DAGStatus.READY,
        dag_json=dag_json,
        workspace_id=target_workspace_id,
        llm_model=source.llm_model,
        created_by=source.created_by or "exploration-promotion",
        project_id=target_project_id,
        workflow_group_id=group.id,
        locked=False,
        template_params=[],
        template_source_dag_id=None,
        exploration_session_id=None,
        exploration_cycle=None,
        promoted_from_session_id=session.id,
        promoted_from_cycle_id=cycle.id,
        promoted_from_dag_id=source.id,
        promoted_at=now,
    )
    db.add(dag)

    nodes_result = await db.execute(select(DAGNode).where(DAGNode.dag_id == source.id))
    source_nodes = list(nodes_result.scalars().all())
    for node in source_nodes:
        db.add(
            DAGNode(
                dag_id=new_dag_id,
                node_id=node.node_id,
                skill_id=node.skill_id,
                skill_step_index=node.skill_step_index,
                description=node.description,
                status=NodeStatus.PENDING,
                depends_on=deepcopy(node.depends_on or []),
                config=deepcopy(node.config or {}),
                input_mapping=deepcopy(node.input_mapping or {}),
                selected_skill_v2_id=node.selected_skill_v2_id,
                skill_selection_reason=node.skill_selection_reason,
                node_type=node.node_type or "agent",
                task_id=None,
                container_id=None,
                output_data=None,
                started_at=None,
                completed_at=None,
            )
        )

    cycle.promoted_dag_id = new_dag_id
    cycle.promoted_at = now
    await db.commit()
    await db.refresh(dag)
    await db.refresh(group)
    return _promotion_result(dag, group, cycle, created=True)


def _promotion_result(
    dag: MasterDAG,
    group: Optional[WorkflowGroup],
    cycle: ExplorationCycle,
    *,
    created: bool,
) -> Dict[str, Any]:
    return {
        "created": created,
        "dag_id": dag.id,
        "workflow_group_id": group.id if group else dag.workflow_group_id,
        "workflow_group_title": group.title if group else None,
        "workspace_id": dag.workspace_id,
        "status": _status(dag.status),
        "promoted_from": {
            "session_id": dag.promoted_from_session_id,
            "cycle_id": dag.promoted_from_cycle_id,
            "cycle_number": cycle.cycle_number,
            "dag_id": dag.promoted_from_dag_id,
        },
    }
