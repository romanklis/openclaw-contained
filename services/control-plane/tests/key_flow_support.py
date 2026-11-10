"""Deterministic fixtures for the key-flow API tests."""
from __future__ import annotations

import os
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models import (
    AgentImage,
    DAGNodeOutput,
    SkillV2,
    SkillV2Source,
    SkillV2Status,
    Task,
    TaskOutput,
    TaskStatus,
)

DATABASE_URL = os.getenv("DATABASE_URL", "")

PYTHON_BLOCK_SOURCE = """def main(inputs):
    value = inputs.get("value", 0)
    return {"value": value + 1}
"""


async def _with_db(fn):
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as db:
            result = await fn(db)
            await db.commit()
            return result
    finally:
        await engine.dispose()


async def _seed_image(db):
    image = await db.get(AgentImage, "openclaw")
    if image is None:
        image = AgentImage(
            id="openclaw",
            name="OpenClaw",
            description="Test base agent image",
            tag="openclaw-agent:openclaw",
            enabled=True,
        )
        db.add(image)
        await db.flush()


async def seed_skill() -> str:
    async def work(db):
        await _seed_image(db)
        skill_id = f"skv2-test-{uuid.uuid4().hex[:8]}"
        db.add(
            SkillV2(
                id=skill_id,
                image_id="openclaw",
                name="Research and report",
                description="Research the account and write the report.",
                instructions="Research Acme and write the report for Acme.",
                status=SkillV2Status.ACTIVE,
                source_type=SkillV2Source.MANUAL,
                tags=["test"],
                created_by="key-flow-tests",
            )
        )
        return skill_id

    return await _with_db(work)


async def seed_task_with_python_deliverable() -> str:
    async def work(db):
        task_id = f"task-test-{uuid.uuid4().hex[:8]}"
        db.add(
            Task(
                id=task_id,
                name="Block promotion fixture",
                description="A completed task with a deterministic driver.",
                status=TaskStatus.COMPLETED,
                workspace_id=f"workspace-{uuid.uuid4().hex[:8]}",
                llm_model="test-model",
                created_by="key-flow-tests",
            )
        )
        await db.flush()
        db.add(
            TaskOutput(
                task_id=task_id,
                iteration=1,
                completed="true",
                output="created main.py",
                model_used="test-model",
                deliverables={"main.py": PYTHON_BLOCK_SOURCE},
                raw_result={"completed": True},
            )
        )
        return task_id

    return await _with_db(work)


async def seed_node_guidance(dag_id: str, node_id: str) -> None:
    async def work(db):
        db.add(
            DAGNodeOutput(
                dag_id=dag_id,
                node_id=node_id,
                status="completed",
                objective="Research Acme.",
                output_text="Acme research completed; report.md created.",
                deliverables_keys=["report.md"],
                acceptance_verdict="pass",
            )
        )

    await _with_db(work)


def fake_cycle_dag(skill_id: str) -> dict[str, Any]:
    return {
        "nodes": [
            {
                "node_id": "research-acme",
                "description": "Research Acme and save the findings.",
                "depends_on": [],
                "selected_skill_v2_id": skill_id,
                "input_mapping": {},
                "config": {
                    "base_image": "openclaw",
                    "selected_skill_v2_id": skill_id,
                    "llm_model": "test-model",
                },
            },
            {
                "node_id": "summarize-acme",
                "description": "Summarize the Acme research for Acme.",
                "depends_on": ["research-acme"],
                "input_mapping": {"source": "research-acme.output"},
                "config": {"base_image": "openclaw", "llm_model": "test-model"},
            },
        ],
        "edges": [],
    }
