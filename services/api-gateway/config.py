"""
Configuration for OpenClaw API Gateway
"""
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # Upstream control-plane
    CONTROL_PLANE_URL: str = "http://control-plane:8000"

    # Optional Redis for persistent session storage (falls back to in-memory)
    REDIS_URL: str = ""

    # Session lifetime
    SESSION_TTL_SECONDS: int = 86400  # 24 h

    # Internal LLM model forwarded to the agent worker.
    # The LLM Router in control-plane selects the provider based on the model
    # name prefix:  gemini-* → Gemini API,  gemma3:*/qwen3:* → Ollama,
    #               claude-* → Anthropic,   gpt-*/o1-*/o3-* → OpenAI.
    DEFAULT_LLM_MODEL: str = "gemini-2.0-flash-exp"

    # Polling & streaming knobs
    POLL_INTERVAL_SECONDS: float = 1.5
    STREAM_TIMEOUT_SECONDS: int = 1800  # 30 min hard ceiling

    # CORS — open by default so any WebUI can reach the gateway
    CORS_ORIGINS: List[str] = ["*"]

    # Public URL where end-users can reach this gateway (for download links).
    # If empty, falls back to http://localhost:8080.
    GATEWAY_PUBLIC_URL: str = "http://localhost:8080"

    # TaskForge dashboard URL (frontend) — used for generating direct links
    # to capability approvals, task details, etc.
    DASHBOARD_URL: str = "http://localhost:3000"

    # Image Builder service URL — polled during image rebuilds to surface
    # build progress to the WebUI user.
    IMAGE_BUILDER_URL: str = "http://openclaw-image-builder:8002"

    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
