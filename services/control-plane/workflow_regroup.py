"""Curated legacy workflow regrouping rules and application."""
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import MasterDAG, WorkflowGroup
from workflow_groups import sanitize_objective_text

logger = logging.getLogger(__name__)


def _text(group: WorkflowGroup) -> str:
    return (
        f"{group.title} {sanitize_objective_text(group.objective, 1200)}"
    ).lower()


def _has_all(text: str, *terms: str) -> bool:
    return all(term in text for term in terms)


def _has_any(text: str, *terms: str) -> bool:
    return any(term in text for term in terms)


def _pdf_rag(text: str) -> bool:
    return _has_all(text, "pdf") and _has_any(text, "rag", "retrieval") and _has_any(text, "approval", "report")


def _electrolux_stock(text: str) -> bool:
    return (
        "electrolux" in text
        and _has_any(text, "stock", "market", "quotation", "quote", "price")
        and not _has_any(text, "pdf", "investor", "investment", "report")
    )


def _electrolux_ir(text: str) -> bool:
    if "electrolux" not in text or _has_any(text, "stock", "market"):
        return False
    if _has_any(text, "competitor", "compare", "analysis", "upload"):
        return False
    return _has_any(
        text,
        "investor relations",
        "investment report",
        "pdf checksum",
        "fetch verify",
        "investor pdf",
    )


def _sales_approval(text: str) -> bool:
    return _has_all(text, "sales", "approval")


def _google_photos(text: str) -> bool:
    return "google photos" in text


def _block_test(text: str) -> bool:
    return "block" in text and _has_any(text, "test", "deterministic")


def _balancing(text: str) -> bool:
    return _has_any(text, "balancing brains", "ball balancing")


def _commercial_loan(text: str) -> bool:
    return "commercial loan" in text


RULES: list[dict[str, Any]] = [
    {
        "key": "pdf_rag_approval",
        "title": "PDF RAG Report Approval Loop",
        "projects": {"docling-rag", None},
        "match": _pdf_rag,
    },
    {
        "key": "electrolux_stock",
        "title": "Electrolux Stock Data Harvester",
        "projects": {None},
        "match": _electrolux_stock,
    },
    {
        "key": "electrolux_ir",
        "title": "Electrolux IR Report Harvester",
        "projects": {None},
        "match": _electrolux_ir,
    },
    {
        "key": "sales_approval",
        "title": "Sales Report Approval",
        "projects": {None},
        "match": _sales_approval,
    },
    {
        "key": "google_photos",
        "title": "Google Photos Album Scraper",
        "projects": {None},
        "match": _google_photos,
    },
    {
        "key": "block_test",
        "title": "Deterministic Block Test",
        "projects": {None},
        "match": _block_test,
    },
    {
        "key": "balancing_brains",
        "title": "ANN2SNN Balancing Brains Demo",
        "projects": {"docling-rag"},
        "match": _balancing,
    },
    {
        "key": "commercial_loan",
        "title": "Commercial Loan Credit Recommendation",
        "projects": {"banking-architecture"},
        "match": _commercial_loan,
    },
]


def _canonical_key(group: WorkflowGroup, locked: bool, run_count: int):
    return (
        0 if locked else 1,
        -run_count,
        group.created_at or datetime.min,
    )


async def build_regroup_proposals(
    db: AsyncSession,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    groups_result = await db.execute(select(WorkflowGroup))
    groups = list(groups_result.scalars().all())
    runs_result = await db.execute(select(MasterDAG))
    runs = list(runs_result.scalars().all())

    runs_by_group: dict[str, list[MasterDAG]] = defaultdict(list)
    for dag in runs:
        if dag.workflow_group_id:
            runs_by_group[dag.workflow_group_id].append(dag)

    def visible_runs(group_id: str) -> list[MasterDAG]:
        group_runs = runs_by_group.get(group_id, [])
        if include_archived:
            return group_runs
        return [dag for dag in group_runs if not dag.archived]

    proposals: list[dict[str, Any]] = []
    claimed: set[str] = set()

    for rule in RULES:
        allowed_projects = rule.get("projects", set())
        candidates = []
        for group in groups:
            if group.id in claimed:
                continue
            if group.project_id not in allowed_projects:
                continue
            if not rule["match"](_text(group)):
                continue
            if not visible_runs(group.id):
                continue
            candidates.append(group)

        if len(candidates) < 2:
            continue

        locked_ids = {
            group.id
            for group in candidates
            if any(bool(dag.locked) for dag in runs_by_group.get(group.id, []))
        }
        canonical = min(
            candidates,
            key=lambda group: _canonical_key(
                group,
                group.id in locked_ids,
                len(visible_runs(group.id)),
            ),
        )
        members = [group for group in candidates if group.id != canonical.id]
        member_run_count = sum(len(visible_runs(group.id)) for group in members)

        proposals.append({
            "key": rule["key"],
            "canonical_group_id": canonical.id,
            "canonical_title": rule["title"],
            "canonical_current_title": canonical.title,
            "project_id": canonical.project_id,
            "member_group_ids": [group.id for group in members],
            "members": [
                {
                    "id": group.id,
                    "title": group.title,
                    "run_count": len(visible_runs(group.id)),
                }
                for group in members
            ],
            "canonical_run_count": len(visible_runs(canonical.id)),
            "total_runs": len(visible_runs(canonical.id)) + member_run_count,
        })
        claimed.update(group.id for group in candidates)

    return proposals


async def apply_regroup_proposals(db: AsyncSession, proposals: list[dict[str, Any]]) -> dict[str, Any]:
    moved_runs = 0
    merged_groups = 0

    for proposal in proposals:
        canonical_id = proposal["canonical_group_id"]
        member_ids = proposal.get("member_group_ids") or []
        if not member_ids:
            continue

        result = await db.execute(
            select(WorkflowGroup).where(WorkflowGroup.id == canonical_id)
        )
        canonical = result.scalar_one_or_none()
        if not canonical:
            continue

        moved = await db.execute(
            update(MasterDAG)
            .where(MasterDAG.workflow_group_id.in_(member_ids))
            .values(workflow_group_id=canonical_id)
        )
        moved_runs += moved.rowcount or 0

        members_result = await db.execute(
            select(WorkflowGroup).where(WorkflowGroup.id.in_(member_ids))
        )
        for member in members_result.scalars().all():
            await db.delete(member)
            merged_groups += 1

        canonical.title = proposal["canonical_title"]
        canonical.title_source = "manual"

    await db.commit()
    logger.info(
        "Regrouped workflows: %s groups merged, %s runs moved",
        merged_groups,
        moved_runs,
    )
    return {
        "applied": len(proposals),
        "merged_groups": merged_groups,
        "moved_runs": moved_runs,
    }
