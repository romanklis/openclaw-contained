"""
SBOM (Software Bill of Materials) endpoints.

Provides:
- GET  /api/tasks/{id}/sbom             — latest SBOM for a task
- GET  /api/tasks/{id}/sbom?version=N   — SBOM for a specific image version
- GET  /api/tasks/{id}/sbom/diff        — diff between two SBOM versions
- GET  /api/sbom/search                 — find tasks containing a specific package
- POST /api/sbom                        — store an SBOM (called by image-builder)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
import logging

from database import get_db
from models import SBOM, SBOMFormat, Task
from schemas import (
    SBOMResponse,
    SBOMDetailResponse,
    SBOMPackage,
    SBOMSearchResult,
    SBOMDiffEntry,
    SBOMDiffResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sbom"])


# ── Helpers ──────────────────────────────────────────────────────────────────

def _sbom_to_response(sbom: SBOM, include_document: bool = False) -> dict:
    """Convert an ORM SBOM to a dict suitable for the response model."""
    data = {
        "id": sbom.id,
        "task_id": sbom.task_id,
        "image_tag": sbom.image_tag,
        "image_version": sbom.image_version,
        "format": sbom.format.value if isinstance(sbom.format, SBOMFormat) else sbom.format,
        "packages": sbom.packages or [],
        "generator": sbom.generator,
        "generated_at": sbom.generated_at,
    }
    if include_document:
        data["document"] = sbom.document
    return data


# ── Ingest (called by image-builder after Trivy scan) ───────────────────────

from pydantic import BaseModel, Field
from typing import Any, Dict


class SBOMCreate(BaseModel):
    task_id: str
    image_tag: str
    image_version: int
    format: str  # "spdx-json" or "cyclonedx-json"
    document: Dict[str, Any]
    packages: List[SBOMPackage] = Field(default_factory=list)
    generator: str = "trivy"


@router.post("/api/sbom", status_code=201)
async def create_sbom(payload: SBOMCreate, db: AsyncSession = Depends(get_db)):
    """Store an SBOM document.  Called by the image-builder after a scan."""
    try:
        fmt = SBOMFormat(payload.format)
    except ValueError:
        raise HTTPException(400, f"Unsupported SBOM format: {payload.format}")

    sbom = SBOM(
        task_id=payload.task_id,
        image_tag=payload.image_tag,
        image_version=payload.image_version,
        format=fmt,
        document=payload.document,
        packages=[p.model_dump() for p in payload.packages],
        generator=payload.generator,
    )
    db.add(sbom)
    await db.flush()
    await db.refresh(sbom)
    logger.info("Stored SBOM id=%s task=%s version=%s (%d packages)",
                sbom.id, sbom.task_id, sbom.image_version, len(sbom.packages))
    return _sbom_to_response(sbom)


# ── Per-task SBOM retrieval ──────────────────────────────────────────────────

@router.get("/api/tasks/{task_id}/sbom", response_model=SBOMDetailResponse)
async def get_task_sbom(
    task_id: str,
    version: Optional[int] = Query(None, description="Image version (default: latest)"),
    format: Optional[str] = Query(None, description="Filter by SBOM format (spdx-json or cyclonedx-json)"),
    db: AsyncSession = Depends(get_db),
):
    """Return the SBOM for a task's container image.

    Without `version`, returns the latest.  Optionally filter by format.
    """
    # Verify task exists
    task_result = await db.execute(select(Task).where(Task.id == task_id))
    if not task_result.scalar_one_or_none():
        raise HTTPException(404, "Task not found")

    query = select(SBOM).where(SBOM.task_id == task_id)
    if version is not None:
        query = query.where(SBOM.image_version == version)
    if format:
        try:
            fmt = SBOMFormat(format)
            query = query.where(SBOM.format == fmt)
        except ValueError:
            raise HTTPException(400, f"Unknown format: {format}")

    query = query.order_by(SBOM.image_version.desc(), SBOM.id.desc()).limit(1)
    result = await db.execute(query)
    sbom = result.scalar_one_or_none()

    if not sbom:
        raise HTTPException(404, "No SBOM found for this task/version")

    return _sbom_to_response(sbom, include_document=True)


@router.get("/api/tasks/{task_id}/sbom/all", response_model=List[SBOMResponse])
async def list_task_sboms(
    task_id: str,
    db: AsyncSession = Depends(get_db),
):
    """List all SBOM versions for a task (without full document)."""
    result = await db.execute(
        select(SBOM)
        .where(SBOM.task_id == task_id)
        .order_by(SBOM.image_version.asc())
    )
    sboms = result.scalars().all()
    return [_sbom_to_response(s) for s in sboms]


# ── SBOM diff between versions ──────────────────────────────────────────────

@router.get("/api/tasks/{task_id}/sbom/diff", response_model=SBOMDiffResponse)
async def diff_sbom_versions(
    task_id: str,
    from_version: int = Query(..., description="Earlier image version"),
    to_version: int = Query(..., description="Later image version"),
    db: AsyncSession = Depends(get_db),
):
    """Show what packages were added, removed, or changed between two image versions."""
    result = await db.execute(
        select(SBOM).where(
            and_(SBOM.task_id == task_id, SBOM.image_version.in_([from_version, to_version]))
        )
    )
    sboms = {s.image_version: s for s in result.scalars().all()}

    if from_version not in sboms:
        raise HTTPException(404, f"No SBOM found for version {from_version}")
    if to_version not in sboms:
        raise HTTPException(404, f"No SBOM found for version {to_version}")

    old_pkgs = {(p["name"], p.get("type", "")): p for p in (sboms[from_version].packages or [])}
    new_pkgs = {(p["name"], p.get("type", "")): p for p in (sboms[to_version].packages or [])}

    changes: list[SBOMDiffEntry] = []

    # Removed
    for key, pkg in old_pkgs.items():
        if key not in new_pkgs:
            changes.append(SBOMDiffEntry(
                change="removed",
                name=pkg["name"],
                type=pkg.get("type"),
                old_version=pkg.get("version"),
            ))

    # Added or changed
    for key, pkg in new_pkgs.items():
        if key not in old_pkgs:
            changes.append(SBOMDiffEntry(
                change="added",
                name=pkg["name"],
                type=pkg.get("type"),
                new_version=pkg.get("version"),
            ))
        else:
            old_ver = old_pkgs[key].get("version")
            new_ver = pkg.get("version")
            if old_ver != new_ver:
                changes.append(SBOMDiffEntry(
                    change="changed",
                    name=pkg["name"],
                    type=pkg.get("type"),
                    old_version=old_ver,
                    new_version=new_ver,
                ))

    # Sort: added first, then changed, then removed
    order = {"added": 0, "changed": 1, "removed": 2}
    changes.sort(key=lambda c: (order.get(c.change, 9), c.name))

    return SBOMDiffResponse(
        task_id=task_id,
        from_version=from_version,
        to_version=to_version,
        changes=changes,
    )


# ── Cross-task package search ────────────────────────────────────────────────

@router.get("/api/sbom/search", response_model=List[SBOMSearchResult])
async def search_sbom_packages(
    package: str = Query(..., min_length=1, description="Package name to search for"),
    version: Optional[str] = Query(None, description="Exact version to match"),
    type: Optional[str] = Query(None, description="Package type (pip, apt, npm)"),
    db: AsyncSession = Depends(get_db),
):
    """Find all tasks whose container images include a specific package.

    Scans the denormalised `packages` JSON column.  Useful for CVE triage:
    'Which tasks use requests==2.31.0?'
    """
    # Fetch all SBOMs (only latest per task) and filter in Python.
    # For very large deployments a GIN index on `packages` would be better,
    # but this is pragmatic for the expected scale.
    subq = (
        select(SBOM.task_id, sa_func.max(SBOM.image_version).label("max_ver"))
        .group_by(SBOM.task_id)
        .subquery()
    )
    query = (
        select(SBOM)
        .join(subq, and_(
            SBOM.task_id == subq.c.task_id,
            SBOM.image_version == subq.c.max_ver,
        ))
    )
    result = await db.execute(query)
    hits: list[SBOMSearchResult] = []

    package_lower = package.lower()
    for sbom in result.scalars().all():
        for pkg in (sbom.packages or []):
            name_match = pkg.get("name", "").lower() == package_lower
            if not name_match:
                continue
            if version and pkg.get("version") != version:
                continue
            if type and pkg.get("type", "").lower() != type.lower():
                continue
            hits.append(SBOMSearchResult(
                sbom_id=sbom.id,
                task_id=sbom.task_id,
                image_tag=sbom.image_tag,
                image_version=sbom.image_version,
                package_name=pkg["name"],
                package_version=pkg.get("version"),
                package_type=pkg.get("type"),
                package_license=pkg.get("license"),
                generated_at=sbom.generated_at,
            ))

    return hits
