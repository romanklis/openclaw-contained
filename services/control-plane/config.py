"""
Configuration management
"""
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """Application settings"""
    
    # Database
    DATABASE_URL: str
    
    # Redis (optional — not currently used)
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # Temporal
    TEMPORAL_HOST: str
    TEMPORAL_NAMESPACE: str = "default"
    TEMPORAL_TASK_QUEUE: str = "openclaw-tasks"
    
    # External services
    POLICY_ENGINE_URL: str = "http://policy-engine:8001"
    IMAGE_BUILDER_URL: str
    
    # Security
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost"]
    
    # Workspaces
    WORKSPACE_ROOT: str = "/workspaces"
    
    class Config:
        env_file = ".env"


settings = Settings()
