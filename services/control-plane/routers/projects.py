"""
Projects router — DAG/skill namespaces.

A project segregates DAGs (and the skills learned from them). Examples map to
projects so different showcase/tenant workflows stay isolated.
"""
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime

from database import get_db
from models import Project

router = APIRouter()


def _serialize(p: Project) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description or "",
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


@router.get("")
async def list_projects(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).order_by(Project.name))
    return [_serialize(p) for p in result.scalars().all()]


@router.post("", status_code=201)
async def create_project(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    pid = str(body.get("id") or "").strip()
    name = str(body.get("name") or "").strip()
    if not pid or not name:
        raise HTTPException(status_code=422, detail="id (slug) and name are required")
    existing = await db.get(Project, pid)
    if existing:
        raise HTTPException(status_code=409, detail=f"Project '{pid}' already exists")
    project = Project(
        id=pid,
        name=name,
        description=str(body.get("description") or ""),
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return _serialize(project)


@router.patch("/{project_id}")
async def update_project(project_id: str, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    if "name" in body:
        project.name = str(body["name"])
    if "description" in body:
        project.description = str(body["description"])
    await db.commit()
    await db.refresh(project)
    return _serialize(project)


@router.delete("/{project_id}")
async def delete_project(project_id: str, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    await db.delete(project)
    await db.commit()
    return {"ok": True}
