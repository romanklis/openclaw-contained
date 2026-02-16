"""
OpenClaw Control Plane - Main Application Entry Point
"""
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from routers import tasks, capabilities, policies, auth, llm, tasks_extended, deployments
from database import engine, Base
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
    
    logger.info("Database initialized")
    
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


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "control-plane",
        "version": "0.1.0"
    }


@app.get("/api")
async def root():
    """Root endpoint"""
    return {
        "message": "OpenClaw Control Plane API",
        "docs": "/docs",
        "health": "/health"
    }
