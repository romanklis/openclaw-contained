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
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import logging

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


class AgentImageUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tag: Optional[str] = None
    enabled: Optional[bool] = None


class AgentImageResponse(BaseModel):
    id: str
    name: str
    description: str
    tag: str
    enabled: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=list[AgentImageResponse])
async def list_agent_images(
    enabled_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """List all registered agent images."""
    query = select(AgentImage)
    if enabled_only:
        query = query.where(AgentImage.enabled.is_(True))
    result = await db.execute(query)
    return list(result.scalars().all())


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


@router.get("/{image_id}", response_model=AgentImageResponse)
async def get_agent_image(image_id: str, db: AsyncSession = Depends(get_db)):
    """Get a single agent image by ID."""
    result = await db.execute(select(AgentImage).where(AgentImage.id == image_id))
    img = result.scalar_one_or_none()
    if not img:
        raise HTTPException(status_code=404, detail=f"Agent image '{image_id}' not found")
    return img


@router.patch("/{image_id}", response_model=AgentImageResponse)
async def update_agent_image(
    image_id: str,
    data: AgentImageUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update an agent image (e.g. enable/disable, update description)."""
    result = await db.execute(select(AgentImage).where(AgentImage.id == image_id))
    img = result.scalar_one_or_none()
    if not img:
        raise HTTPException(status_code=404, detail=f"Agent image '{image_id}' not found")

    for field, value in data.model_dump(exclude_none=True).items():
        setattr(img, field, value)

    await db.commit()
    await db.refresh(img)
    return img


@router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_image(image_id: str, db: AsyncSession = Depends(get_db)):
    """Remove an agent image from the registry."""
    result = await db.execute(select(AgentImage).where(AgentImage.id == image_id))
    img = result.scalar_one_or_none()
    if not img:
        raise HTTPException(status_code=404, detail=f"Agent image '{image_id}' not found")

    await db.delete(img)
    await db.commit()
