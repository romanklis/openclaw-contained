"""
Skill Registry — CRUD for reusable skill templates.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import Skill
from schemas import SkillCreate, SkillUpdate, SkillResponse
import uuid
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


def _gen_skill_id() -> str:
    return f"skill-{uuid.uuid4().hex[:8]}"


@router.post("", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
async def create_skill(data: SkillCreate, db: AsyncSession = Depends(get_db)):
    """Create a new skill template."""
    # Check name uniqueness
    existing = await db.execute(select(Skill).where(Skill.name == data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Skill with name '{data.name}' already exists")

    skill = Skill(
        id=_gen_skill_id(),
        name=data.name,
        description=data.description,
        input_schema=data.input_schema,
        output_artifacts=data.output_artifacts,
        steps=[s.model_dump() for s in data.steps],
        tags=data.tags,
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return skill


@router.get("", response_model=list[SkillResponse])
async def list_skills(
    tag: str | None = Query(None, description="Filter by tag"),
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List all skills, optionally filtered by tag."""
    query = select(Skill).offset(skip).limit(limit)
    result = await db.execute(query)
    skills = list(result.scalars().all())

    if tag:
        skills = [s for s in skills if tag in (s.tags or [])]

    return skills


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(skill_id: str, db: AsyncSession = Depends(get_db)):
    """Get a single skill by ID."""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found")
    return skill


@router.put("/{skill_id}", response_model=SkillResponse)
async def update_skill(skill_id: str, data: SkillUpdate, db: AsyncSession = Depends(get_db)):
    """Update a skill (bumps version)."""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found")

    if data.description is not None:
        skill.description = data.description
    if data.input_schema is not None:
        skill.input_schema = data.input_schema
    if data.output_artifacts is not None:
        skill.output_artifacts = data.output_artifacts
    if data.steps is not None:
        skill.steps = [s.model_dump() for s in data.steps]
    if data.tags is not None:
        skill.tags = data.tags

    skill.version = (skill.version or 1) + 1
    await db.commit()
    await db.refresh(skill)
    return skill


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(skill_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a skill."""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found")
    await db.delete(skill)
    await db.commit()


@router.post("/seed", status_code=status.HTTP_200_OK)
async def seed_skills(db: AsyncSession = Depends(get_db)):
    """Seed built-in skill templates."""
    built_in = [
        {
            "name": "research",
            "description": "Research a topic: search for information, extract content, and summarize findings.",
            "input_schema": {"topic": "string", "depth": "string (brief|detailed)"},
            "output_artifacts": ["research_summary.md", "sources.json"],
            "steps": [
                {"step_id": "search", "name": "Search", "description": "Search the web or documentation for relevant information", "base_image": "nanobot", "tool_hints": ["web_search"]},
                {"step_id": "extract", "name": "Extract", "description": "Extract and organize key information from search results", "base_image": "nanobot", "tool_hints": ["read", "write"]},
                {"step_id": "summarize", "name": "Summarize", "description": "Produce a structured summary of findings", "base_image": "nanobot", "tool_hints": ["write"]},
            ],
            "tags": ["research", "analysis", "information-gathering"],
        },
        {
            "name": "build-webapp",
            "description": "Plan, implement, and validate a web application.",
            "input_schema": {"requirements": "string", "framework": "string (optional)"},
            "output_artifacts": ["app/", "README.md"],
            "steps": [
                {"step_id": "plan", "name": "Plan Architecture", "description": "Design the application architecture, choose frameworks, define file structure", "base_image": "openclaw", "tool_hints": ["write"]},
                {"step_id": "implement", "name": "Implement Code", "description": "Write the application code based on the plan", "base_image": "openclaw", "tool_hints": ["write", "exec"]},
                {"step_id": "validate", "name": "Validate", "description": "Run syntax checks, linting, and basic validation", "base_image": "openclaw", "tool_hints": ["exec"]},
            ],
            "tags": ["development", "web", "application"],
        },
        {
            "name": "write-tests",
            "description": "Analyze existing code and generate comprehensive tests.",
            "input_schema": {"code_path": "string", "test_framework": "string (optional)"},
            "output_artifacts": ["tests/", "test_report.md"],
            "steps": [
                {"step_id": "analyze", "name": "Analyze Code", "description": "Read and understand the codebase structure and logic", "base_image": "openclaw", "tool_hints": ["read"]},
                {"step_id": "generate", "name": "Generate Tests", "description": "Write test cases covering key functionality", "base_image": "openclaw", "tool_hints": ["write"]},
                {"step_id": "run", "name": "Run Tests", "description": "Execute tests and report results", "base_image": "openclaw", "tool_hints": ["exec"]},
            ],
            "tags": ["testing", "quality", "verification"],
        },
        {
            "name": "code-review",
            "description": "Review code quality, security, and best practices.",
            "input_schema": {"code_path": "string", "focus_areas": "string (optional)"},
            "output_artifacts": ["review_report.md", "REVIEW_VERDICT.md"],
            "steps": [
                {"step_id": "read", "name": "Read Code", "description": "Read and understand the code to be reviewed", "base_image": "nanobot", "tool_hints": ["read"]},
                {"step_id": "analyze", "name": "Analyze Quality", "description": "Check code quality, patterns, security issues, and best practices", "base_image": "nanobot", "tool_hints": ["read", "write"]},
                {"step_id": "verdict", "name": "Write Verdict", "description": "Produce a review report with PASS/FAIL verdict", "base_image": "nanobot", "tool_hints": ["write"]},
            ],
            "tags": ["review", "quality", "security"],
        },
        {
            "name": "deploy",
            "description": "Prepare and deploy an application as a running service.",
            "input_schema": {"app_path": "string", "port": "integer", "entrypoint": "string"},
            "output_artifacts": ["Dockerfile", "deployment_config.json"],
            "steps": [
                {"step_id": "prepare", "name": "Prepare Deployment", "description": "Create Dockerfile and deployment configuration", "base_image": "openclaw", "tool_hints": ["write"]},
                {"step_id": "build", "name": "Build Container", "description": "Build the deployment container image", "base_image": "openclaw", "tool_hints": ["exec"]},
                {"step_id": "start", "name": "Start Service", "description": "Deploy and start the service", "base_image": "openclaw", "tool_hints": ["exec"]},
            ],
            "tags": ["deployment", "devops", "infrastructure"],
        },
        {
            "name": "write-script",
            "description": "Plan, implement, and test a standalone script.",
            "input_schema": {"task_description": "string", "language": "string (optional)"},
            "output_artifacts": ["script.*", "output.txt"],
            "steps": [
                {"step_id": "plan", "name": "Plan Script", "description": "Plan the script structure and approach", "base_image": "nanobot", "tool_hints": ["write"]},
                {"step_id": "implement", "name": "Implement Script", "description": "Write the script code", "base_image": "openclaw", "tool_hints": ["write"]},
                {"step_id": "test", "name": "Test Script", "description": "Run the script and verify output", "base_image": "openclaw", "tool_hints": ["exec"]},
            ],
            "tags": ["scripting", "automation"],
        },
    ]

    created = []
    skipped = []
    for skill_def in built_in:
        existing = await db.execute(select(Skill).where(Skill.name == skill_def["name"]))
        if existing.scalar_one_or_none():
            skipped.append(skill_def["name"])
            continue

        skill = Skill(
            id=_gen_skill_id(),
            name=skill_def["name"],
            description=skill_def["description"],
            input_schema=skill_def["input_schema"],
            output_artifacts=skill_def["output_artifacts"],
            steps=skill_def["steps"],
            tags=skill_def["tags"],
        )
        db.add(skill)
        created.append(skill_def["name"])

    await db.commit()
    return {"created": created, "skipped": skipped}
