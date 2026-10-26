"""Exploration session orchestration: cycles, replanning, and reconciliation."""
import logging
import uuid
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from attachments import save_attachments
from models import (
    CapabilityRequest,
    DAGNode,
    DagUserRequest,
    DeepReview,
    Deployment,
    ExplorationCycle,
    ExplorationMessage,
    DAGStatus,
    ExplorationSession,
    MasterDAG,
    NodeStatus,
    RequestStatus,
    TaskOutput,
    AgentTurn,
    LlmUsage,
    WorkflowGroup,
)
from planner import plan_dag
from workflow_groups import ensure_workflow_group

logger = logging.getLogger(__name__)

MAX_EXPLORATION_CYCLES = 20
LLM_ROUTER_URL = "http://localhost:8000/api/llm/v1/chat/completions"


def _gen_session_id() -> str:
    return f"exp-{uuid.uuid4().hex[:8]}"


def _gen_cycle_id() -> str:
    return f"cyc-{uuid.uuid4().hex[:8]}"


def _gen_dag_id() -> str:
    return f"dag-{uuid.uuid4().hex[:8]}"


def _status(value: Any) -> str:
    return getattr(value, "value", value) or "unknown"


def _planning_defaults() -> dict[str, str]:
    try:
        from routers import dags as dags_module
        return dags_module._dag_model_defaults
    except Exception:
        return {}


def _planning_model() -> str:
    return _planning_defaults().get("planning_model") or "gemini-flash-lite-latest"


THINKING_BUDGET_PROFILES = {
    "lean": {
        "max_turns": 12,
        "max_iterations": 2,
        "cycle_turns": 48,
        "planner": "Lean: use the fewest useful steps and avoid redundant discovery.",
    },
    "standard": {
        "max_turns": 24,
        "max_iterations": 5,
        "cycle_turns": 96,
        "planner": "Standard: work efficiently and reuse existing deliverables.",
    },
    "deep": {
        "max_turns": None,
        "max_iterations": 15,
        "cycle_turns": 240,
        "planner": "Deep: allow thorough investigation when the task requires it.",
    },
}

QUALITY_BUDGET_PROFILES = {
    "minimal": {
        "verification_mode": "off",
        "retry_on_fail": False,
        "planner": (
            "Minimal cross-check: do not add separate verification, report, README, "
            "or output-regeneration nodes; fold tests into the implementation node. "
            "Do not create REPORT.md, VERIFICATION.md, provenance, or extra logs."
        ),
        "agent": (
            "Do not create REPORT.md, VERIFICATION.md, provenance files, or extra "
            "verification logs unless explicitly requested. Run only the tests needed "
            "to validate the requested deliverable and keep the final response concise."
        ),
    },
    "standard": {
        "verification_mode": "deterministic",
        "retry_on_fail": False,
        "planner": (
            "Standard cross-check: do not add a separate LLM verifier or report node. "
            "At most one deterministic verification block is allowed. Keep README concise."
        ),
        "agent": (
            "Keep documentation concise. Do not create verbose REPORT/VERIFICATION/"
            "provenance artifacts unless explicitly requested. Run the requested tests "
            "once and report the exact result."
        ),
    },
    "full": {
        "verification_mode": "full",
        "retry_on_fail": True,
        "planner": "Full cross-check: full verification and report artifacts are allowed.",
        "agent": "",
    },
}

THINKING_BUDGET_LEVELS = set(THINKING_BUDGET_PROFILES)
QUALITY_BUDGET_LEVELS = set(QUALITY_BUDGET_PROFILES)


def normalize_thinking_budget(value: str | None) -> str:
    return value if value in THINKING_BUDGET_LEVELS else "standard"


def normalize_quality_budget(value: str | None) -> str:
    return value if value in QUALITY_BUDGET_LEVELS else "standard"


def _apply_execution_budgets(
    dag_json: dict,
    thinking_budget: str,
    quality_budget: str,
) -> dict:
    thinking = THINKING_BUDGET_PROFILES[normalize_thinking_budget(thinking_budget)]
    quality = QUALITY_BUDGET_PROFILES[normalize_quality_budget(quality_budget)]
    nodes = dag_json.get("nodes") or []
    agent_nodes = [
        node for node in nodes
        if str(node.get("node_type") or (node.get("config") or {}).get("type") or "agent").lower()
        == "agent"
    ]
    node_count = max(1, len(agent_nodes))
    cycle_turns = int(thinking["cycle_turns"])
    for node in agent_nodes:
        config = node.setdefault("config", {})
        depends_on = node.get("depends_on") or []
        upstream_count = max(1, len(depends_on))
        if thinking["max_turns"] is None:
            dynamic = min(150, 30 + 8 * upstream_count)
            node_turns = min(dynamic, max(4, -(-cycle_turns // node_count)))
        else:
            node_turns = min(
                int(thinking["max_turns"]),
                max(4, -(-cycle_turns // node_count)),
            )
        config["thinking_budget"] = normalize_thinking_budget(thinking_budget)
        config["quality_budget"] = normalize_quality_budget(quality_budget)
        config["max_turns"] = node_turns
        config["max_iterations"] = int(thinking["max_iterations"])
        config["verification_mode"] = quality["verification_mode"]
        assessment = config.setdefault("objective_assessment", {})
        assessment["retry_on_fail"] = bool(quality["retry_on_fail"])
        gate = config.setdefault("deliverable_gate", {})
        gate["enabled"] = quality["verification_mode"] != "off"
        if quality["agent"]:
            config["quality_instructions"] = quality["agent"]
    dag_json["execution_profile"] = {
        "thinking_budget": normalize_thinking_budget(thinking_budget),
        "quality_budget": normalize_quality_budget(quality_budget),
        "cycle_turn_budget": cycle_turns,
        "agent_nodes": len(agent_nodes),
    }
    return dag_json


def _agent_model(session: ExplorationSession) -> str:
    return session.model or _planning_defaults().get("agent_model") or "deepseek-v4-flash"


async def _create_cycle_dag(
    session: ExplorationSession,
    objective: str,
    cycle_number: int,
    db: AsyncSession,
    files: Optional[Iterable[Any]] = None,
    skill_ids: Optional[list[str]] = None,
    attachments: Optional[list[dict]] = None,
) -> MasterDAG:
    from routers.dags import (  # local import to avoid router import cycles
        _normalize_interactive_nodes,
        _normalize_node_skill_fields,
        _resolve_node_capabilities,
    )

    agent_model = _agent_model(session)
    if files:
        attachments = await save_attachments(session.workspace_id or "", files)

    thinking_budget = normalize_thinking_budget(session.thinking_budget)
    quality_budget = normalize_quality_budget(session.quality_budget)
    dag_json = await plan_dag(
        objective,
        _planning_model(),
        db,
        skill_ids=skill_ids,
        agent_model=agent_model,
        thinking_budget=thinking_budget,
        quality_budget=quality_budget,
    )
    usage_entries = dag_json.pop("_llm_usage", [])
    dag_json = _normalize_interactive_nodes(dag_json)
    await _resolve_node_capabilities(dag_json, db)
    dag_json = _apply_execution_budgets(dag_json, thinking_budget, quality_budget)
    if attachments:
        dag_json["input_attachments"] = attachments
        for node_def in dag_json.get("nodes", []):
            node_def.setdefault("config", {})["input_attachments"] = attachments

    dag_id = _gen_dag_id()
    dag = MasterDAG(
        id=dag_id,
        objective=objective,
        status=DAGStatus.READY,
        dag_json=dag_json,
        workspace_id=session.workspace_id,
        llm_model=agent_model,
        workflow_group_id=session.workflow_group_id,
        exploration_session_id=session.id,
        exploration_cycle=cycle_number,
        project_id=session.project_id,
    )
    db.add(dag)

    for node_def in dag_json.get("nodes", []):
        node_config = dict(node_def.get("config") or {})
        skill_id, selected_skill_v2_id, reason = _normalize_node_skill_fields(node_def, node_config)
        node_def["skill_id"] = skill_id
        if selected_skill_v2_id:
            node_def.setdefault("config", {})["selected_skill_v2_id"] = selected_skill_v2_id
        if reason:
            node_def.setdefault("config", {})["skill_selection_reason"] = reason
        node_type = node_def.get("node_type", "agent")
        if node_type not in ("agent", "decision", "input", "block"):
            node_type = "agent"
        db.add(DAGNode(
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
            skill_selection_reason=reason,
            node_type=node_type,
        ))

    await db.commit()
    await db.refresh(dag)
    for usage in usage_entries:
        db.add(LlmUsage(
            dag_id=dag.id,
            session_id=session.id,
            purpose="planning",
            provider=str(usage.get("provider") or ""),
            model=str(usage.get("model") or ""),
            attempt=int(usage.get("attempt") or 1),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            cached_input_tokens=int(usage.get("cached_input_tokens") or 0),
            reasoning_tokens=int(usage.get("reasoning_tokens") or 0),
            request_id=str(usage.get("request_id") or ""),
        ))
    if usage_entries:
        await db.commit()
    return dag


async def create_exploration_session(
    objective: str,
    model: Optional[str],
    project_id: Optional[str],
    db: AsyncSession,
    files: Optional[Iterable[Any]] = None,
    skill_ids: Optional[list[str]] = None,
    thinking_budget: str = "standard",
    quality_budget: str = "standard",
) -> tuple[ExplorationSession, MasterDAG, ExplorationCycle]:
    thinking_budget = normalize_thinking_budget(thinking_budget)
    quality_budget = normalize_quality_budget(quality_budget)
    if project_id == "__general__":
        project_id = None
    group = await ensure_workflow_group(
        objective,
        project_id,
        db,
        llm_model=model or _planning_defaults().get("agent_model") or "deepseek-v4-flash",
        mode="exploration",
        force_new=True,
    )
    session = ExplorationSession(
        id=_gen_session_id(),
        workflow_group_id=group.id,
        title=group.title,
        objective=objective,
        status="planning",
        model=model or _planning_defaults().get("agent_model") or "deepseek-v4-flash",
        thinking_budget=thinking_budget,
        quality_budget=quality_budget,
        project_id=project_id,
        workspace_id=f"workspace-{_gen_dag_id()}",
        current_cycle=0,
    )
    db.add(session)
    await db.flush()

    try:
        dag = await _create_cycle_dag(
            session, objective, 1, db, files=files, skill_ids=skill_ids
        )
    except Exception as exc:
        session.status = "error"
        db.add(ExplorationMessage(
            session_id=session.id,
            role="system",
            kind="error",
            content=f"Could not plan the first cycle: {exc}",
        ))
        await db.commit()
        raise

    cycle = ExplorationCycle(
        id=_gen_cycle_id(),
        session_id=session.id,
        cycle_number=1,
        dag_id=dag.id,
        status="proposed",
    )
    db.add(cycle)
    session.current_cycle = 1
    session.status = "awaiting_run"
    message = _proposal_message(session, cycle, dag)
    db.add(message)
    await db.commit()
    return session, dag, cycle


def _proposal_message(
    session: ExplorationSession,
    cycle: ExplorationCycle,
    dag: MasterDAG,
) -> ExplorationMessage:
    nodes = list((dag.dag_json or {}).get("nodes") or [])
    return ExplorationMessage(
        session_id=session.id,
        cycle_id=cycle.id,
        role="assistant",
        kind="cycle_proposal",
        content=f"Cycle {cycle.cycle_number} is ready for review.",
        metadata_json={
            "dag_id": dag.id,
            "cycle_number": cycle.cycle_number,
            "model": dag.llm_model,
            "node_count": len(nodes),
            "thinking_budget": session.thinking_budget or "standard",
            "quality_budget": session.quality_budget or "standard",
            "objective": dag.objective,
            "nodes": [
                {
                    "node_id": node.get("node_id"),
                    "node_type": node.get("node_type", "agent"),
                    "description": (node.get("description") or "")[:160],
                }
                for node in nodes
            ],
        },
    )


async def _cycle_result(db: AsyncSession, cycle: ExplorationCycle, dag: MasterDAG) -> dict[str, Any]:
    from routers.workflows import _review_maps, _run_output_blocker, _run_review_summary

    nodes_result = await db.execute(select(DAGNode).where(DAGNode.dag_id == dag.id))
    nodes = list(nodes_result.scalars().all())
    session_result = await db.execute(select(ExplorationSession).where(ExplorationSession.id == cycle.session_id))
    cycle_session = session_result.scalar_one_or_none()
    reviews_result = await db.execute(select(DeepReview).where(DeepReview.dag_id == dag.id))
    reviews_by_dag = _review_maps(list(reviews_result.scalars().all()))
    reviews = reviews_by_dag.get(dag.id, [])
    score, flagged, blocker, blocker_node = _run_review_summary(reviews)
    key_output, run_blocker, active_node = _run_output_blocker(nodes, reviews)
    deliverables: list[str] = []
    for node in nodes:
        output = node.output_data or {}
        keys = output.get("deliverables_keys")
        if not keys and isinstance(output.get("deliverables"), dict):
            keys = list(output.get("deliverables").keys())
        for key in keys or []:
            path = str(key).lstrip("/")
            if path.startswith("workspace/"):
                path = path[len("workspace/"):]
            if path and not path.startswith(f"{node.node_id}/"):
                path = f"{node.node_id}/{path}"
            if path and path not in deliverables:
                deliverables.append(path)
    duration = None
    if dag.started_at and dag.completed_at:
        try:
            duration = max(0, int((dag.completed_at - dag.started_at).total_seconds()))
        except Exception:
            duration = None
    task_ids = [node.task_id for node in nodes if node.task_id]
    turn_count = 0
    iteration_count = 0
    approval_wait_ms = 0
    if task_ids:
        stats = await db.execute(
            select(
                func.count(TaskOutput.id),
                func.coalesce(func.sum(TaskOutput.turn_count), 0),
                func.coalesce(func.sum(TaskOutput.approval_wait_ms), 0),
            ).where(TaskOutput.task_id.in_(task_ids))
        )
        iteration_count, turn_sum, approval_wait_ms = stats.one()
        turns = await db.execute(
            select(func.count(AgentTurn.id)).where(AgentTurn.task_id.in_(task_ids))
        )
        turn_count = max(int(turn_sum or 0), int(turns.scalar() or 0))
    return {
        "dag_id": dag.id,
        "cycle_number": cycle.cycle_number,
        "status": _status(dag.status),
        "score": score,
        "flagged": flagged,
        "duration_seconds": duration,
        "turn_count": turn_count,
        "iteration_count": int(iteration_count or 0),
        "approval_wait_seconds": round(float(approval_wait_ms or 0) / 1000, 1),
        "thinking_budget": (cycle_session.thinking_budget if cycle_session else None) or "standard",
        "quality_budget": (cycle_session.quality_budget if cycle_session else None) or "standard",
        "blocker": run_blocker or blocker,
        "node_id": active_node or blocker_node,
        "key_output": key_output,
        "deliverables": deliverables,
        "issue_count": sum(len(r.issues or []) for r in reviews),
    }


async def pending_capability_requests(dag_id: str, db: AsyncSession) -> list[CapabilityRequest]:
    """Pending capability approvals for tasks in one DAG."""
    nodes_result = await db.execute(
        select(DAGNode.task_id).where(
            DAGNode.dag_id == dag_id,
            DAGNode.task_id.is_not(None),
        )
    )
    task_ids = [row[0] for row in nodes_result.all() if row[0]]
    if not task_ids:
        return []
    result = await db.execute(
        select(CapabilityRequest)
        .where(
            CapabilityRequest.task_id.in_(task_ids),
            CapabilityRequest.status == RequestStatus.PENDING,
        )
        .order_by(CapabilityRequest.requested_at.asc())
    )
    return list(result.scalars().all())


async def reconcile_session(session: ExplorationSession, db: AsyncSession) -> ExplorationSession:
    if session.status == "finished":
        return session
    cycles_result = await db.execute(
        select(ExplorationCycle)
        .where(ExplorationCycle.session_id == session.id)
        .order_by(ExplorationCycle.cycle_number.desc())
    )
    cycles = list(cycles_result.scalars().all())
    if not cycles:
        return session

    cycle = cycles[0]
    dag_result = await db.execute(select(MasterDAG).where(MasterDAG.id == cycle.dag_id))
    dag = dag_result.scalar_one_or_none()
    if not dag:
        session.status = "error"
        await db.commit()
        return session

    pending_caps = await pending_capability_requests(dag.id, db)
    pending_result = await db.execute(
        select(DagUserRequest).where(
            DagUserRequest.dag_id == dag.id,
            DagUserRequest.status == "pending",
        )
    )
    pending = list(pending_result.scalars().all())
    if pending_caps:
        session.status = "awaiting_approval"
        cycle.status = "running"
    elif pending:
        session.status = "awaiting_input"
        cycle.status = "running"
    else:
        status = _status(dag.status)
        if status == "running":
            session.status = "running"
            cycle.status = "running"
            await db.execute(
                delete(ExplorationMessage).where(
                    ExplorationMessage.cycle_id == cycle.id,
                    ExplorationMessage.kind == "cycle_result",
                )
            )
        elif status == "ready":
            session.status = "awaiting_run"
            cycle.status = "proposed"
        else:
            session.status = "awaiting_feedback"
            if status in ("completed",):
                cycle.status = "completed"
            elif status in ("failed",):
                cycle.status = "failed"
            elif status in ("cancelled",):
                cycle.status = "cancelled"

    # Ensure input cards exist and reflect request status.
    if pending:
        existing = await db.execute(
            select(ExplorationMessage).where(
                ExplorationMessage.session_id == session.id,
                ExplorationMessage.kind == "cycle_input",
            )
        )
        existing_by_request = {
            (msg.metadata_json or {}).get("request_id"): msg
            for msg in existing.scalars().all()
        }
        for request in pending:
            if request.id in existing_by_request:
                continue
            db.add(ExplorationMessage(
                session_id=session.id,
                cycle_id=cycle.id,
                role="system",
                kind="cycle_input",
                content=request.prompt or f"Input required for {request.node_id}",
                metadata_json={
                    "request_id": request.id,
                    "dag_id": dag.id,
                    "node_id": request.node_id,
                    "input_kind": request.kind,
                    "prompt": request.prompt,
                    "payload": request.payload or {},
                    "status": "pending",
                },
            ))

    # Ensure exactly one result card for a terminal cycle.
    if session.status == "awaiting_feedback":
        existing = await db.execute(
            select(ExplorationMessage).where(
                ExplorationMessage.cycle_id == cycle.id,
                ExplorationMessage.kind == "cycle_result",
            )
        )
        if not existing.scalars().first():
            result = await _cycle_result(db, cycle, dag)
            db.add(ExplorationMessage(
                session_id=session.id,
                cycle_id=cycle.id,
                role="system",
                kind="cycle_result",
                content=f"Cycle {cycle.cycle_number} finished: {result['status']}",
                metadata_json=result,
            ))

    session.last_activity_at = datetime.utcnow()
    await db.commit()
    await db.refresh(session)
    return session


async def _exploration_state_lines(
    session: ExplorationSession,
    cycles: list[ExplorationCycle],
    db: AsyncSession,
) -> list[str]:
    lines: list[str] = []
    try:
        from explorations_deploy import detect_cycle_deployables
        apps = await detect_cycle_deployables(session, db)
        for app in apps:
            lines.append(
                "Current app candidate: "
                f"{app.app_path} ({app.framework}, {app.entrypoint}, port {app.port}, "
                f"cycle {app.cycle_number}, node {app.source_node or 'unknown'})"
            )
    except Exception as exc:
        logger.debug("Could not build app state for planning: %s", exc)

    task_ids: set[str] = set()
    if cycles:
        nodes_result = await db.execute(
            select(DAGNode).where(DAGNode.dag_id.in_([cycle.dag_id for cycle in cycles]))
        )
        task_ids = {node.task_id for node in nodes_result.scalars().all() if node.task_id}
    if task_ids:
        deployments_result = await db.execute(
            select(Deployment)
            .where(Deployment.task_id.in_(task_ids))
            .order_by(Deployment.created_at.desc())
            .limit(10)
        )
        for deployment in deployments_result.scalars().all():
            error = (deployment.error or "").replace("\n", " ")[:240]
            lines.append(
                "Deployment trail: "
                f"{deployment.id} task={deployment.task_id} status={getattr(deployment.status, 'value', deployment.status)} "
                f"entrypoint={deployment.entrypoint or 'none'} port={deployment.port or 'none'}"
                + (f" error={error}" if error else "")
            )
    return lines


async def plan_next_cycle(
    session: ExplorationSession,
    feedback: str,
    db: AsyncSession,
    source_feedback: Optional[str] = None,
) -> tuple[MasterDAG, ExplorationCycle]:
    if session.status not in ("awaiting_feedback", "error"):
        raise ValueError("Exploration is not waiting for feedback")
    if session.current_cycle >= MAX_EXPLORATION_CYCLES:
        raise ValueError(f"Exploration reached the {MAX_EXPLORATION_CYCLES}-cycle limit")

    cycles_result = await db.execute(
        select(ExplorationCycle)
        .where(ExplorationCycle.session_id == session.id)
        .order_by(ExplorationCycle.cycle_number.asc())
    )
    cycles = list(cycles_result.scalars().all())
    current = cycles[-1] if cycles else None
    if current and not current.finalized_at:
        current.feedback = feedback
        current.source_feedback = source_feedback or current.source_feedback
        current.status = "finalized"
        current.finalized_at = datetime.utcnow()

    context_parts = []
    for cycle in cycles:
        dag_result = await db.execute(select(MasterDAG).where(MasterDAG.id == cycle.dag_id))
        dag = dag_result.scalar_one_or_none()
        if not dag:
            continue
        result = await _cycle_result(db, cycle, dag)
        raw_feedback = (cycle.source_feedback or "").strip()
        context_parts.append(
            f"- Cycle {cycle.cycle_number}: status={result['status']}, "
            f"score={result['score']}, outputs={', '.join(result['deliverables']) or 'none'}, "
            f"blocker={(result['blocker'] or 'none')[:300]}"
            + (f", user_feedback={raw_feedback[:800]}" if raw_feedback else "")
        )
        node_result = await db.execute(select(DAGNode).where(DAGNode.dag_id == dag.id))
        for node in node_result.scalars().all():
            context_parts.append(
                f"  - node {node.node_id} ({node.node_type}): "
                f"{(node.description or '')[:180]}"
            )

    context_parts.extend(await _exploration_state_lines(session, cycles, db))
    next_cycle = (current.cycle_number + 1) if current else 1
    objective = (
        f"{session.objective}\n\n"
        "EXPLORATION REPLANNING CONTEXT:\n"
        "This is a continuous exploration. The cycles below are finalized and "
        "their outputs are already in the shared workspace. Do not redo finalized "
        "work; build on those deliverables and use them as inputs where useful.\n"
        + ("\n".join(context_parts) if context_parts else "- No finalized cycles yet.")
        + f"\n\nUSER FEEDBACK FOR THE NEXT CYCLE:\n{feedback.strip()}\n\n"
        f"Plan only cycle {next_cycle} of this exploration. Keep the plan focused "
        "on the next advance; the user will review and run it."
    )

    dag = await _create_cycle_dag(session, objective, next_cycle, db)
    cycle = ExplorationCycle(
        id=_gen_cycle_id(),
        session_id=session.id,
        cycle_number=next_cycle,
        dag_id=dag.id,
        status="proposed",
    )
    db.add(cycle)
    session.current_cycle = next_cycle
    session.status = "awaiting_run"
    db.add(_proposal_message(session, cycle, dag))
    await db.commit()
    return dag, cycle


async def build_chat_context(session: ExplorationSession, db: AsyncSession) -> list[dict[str, Any]]:
    cycles_result = await db.execute(
        select(ExplorationCycle)
        .where(ExplorationCycle.session_id == session.id)
        .order_by(ExplorationCycle.cycle_number.asc())
    )
    cycles = list(cycles_result.scalars().all())
    cycle_lines = []
    for cycle in cycles:
        dag_result = await db.execute(select(MasterDAG).where(MasterDAG.id == cycle.dag_id))
        dag = dag_result.scalar_one_or_none()
        if not dag:
            continue
        if str(getattr(cycle.status, "value", cycle.status)) in ("proposed", "running"):
            continue
        result = await _cycle_result(db, cycle, dag)
        cycle_lines.append(
            f"Cycle {cycle.cycle_number}: {result['status']}, score={result['score']}, "
            f"deliverables={', '.join(result['deliverables']) or 'none'}, "
            f"blocker={result['blocker'] or 'none'}"
        )
    current_cycle = cycles[-1] if cycles else None
    plan_status = ""
    if session.status == "awaiting_run" and current_cycle:
        plan_status = (
            f"\nCurrent plan: Cycle {current_cycle.cycle_number} is planned but NOT run. "
            "It has no deliverables or score until the user runs it. Do not describe it "
            "as completed or finalized, and do not offer to prepare a next cycle."
        )
    elif session.status == "running" and current_cycle:
        plan_status = (
            f"\nCurrent cycle: Cycle {current_cycle.cycle_number} is running. "
            "There are no final deliverables yet."
        )
    system = (
        "You are the TaskForge exploration copilot. You help the user reason about "
        "exploration outcomes, deliverables, blockers, and next steps. You never "
        "execute workflows or claim to have run a cycle. You may discuss freely. "
        "Use propose_exploration_cycle only when the user gives an actionable change "
        "or next-cycle instruction and the session is awaiting feedback (or error). "
        "Use queue_exploration_feedback only for actionable feedback while a cycle is "
        "running or planned. Do not call a cycle tool for questions, explanations, "
        "status, or finalization. Answers about existing work should be conversation. "
        "A proposed/ready cycle is a plan, not a result. If the user asks whether it "
        "is done or finalized while awaiting_run, say it has not run, point to the "
        "Run cycle action, and do not offer to mark it done or plan a next cycle. "
        "Finalization is only via the explicit Finish control. If intent is ambiguous, "
        "answer and ask one clarifying question; do not create or queue a cycle. "
        "The proposal tool creates a ready DAG; the user must review and press Run. "
        "Never claim that a proposal exists until the tool call succeeds. Be concise, "
        "reference deliverables by name, and do not reveal internal reasoning.\n\n"
        f"Exploration objective: {session.objective}\n"
        f"Session status: {session.status}\n"
        f"Current cycle: {session.current_cycle}\n"
        f"Cycle history:\n" + ("\n".join(cycle_lines) if cycle_lines else "No cycles yet.")
        + plan_status
        + ("\n\n" + "\n".join(await _exploration_state_lines(session, cycles, db)) if cycles else "")
    )
    if cycles:
        pending_caps = await pending_capability_requests(cycles[-1].dag_id, db)
        if pending_caps:
            system += "\nPending approvals: " + ", ".join(
                f"{getattr(cap.capability_type, 'value', cap.capability_type)} {cap.resource_name}"
                for cap in pending_caps
            )
    messages_result = await db.execute(
        select(ExplorationMessage)
        .where(
            ExplorationMessage.session_id == session.id,
            ExplorationMessage.kind == "text",
        )
        .order_by(ExplorationMessage.created_at.asc())
    )
    history = [
        {"role": msg.role, "content": msg.content}
        for msg in messages_result.scalars().all()
        if msg.role in ("user", "assistant") and msg.content
    ][-20:]
    return [{"role": "system", "content": system}] + history


async def finish_session(session: ExplorationSession, db: AsyncSession) -> None:
    session.status = "finished"
    session.last_activity_at = datetime.utcnow()
    await db.commit()
