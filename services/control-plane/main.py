"""
OpenClaw Control Plane - Main Application Entry Point
"""
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
from sqlalchemy import text

from routers import tasks, capabilities, policies, auth, llm, tasks_extended, deployments, sbom, supply_chain, skills, environments, dags
from routers import openai_dag
from routers import agent_images as agent_images_router
from routers import skill_learning as skill_learning_router
from routers import dag_user_requests
from database import engine, Base, async_session
from config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    logger.info("Starting OpenClaw Control Plane")
    
    # Create database tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Transitional compatibility migration: ensure DAG columns exist on tasks.
        # Some existing DB volumes were created before the DAG schema landed.
        await conn.execute(text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS dag_id VARCHAR"))
        await conn.execute(text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS node_id VARCHAR"))
        # Transitional compatibility migration: ensure AgentImage suitability
        # columns exist so planning can select images by capabilities/use-cases.
        await conn.execute(text("ALTER TABLE agent_images ADD COLUMN IF NOT EXISTS runtime VARCHAR"))
        await conn.execute(text("ALTER TABLE agent_images ADD COLUMN IF NOT EXISTS capabilities JSON"))
        await conn.execute(text("ALTER TABLE agent_images ADD COLUMN IF NOT EXISTS best_for JSON"))
        await conn.execute(text("ALTER TABLE agent_images ADD COLUMN IF NOT EXISTS avoid_for JSON"))
        # v2 skill learning system columns
        await conn.execute(text("ALTER TABLE dag_nodes ADD COLUMN IF NOT EXISTS selected_skill_v2_id VARCHAR"))
        await conn.execute(text("ALTER TABLE dag_nodes ADD COLUMN IF NOT EXISTS skill_selection_reason TEXT"))
        # DAG templating / routines columns
        await conn.execute(text("ALTER TABLE master_dags ADD COLUMN IF NOT EXISTS locked BOOLEAN DEFAULT FALSE"))
        await conn.execute(text("ALTER TABLE master_dags ADD COLUMN IF NOT EXISTS template_params JSON"))
        await conn.execute(text("ALTER TABLE master_dags ADD COLUMN IF NOT EXISTS template_source_dag_id VARCHAR"))
        # DAG archiving (soft delete / declutter)
        await conn.execute(text("ALTER TABLE master_dags ADD COLUMN IF NOT EXISTS archived BOOLEAN DEFAULT FALSE"))
        # Interactive steps: decision / input node type on DAG nodes
        await conn.execute(text("ALTER TABLE dag_nodes ADD COLUMN IF NOT EXISTS node_type VARCHAR DEFAULT 'agent'"))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dag_user_requests (
                id SERIAL PRIMARY KEY,
                dag_id VARCHAR NOT NULL,
                node_id VARCHAR NOT NULL,
                task_id VARCHAR,
                kind VARCHAR NOT NULL,
                prompt TEXT DEFAULT '',
                payload JSON DEFAULT '{}'::json,
                status VARCHAR DEFAULT 'pending',
                answer JSON,
                answered_by VARCHAR,
                created_at TIMESTAMP DEFAULT now(),
                answered_at TIMESTAMP
            )
        """))
        # Task status enum values (kept in sync with models.TaskStatus)
        await conn.execute(text("ALTER TYPE taskstatus ADD VALUE IF NOT EXISTS 'WAITING_APPROVAL'"))
        await conn.execute(text("ALTER TYPE taskstatus ADD VALUE IF NOT EXISTS 'BUILDING_IMAGE'"))

    logger.info("Database initialized")

    # Load persisted DAG model defaults (planning/agent/deep-review models) from
    # the DB at startup so a saved deep-review model selection is honored even
    # before the LLM Router page is visited (previously only reloaded on GET).
    try:
        await dags._load_dag_model_defaults_from_db()
    except Exception as e:
        logger.warning(f"Could not load DAG model defaults at startup: {e}")

    # Auto-seed supply-chain from YAML if DB is empty
    try:
        from sqlalchemy import select
        from models import SupplyChainPackage
        async with async_session() as session:
            result = await session.execute(select(SupplyChainPackage).limit(1))
            if not result.scalar_one_or_none():
                logger.info("Supply-chain DB empty — seeding from YAML…")
                from routers.supply_chain import seed_from_yaml
                await seed_from_yaml(session)
                await session.commit()
                logger.info("Supply-chain seeded successfully")
    except Exception as exc:
        logger.warning(f"Supply-chain auto-seed skipped: {exc}")

    # Sync agent images from agent_profiles.yaml (upsert) so the DB catalog
    # stays the single source of truth for the image catalog.
    try:
        from sqlalchemy import select
        from models import AgentImage
        from pathlib import Path
        import yaml as _yaml
        async with async_session() as session:
            candidates = [
                Path("/agent-images/agent_profiles.yaml"),
                Path(__file__).resolve().parent.parent.parent / "agent-images" / "agent_profiles.yaml",
            ]
            source = None
            for p in candidates:
                if p.is_file():
                    source = p
                    break
            if source is not None:
                data = _yaml.safe_load(source.read_text()) or {}
                base_images = data.get("base_images", {})
                created = 0
                updated = 0
                if isinstance(base_images, dict):
                    for img_id, info in base_images.items():
                        if not isinstance(info, dict):
                            continue
                        result = await session.execute(select(AgentImage).where(AgentImage.id == img_id))
                        existing = result.scalar_one_or_none()
                        if existing is None:
                            session.add(AgentImage(
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
                        else:
                            existing.name = info.get("name", existing.name)
                            existing.description = info.get("description", existing.description)
                            existing.tag = info.get("tag", existing.tag)
                            existing.enabled = bool(info.get("enabled", True))
                            existing.runtime = info.get("runtime", existing.runtime)
                            existing.capabilities = info.get("capabilities", existing.capabilities)
                            existing.best_for = info.get("best_for", existing.best_for)
                            existing.avoid_for = info.get("avoid_for", existing.avoid_for)
                            updated += 1
                await session.commit()
                logger.info("Agent images synced from %s (created=%s updated=%s)", source, created, updated)
    except Exception as exc:
        logger.warning(f"Agent images sync skipped: {exc}")

    yield
    
    # Shutdown
    logger.info("Shutting down OpenClaw Control Plane")
    await engine.dispose()


# Create FastAPI application
app = FastAPI(
    title="OpenClaw Control Plane",
    description="Policy-driven agent orchestration platform",
    version="0.1.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(capabilities.router, prefix="/api/capabilities", tags=["capabilities"])
app.include_router(policies.router, prefix="/api/policies", tags=["policies"])
app.include_router(llm.router)
app.include_router(tasks_extended.router)
app.include_router(deployments.router, prefix="/api/deployments", tags=["deployments"])
app.include_router(sbom.router)
app.include_router(supply_chain.router)
app.include_router(skills.router, prefix="/api/skills", tags=["skills"])
app.include_router(environments.router, prefix="/api/environments", tags=["environments"])
app.include_router(dag_user_requests.router, prefix="/api/dags", tags=["dag-user-requests"])
app.include_router(dags.router, prefix="/api/dags", tags=["dags"])
app.include_router(openai_dag.router, prefix="/api/dag-ui", tags=["openai-dag"])
app.include_router(agent_images_router.router, prefix="/api/agent-images", tags=["agent-images"])
app.include_router(skill_learning_router.router, prefix="/api/skill-learning", tags=["skill-learning"])
app.include_router(dag_user_requests.router, prefix="/api/dags", tags=["dag-user-requests"])


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "control-plane",
        "version": "0.1.0"
    }


@app.get("/api/system/info")
async def system_info():
    """System information including sandbox mode and security posture."""
    import os
    sandbox_mode = os.getenv("AGENT_SANDBOX_MODE", "gvisor")
    return {
        "sandbox_mode": sandbox_mode,
        "sandbox_secure": sandbox_mode == "gvisor",
        "version": "0.1.0",
    }


@app.get("/api")
async def root():
    """Root endpoint"""
    return {
        "message": "OpenClaw Control Plane API",
        "docs": "/docs",
        "health": "/health"
    }
