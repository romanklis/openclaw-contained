"""
Master-DAG Planner — LLM-driven decomposition of user objectives into DAGs.

Uses the existing LLM router (POST /api/llm/v1/chat/completions) to generate
a DAG JSON plan from a user's objective and the available skill registry.
"""
import json
import logging
import httpx
import re
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models import Skill, AgentImage, SkillV2, SkillV2Status, Block
from dag_validator import validate_dag

logger = logging.getLogger(__name__)

# Internal URL for the LLM router (same process, but via HTTP for consistency)
LLM_ROUTER_URL = "http://localhost:8000/api/llm/v1/chat/completions"


def _extract_json_object(text: str):
    """Robustly extract the first valid JSON object from an LLM response.

    Handles markdown code fences, leading/trailing prose, and nested braces
    inside string values. Returns the parsed object or None.
    """
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None


def is_gemini_lite_model(model: str) -> bool:
    """Return True if model belongs to the gemini-lite family."""
    normalized = (model or "").strip().lower()
    return "gemini" in normalized and "lite" in normalized


def enforce_gemini_lite_execution_model(dag_json: dict, execution_model: str) -> dict:
    """Normalize all DAG node execution models to the provided model."""
    return set_node_execution_model(dag_json, execution_model)


def set_node_execution_model(dag_json: dict, execution_model: str) -> dict:
    """Set the LLM model on every DAG node config (blocks have no LLM)."""
    for node in dag_json.get("nodes", []):
        if str(node.get("node_type") or node.get("config", {}).get("type") or "").lower() == "block":
            continue
        config = node.get("config") or {}
        config["llm_model"] = execution_model
        node["config"] = config
    return dag_json


async def _build_base_images_section(db: AsyncSession) -> str:
    """Query agent_images table and build a human-readable list for the planner prompt."""
    _FALLBACK = (
        '"openclaw" (full Python+Node), "nanobot" (lightweight Python), '
        '"picoclaw" (shell-only), "zeroclaw" (Python+Rust)'
    )
    try:
        result = await db.execute(
            select(AgentImage).where(AgentImage.enabled.is_(True))
        )
        images = result.scalars().all()
        if images:
            formatted = []
            for img in images:
                capabilities = ", ".join(img.capabilities or []) or "(none listed)"
                best_for = ", ".join(img.best_for or []) or "general tasks"
                avoid_for = ", ".join(img.avoid_for or []) or "(none listed)"
                runtime = img.runtime or "unspecified runtime"
                formatted.append(
                    f'"{img.id}" (runtime: {runtime}; best_for: {best_for}; '
                    f'capabilities: {capabilities}; avoid_for: {avoid_for}; '
                    f'description: {img.description})'
                )
            return ", ".join(formatted)
    except Exception:
        pass
    return _FALLBACK


async def _build_skills_v2_section(db: AsyncSession) -> str:
    """Build a concise per-image skills section from active v2 skill nodes."""
    try:
        result = await db.execute(
            select(SkillV2).where(SkillV2.status == SkillV2Status.ACTIVE).order_by(
                SkillV2.image_id, SkillV2.confidence_score.desc()
            )
        )
        skills = result.scalars().all()
        if not skills:
            return ""

        by_image: dict[str, list] = {}
        for s in skills:
            by_image.setdefault(s.image_id, []).append(s)

        lines = ["## Image-Scoped Skills (v2) — MANDATORY\n"
                 "IMPORTANT: For every DAG node, check if its base_image matches one of the images below. "
                 "If a skill matches the node's task, you MUST set config.selected_skill_v2_id to the skill id "
                 "and config.skill_selection_reason to a brief explanation. "
                 "Omitting this when a match exists is an error.\n"]
        for image_id, image_skills in by_image.items():
            lines.append(f"### Image: {image_id}  ← nodes with base_image=\"{image_id}\" MUST use one of these skills if the task matches")
            for s in image_skills[:10]:  # cap per image to avoid prompt bloat
                lines.append(
                    f"  - SKILL id={s.id} | {s.name} (confidence={s.confidence_score}) "
                    f"[{', '.join(s.tags or [])}]\n"
                    f"    What it does: {s.description[:160]}"
                )
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("Could not build v2 skills section: %s", exc)
        return ""


def _normalize_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


# Words too generic to establish skill relevance — their co-occurrence alone
# must never cause a skill to be attached to a node.
_SKILL_MATCH_STOPWORDS = {
    "browser", "python", "using", "use", "used", "create", "build", "write",
    "file", "files", "api", "http", "https", "report", "reports", "download",
    "upload", "verify", "verification", "verified", "login", "log", "session",
    "web", "request", "requests", "page", "pages", "data", "list", "listing",
    "search", "query", "result", "results", "step", "task", "agent", "image",
    "images", "openclaw", "code", "script", "doc", "docs", "tool", "com", "www",
    "html", "json", "content", "url", "urls", "text", "sample", "account",
    "service", "call", "need", "must", "work", "new", "via", "into", "from",
    "with", "the", "and", "a", "an", "in", "it", "its", "to", "of", "for", "on",
    "your", "this", "that", "appears", "short", "use", "out", "well", "will",
    "can", "has", "have", "should", "when", "after", "then", "check", "look",
    "see", "go", "get", "make", "find", "add", "remove", "list", "read",
    "ensure", "photo", "photos", "album", "media", "gallery", "source",
    "network", "navigate", "collect", "resolve", "resolved", "direct", "open",
    "robust", "extractor", "scrape", "scraper", "crawl", "remote", "review",
}


def _meaningful_tokens(text: str) -> set[str]:
    return {t for t in _normalize_tokens(text) if len(t) >= 6 and t not in _SKILL_MATCH_STOPWORDS}


def _pick_matching_v2_skill(node: dict, objective: str, candidates: list[SkillV2]) -> SkillV2 | None:
    """Pick a best-effort matching v2 skill for a DAG node.

    Conservative: only a genuine overlap of domain terms (>= 2 meaningful
    tokens) attaches a skill; otherwise the node stays skill-less and the agent
    follows its inline instructions (custom).
    """
    if not candidates:
        return None

    node_text = " ".join([
        node.get("description", ""),
        objective or "",
        json.dumps(node.get("input_mapping", {}) or {}),
    ])
    node_tokens = _meaningful_tokens(node_text)

    best: SkillV2 | None = None
    best_score = 0
    for skill in candidates:
        skill_text = " ".join([
            skill.name or "",
            skill.description or "",
            " ".join(skill.tags or []),
        ])
        skill_tokens = _meaningful_tokens(skill_text)
        overlap = len(node_tokens.intersection(skill_tokens))
        if overlap > best_score:
            best = skill
            best_score = overlap

    return best if best_score >= 2 else None


async def _apply_v2_skill_fallback(db: AsyncSession, dag_json: dict, objective: str) -> dict:
    """Fill selected_skill_v2_id when model omits it but a matching active v2 skill exists."""
    try:
        result = await db.execute(
            select(SkillV2).where(SkillV2.status == SkillV2Status.ACTIVE).order_by(
                SkillV2.image_id, SkillV2.confidence_score.desc(), SkillV2.created_at.desc()
            )
        )
        active_skills = result.scalars().all()
        if not active_skills:
            return dag_json

        by_image: dict[str, list[SkillV2]] = {}
        for skill in active_skills:
            by_image.setdefault(skill.image_id, []).append(skill)

        for node in dag_json.get("nodes", []):
            config = node.setdefault("config", {})
            if str(node.get("node_type") or config.get("type") or "").lower() == "block" or config.get("block"):
                continue
            if config.get("selected_skill_v2_id"):
                continue

            image_id = config.get("base_image")
            if not image_id:
                continue

            selected = _pick_matching_v2_skill(node, objective, by_image.get(image_id, []))
            if not selected:
                continue

            reason = (
                f"Auto-selected image-scoped skill '{selected.name}' "
                f"for node on image '{image_id}'."
            )
            config["selected_skill_v2_id"] = selected.id
            config["skill_selection_reason"] = reason
            node["selected_skill_v2_id"] = selected.id
            node["skill_selection_reason"] = reason
        # Auto-set require_real_sources=true for any node whose task signals
        # a real-world fetch requirement (browsing, API, FRED, annual reports, etc.)
        # so the gate will reject hallucinated/mocked outputs.
        _FETCH_SIGNALS = {
            "browser", "browser_v2", "browser_v3",   # image types
        }
        _FETCH_DESC_KEYWORDS = [
            "fetch", "browse", "scrape", "download", "retrieve",
            "crawl", "extract from", "visit", "read from url", "get from",
            "annual report", "investor relation", "fred", "placera",
            "atlascopco", "html", "http", "https", "web page",
        ]
        for node in dag_json.get("nodes", []):
            config = node.setdefault("config", {})
            if str(node.get("node_type") or config.get("type") or "").lower() == "block" or config.get("block"):
                continue
            gate_cfg = config.setdefault("deliverable_gate", {})
            # Skip nodes that already have an explicit setting
            if "require_real_sources" in gate_cfg:
                continue
            image_id = (config.get("base_image") or "").lower()
            desc = (node.get("description") or "").lower()
            if image_id in _FETCH_SIGNALS or any(k in desc for k in _FETCH_DESC_KEYWORDS):
                gate_cfg["require_real_sources"] = True
                logger.debug(
                    "Auto-set require_real_sources=true for node %s (image=%s)",
                    node.get("node_id"), image_id,
                )

        return dag_json
    except Exception as exc:
        logger.debug("Could not apply v2 skill fallback: %s", exc)
        return dag_json


def _normalize_interactive_nodes(dag_json: dict) -> dict:
    """Ensure interactive node types are set.

    Safety net for planner output: any node that is the source of a
    "decision:<value>" edge is treated as a decision node (with options derived
    from the edge values if the LLM did not supply a config), and input nodes
    get their config.type set.
    """
    edges = dag_json.get("edges", [])
    decision_values: dict[str, list[str]] = {}
    for edge in edges:
        cond = str(edge.get("condition") or "")
        if cond.startswith("decision:"):
            decision_values.setdefault(str(edge.get("from_node")), []).append(cond.split(":", 1)[1].strip())

    for node in dag_json.get("nodes", []):
        nid = str(node.get("node_id") or "")
        cfg = node.setdefault("config", {})
        if nid in decision_values:
            node["node_type"] = "decision"
            cfg["type"] = "decision"
            cfg.setdefault("question", f"Decision required at '{nid}'")
            payload = cfg.setdefault("payload", {})
            options = payload.setdefault("options", [])
            if not options:
                for v in decision_values[nid]:
                    if v not in [o.get("value") for o in options]:
                        options.append({"label": v.replace("_", " ").capitalize(), "value": v})
        if str(node.get("node_type") or "agent") == "input":
            cfg["type"] = "input"
            cfg.setdefault("prompt", f"Please provide the requested input at '{nid}'")
    return dag_json


PLANNER_SYSTEM_PROMPT = """\
You are a task decomposition planner for TaskForge, a universal task orchestration platform.

Your job: Given a user's objective, decompose it into a directed acyclic graph (DAG) of tasks.
Each node in the DAG represents one unit of work executed in an isolated container.

## Available Skills
These are reusable skill templates. Each skill has steps that can become individual DAG nodes.
Use them when they match the task requirements. For unique tasks, create inline nodes (skill_id: null).

{skills_section}

{skills_v2_section}

{blocks_section}

## DAG JSON Schema
You MUST respond with ONLY a valid JSON object (no markdown, no text before/after):

{{
  "nodes": [
    {{
      "node_id": "unique-descriptive-id",
      "node_type": "agent",
      "skill_id": "skill-xxx" or null,
      "skill_step_index": 0,
      "description": "What this node does",
      "depends_on": ["other-node-id"],
      "config": {{
        "base_image": "openclaw",
        "llm_model": null,
        "timeout_minutes": 15,
        "deploy_authorized": false,
        "selected_skill_v2_id": null,
        "skill_selection_reason": null
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
      "condition": "decision:approve",
      "edge_type": "rework"
    }}
  ]
}}

Supported edge conditions: "on_success" (follow when the source completed), "on_failure" (follow when the source failed), "decision:<value>" (follow when a decision node's answer equals <value>). Omit "condition" to always follow.

## Interactive Nodes (decision / input)
Some steps require HUMAN input mid-flow. Use them ONLY when the objective explicitly calls for a review, approval, or user-provided data. They pause the workflow until the user responds.

### decision node (node_type: "decision")
Pauses the workflow and asks the user to pick one option. The runtime then runs ONLY the branch matching the chosen option; other branches are skipped.
- config: {{ "type": "decision", "question": "Approve the generated report?", "payload": {{ "require_justification": false, "options": [ {{ "label": "Approve", "value": "approve" }}, {{ "label": "Rework", "value": "rework", "require_justification": true }} ] }} }}
- "require_justification": true (either on the payload — all options — or on a specific option) makes the UI show a REQUIRED text field where the user explains their choice (e.g. why they reject/rework). Set it on options where an explanation matters (reject/rework/cancel), not on simple accepts.
- The user may always add an optional justification; the chosen option is stored on the node output as "choice" and "justification".
- When a rework node follows a rejected decision, its node_objective MUST tell the agent to read the rejection justification from input_data (the decision node's output contains "justification", e.g. input_data.approve-report.justification) and address it specifically.
- Routing: add one edge per option from the decision node to that option's next node, with condition "decision:<value>" (e.g. "decision:approve", "decision:rework").
- The chosen option is stored on the node output as "choice" (and "justification" if the user provided one).
- IMPORTANT: do NOT put a single downstream node that depends on BOTH branch targets (the skipped branch would block it). Keep the branches separate (they may terminate the flow, or each lead to its own next step).
- For APPROVAL gates, prefer THREE options: "accept", "reject", "cancel" — accept proceeds, reject routes to a rework step, and cancel ends the DAG (no edge for the cancel option, so the remaining steps are skipped).
- CLOSED LOOP: to re-approve after rework, add a loop-back edge from the rework node back to the decision node with edge_type "loop" (and condition "on_success"). The runtime then re-runs the decision node after rework so the user can re-approve. Example:
  {{ "from_node": "rework-node", "to_node": "approval-node", "condition": "on_success", "edge_type": "loop" }}

### input node (node_type: "input")
Pauses the workflow and asks the user to provide data (measurements, parameters, values).
- config: {{ "type": "input", "prompt": "Enter the target revenue for verification", "payload": {{ "fields": [ {{ "key": "target_revenue", "label": "Target Revenue", "type": "number" }}, {{ "key": "quarter", "label": "Quarter", "type": "text" }} ] }} }}
- field "type": text | number | select.
- The user's values are stored on the node output under "fields". Downstream nodes can reference them via input_mapping, e.g. {{ "target_revenue": "collect-kpi.fields.target_revenue" }}.

### agent node (node_type: "agent")
Default — executes in an isolated container using the selected base_image + skill. No human interaction.

## Rules
1. Minimize total nodes — combine trivial steps.
2. Maximize parallelism — nodes without dependencies should run concurrently.
3. Use skills when they match; use inline nodes (skill_id: null) for one-off tasks.
4. Every node must have a unique node_id.
5. depends_on lists the node_ids that must complete before this node starts.
6. Only the final deployment node should have deploy_authorized: true.
7. Use review/verdict (decision) nodes sparingly — only when the objective explicitly calls for a human review/approval gate, not as a default for every step. When used, give 2-3 clear options and wire each branch with a "decision:<value>" edge condition.
8. Use input nodes only when the user must supply data (measurements/parameters) mid-flow that cannot be derived automatically; wire the fields so downstream nodes can consume them via input_mapping.
9. base_image options: {base_images_section}. Choose using runtime + capabilities + best_for, and avoid images whose avoid_for conflicts with the node's task.
10. Set each node config.llm_model to the requested execution model and do not use GPT models.
11. MANDATORY SKILL SELECTION: For every node whose base_image appears in the "Image-Scoped Skills (v2)" section above, you MUST check if any skill listed under that image matches the node's task. If it matches, you MUST set config.selected_skill_v2_id to that skill's exact id string and config.skill_selection_reason to a one-sentence explanation. Only leave both null if absolutely no skill is relevant. Failing to reference an available matching skill is a planning error.
12. INPUT MAPPING: Do NOT include "input_mapping" in node definitions unless you need to selectively map specific outputs. The runtime automatically passes ALL dependency outputs to a node when "input_mapping" is omitted or empty  ({{}}). Only include "input_mapping" if you need to selectively rename or pick specific fields from dependency outputs.
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


_BLOCK_STOPWORDS = frozenset(
    "the a an and or for to of in on with from this that these those is are be do does did "
    "block blocks node step steps deterministic fetch fetching data stock value values price "
    "list page current today latest via using use used".split()
)


def _block_tokens(*texts) -> set:
    toks: set = set()
    for t in texts:
        if not t:
            continue
        for w in re.findall(r"[a-z0-9]+", str(t).lower()):
            if len(w) > 2 and w not in _BLOCK_STOPWORDS:
                toks.add(w)
    return toks


def _block_purpose(b) -> str:
    """Semantic one-liner for a block for the planner.

    Prefers the description's first line when it is capability text; falls back
    to a humanized block id (learned blocks often store only provenance text)."""
    d = (getattr(b, "description", None) or "").strip()
    first = (d.split("\n")[0] if d else "").strip()
    if first and not first.lower().startswith(("learned from", "source:", "model:")):
        return first[:220]
    human = re.sub(r"[-_]+", " ", getattr(b, "id", "") or "").strip()
    return f"Deterministic step: {human}."


async def _build_blocks_section(db: AsyncSession, objective: str = "") -> str:
    """List active deterministic blocks for the planner and instruct its use.

    Ranks objective-overlapping blocks first (deterministic grounding so the
    planning LLM does not silently skip a learned block that matches the task)."""
    rows = (await db.execute(
        select(Block).where(Block.status == "active").order_by(Block.name.asc())
    )).scalars().all()
    if not rows:
        return "## Deterministic Blocks\n\nNo deterministic blocks are registered yet."
    lines = ["## Deterministic Blocks",
             "Deterministic blocks run with NO LLM and NO agent container — pure, typed, repeatable operations.",
             "PREFER THEM: for any step that is a well-defined operation (fetch/convert/search/api/download), emit:",
             '  {"node_id": "…", "node_type": "block", "depends_on": ["<upstream>"],',
             '   "config": {"type": "block", "block": "<id>", "inputs": {…}}}',
             "Upstream outputs are passed to the block automatically as `<upstream-node-id>` input keys when",
             'input_mapping is empty; set "depends_on" to the upstream nodes whose outputs it needs.',
             "Only use node_type 'agent' when NO block fits the step — and if you do, note 'CAPABILITY GAP:' in the node description.",
             "Block nodes have no base_image, no skill, no llm_model, and no deep review.\n"]

    def _format(b):
        code = getattr(b, "code", None)
        code_meta = code if isinstance(code, dict) else {}
        runtime = code_meta.get("runtime") or "python"
        ins = b.inputs_schema or {}
        props = ins.get("properties") if isinstance(ins, dict) else {}
        req = set(ins.get("required") or []) if isinstance(ins, dict) else set()
        ins_part = ", ".join(
            f"{name}:{props[name].get('type', 'any')}{'(required)' if name in req else ''}"
            for name in (list(props.keys())[:6] if isinstance(props, dict) else [])
        ) or "-"
        outs = b.outputs_schema or {}
        out_props = outs.get("properties") if isinstance(outs, dict) else {}
        out_part = ", ".join(list(out_props.keys())[:6]) if isinstance(out_props, dict) else "-"
        return (
            f"- id: {b.id} | runtime: {runtime} | what: {_block_purpose(b)} | "
            f"inputs: {ins_part} | outputs: {out_part}"
        )

    objective_tokens = _block_tokens(objective) if objective else set()
    if objective_tokens:
        scored = []
        for b in rows:
            bt = _block_tokens(getattr(b, "id", ""), getattr(b, "name", ""), _block_purpose(b))
            overlap = sorted(objective_tokens & bt)
            if overlap:
                scored.append((len(overlap), -b.id.count("-"), b))
        scored.sort(key=lambda x: (-x[0], x[1]))
        strong = scored[:6]
        if strong:
            lines.append("### Strong candidates for THIS objective — strongly prefer one of these when a step matches:")
            lines.append("\n".join(_format(b) for _, _, b in strong))
            lines.append("")

    lines.append("### All available blocks")
    shown = 0
    for b in rows:
        if shown >= 50:
            break
        lines.append(_format(b))
        shown += 1
    return "\n".join(lines)


async def plan_dag(objective: str, llm_model: str, db: AsyncSession, base_image: str | None = None, skill_ids: list[str] | None = None, agent_model: str | None = None) -> dict:
    """Generate a DAG plan from a user objective using the LLM router.

    Args:
        objective: The user's goal/objective.
        llm_model: The LLM model to use for planning (the planner LLM).
        db: Database session for fetching skills.
        base_image: Override base_image for all nodes (e.g. "zeroclaw").
        skill_ids: If provided, only use these skills (user-selected).
        agent_model: The LLM model to set on DAG node configs for agent execution.
                     If None, defaults to llm_model (backward compat).

    Returns:
        Validated DAG JSON dict with 'nodes' and 'edges'.

    Raises:
        ValueError: If the LLM produces invalid output after retries.
    """
    agent_model = agent_model or llm_model
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
    skills_v2_section = await _build_skills_v2_section(db)
    system_prompt = PLANNER_SYSTEM_PROMPT.format(
        skills_section=skills_section,
        skills_v2_section=skills_v2_section,
        blocks_section=await _build_blocks_section(db, objective=objective),
        base_images_section=await _build_base_images_section(db),
    )

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
                        # DeepSeek V4 is a reasoning model — the chain-of-thought
                        # counts toward max_tokens. Disable thinking for this
                        # structured-JSON call (faster, no truncation) and keep a
                        # generous max_tokens as a safety net.
                        "max_tokens": 30000,
                        "thinking": {"type": "disabled"},
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

            # Parse JSON — prefer a direct parse, fall back to extracting the
            # first complete JSON object (in case the model wraps the answer in
            # prose or trailing reasoning).
            try:
                dag_json = json.loads(content)
            except json.JSONDecodeError:
                extracted = _extract_json_object(content)
                if extracted is None:
                    last_error = "Invalid JSON: no parseable object returned"
                    logger.warning(f"Planner attempt {attempt}/{max_attempts}: {last_error}")
                    continue
                dag_json = extracted

            # Validate DAG structure
            is_valid, errors = validate_dag(dag_json, {s.id: True for s in skills})
            if not is_valid:
                last_error = f"DAG validation failed: {'; '.join(errors)}"
                logger.warning(f"Planner attempt {attempt}/{max_attempts}: {last_error}")
                continue

            dag_json = set_node_execution_model(dag_json, agent_model)

            # Override base_image on all nodes if the caller specified one
            if base_image:
                for node in dag_json.get("nodes", []):
                    if "config" not in node:
                        node["config"] = {}
                    node["config"]["base_image"] = base_image

            dag_json = await _apply_v2_skill_fallback(db, dag_json, objective)

            logger.info(f"Planner generated valid DAG with {len(dag_json.get('nodes', []))} nodes")
            return dag_json

    raise ValueError(f"Planner failed after {max_attempts} attempts. Last error: {last_error}")
