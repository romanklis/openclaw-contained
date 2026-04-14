"""
Master-DAG Planner — LLM-driven decomposition of user objectives into DAGs.

Uses the existing LLM router (POST /api/llm/v1/chat/completions) to generate
a DAG JSON plan from a user's objective and the available skill registry.
"""
import json
import logging
import httpx
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models import Skill
from dag_validator import validate_dag

logger = logging.getLogger(__name__)

# Internal URL for the LLM router (same process, but via HTTP for consistency)
LLM_ROUTER_URL = "http://localhost:8000/api/llm/v1/chat/completions"


def is_gemini_lite_model(model: str) -> bool:
    """Return True if model belongs to the gemini-lite family."""
    normalized = (model or "").strip().lower()
    return "gemini" in normalized and "lite" in normalized


def enforce_gemini_lite_execution_model(dag_json: dict, execution_model: str) -> dict:
    """Normalize all DAG node execution models to the provided gemini-lite model."""
    for node in dag_json.get("nodes", []):
        config = node.get("config") or {}
        config["llm_model"] = execution_model
        node["config"] = config
    return dag_json

PLANNER_SYSTEM_PROMPT = """\
You are a task decomposition planner for TaskForge, a universal task orchestration platform.

Your job: Given a user's objective, decompose it into a directed acyclic graph (DAG) of tasks.
Each node in the DAG represents one unit of work executed in an isolated container.

## Available Skills
These are reusable skill templates. Each skill has steps that can become individual DAG nodes.
Use them when they match the task requirements. For unique tasks, create inline nodes (skill_id: null).

{skills_section}

## DAG JSON Schema
You MUST respond with ONLY a valid JSON object (no markdown, no text before/after):

{{
  "nodes": [
    {{
      "node_id": "unique-descriptive-id",
      "skill_id": "skill-xxx" or null,
      "skill_step_index": 0,
      "description": "What this node does",
      "depends_on": ["other-node-id"],
      "config": {{
        "base_image": "openclaw",
        "llm_model": null,
        "timeout_minutes": 15,
        "deploy_authorized": false
      }},
      "input_mapping": {{
        "input_name": "dependency-node-id.output_key"
      }}
    }}
  ],
  "edges": [
    {{
      "from_node": "review-node-id",
      "to_node": "target-node-id",
      "condition": "review-node-id.verdict == 'FAIL'",
      "edge_type": "rework"
    }}
  ]
}}

## Rules
1. Minimize total nodes — combine trivial steps.
2. Maximize parallelism — nodes without dependencies should run concurrently.
3. Use skills when they match; use inline nodes (skill_id: null) for one-off tasks.
4. Every node must have a unique node_id.
5. depends_on lists the node_ids that must complete before this node starts.
6. Only the final deployment node should have deploy_authorized: true.
7. Use review/verdict nodes sparingly — only when quality gates are needed.
8. base_image options: "openclaw" (full Python+Node), "nanobot" (lightweight Python), "picoclaw" (shell-only), "zeroclaw" (Python+Rust)
9. Set each node config.llm_model to the requested execution model and do not use GPT models.
"""


def _build_skills_section(skills: list[dict]) -> str:
    """Format skills for the system prompt."""
    if not skills:
        return "(No skills registered yet. Create all nodes as inline.)"

    lines = []
    for s in skills:
        steps_str = " → ".join(
            f"{st.get('name', st.get('step_id', '?'))}" for st in s.get("steps", [])
        )
        skill_block = (
            f"- **{s['name']}** (id: {s['id']}): {s.get('description', '')}\n"
            f"  Steps: {steps_str}\n"
            f"  Inputs: {json.dumps(s.get('input_schema', {}))}\n"
            f"  Outputs: {json.dumps(s.get('output_artifacts', []))}"
        )
        # Include installation/setup instructions if available
        instructions = s.get('instructions', '')
        if instructions:
            # Truncate very long instructions for the planner prompt
            preview = instructions[:2000]
            if len(instructions) > 2000:
                preview += "\n  ... (truncated)"
            skill_block += f"\n  Instructions: {preview}"
        lines.append(skill_block)
    return "\n".join(lines)


async def plan_dag(objective: str, llm_model: str, db: AsyncSession, base_image: str | None = None, skill_ids: list[str] | None = None) -> dict:
    """Generate a DAG plan from a user objective using the LLM router.

    Args:
        objective: The user's goal/objective.
        llm_model: The LLM model to use for planning.
        db: Database session for fetching skills.
        base_image: Override base_image for all nodes (e.g. "zeroclaw").
        skill_ids: If provided, only use these skills (user-selected).

    Returns:
        Validated DAG JSON dict with 'nodes' and 'edges'.

    Raises:
        ValueError: If the LLM produces invalid output after retries.
    """
    # Fetch skills — either user-selected or all
    if skill_ids:
        result = await db.execute(select(Skill).where(Skill.id.in_(skill_ids)))
    else:
        result = await db.execute(select(Skill))
    skills = result.scalars().all()
    skills_data = [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "instructions": s.instructions or "",
            "steps": s.steps or [],
            "input_schema": s.input_schema or {},
            "output_artifacts": s.output_artifacts or [],
        }
        for s in skills
    ]
    skills_map = {s.id: s for s in skills}

    skills_section = _build_skills_section(skills_data)
    system_prompt = PLANNER_SYSTEM_PROMPT.format(skills_section=skills_section)

    max_attempts = 3
    last_error = ""

    async with httpx.AsyncClient(timeout=120.0) as client:
        for attempt in range(1, max_attempts + 1):
            user_msg = objective
            if attempt > 1:
                user_msg = (
                    f"{objective}\n\n"
                    f"[Previous attempt failed: {last_error}. "
                    f"Please fix and return valid JSON only.]"
                )

            try:
                resp = await client.post(
                    LLM_ROUTER_URL,
                    json={
                        "model": llm_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_msg},
                        ],
                        "temperature": 0.3,
                        "stream": False,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                last_error = f"LLM request failed: {e}"
                logger.warning(f"Planner attempt {attempt}/{max_attempts}: {last_error}")
                continue

            # Extract content
            content = ""
            choices = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")

            # Strip markdown fences if present
            content = content.strip()
            if content.startswith("```"):
                # Remove ```json ... ``` wrapping
                lines = content.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                content = "\n".join(lines)

            # Parse JSON
            try:
                dag_json = json.loads(content)
            except json.JSONDecodeError as e:
                last_error = f"Invalid JSON: {e}"
                logger.warning(f"Planner attempt {attempt}/{max_attempts}: {last_error}")
                continue

            # Validate DAG structure
            is_valid, errors = validate_dag(dag_json, {s.id: True for s in skills})
            if not is_valid:
                last_error = f"DAG validation failed: {'; '.join(errors)}"
                logger.warning(f"Planner attempt {attempt}/{max_attempts}: {last_error}")
                continue

            dag_json = enforce_gemini_lite_execution_model(dag_json, llm_model)

            # Override base_image on all nodes if the caller specified one
            if base_image:
                for node in dag_json.get("nodes", []):
                    if "config" not in node:
                        node["config"] = {}
                    node["config"]["base_image"] = base_image

            logger.info(f"Planner generated valid DAG with {len(dag_json.get('nodes', []))} nodes")
            return dag_json

    raise ValueError(f"Planner failed after {max_attempts} attempts. Last error: {last_error}")
