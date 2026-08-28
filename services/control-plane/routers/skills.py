"""
Skill Registry — CRUD for reusable skill templates.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import Skill
from schemas import SkillCreate, SkillUpdate, SkillResponse
import uuid
import logging
import zipfile
import io
import re
from sqlalchemy import update as sa_update

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
        instructions=data.instructions or "",
        input_schema=data.input_schema,
        output_artifacts=data.output_artifacts,
        steps=[s.model_dump() for s in data.steps],
        tags=data.tags,
        source_url=data.source_url or "",
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
    if data.instructions is not None:
        skill.instructions = data.instructions
    if data.input_schema is not None:
        skill.input_schema = data.input_schema
    if data.output_artifacts is not None:
        skill.output_artifacts = data.output_artifacts
    if data.steps is not None:
        skill.steps = [s.model_dump() for s in data.steps]
    if data.tags is not None:
        skill.tags = data.tags
    if data.source_url is not None:
        skill.source_url = data.source_url

    skill.version = (skill.version or 1) + 1
    await db.commit()
    await db.refresh(skill)
    return skill


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(skill_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a skill. Nullifies any DAG node references first."""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found")
    # Nullify FK references in dag_nodes so delete doesn't violate constraint
    from models import DAGNode
    await db.execute(
        sa_update(DAGNode).where(DAGNode.skill_id == skill_id).values(skill_id=None)
    )
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
        {
            "name": "integrate-octave-simulation-stack",
            "description": "Integrate an Octave simulation backend with a Python Flask API and a frontend data flow.",
            "instructions": (
                "Use this skill when a project needs Octave simulation code served through Python and consumed by a UI.\n\n"
                "Integration checklist:\n"
                "1. Define a stable simulation contract (inputs, units, defaults, output schema).\n"
                "2. Keep Octave computations in dedicated scripts/functions and call them from Python using subprocess or oct2py-style adapters.\n"
                "3. Convert Octave outputs into JSON-safe structures in Python (lists, dicts, scalar metadata).\n"
                "4. Expose Flask routes for health, parameter validation, simulation run, and result fetch.\n"
                "5. Add CORS and versioned API paths for frontend compatibility.\n"
                "6. Provide frontend integration notes: payload shape, polling/streaming approach, and error states.\n"
                "7. Ship quick validation artifacts: sample request/response JSON, endpoint tests, and a runbook."
            ),
            "input_schema": {
                "simulation_model_path": "string",
                "api_requirements": "string",
                "frontend_data_requirements": "string",
            },
            "output_artifacts": [
                "app.py",
                "simulation_bridge.py",
                "octave/",
                "api_contract.json",
                "frontend_integration.md",
                "tests/test_api.py",
            ],
            "steps": [
                {
                    "step_id": "contract",
                    "name": "Define Simulation Contract",
                    "description": "Specify simulation input/output schema and validation rules shared across Octave, API, and frontend.",
                    "base_image": "octaveclaw",
                    "tool_hints": ["read", "write"],
                },
                {
                    "step_id": "bridge",
                    "name": "Implement Octave-Python Bridge",
                    "description": "Implement Python bridge layer that executes Octave simulation functions and normalizes outputs.",
                    "base_image": "octaveclaw",
                    "tool_hints": ["write", "exec"],
                },
                {
                    "step_id": "api",
                    "name": "Expose Flask API",
                    "description": "Build Flask endpoints for health, simulation execution, and structured result delivery.",
                    "base_image": "octaveclaw",
                    "tool_hints": ["write", "exec"],
                },
                {
                    "step_id": "frontend-handoff",
                    "name": "Document Frontend Integration",
                    "description": "Provide frontend payload examples, fetch flow, and UI error/retry behavior for simulation results.",
                    "base_image": "openclaw",
                    "tool_hints": ["write"],
                },
                {
                    "step_id": "validate",
                    "name": "Validate End-to-End",
                    "description": "Run API checks and sample simulations to verify Octave-to-API-to-frontend data continuity.",
                    "base_image": "octaveclaw",
                    "tool_hints": ["exec", "write"],
                },
            ],
            "tags": ["octave", "flask", "simulation", "frontend", "integration"],
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
            instructions=skill_def.get("instructions", ""),
            input_schema=skill_def["input_schema"],
            output_artifacts=skill_def["output_artifacts"],
            steps=skill_def["steps"],
            tags=skill_def["tags"],
        )
        db.add(skill)
        created.append(skill_def["name"])

    await db.commit()
    return {"created": created, "skipped": skipped}


def _parse_skill_md(content: str) -> dict:
    """Parse a SKILL.md file to extract name, description from YAML frontmatter and body."""
    result = {"name": "", "description": "", "instructions": content}

    # Try YAML frontmatter (--- ... ---)
    fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
    if fm_match:
        frontmatter = fm_match.group(1)
        for line in frontmatter.split('\n'):
            line = line.strip()
            if line.startswith('name:'):
                result["name"] = line[5:].strip().strip('"').strip("'")
            elif line.startswith('description:'):
                result["description"] = line[12:].strip().strip('"').strip("'")

    # Fallback: extract name from first H1 heading
    if not result["name"]:
        h1_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        if h1_match:
            result["name"] = h1_match.group(1).strip()

    # Fallback: extract description from first paragraph after H1
    if not result["description"]:
        desc_match = re.search(r'^#\s+.+\n+(.+)$', content, re.MULTILINE)
        if desc_match:
            result["description"] = desc_match.group(1).strip()[:500]

    return result


@router.post("/import", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
async def import_skill_zip(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Import a skill from a ClawHub zip file.

    Extracts SKILL.md, parses name/description from frontmatter,
    stores the full content as instructions.
    """
    if not file.filename or not file.filename.endswith('.zip'):
        raise HTTPException(status_code=400, detail="File must be a .zip archive")

    # Read and validate zip
    zip_bytes = await file.read()
    if len(zip_bytes) > 50 * 1024 * 1024:  # 50MB limit (matches ClawHub)
        raise HTTPException(status_code=400, detail="File too large (max 50MB)")

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")

    # Find SKILL.md (may be at root or in a subdirectory)
    skill_md_path = None
    for name in zf.namelist():
        if name.endswith('SKILL.md') and not name.startswith('__MACOSX'):
            skill_md_path = name
            break

    if not skill_md_path:
        raise HTTPException(status_code=400, detail="No SKILL.md found in zip")

    skill_content = zf.read(skill_md_path).decode('utf-8', errors='replace')
    parsed = _parse_skill_md(skill_content)

    if not parsed["name"]:
        # Use filename as fallback
        parsed["name"] = file.filename.replace('.zip', '').strip()

    if not parsed["name"]:
        raise HTTPException(status_code=400, detail="Could not determine skill name from SKILL.md")

    # Slugify the name for uniqueness check
    slug = re.sub(r'[^a-z0-9-]', '-', parsed["name"].lower()).strip('-')
    slug = re.sub(r'-+', '-', slug)

    # Check if skill with this name already exists — update it
    existing = await db.execute(select(Skill).where(Skill.name == slug))
    existing_skill = existing.scalar_one_or_none()
    if existing_skill:
        existing_skill.description = parsed["description"]
        existing_skill.instructions = parsed["instructions"]
        existing_skill.version = (existing_skill.version or 1) + 1
        await db.commit()
        await db.refresh(existing_skill)
        logger.info(f"Updated existing skill '{slug}' from zip import")
        return existing_skill

    skill = Skill(
        id=_gen_skill_id(),
        name=slug,
        description=parsed["description"],
        instructions=parsed["instructions"],
        source_url=f"clawhub.ai/{slug}",
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    logger.info(f"Imported new skill '{slug}' from zip")
    return skill
