"""Workflow-group APIs: grouped run history, maturity, and determinism."""
import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session, get_db
from models import DAGNode, DeepReview, ExplorationSession, MasterDAG, WorkflowGroup
from workflow_groups import (
    derive_title,
    generate_workflow_titles_batch,
    is_suspicious_title,
    safe_display_title,
)
from schemas import (
    WorkflowDetail,
    WorkflowNodeChip,
    WorkflowNodeCounts,
    WorkflowMergeRequest,
    WorkflowRunSummary,
    WorkflowSummary,
)
from workflow_regroup import apply_regroup_proposals, build_regroup_proposals

logger = logging.getLogger(__name__)
router = APIRouter()

EXECUTABLE_TYPES = {"agent", "block"}
_TOKEN_CACHE: dict[str, tuple[float, dict[str, int]]] = {}
_TOKEN_TTL_SECONDS = 600


def _status(value: Any) -> str:
    return getattr(value, "value", value) or "unknown"


def _seconds(start: Optional[datetime], end: Optional[datetime]) -> Optional[int]:
    if not start or not end:
        return None
    try:
        return max(0, int((end - start).total_seconds()))
    except Exception:
        try:
            return max(0, int(end.timestamp() - start.timestamp()))
        except Exception:
            return None


def _review_maps(reviews: list[DeepReview]):
    """Latest review per (dag_id, node_id), plus per-DAG aggregates."""
    latest_by_key: dict[tuple[str, str], DeepReview] = {}
    for review in reviews:
        if not review.dag_id:
            continue
        key = (review.dag_id, review.node_id)
        current = latest_by_key.get(key)
        if current is None or (review.created_at or datetime.min) > (current.created_at or datetime.min):
            latest_by_key[key] = review

    by_dag: dict[str, list[DeepReview]] = defaultdict(list)
    for (dag_id, _), review in latest_by_key.items():
        by_dag[dag_id].append(review)

    return by_dag


def _run_review_summary(reviews: list[DeepReview]) -> tuple[Optional[int], bool, Optional[str], Optional[str]]:
    """Return (average score, flagged, top blocker text, top issue node)."""
    if not reviews:
        return None, False, None, None
    scores = [int(review.score) for review in reviews if review.score is not None]
    score = round(sum(scores) / len(scores)) if scores else None
    flagged = any(
        str(review.verdict or "").lower() != "clean" or bool(review.issues)
        for review in reviews
    )

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    top: tuple[int, Any, str] | None = None
    for review in reviews:
        for issue in review.issues or []:
            if not isinstance(issue, dict):
                continue
            rank = severity_rank.get(str(issue.get("severity") or "").lower(), 3)
            if top is None or rank < top[0]:
                top = (rank, issue, review.node_id)
    blocker = None
    blocker_node = None
    if top:
        blocker = str(top[1].get("finding") or top[1].get("recommendation") or "").strip() or None
        blocker_node = top[2]
    return score, flagged, blocker, blocker_node


def _topological_nodes(nodes: list[DAGNode]) -> list[DAGNode]:
    by_id = {node.node_id: node for node in nodes}
    indegree = {node.node_id: 0 for node in nodes}
    children: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        for dep in node.depends_on or []:
            if dep in by_id:
                indegree[node.node_id] += 1
                children[dep].append(node.node_id)
    queue = [node.node_id for node in nodes if indegree[node.node_id] == 0]
    ordered: list[DAGNode] = []
    while queue:
        node_id = queue.pop(0)
        ordered.append(by_id[node_id])
        for child in children.get(node_id, []):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    seen = {node.node_id for node in ordered}
    ordered.extend(node for node in nodes if node.node_id not in seen)
    return ordered


def _node_label(node: DAGNode) -> str:
    node_type = str(node.node_type or "agent")
    config = node.config or {}
    if node_type == "block":
        return str(config.get("block") or node.node_id)
    description = str(node.description or "").strip()
    if description:
        return description[:44] + ("…" if len(description) > 44 else "")
    return node.node_id


def _key_output(nodes: list[DAGNode]) -> Optional[str]:
    for node in nodes:
        output = node.output_data or {}
        keys = output.get("deliverables_keys")
        if not keys and isinstance(output.get("deliverables"), dict):
            keys = list(output.get("deliverables").keys())
        for key in keys or []:
            if key:
                return str(key).split("/")[-1]
    return None


def _run_output_blocker(
    nodes: list[DAGNode],
    reviews: list[DeepReview],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    _, _, blocker, blocker_node = _run_review_summary(reviews)
    failed_node = next(
        (node for node in nodes if _status(node.status) == "failed"),
        None,
    )
    blocker = blocker or (
        str(failed_node.description or failed_node.node_id) if failed_node else None
    )
    active_node = blocker_node or (failed_node.node_id if failed_node else None)
    return _key_output(nodes), blocker, active_node


def _run_summary(
    dag: MasterDAG,
    number: int,
    reviews: list[DeepReview],
    nodes: list[DAGNode],
) -> WorkflowRunSummary:
    score, flagged, _, _ = _run_review_summary(reviews)
    output, blocker, _ = _run_output_blocker(nodes, reviews)
    end = dag.completed_at or (datetime.utcnow() if _status(dag.status) == "running" else None)
    return WorkflowRunSummary(
        id=dag.id,
        status=_status(dag.status),
        created_at=dag.created_at,
        completed_at=dag.completed_at,
        duration_seconds=_seconds(dag.started_at, end),
        run_number=number,
        locked=bool(dag.locked),
        archived=bool(dag.archived),
        score=score,
        flagged=flagged,
        key_output=output,
        blocker=blocker,
    )


def _build_summary(
    group: WorkflowGroup,
    runs: list[MasterDAG],
    nodes_by_dag: dict[str, list[DAGNode]],
    reviews_by_dag: dict[str, list[DeepReview]],
    archived_view: bool,
) -> Optional[WorkflowSummary]:
    ordered = sorted(runs, key=lambda dag: dag.created_at or datetime.min)
    visible = [dag for dag in ordered if bool(dag.archived) == archived_view]
    if not visible:
        return None

    latest = visible[-1]
    latest_nodes = _topological_nodes(nodes_by_dag.get(latest.id, []))
    executable = [node for node in latest_nodes if str(node.node_type or "agent") in EXECUTABLE_TYPES]
    block_count = sum(1 for node in executable if str(node.node_type or "") == "block")
    determinism = round((block_count / len(executable)) * 100) if executable else 0
    locked = any(bool(dag.locked) for dag in ordered)

    latest_reviews = reviews_by_dag.get(latest.id, [])
    latest_score, _, _, _ = _run_review_summary(latest_reviews)
    key_output, blocker, active_node = _run_output_blocker(latest_nodes, latest_reviews)

    latest_terminal = [
        dag for dag in visible
        if _status(dag.status) in ("completed", "failed")
    ][-5:]
    completed_terminal = sum(1 for dag in latest_terminal if _status(dag.status) == "completed")
    reliability = (
        round((completed_terminal / len(latest_terminal)) * 100)
        if latest_terminal else None
    )
    completed_runs = [dag for dag in visible if _status(dag.status) == "completed"]
    durations = [
        value for value in (
            _seconds(dag.started_at, dag.completed_at) for dag in completed_runs
        )
        if value is not None
    ]
    avg_runtime = round(sum(durations) / len(durations)) if durations else None

    flagged_nodes = {
        review.node_id for review in latest_reviews
        if str(review.verdict or "").lower() != "clean" or bool(review.issues)
    }
    node_counts = WorkflowNodeCounts(
        total=len(latest_nodes),
        completed=sum(1 for node in latest_nodes if _status(node.status) == "completed"),
        failed=sum(1 for node in latest_nodes if _status(node.status) == "failed"),
        flagged=sum(1 for node in latest_nodes if node.node_id in flagged_nodes),
    )

    number_by_id = {dag.id: index + 1 for index, dag in enumerate(ordered)}
    recent = list(reversed(visible[-5:]))
    run_summaries = [
        _run_summary(
            dag,
            number_by_id[dag.id],
            reviews_by_dag.get(dag.id, []),
            nodes_by_dag.get(dag.id, []),
        )
        for dag in recent
    ]
    latest_summary = _run_summary(
        latest,
        number_by_id[latest.id],
        latest_reviews,
        nodes_by_dag.get(latest.id, []),
    )

    updated_at = max(
        (
            dag.completed_at or dag.started_at or dag.created_at
            for dag in ordered
            if dag.completed_at or dag.started_at or dag.created_at
        ),
        default=group.created_at,
    )

    return WorkflowSummary(
        id=group.id,
        title=safe_display_title(
            group.title, group.objective, getattr(group, "title_source", None)
        ),
        objective=group.objective,
        project_id=group.project_id,
        mode=getattr(group, "mode", "experiment") or "experiment",
        created_at=group.created_at,
        updated_at=updated_at,
        stage="hardened" if block_count > 0 else ("locked" if locked else "experimenting"),
        locked=locked,
        determinism_pct=determinism,
        block_count=block_count,
        step_count=len(executable),
        run_count=len(ordered),
        completed_count=sum(1 for dag in ordered if _status(dag.status) == "completed"),
        failed_count=sum(1 for dag in ordered if _status(dag.status) == "failed"),
        running_count=sum(1 for dag in ordered if _status(dag.status) == "running"),
        latest_run=latest_summary,
        recent_runs=run_summaries,
        latest_score=latest_score,
        reliability_pct=reliability,
        avg_runtime_seconds=avg_runtime,
        latest_nodes=[
            WorkflowNodeChip(
                node_id=node.node_id,
                node_type=str(node.node_type or "agent"),
                label=_node_label(node),
                status=_status(node.status),
            )
            for node in latest_nodes
        ],
        latest_node_counts=node_counts,
        blocker=blocker,
        key_output=key_output,
        latest_active_node_id=active_node,
    )


async def _load_enhancements(groups: list[WorkflowGroup], db: AsyncSession):
    group_ids = [group.id for group in groups]
    if not group_ids:
        return {}, {}, {}
    runs_result = await db.execute(
        select(MasterDAG).where(MasterDAG.workflow_group_id.in_(group_ids))
    )
    runs = list(runs_result.scalars().all())
    runs_by_group: dict[str, list[MasterDAG]] = defaultdict(list)
    for dag in runs:
        runs_by_group[dag.workflow_group_id].append(dag)

    dag_ids = [dag.id for dag in runs]
    nodes_by_dag: dict[str, list[DAGNode]] = defaultdict(list)
    reviews_by_dag: dict[str, list[DeepReview]] = defaultdict(list)
    if dag_ids:
        nodes_result = await db.execute(select(DAGNode).where(DAGNode.dag_id.in_(dag_ids)))
        for node in nodes_result.scalars().all():
            nodes_by_dag[node.dag_id].append(node)
        reviews_result = await db.execute(select(DeepReview).where(DeepReview.dag_id.in_(dag_ids)))
        reviews_by_dag = _review_maps(list(reviews_result.scalars().all()))
    return runs_by_group, nodes_by_dag, reviews_by_dag


@router.get("", response_model=list[WorkflowSummary])
async def list_workflows(
    archived: bool = False,
    project_id: Optional[str] = None,
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    query = select(WorkflowGroup)
    if project_id == "__general__":
        query = query.where(WorkflowGroup.project_id.is_(None))
    elif project_id:
        query = query.where(WorkflowGroup.project_id == project_id)
    result = await db.execute(query)
    groups = list(result.scalars().all())
    runs_by_group, nodes_by_dag, reviews_by_dag = await _load_enhancements(groups, db)

    summaries = []
    for group in groups:
        summary = _build_summary(
            group,
            runs_by_group.get(group.id, []),
            nodes_by_dag,
            reviews_by_dag,
            archived_view=archived,
        )
        if summary:
            summaries.append(summary)
    if summaries:
        session_result = await db.execute(
            select(ExplorationSession).where(
                ExplorationSession.workflow_group_id.in_([item.id for item in summaries])
            )
        )
        session_by_group = {
            session.workflow_group_id: session.id
            for session in session_result.scalars().all()
        }
        for summary in summaries:
            summary.exploration_session_id = session_by_group.get(summary.id)
    summaries.sort(key=lambda item: item.updated_at or datetime.min, reverse=True)
    return summaries[:limit]


@router.post("/regenerate-titles")
async def regenerate_workflow_titles(
    limit: int = Query(50, ge=1, le=500),
    archived: bool = False,
    model: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Backfill clean LLM titles for legacy workflow groups."""
    if not model:
        try:
            from routers import dags as dags_module
            model = dags_module._dag_model_defaults.get("planning_model")
        except Exception:
            model = None

    result = await db.execute(
        select(WorkflowGroup)
        .where((WorkflowGroup.title_source.is_(None)) | (~WorkflowGroup.title_source.in_(["llm", "manual"])))
        .order_by(WorkflowGroup.created_at.asc())
    )
    candidates = list(result.scalars().all())
    if not candidates:
        return {"updated": 0, "failed": 0, "remaining": 0}

    group_ids = [group.id for group in candidates]
    runs_result = await db.execute(
        select(MasterDAG.workflow_group_id, MasterDAG.archived).where(
            MasterDAG.workflow_group_id.in_(group_ids)
        )
    )
    archived_by_group: dict[str, bool] = defaultdict(bool)
    active_by_group: dict[str, bool] = defaultdict(bool)
    for group_id, is_archived in runs_result.all():
        if is_archived:
            archived_by_group[group_id] = True
        else:
            active_by_group[group_id] = True

    selected = [
        group for group in candidates
        if (archived_by_group.get(group.id) if archived else active_by_group.get(group.id))
    ][:limit]

    updated = 0
    for start in range(0, len(selected), 10):
        batch = selected[start : start + 10]
        titles = await generate_workflow_titles_batch(batch, model)
        for group in batch:
            title = titles.get(group.id, derive_title(group.objective))
            if title and not is_suspicious_title(title) and title != derive_title(group.objective):
                group.title = title
                group.title_source = "llm"
                updated += 1
            else:
                group.title = derive_title(group.objective)
                group.title_source = "derived"
    await db.commit()

    remaining_result = await db.execute(
        select(WorkflowGroup).where(
            (WorkflowGroup.title_source.is_(None)) | (~WorkflowGroup.title_source.in_(["llm", "manual"]))
        )
    )
    remaining = len(list(remaining_result.scalars().all()))
    return {"updated": updated, "failed": len(selected) - updated, "remaining": remaining}


@router.post("/regroup")
async def regroup_workflows(
    dry_run: bool = True,
    include_archived: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Merge legacy duplicate groups using curated project-scoped rules."""
    proposals = await build_regroup_proposals(db, include_archived=include_archived)
    if dry_run:
        return {"dry_run": True, "proposals": proposals}
    result = await apply_regroup_proposals(db, proposals)
    return {"dry_run": False, "proposals": proposals, **result}


@router.post("/{target_id}/merge", response_model=WorkflowSummary)
async def merge_workflow(
    target_id: str,
    body: WorkflowMergeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Move all runs from another workflow group into this target group."""
    if target_id == body.source_id:
        raise HTTPException(status_code=400, detail="Cannot merge a workflow into itself")

    target_result = await db.execute(
        select(WorkflowGroup).where(WorkflowGroup.id == target_id)
    )
    target = target_result.scalar_one_or_none()
    source_result = await db.execute(
        select(WorkflowGroup).where(WorkflowGroup.id == body.source_id)
    )
    source = source_result.scalar_one_or_none()
    if not target or not source:
        raise HTTPException(status_code=404, detail="Workflow group not found")
    if target.project_id != source.project_id and not body.allow_cross_project:
        raise HTTPException(
            status_code=400,
            detail="Cross-project merge requires allow_cross_project=true",
        )

    await db.execute(
        update(MasterDAG)
        .where(MasterDAG.workflow_group_id == source.id)
        .values(workflow_group_id=target.id)
    )
    await db.delete(source)
    await db.commit()

    runs_by_group, nodes_by_dag, reviews_by_dag = await _load_enhancements([target], db)
    runs = runs_by_group.get(target.id, [])
    summary = _build_summary(target, runs, nodes_by_dag, reviews_by_dag, archived_view=False)
    if summary is None:
        summary = _build_summary(target, runs, nodes_by_dag, reviews_by_dag, archived_view=True)
    if summary is None:
        raise HTTPException(status_code=404, detail="Workflow has no runs")
    return summary


@router.get("/{group_id}/run-tokens")
async def get_run_tokens(group_id: str, db: AsyncSession = Depends(get_db)):
    """Lazily aggregate token usage per run, cached for 10 minutes."""
    group_result = await db.execute(
        select(WorkflowGroup).where(WorkflowGroup.id == group_id)
    )
    if not group_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Workflow group not found")

    runs_result = await db.execute(
        select(MasterDAG.id).where(MasterDAG.workflow_group_id == group_id)
    )
    run_ids = [row[0] for row in runs_result.all()]
    if not run_ids:
        return {}

    nodes_result = await db.execute(
        select(DAGNode.dag_id, DAGNode.task_id).where(DAGNode.dag_id.in_(run_ids))
    )
    task_by_dag: dict[str, str] = {}
    for dag_id, task_id in nodes_result.all():
        if task_id and dag_id not in task_by_dag:
            task_by_dag[dag_id] = task_id

    semaphore = asyncio.Semaphore(3)

    async def one_run(dag_id: str) -> tuple[str, Optional[dict[str, int]]]:
        cached = _TOKEN_CACHE.get(dag_id)
        if cached and (time.time() - cached[0]) < _TOKEN_TTL_SECONDS:
            return dag_id, cached[1]
        task_id = task_by_dag.get(dag_id)
        if not task_id:
            return dag_id, None
        async with semaphore:
            try:
                from routers.tasks_extended import get_audit_turns

                async with async_session() as session:
                    data = await asyncio.wait_for(
                        get_audit_turns(task_id, session),
                        timeout=20,
                    )
                totals = {
                    "total": int(data.get("total_input_tokens", 0))
                    + int(data.get("total_output_tokens", 0)),
                    "input": int(data.get("total_input_tokens", 0)),
                    "output": int(data.get("total_output_tokens", 0)),
                }
                _TOKEN_CACHE[dag_id] = (time.time(), totals)
                return dag_id, totals
            except Exception as exc:
                logger.info("Token aggregation failed for %s: %s", dag_id, exc)
                return dag_id, None

    results = await asyncio.gather(*(one_run(run_id) for run_id in run_ids))
    return {run_id: totals for run_id, totals in results}


@router.get("/{group_id}", response_model=WorkflowDetail)
async def get_workflow(group_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(WorkflowGroup).where(WorkflowGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Workflow group not found")

    runs_by_group, nodes_by_dag, reviews_by_dag = await _load_enhancements([group], db)
    runs = runs_by_group.get(group.id, [])
    summary = _build_summary(group, runs, nodes_by_dag, reviews_by_dag, archived_view=False)
    if summary is None:
        summary = _build_summary(group, runs, nodes_by_dag, reviews_by_dag, archived_view=True)
    if summary is None:
        raise HTTPException(status_code=404, detail="Workflow has no runs")

    session_result = await db.execute(
        select(ExplorationSession).where(ExplorationSession.workflow_group_id == group.id)
    )
    session = session_result.scalar_one_or_none()
    summary.exploration_session_id = session.id if session else None

    ordered = sorted(runs, key=lambda dag: dag.created_at or datetime.min)
    number_by_id = {dag.id: index + 1 for index, dag in enumerate(ordered)}
    run_summaries = [
        _run_summary(
            dag,
            number_by_id[dag.id],
            reviews_by_dag.get(dag.id, []),
            nodes_by_dag.get(dag.id, []),
        )
        for dag in reversed(ordered)
    ]
    return WorkflowDetail(**summary.model_dump(), runs=run_summaries)
