"""Durable DAG token-usage aggregation."""
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AgentTurn, DAGNode, LlmUsage, MasterDAG, ModelPrice, Task

AGENT_BUCKET = "agent"
ORCHESTRATION = "orchestration"


def _empty() -> dict[str, Any]:
    return {
        "input": 0,
        "output": 0,
        "total": 0,
        "cached_input": 0,
        "reasoning": 0,
        "calls": 0,
        "turns": 0,
        "cost_usd": None,
    }


def _add(bucket: dict[str, Any], *, input_tokens: int = 0, output_tokens: int = 0,
         total_tokens: int = 0, cached: int = 0, reasoning: int = 0,
         calls: int = 1, turns: int = 0) -> None:
    bucket["input"] += int(input_tokens or 0)
    bucket["output"] += int(output_tokens or 0)
    bucket["total"] += int(total_tokens or (int(input_tokens or 0) + int(output_tokens or 0)))
    bucket["cached_input"] += int(cached or 0)
    bucket["reasoning"] += int(reasoning or 0)
    bucket["calls"] += int(calls or 0)
    bucket["turns"] += int(turns or 0)


async def get_dag_token_usage(dag_id: str, db: AsyncSession) -> dict[str, Any]:
    usage: dict[str, Any] = {
        "dag_id": dag_id,
        "total": _empty(),
        "buckets": {
            "planning": _empty(),
            "agent": _empty(),
            "gate_triage": _empty(),
            "deep_review": _empty(),
            "other_dag": _empty(),
            "orchestration": _empty(),
        },
        "by_model": [],
        "by_node": [],
        "coverage": "none",
    }

    nodes_result = await db.execute(select(DAGNode).where(DAGNode.dag_id == dag_id))
    nodes = list(nodes_result.scalars().all())
    task_to_node = {node.task_id: node.node_id for node in nodes if node.task_id}
    task_ids = list(task_to_node.keys())
    task_models: dict[str, str] = {}
    if task_ids:
        task_rows = await db.execute(select(Task).where(Task.id.in_(task_ids)))
        task_models = {task.id: task.llm_model for task in task_rows.scalars().all() if task.llm_model}

    agent_by_model: dict[str, dict[str, Any]] = {}
    if task_ids:
        turns_result = await db.execute(
            select(AgentTurn).where(AgentTurn.task_id.in_(task_ids))
        )
        for turn in turns_result.scalars().all():
            total = int(turn.total_tokens or 0) or (int(turn.input_tokens or 0) + int(turn.output_tokens or 0))
            _add(
                usage["buckets"]["agent"],
                input_tokens=turn.input_tokens or 0,
                output_tokens=turn.output_tokens or 0,
                total_tokens=total,
                calls=1,
                turns=1,
            )
            model_name = turn.model or task_models.get(turn.task_id) or "unknown"
            key = f"{turn.provider or 'unknown'}/{model_name}"
            agent_by_model.setdefault(key, _empty())
            _add(agent_by_model[key], input_tokens=turn.input_tokens or 0,
                 output_tokens=turn.output_tokens or 0, total_tokens=total, calls=1, turns=1)

    usage_rows_result = await db.execute(
        select(LlmUsage).where(LlmUsage.dag_id == dag_id)
    )
    usage_rows = list(usage_rows_result.scalars().all())
    for row in usage_rows:
        purpose = row.purpose or "other_dag"
        bucket = usage["buckets"].get(purpose) or usage["buckets"]["other_dag"]
        _add(
            bucket,
            input_tokens=row.input_tokens or 0,
            output_tokens=row.output_tokens or 0,
            total_tokens=row.total_tokens or 0,
            cached=row.cached_input_tokens or 0,
            reasoning=row.reasoning_tokens or 0,
        )

    # DAG total excludes ordinary orchestration/chat usage.
    for key, bucket in usage["buckets"].items():
        if key == ORCHESTRATION:
            continue
        _add(
            usage["total"],
            input_tokens=bucket["input"],
            output_tokens=bucket["output"],
            total_tokens=bucket["total"],
            cached=bucket["cached_input"],
            reasoning=bucket["reasoning"],
            calls=bucket["calls"],
            turns=bucket["turns"],
        )

    model_usage: dict[str, dict[str, Any]] = {}
    for key, bucket in agent_by_model.items():
        model_usage[key] = bucket
    for row in usage_rows:
        key = f"{row.provider or 'unknown'}/{row.model or 'unknown'}"
        bucket = model_usage.setdefault(key, _empty())
        _add(bucket, input_tokens=row.input_tokens or 0,
             output_tokens=row.output_tokens or 0, total_tokens=row.total_tokens or 0,
             cached=row.cached_input_tokens or 0, reasoning=row.reasoning_tokens or 0)

    price_rows = await db.execute(select(ModelPrice))
    prices = {price.model: price for price in price_rows.scalars().all()}
    priced_total = 0.0
    unpriced = False
    model_list = []
    for key, bucket in sorted(model_usage.items(), key=lambda item: item[0]):
        provider, _, model_name = key.partition("/")
        price = prices.get(model_name)
        cost = None
        if price is not None:
            cost = (
                (bucket["input"] / 1_000_000.0) * float(price.input_per_million or 0)
                + (bucket["output"] / 1_000_000.0) * float(price.output_per_million or 0)
            )
            priced_total += cost
        else:
            unpriced = True
        model_list.append({
            "provider": provider,
            "model": model_name,
            **bucket,
            "cost_usd": round(cost, 6) if cost is not None else None,
        })
    usage["by_model"] = model_list
    usage["total"]["cost_usd"] = None if unpriced else round(priced_total, 6)
    usage["pricing_configured"] = not unpriced and bool(prices)

    node_usage: dict[str, dict[str, Any]] = {}
    if task_ids:
        turns_result = await db.execute(select(AgentTurn).where(AgentTurn.task_id.in_(task_ids)))
        for turn in turns_result.scalars().all():
            node_id = task_to_node.get(turn.task_id) or turn.task_id
            bucket = node_usage.setdefault(node_id, _empty())
            _add(bucket, input_tokens=turn.input_tokens or 0,
                 output_tokens=turn.output_tokens or 0, total_tokens=turn.total_tokens or 0, turns=1)
    for row in usage_rows:
        node_id = row.node_id or row.task_id or "orchestration"
        bucket = node_usage.setdefault(node_id, _empty())
        _add(bucket, input_tokens=row.input_tokens or 0,
             output_tokens=row.output_tokens or 0, total_tokens=row.total_tokens or 0)
    usage["by_node"] = [
        {"node_id": key, **value} for key, value in sorted(node_usage.items(), key=lambda item: item[0])
    ]

    if usage["total"]["calls"] == 0:
        usage["coverage"] = "none"
    elif usage_rows:
        usage["coverage"] = "provider_reported"
    else:
        usage["coverage"] = "agent_only"
    return usage


async def get_session_token_usage(session_id: str, db: AsyncSession) -> dict[str, Any]:
    dags_result = await db.execute(
        select(MasterDAG).where(MasterDAG.exploration_session_id == session_id)
    )
    dags = list(dags_result.scalars().all())
    runs = []
    total = _empty()
    for dag in dags:
        run = await get_dag_token_usage(dag.id, db)
        runs.append(run)
        _add(total, input_tokens=run["total"]["input"], output_tokens=run["total"]["output"],
             total_tokens=run["total"]["total"], cached=run["total"]["cached_input"],
             reasoning=run["total"]["reasoning"], calls=run["total"]["calls"],
             turns=run["total"]["turns"])
    return {"session_id": session_id, "total": total, "runs": runs}


async def get_cycle_token_usage(dag_id: str, db: AsyncSession) -> dict[str, Any]:
    return await get_dag_token_usage(dag_id, db)


async def get_token_totals_for_dags(dag_ids: list[str], db: AsyncSession) -> dict[str, dict[str, Any]]:
    """Fast token totals for workflow-list rows (one query per source)."""
    totals: dict[str, dict[str, Any]] = {
        dag_id: _empty() for dag_id in dag_ids
    }
    if not dag_ids:
        return totals
    nodes_result = await db.execute(select(DAGNode).where(DAGNode.dag_id.in_(dag_ids)))
    nodes = list(nodes_result.scalars().all())
    task_to_dag = {node.task_id: node.dag_id for node in nodes if node.task_id}
    task_ids = list(task_to_dag.keys())
    if task_ids:
        turns_result = await db.execute(select(AgentTurn).where(AgentTurn.task_id.in_(task_ids)))
        for turn in turns_result.scalars().all():
            dag_id = task_to_dag.get(turn.task_id)
            if not dag_id:
                continue
            total = int(turn.total_tokens or 0) or (int(turn.input_tokens or 0) + int(turn.output_tokens or 0))
            _add(totals[dag_id], input_tokens=turn.input_tokens or 0,
                 output_tokens=turn.output_tokens or 0, total_tokens=total, calls=1, turns=1)
    usage_result = await db.execute(select(LlmUsage).where(LlmUsage.dag_id.in_(dag_ids)))
    for row in usage_result.scalars().all():
        if not row.dag_id:
            continue
        _add(totals[row.dag_id], input_tokens=row.input_tokens or 0,
             output_tokens=row.output_tokens or 0, total_tokens=row.total_tokens or 0,
             cached=row.cached_input_tokens or 0, reasoning=row.reasoning_tokens or 0)
    return totals
