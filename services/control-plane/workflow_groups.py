"""Workflow-group helpers: identity, titles, creation, and backfill."""
import json
import logging
import re
import uuid
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import MasterDAG, WorkflowGroup

logger = logging.getLogger(__name__)

LLM_ROUTER_URL = "http://localhost:8000/api/llm/v1/chat/completions"
REVISION_MARKER = "\n\n--- REVISION CONTEXT ---"

URL_RE = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
TOKEN_RE = re.compile(
    r"\b(?:github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9]{10,}|"
    r"sk-[A-Za-z0-9_-]{10,}|xox[abp]-[A-Za-z0-9-]{10,}|"
    r"AKIA[0-9A-Z]{16})\b"
)
FENCE_RE = re.compile(r"```[\s\S]*?```")
GENERIC_PREFIX_RE = re.compile(
    r"^(?:system\s*/\s*task role|you are|please|i want you to|i would like you to|"
    r"objective|task|goal|request)\s*[:;\-]?\s*",
    re.IGNORECASE,
)
CODE_LINE_RE = re.compile(
    r"^\s*(?:#|import\s|from\s|class\s|def\s|if\s+__name__|print\(|"
    r"torch\.|np\.|plt\.|for\s+|while\s+|return\s+)"
)
SUSPICIOUS_TITLE_RE = re.compile(
    r"(?:https?://|www\.|github_pat_|gh[pousr]_|sk-|xox[abp]-|AKIA|\n)",
    re.IGNORECASE,
)


def base_objective(objective: str) -> str:
    text = str(objective or "").strip()
    if REVISION_MARKER in text:
        text = text.split(REVISION_MARKER, 1)[0].strip()
    return text or "Untitled workflow"


def normalize_project_id(project_id: Optional[str]) -> Optional[str]:
    if not project_id or project_id == "__general__":
        return None
    return project_id


def objective_key(objective: str) -> str:
    text = base_objective(objective).lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text[:2000]


def sanitize_objective_text(objective: str, max_len: int = 4000) -> str:
    """Remove URLs, code blocks, and credential-like strings from display text."""
    text = FENCE_RE.sub(" ", base_objective(objective))
    kept_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) > 240 and (
            stripped.count(";") > 2
            or stripped.count("{") + stripped.count("}") > 2
        ):
            continue
        if CODE_LINE_RE.match(stripped):
            continue
        kept_lines.append(stripped)
    text = " ".join(kept_lines)
    text = URL_RE.sub(" ", text)
    text = TOKEN_RE.sub(" ", text)
    text = re.sub(r"[*_`#>]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len] or "Untitled workflow"


def _clean_title(value: str) -> str:
    title = URL_RE.sub(" ", str(value or ""))
    title = TOKEN_RE.sub(" ", title)
    title = re.sub(r"\s+", " ", title).strip().strip('"').strip("'").rstrip(".")
    if len(title) > 60:
        title = title[:59].rstrip() + "…"
    return title


def derive_title(objective: str, max_len: int = 60) -> str:
    text = sanitize_objective_text(objective)
    previous = None
    while previous != text:
        previous = text
        text = GENERIC_PREFIX_RE.sub("", text).strip()

    sentences = re.split(r"(?<=[.!?])\s+", text)
    candidate = next(
        (
            sentence.strip()
            for sentence in sentences
            if len(sentence.strip().split()) >= 3
        ),
        text,
    )
    candidate = _clean_title(candidate)
    if len(candidate) > max_len:
        candidate = " ".join(candidate.split()[:8])
    if len(candidate) > max_len:
        candidate = candidate[: max_len - 1].rstrip() + "…"
    return candidate or "Untitled workflow"


def is_suspicious_title(title: str) -> bool:
    text = str(title or "").strip()
    return (
        not text
        or len(text) > 80
        or bool(SUSPICIOUS_TITLE_RE.search(text))
    )


def safe_display_title(title: str, objective: str, title_source: Optional[str]) -> str:
    if title_source in ("llm", "manual") and not is_suspicious_title(title):
        return title
    return derive_title(objective)


async def generate_workflow_title(
    objective: str,
    llm_model: Optional[str] = None,
    timeout: float = 6.0,
) -> tuple[str, bool]:
    """Ask the LLM router for a short title. Returns (title, used_llm)."""
    fallback = derive_title(objective)
    if not llm_model:
        return fallback, False
    prompt = (
        "Create a concise human-readable workflow title for the objective below. "
        "Use 3-6 words, title case, no quotes, no URLs, no credentials, and no "
        "punctuation at the end. Return only the title.\n\nObjective:\n"
        f"{sanitize_objective_text(objective, 1200)}"
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                LLM_ROUTER_URL,
                json={
                    "model": llm_model,
                    "messages": [
                        {"role": "system", "content": "You name reusable AI workflows."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 40,
                },
            )
            if resp.status_code != 200:
                return fallback, False
            data = resp.json()
            content = (
                (data.get("choices") or [{}])[0].get("message", {}).get("content")
                or data.get("response")
                or ""
            )
            title = _clean_title(str(content).splitlines()[0] if str(content).strip() else "")
            if not title or is_suspicious_title(title):
                return fallback, False
            return title, True
    except Exception as exc:
        logger.info("Workflow title generation failed, using fallback: %s", exc)
        return fallback, False


def _extract_json_object(text: str):
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except Exception:
        return None


async def generate_workflow_titles_batch(
    groups: list[WorkflowGroup],
    llm_model: Optional[str] = None,
    timeout: float = 30.0,
) -> dict[str, str]:
    """Generate titles for up to 10 groups in one LLM call."""
    if not groups:
        return {}
    fallback = {group.id: derive_title(group.objective) for group in groups}
    if not llm_model:
        return fallback

    payload = [
        {
            "id": group.id,
            "objective": sanitize_objective_text(group.objective, 700),
        }
        for group in groups
    ]
    prompt = (
        "Create concise 3-6 word human-readable workflow titles for each item. "
        "Return ONLY a JSON object mapping each id to its title. No URLs, no "
        "credentials, no quotes around the values, no trailing punctuation.\n\n"
        f"{json.dumps(payload, ensure_ascii=True)}"
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                LLM_ROUTER_URL,
                json={
                    "model": llm_model,
                    "messages": [
                        {"role": "system", "content": "You name reusable AI workflows."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 900,
                },
            )
            if resp.status_code != 200:
                return fallback
            data = resp.json()
            content = (
                (data.get("choices") or [{}])[0].get("message", {}).get("content")
                or data.get("response")
                or ""
            )
            parsed = _extract_json_object(str(content)) or {}
    except Exception as exc:
        logger.info("Batch workflow title generation failed, using fallbacks: %s", exc)
        return fallback

    result = dict(fallback)
    for group in groups:
        title = _clean_title(parsed.get(group.id, ""))
        if title and not is_suspicious_title(title):
            result[group.id] = title
    return result


async def ensure_workflow_group(
    objective: str,
    project_id: Optional[str],
    db: AsyncSession,
    llm_model: Optional[str] = None,
    existing_group_id: Optional[str] = None,
    use_llm_title: bool = True,
    mode: Optional[str] = None,
    force_new: bool = False,
) -> WorkflowGroup:
    """Return the group for this objective, creating it if necessary."""
    project_id = normalize_project_id(project_id)
    if existing_group_id:
        result = await db.execute(select(WorkflowGroup).where(WorkflowGroup.id == existing_group_id))
        group = result.scalar_one_or_none()
        if group:
            return group

    key = objective_key(objective)
    query = select(WorkflowGroup).where(WorkflowGroup.objective_key == key)
    if force_new:
        query = query.where(WorkflowGroup.id == "__never__")
    if project_id is None:
        query = query.where(WorkflowGroup.project_id.is_(None))
    else:
        query = query.where(WorkflowGroup.project_id == project_id)
    result = await db.execute(query)
    group = result.scalars().first()
    if group:
        return group

    group = WorkflowGroup(
        id=f"wf-{uuid.uuid4().hex[:8]}",
        title=derive_title(objective),
        title_source="derived",
        mode=mode or "experiment",
        objective=base_objective(objective),
        objective_key=key,
        project_id=project_id,
    )
    db.add(group)
    await db.flush()

    if use_llm_title:
        title, used_llm = await generate_workflow_title(objective, llm_model)
        group.title = title
        group.title_source = "llm" if used_llm else "derived"
    return group


async def backfill_workflow_groups(db: AsyncSession) -> int:
    """Attach legacy DAGs to groups. Returns the number of DAGs updated."""
    result = await db.execute(
        select(MasterDAG)
        .where(MasterDAG.workflow_group_id.is_(None))
        .order_by(MasterDAG.created_at.asc())
    )
    dags = list(result.scalars().all())
    if not dags:
        return 0

    all_result = await db.execute(select(MasterDAG))
    all_dags = {dag.id: dag for dag in all_result.scalars().all()}

    # Pass 1: template instances whose source is already grouped.
    for dag in dags:
        if dag.workflow_group_id:
            continue
        source = all_dags.get(dag.template_source_dag_id or "")
        if source and source.workflow_group_id:
            dag.workflow_group_id = source.workflow_group_id

    groups_result = await db.execute(select(WorkflowGroup))
    groups_by_key = {
        (group.project_id, group.objective_key): group
        for group in groups_result.scalars().all()
    }

    updated = 0
    for dag in dags:
        if dag.workflow_group_id:
            updated += 1
            continue
        key = objective_key(dag.objective)
        map_key = (normalize_project_id(dag.project_id), key)
        group = groups_by_key.get(map_key)
        if not group:
            group = WorkflowGroup(
                id=f"wf-{uuid.uuid4().hex[:8]}",
                title=derive_title(dag.objective),
                title_source="derived",
                mode="experiment",
                objective=base_objective(dag.objective),
                objective_key=key,
                project_id=normalize_project_id(dag.project_id),
            )
            db.add(group)
            groups_by_key[map_key] = group
        dag.workflow_group_id = group.id
        updated += 1

    # Pass 2: template instances whose source was grouped in pass 1.
    for dag in dags:
        if dag.template_source_dag_id:
            source = all_dags.get(dag.template_source_dag_id)
            if source and source.workflow_group_id and not dag.workflow_group_id:
                dag.workflow_group_id = source.workflow_group_id
                updated += 1

    await db.commit()
    if updated:
        logger.info("Backfilled %s DAG(s) into workflow groups", updated)
    return updated


async def cleanup_empty_group(group_id: Optional[str], db: AsyncSession) -> None:
    if not group_id:
        return
    remaining = await db.execute(
        select(MasterDAG.id).where(MasterDAG.workflow_group_id == group_id).limit(1)
    )
    if remaining.scalar_one_or_none() is None:
        group = await db.execute(select(WorkflowGroup).where(WorkflowGroup.id == group_id))
        group_obj = group.scalar_one_or_none()
        if group_obj:
            await db.delete(group_obj)
