"""
OpenClaw Control Plane - Main Application Entry Point
"""
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from routers import tasks, capabilities, policies, auth, llm, tasks_extended, deployments, sbom, task_forces, supply_chain, ceremony_state
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
    
    # Add new ceremony columns if missing (review_gate support)
    async with engine.begin() as conn:
        from sqlalchemy import text
        # Add review_gate to the PostgreSQL enum if not present
        try:
            await conn.execute(text(
                "ALTER TYPE ceremonytype ADD VALUE IF NOT EXISTS 'REVIEW_GATE'"
            ))
            logger.info("Added 'REVIEW_GATE' to ceremonytype enum")
        except Exception:
            pass  # already exists or not PostgreSQL
        for col, col_type, default in [
            ("review_target_order", "INTEGER", None),
            ("max_rework_cycles", "INTEGER", "2"),
            ("verdict_file", "VARCHAR", "'REVIEW_BRIEF.md'"),
        ]:
            try:
                default_clause = f" DEFAULT {default}" if default else ""
                await conn.execute(text(
                    f"ALTER TABLE task_force_ceremonies ADD COLUMN {col} {col_type}{default_clause}"
                ))
                logger.info(f"Added column task_force_ceremonies.{col}")
            except Exception:
                pass  # column already exists

        # --- Ceremony State tables (artifacts, verdicts, state exchange) ---

        # Add ceremony_artifacts enum type (use PL/pgSQL to avoid aborting transaction)
        try:
            await conn.execute(text("""
                DO $$
                BEGIN
                    CREATE TYPE artifactkind AS ENUM ('plan', 'review_brief', 'verdict',
                        'summary', 'sync_notes', 'rework_feedback', 'custom');
                EXCEPTION WHEN duplicate_object THEN NULL;
                END $$;
            """))
            logger.info("Ensured artifactkind enum type exists")
        except Exception:
            pass

        # Create ceremony_artifacts table if missing
        try:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS ceremony_artifacts (
                    id SERIAL PRIMARY KEY,
                    task_force_id VARCHAR NOT NULL REFERENCES task_forces(id),
                    ceremony_id INTEGER REFERENCES task_force_ceremonies(id),
                    task_id VARCHAR REFERENCES tasks(id),
                    kind VARCHAR NOT NULL,
                    filename VARCHAR,
                    title VARCHAR,
                    content TEXT NOT NULL,
                    metadata JSON,
                    verdict VARCHAR,
                    rework_cycle INTEGER DEFAULT 0,
                    superseded_by INTEGER REFERENCES ceremony_artifacts(id),
                    created_at TIMESTAMP DEFAULT now()
                )
            """))
            logger.info("Ensured ceremony_artifacts table exists")
        except Exception as e:
            logger.warning(f"ceremony_artifacts migration: {e}")

        # Create agent_state_exchanges table if missing
        try:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS agent_state_exchanges (
                    id SERIAL PRIMARY KEY,
                    task_force_id VARCHAR NOT NULL REFERENCES task_forces(id),
                    from_task_id VARCHAR NOT NULL,
                    to_task_id VARCHAR,
                    state_type VARCHAR NOT NULL,
                    subject VARCHAR,
                    body TEXT,
                    state_data JSON,
                    created_at TIMESTAMP DEFAULT now()
                )
            """))
            logger.info("Ensured agent_state_exchanges table exists")
        except Exception as e:
            logger.warning(f"agent_state_exchanges migration: {e}")

        # Create indexes for new tables
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS ix_ceremony_artifacts_tf ON ceremony_artifacts(task_force_id)",
            "CREATE INDEX IF NOT EXISTS ix_agent_state_exchanges_tf ON agent_state_exchanges(task_force_id)",
        ]:
            try:
                await conn.execute(text(idx_sql))
            except Exception:
                pass

        # Drop FK constraint on from_task_id if it exists (allow "system" as sender)
        try:
            await conn.execute(text("""
                DO $$
                DECLARE r RECORD;
                BEGIN
                    FOR r IN (
                        SELECT constraint_name FROM information_schema.table_constraints
                        WHERE table_name='agent_state_exchanges'
                          AND constraint_type='FOREIGN KEY'
                          AND constraint_name LIKE '%from_task_id%'
                    ) LOOP
                        EXECUTE 'ALTER TABLE agent_state_exchanges DROP CONSTRAINT ' || r.constraint_name;
                    END LOOP;
                END $$;
            """))
        except Exception as e:
            logger.warning(f"FK migration: {e}")

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
app.include_router(task_forces.router, prefix="/api/task-forces", tags=["task-forces"])
app.include_router(supply_chain.router)
app.include_router(ceremony_state.router, prefix="/api", tags=["ceremony-state"])


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
