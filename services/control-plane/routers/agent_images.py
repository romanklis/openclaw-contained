"""
Agent Images Registry — CRUD for named base images available to the planner.

Users nominate post-build images here so the planner LLM can discover and
select the most suitable image for each DAG node based on the description.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import AgentImage
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from pathlib import Path
import logging
import yaml

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AgentImageCreate(BaseModel):
    id: str          # e.g. "browser"  — becomes the base_image value in DAG nodes
    name: str        # e.g. "Web Agent"
    description: str # shown to the planner LLM
    tag: str = ""    # e.g. "openclaw-agent:browser"
    enabled: bool = True
    runtime: str = ""
    capabilities: list[str] = Field(default_factory=list)
    best_for: list[str] = Field(default_factory=list)
    avoid_for: list[str] = Field(default_factory=list)


class AgentImageUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tag: Optional[str] = None
    enabled: Optional[bool] = None
    runtime: Optional[str] = None
    capabilities: Optional[list[str]] = None
    best_for: Optional[list[str]] = None
    avoid_for: Optional[list[str]] = None


class AgentImageResponse(BaseModel):
    id: str
    name: str
    description: str
    tag: str
    enabled: bool
    runtime: str
    capabilities: list[str]
    best_for: list[str]
    avoid_for: list[str]
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AgentImageSyncResponse(BaseModel):
    created: int
    updated: int
    skipped: int
    source: str


async def _get_image_or_404(image_id: str, db: AsyncSession) -> AgentImage:
    result = await db.execute(select(AgentImage).where(AgentImage.id == image_id))
    img = result.scalar_one_or_none()
    if not img:
        raise HTTPException(status_code=404, detail=f"Agent image '{image_id}' not found")
    return img


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=list[AgentImageResponse])
async def list_agent_images(
    enabled_only: bool = False,
    capability: Optional[str] = None,
    use_case: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """List all registered agent images."""
    query = select(AgentImage)
    if enabled_only:
        query = query.where(AgentImage.enabled.is_(True))
    result = await db.execute(query)
    rows = list(result.scalars().all())

    if capability:
        needle = capability.strip().lower()
        rows = [
            img for img in rows
            if any(needle in c.lower() for c in (img.capabilities or []))
        ]

    if use_case:
        needle = use_case.strip().lower()
        rows = [
            img for img in rows
            if any(needle in c.lower() for c in (img.best_for or []))
        ]

    return rows


@router.post("", response_model=AgentImageResponse, status_code=status.HTTP_201_CREATED)
async def create_agent_image(data: AgentImageCreate, db: AsyncSession = Depends(get_db)):
    """Register a new agent image (nominate a post-build image as a base image)."""
    existing = await db.execute(select(AgentImage).where(AgentImage.id == data.id))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Agent image '{data.id}' already exists")

    img = AgentImage(**data.model_dump())
    db.add(img)
    await db.commit()
    await db.refresh(img)
    logger.info("Registered agent image: %s", data.id)
    return img


@router.post("/sync-from-yaml", response_model=AgentImageSyncResponse)
async def sync_agent_images_from_yaml(db: AsyncSession = Depends(get_db)):
    """Upsert image metadata from agent_profiles.yaml, including enabled flags."""
    candidates = [
        Path("/agent-images/agent_profiles.yaml"),
        Path(__file__).resolve().parent.parent.parent.parent / "agent-images" / "agent_profiles.yaml",
    ]

    source = None
    for p in candidates:
        if p.is_file():
            source = p
            break

    if source is None:
        raise HTTPException(status_code=404, detail="agent_profiles.yaml not found")

    data = yaml.safe_load(source.read_text()) or {}
    base_images = data.get("base_images", {})
    if not isinstance(base_images, dict):
        raise HTTPException(status_code=422, detail="Invalid YAML: base_images must be a mapping")

    created = 0
    updated = 0
    skipped = 0

    for img_id, info in base_images.items():
        if not isinstance(info, dict):
            skipped += 1
            continue

        result = await db.execute(select(AgentImage).where(AgentImage.id == img_id))
        existing = result.scalar_one_or_none()
        if existing is None:
            db.add(AgentImage(
                id=img_id,
                name=info.get("name", img_id.capitalize()),
                description=info.get("description", ""),
                tag=info.get("tag", f"openclaw-agent:{img_id}"),
                enabled=bool(info.get("enabled", True)),
                runtime=info.get("runtime", ""),
                capabilities=info.get("capabilities", []),
                best_for=info.get("best_for", []),
                avoid_for=info.get("avoid_for", []),
            ))
            created += 1
            continue

        existing.name = info.get("name", existing.name)
        existing.description = info.get("description", existing.description)
        existing.tag = info.get("tag", existing.tag)
        existing.enabled = bool(info.get("enabled", True))
        existing.runtime = info.get("runtime", existing.runtime)
        existing.capabilities = info.get("capabilities", existing.capabilities)
        existing.best_for = info.get("best_for", existing.best_for)
        existing.avoid_for = info.get("avoid_for", existing.avoid_for)
        updated += 1

    await db.commit()
    logger.info(
        "Synced agent images from YAML: created=%s updated=%s skipped=%s source=%s",
        created,
        updated,
        skipped,
        source,
    )
    return AgentImageSyncResponse(
        created=created,
        updated=updated,
        skipped=skipped,
        source=str(source),
    )


@router.get("/{image_id}", response_model=AgentImageResponse)
async def get_agent_image(image_id: str, db: AsyncSession = Depends(get_db)):
    """Get a single agent image by ID."""
    img = await _get_image_or_404(image_id, db)
    return img


@router.patch("/{image_id}", response_model=AgentImageResponse)
async def update_agent_image(
    image_id: str,
    data: AgentImageUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update an agent image (e.g. enable/disable, update description)."""
    img = await _get_image_or_404(image_id, db)

    for field, value in data.model_dump(exclude_none=True).items():
        setattr(img, field, value)

    await db.commit()
    await db.refresh(img)
    return img


@router.post("/{image_id}/enable", response_model=AgentImageResponse)
async def enable_agent_image(image_id: str, db: AsyncSession = Depends(get_db)):
    """Enable an image so the planner can select it."""
    img = await _get_image_or_404(image_id, db)
    img.enabled = True
    await db.commit()
    await db.refresh(img)
    return img


@router.post("/{image_id}/disable", response_model=AgentImageResponse)
async def disable_agent_image(image_id: str, db: AsyncSession = Depends(get_db)):
    """Disable an image so the planner will not select it."""
    img = await _get_image_or_404(image_id, db)
    img.enabled = False
    await db.commit()
    await db.refresh(img)
    return img


@router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_image(image_id: str, db: AsyncSession = Depends(get_db)):
    """Remove an agent image from the registry."""
    img = await _get_image_or_404(image_id, db)

    await db.delete(img)
    await db.commit()
