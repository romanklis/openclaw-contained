"""Exploration session orchestration: cycles, replanning, and reconciliation."""
import logging
import uuid
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from attachments import save_attachments
from models import (
    DAGNode,
    DagUserRequest,
    DeepReview,
    ExplorationCycle,
    ExplorationMessage,
    DAGStatus,
    ExplorationSession,
    MasterDAG,
    NodeStatus,
    TaskOutput,
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

    dag_json = await plan_dag(
        objective,
        _planning_model(),
        db,
        skill_ids=skill_ids,
        agent_model=agent_model,
    )
    dag_json = _normalize_interactive_nodes(dag_json)
    await _resolve_node_capabilities(dag_json, db)
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
    return dag


async def create_exploration_session(
    objective: str,
    model: Optional[str],
    project_id: Optional[str],
    db: AsyncSession,
    files: Optional[Iterable[Any]] = None,
    skill_ids: Optional[list[str]] = None,
) -> tuple[ExplorationSession, MasterDAG, ExplorationCycle]:
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
    return {
        "dag_id": dag.id,
        "cycle_number": cycle.cycle_number,
        "status": _status(dag.status),
        "score": score,
        "flagged": flagged,
        "duration_seconds": duration,
        "blocker": run_blocker or blocker,
        "node_id": active_node or blocker_node,
        "key_output": key_output,
        "deliverables": deliverables,
        "issue_count": sum(len(r.issues or []) for r in reviews),
    }


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

    pending_result = await db.execute(
        select(DagUserRequest).where(
            DagUserRequest.dag_id == dag.id,
            DagUserRequest.status == "pending",
        )
    )
    pending = list(pending_result.scalars().all())
    if pending:
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


async def plan_next_cycle(
    session: ExplorationSession,
    feedback: str,
    db: AsyncSession,
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
        current.status = "finalized"
        current.finalized_at = datetime.utcnow()

    context_parts = []
    for cycle in cycles:
        dag_result = await db.execute(select(MasterDAG).where(MasterDAG.id == cycle.dag_id))
        dag = dag_result.scalar_one_or_none()
        if not dag:
            continue
        result = await _cycle_result(db, cycle, dag)
        context_parts.append(
            f"- Cycle {cycle.cycle_number}: status={result['status']}, "
            f"score={result['score']}, outputs={', '.join(result['deliverables'][:10]) or 'none'}, "
            f"blocker={(result['blocker'] or 'none')[:300]}, feedback={(cycle.feedback or 'none')[:500]}"
        )

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
        result = await _cycle_result(db, cycle, dag)
        cycle_lines.append(
            f"Cycle {cycle.cycle_number}: {result['status']}, score={result['score']}, "
            f"deliverables={', '.join(result['deliverables'][:10]) or 'none'}, "
            f"blocker={result['blocker'] or 'none'}"
        )
    system = (
        "You are the TaskForge exploration copilot. You help the user reason about "
        "exploration outcomes, deliverables, blockers, and next steps. You never "
        "execute workflows or claim to have run a cycle. In chat mode you can only "
        "converse. In feedback mode the backend may use your "
        "propose_exploration_cycle tool to prepare the next ready DAG; the user "
        "must review and press Run. Be concise, reference deliverables by name, "
        "and ask for clarification when the next step is ambiguous. Do not reveal "
        "internal reasoning or planning narration; speak directly to the user.\n\n"
        f"Exploration objective: {session.objective}\n"
        f"Session status: {session.status}\n"
        f"Current cycle: {session.current_cycle}\n"
        f"Cycle history:\n" + ("\n".join(cycle_lines) if cycle_lines else "No cycles yet.")
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
