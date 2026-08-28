"""
Node Environments — Reusable execution environments with capability tracking.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import NodeEnvironment
from schemas import NodeEnvironmentCreate, NodeEnvironmentFork, NodeEnvironmentResponse
import uuid
import hashlib
import json
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


def _gen_env_id() -> str:
    return f"env-{uuid.uuid4().hex[:8]}"


def _compute_fingerprint(capabilities: list[str], base_image: str) -> str:
    """Compute a deterministic fingerprint from sorted capabilities + base image."""
    data = json.dumps({"base_image": base_image, "capabilities": sorted(capabilities)}, sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()[:16]


@router.post("", response_model=NodeEnvironmentResponse, status_code=status.HTTP_201_CREATED)
async def create_environment(data: NodeEnvironmentCreate, db: AsyncSession = Depends(get_db)):
    """Create a new reusable execution environment."""
    fingerprint = _compute_fingerprint(data.capabilities, data.base_image)

    # Check for existing environment with same fingerprint
    existing = await db.execute(
        select(NodeEnvironment).where(NodeEnvironment.capability_fingerprint == fingerprint)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Environment with identical capabilities already exists (fingerprint: {fingerprint})"
        )

    env = NodeEnvironment(
        id=_gen_env_id(),
        name=data.name,
        description=data.description,
        base_image=data.base_image,
        capabilities=data.capabilities,
        capability_fingerprint=fingerprint,
        current_image_tag=f"localhost:5000/openclaw-agent:{data.base_image}",
        version=0,
    )
    db.add(env)
    await db.commit()
    await db.refresh(env)
    return env


@router.get("", response_model=list[NodeEnvironmentResponse])
async def list_environments(
    capability: str | None = Query(None, description="Filter by capability substring"),
    base_image: str | None = Query(None, description="Filter by base image"),
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List available execution environments."""
    query = select(NodeEnvironment).offset(skip).limit(limit)
    if base_image:
        query = query.where(NodeEnvironment.base_image == base_image)
    result = await db.execute(query)
    envs = list(result.scalars().all())

    if capability:
        envs = [e for e in envs if any(capability in c for c in (e.capabilities or []))]

    return envs


@router.get("/{env_id}", response_model=NodeEnvironmentResponse)
async def get_environment(env_id: str, db: AsyncSession = Depends(get_db)):
    """Get an environment by ID."""
    result = await db.execute(select(NodeEnvironment).where(NodeEnvironment.id == env_id))
    env = result.scalar_one_or_none()
    if not env:
        raise HTTPException(status_code=404, detail=f"Environment {env_id} not found")
    return env


@router.post("/{env_id}/fork", response_model=NodeEnvironmentResponse, status_code=status.HTTP_201_CREATED)
async def fork_environment(env_id: str, data: NodeEnvironmentFork, db: AsyncSession = Depends(get_db)):
    """Fork an existing environment for modification."""
    result = await db.execute(select(NodeEnvironment).where(NodeEnvironment.id == env_id))
    parent = result.scalar_one_or_none()
    if not parent:
        raise HTTPException(status_code=404, detail=f"Environment {env_id} not found")

    forked = NodeEnvironment(
        id=_gen_env_id(),
        name=data.name,
        description=data.description or f"Forked from {parent.name}",
        base_image=parent.base_image,
        capabilities=list(parent.capabilities or []),
        capability_fingerprint=parent.capability_fingerprint,
        current_image_tag=parent.current_image_tag,
        version=parent.version,
        parent_env_id=parent.id,
    )
    db.add(forked)
    await db.commit()
    await db.refresh(forked)
    return forked
