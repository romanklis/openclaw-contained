"""
OpenAI-compatible Pydantic schemas for the API Gateway.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Incoming request
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant" | "system"
    content: Union[str, List[Any]] = ""
    name: Optional[str] = None

    def text(self) -> str:
        """Return plain-text content regardless of whether it's a string or a
        content-part array (e.g., vision messages with image_url parts)."""
        if isinstance(self.content, str):
            return self.content
        return "\n".join(
            p.get("text", "")
            for p in self.content
            if isinstance(p, dict) and p.get("type") == "text"
        )


class ChatCompletionRequest(BaseModel):
    model: str = "taskforge-iterator"
    messages: List[ChatMessage]
    stream: Optional[bool] = True
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None

    # OpenClaw extension — override the internal LLM model used by the agent
    llm_model: Optional[str] = None


# ---------------------------------------------------------------------------
# Streaming response (SSE chunks)
# ---------------------------------------------------------------------------


class ModelCard(BaseModel):
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "openclaw"
    permission: List[Any] = []
    root: Optional[str] = None
    parent: Optional[str] = None


class ModelList(BaseModel):
    object: str = "list"
    data: List[ModelCard]


# ---------------------------------------------------------------------------
# Non-streaming response
# ---------------------------------------------------------------------------


class NonStreamChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"
    logprobs: None = None


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[NonStreamChoice]
    usage: Usage
