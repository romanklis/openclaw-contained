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

    logger.info("Database initialized")

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

    # Auto-seed agent images from agent_profiles.yaml if DB is empty
    try:
        from sqlalchemy import select
        from models import AgentImage
        from pathlib import Path
        import yaml as _yaml
        async with async_session() as session:
            result = await session.execute(select(AgentImage).limit(1))
            if not result.scalar_one_or_none():
                candidates = [
                    Path("/agent-images/agent_profiles.yaml"),
                    Path(__file__).resolve().parent.parent.parent / "agent-images" / "agent_profiles.yaml",
                ]
                for p in candidates:
                    if p.is_file():
                        data = _yaml.safe_load(p.read_text()) or {}
                        base_images = data.get("base_images", {})
                        for img_id, info in base_images.items():
                            if not isinstance(info, dict):
                                continue
                            session.add(AgentImage(
                                id=img_id,
                                name=img_id.capitalize(),
                                description=info.get("description", ""),
                                tag=info.get("tag", f"openclaw-agent:{img_id}"),
                                enabled=True,
                            ))
                        await session.commit()
                        logger.info("Agent images seeded from %s", p)
                        break
    except Exception as exc:
        logger.warning(f"Agent images auto-seed skipped: {exc}")

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
app.include_router(dags.router, prefix="/api/dags", tags=["dags"])
app.include_router(openai_dag.router, prefix="/api/dag-ui", tags=["openai-dag"])
app.include_router(agent_images_router.router, prefix="/api/agent-images", tags=["agent-images"])


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
