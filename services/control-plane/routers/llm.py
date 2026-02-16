"""
LLM Router — Unified proxy for all LLM providers.

OpenClaw inside the agent container is configured to talk to this router
as if it were a standard OpenAI-compatible endpoint. The router inspects
the requested model name, selects the correct backend provider, translates
the request format, and forwards it transparently.

Supported providers:
  • Ollama    — local models (gemma3:*, qwen3:*, llama3:*, mistral:*, etc.)
  • Gemini    — Google models (gemini-*)
  • Anthropic — Claude models (claude-*)
  • OpenAI    — GPT models (gpt-*, o1-*, o3-*)

The agent never sees which backend is used; it just sends OpenAI-format
requests to  POST /api/llm/v1/chat/completions
"""
from __future__ import annotations

import os
import json
import logging
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional, Union

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/llm", tags=["llm"])

# ---------------------------------------------------------------------------
# Provider configuration — persisted in PostgreSQL
# ---------------------------------------------------------------------------
# In-memory cache, loaded from DB on first request and updated on POST
_config: Dict[str, str] = {
    "OLLAMA_URL": os.getenv("OLLAMA_URL", "http://host.docker.internal:11434"),
    "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", ""),
    "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
    "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
}
_config_loaded = False  # Whether we've loaded from DB yet

_CONFIG_KEYS = ["OLLAMA_URL", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"]

# ---------------------------------------------------------------------------
# Gemini thought_signature cache
# ---------------------------------------------------------------------------
# Newer Gemini models (gemini-flash-latest, gemini-2.5-*) return a
# `thought_signature` in each tool_call's `extra_content.google` field.
# When the conversation is sent back (with the assistant's tool_calls in
# history), Gemini requires the thought_signature to be present.  OpenClaw's
# JS SDK strips unknown fields, so we cache signatures here and re-inject
# them when we see the same tool_call IDs come back.
_thought_sig_cache: Dict[str, str] = {}  # tool_call_id → thought_signature

# ---------------------------------------------------------------------------
# Per-task interaction tracking
# ---------------------------------------------------------------------------
# Every LLM request/response that flows through the router is recorded here,
# keyed by task_id (extracted from the Authorization header: "Bearer task:<id>").
# The worker fetches this after the agent finishes to include in the Temporal
# activity result, giving full visibility into what the agent did.
#
# Structure: { task_id: [ {turn, timestamp, request_summary, response_summary}, ... ] }
_task_interactions: Dict[str, List[Dict[str, Any]]] = {}
_MAX_INTERACTIONS_PER_TASK = 100  # safety cap


def _extract_task_id_from_auth(request: Request) -> Optional[str]:
    """Extract task_id from Authorization: Bearer task:<task_id>"""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer task:"):
        return auth[len("Bearer task:"):]
    return None


def _record_interaction(
    task_id: str,
    req: "ChatCompletionRequest",
    response_content: Optional[str],
    response_tool_calls: Optional[List[Any]],
    finish_reason: Optional[str],
    usage: Optional[Dict[str, int]],
    provider: str,
    is_streaming: bool = False,
):
    """Record one LLM turn (request+response) for a task."""
    if not task_id:
        return

    if task_id not in _task_interactions:
        _task_interactions[task_id] = []
    interactions = _task_interactions[task_id]

    if len(interactions) >= _MAX_INTERACTIONS_PER_TASK:
        return  # safety cap

    turn = len(interactions) + 1

    # Summarize the request: what messages were sent
    # Focus on tool results (what came back from exec/write/read)
    request_summary: Dict[str, Any] = {
        "msg_count": len(req.messages),
        "roles": [m.role for m in req.messages],
    }

    # Extract tool results from the request (these are the most valuable for tracing)
    tool_results = []
    for m in req.messages:
        if m.role == "tool":
            extras = m.model_extra or {}
            tool_results.append({
                "tool_call_id": extras.get("tool_call_id", ""),
                "content": (m.content or "")[:2000],  # tool result content (truncated)
            })
    if tool_results:
        request_summary["tool_results"] = tool_results

    # Summarize the response: what the LLM decided to do
    response_summary: Dict[str, Any] = {
        "finish_reason": finish_reason,
    }
    if response_content:
        response_summary["content"] = response_content[:2000]
    if response_tool_calls:
        tc_summaries = []
        for tc in response_tool_calls:
            tc_dict = tc if isinstance(tc, dict) else (tc.model_dump() if hasattr(tc, "model_dump") else {})
            fn = tc_dict.get("function", {})
            tc_summary: Dict[str, Any] = {
                "id": tc_dict.get("id", ""),
                "name": fn.get("name", ""),
            }
            # Parse arguments to show them readable
            args_str = fn.get("arguments", "{}")
            try:
                import json as _j
                args = _j.loads(args_str) if isinstance(args_str, str) else args_str
                # For write tool: show path, truncate content
                if isinstance(args, dict):
                    summarized_args = {}
                    for k, v in args.items():
                        if isinstance(v, str) and len(v) > 500:
                            summarized_args[k] = v[:500] + f"... ({len(v)} chars)"
                        else:
                            summarized_args[k] = v
                    tc_summary["arguments"] = summarized_args
                else:
                    tc_summary["arguments"] = args
            except Exception:
                tc_summary["arguments"] = args_str[:500] if isinstance(args_str, str) else str(args_str)[:500]
            tc_summaries.append(tc_summary)
        response_summary["tool_calls"] = tc_summaries

    if usage:
        response_summary["usage"] = usage

    interactions.append({
        "turn": turn,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "provider": provider,
        "streaming": is_streaming,
        "request": request_summary,
        "response": response_summary,
    })
    logger.info(f"📝 Recorded interaction turn {turn} for task {task_id} | "
                f"finish={finish_reason} | tool_calls={len(response_tool_calls or [])}")


async def _get_db_session() -> AsyncSession:
    """Get a DB session for config persistence."""
    from database import async_session
    return async_session()


async def _load_config_from_db():
    """Load provider config from the database (once on first request)."""
    global _config_loaded
    if _config_loaded:
        return
    try:
        session = await _get_db_session()
        async with session:
            # Ensure table exists (create_all runs on startup, but be safe)
            result = await session.execute(
                text("SELECT key, value FROM llm_provider_config")
            )
            rows = result.fetchall()
            for key, value in rows:
                if key in _CONFIG_KEYS and value:
                    _config[key] = value
            _config_loaded = True
            configured = [k for k in _CONFIG_KEYS if _config.get(k)]
            logger.info(f"📦 LLM config loaded from DB: {configured}")
    except Exception as e:
        # Table might not exist yet on very first startup
        logger.warning(f"⚠️ Could not load LLM config from DB (will use env/defaults): {e}")
        _config_loaded = True  # Don't retry every request


async def _save_config_to_db(key: str, value: str):
    """Persist a single config key to the database."""
    try:
        session = await _get_db_session()
        async with session:
            # Upsert using ON CONFLICT
            await session.execute(
                text(
                    "INSERT INTO llm_provider_config (key, value, updated_at) "
                    "VALUES (:key, :value, NOW()) "
                    "ON CONFLICT (key) DO UPDATE SET value = :value, updated_at = NOW()"
                ),
                {"key": key, "value": value},
            )
            await session.commit()
    except Exception as e:
        logger.error(f"❌ Failed to persist LLM config key {key}: {e}")


# Convenience accessors (read from mutable dict)
def _ollama_url() -> str: return _config["OLLAMA_URL"]
def _gemini_key() -> str: return _config["GEMINI_API_KEY"]
def _anthropic_key() -> str: return _config["ANTHROPIC_API_KEY"]
def _openai_key() -> str: return _config["OPENAI_API_KEY"]


# ---------------------------------------------------------------------------
# Schemas — OpenAI-compatible chat completions
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    model_config = {"extra": "allow"}  # pass through tool_calls etc.

    role: str
    content: Any  # str or list of {"type": "text", "text": "..."} parts or None (when tool_calls present)
    tool_calls: Optional[List[Any]] = None  # OpenAI tool_calls array
    function_call: Optional[Any] = None     # legacy function_call
    refusal: Optional[str] = None

    @field_validator("content", mode="before")
    @classmethod
    def normalise_content(cls, v: Any) -> Any:
        """Accept OpenAI multi-part content and flatten to plain string.
        Return None as-is (valid when tool_calls are present)."""
        if v is None:
            return None
        if isinstance(v, str):
            return v
        if isinstance(v, list):
            parts = []
            for item in v:
                if isinstance(item, dict):
                    # {"type": "text", "text": "..."}
                    if item.get("type") == "text":
                        parts.append(item.get("text", ""))
                    else:
                        parts.append(str(item))
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)
        return str(v)


class ChatCompletionRequest(BaseModel):
    model_config = {"extra": "allow"}  # pass through tools, tool_choice, etc.

    model: str
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 4096
    stream: Optional[bool] = False
    top_p: Optional[float] = None
    stop: Optional[Any] = None  # can be str or list


class ChatCompletionChoice(BaseModel):
    model_config = {"extra": "allow"}

    index: int = 0
    message: ChatMessage
    finish_reason: Optional[str] = "stop"


class UsageInfo(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    model_config = {"extra": "allow"}

    id: str = "chatcmpl-openclaw"
    object: str = "chat.completion"
    created: int = 0
    model: str
    choices: List[ChatCompletionChoice]
    usage: UsageInfo = UsageInfo()


# ---------------------------------------------------------------------------
# Provider routing logic
# ---------------------------------------------------------------------------

def detect_provider(model: str) -> str:
    """Detect which provider to use based on model name."""
    model_lower = model.lower()

    if model_lower.startswith("gemini"):
        return "gemini"
    if model_lower.startswith("claude"):
        return "anthropic"
    if any(model_lower.startswith(p) for p in ("gpt-", "o1-", "o3-", "o4-")):
        return "openai"

    # Everything else → Ollama (local)
    return "ollama"


# ---------------------------------------------------------------------------
# SSE Streaming — convert a non-streaming response to SSE chunk stream
# ---------------------------------------------------------------------------
# OpenClaw ALWAYS sends stream:true and uses `for await (const chunk of stream)`
# to process responses. It looks at chunk.choices[0].delta.tool_calls to find
# tool calls. Our backend calls are non-streaming, so we convert the single
# response into a proper SSE stream that the OpenAI JS SDK can parse.

async def _generate_sse_chunks(
    response: "ChatCompletionResponse",
    req_model: str,
) -> AsyncIterator[str]:
    """Convert a ChatCompletionResponse into OpenAI-format SSE chunks.
    
    Emits chunks matching OpenAI's streaming chat completion format:
    - First chunk: role delta
    - Content chunks: text delta(s) 
    - Tool call chunks: tool_calls delta(s)
    - Final chunk: finish_reason + usage
    - [DONE] sentinel
    """
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = response.created or int(time.time())
    model = response.model or req_model

    if not response.choices:
        yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': []})}\n\n"
        yield "data: [DONE]\n\n"
        return

    choice = response.choices[0]
    msg = choice.message
    finish_reason = choice.finish_reason

    # Chunk 1: role indicator
    yield "data: " + json.dumps({
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {"role": "assistant", "content": None},
            "finish_reason": None,
        }],
    }) + "\n\n"

    # Chunk 2+: content deltas (break text into small pieces for realistic streaming)
    content = msg.content
    if content:
        # Send content in chunks of ~100 chars for realistic streaming
        chunk_size = 100
        for i in range(0, len(content), chunk_size):
            text_piece = content[i:i + chunk_size]
            yield "data: " + json.dumps({
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": text_piece},
                    "finish_reason": None,
                }],
            }) + "\n\n"

    # Chunk 3+: tool_calls deltas
    # The OpenAI streaming format sends tool_calls as:
    #   First chunk for each call: {index, id, type, function: {name, arguments: ""}}
    #   Subsequent chunks: {index, function: {arguments: "<partial>"}}
    if msg.tool_calls:
        for tc_index, tc in enumerate(msg.tool_calls):
            tc_dict = tc if isinstance(tc, dict) else tc.model_dump() if hasattr(tc, "model_dump") else {}
            fn = tc_dict.get("function", {})
            tc_id = tc_dict.get("id", f"call_{uuid.uuid4().hex[:8]}")
            tc_name = fn.get("name", "")
            tc_args = fn.get("arguments", "{}")

            # First chunk for this tool call: id + name + start of args
            yield "data: " + json.dumps({
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {
                        "tool_calls": [{
                            "index": tc_index,
                            "id": tc_id,
                            "type": "function",
                            "function": {
                                "name": tc_name,
                                "arguments": "",
                            },
                        }],
                    },
                    "finish_reason": None,
                }],
            }) + "\n\n"

            # Send arguments in chunks for realistic streaming
            args_chunk_size = 200
            for i in range(0, len(tc_args), args_chunk_size):
                args_piece = tc_args[i:i + args_chunk_size]
                yield "data: " + json.dumps({
                    "id": chunk_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {
                            "tool_calls": [{
                                "index": tc_index,
                                "function": {
                                    "arguments": args_piece,
                                },
                            }],
                        },
                        "finish_reason": None,
                    }],
                }) + "\n\n"

    # Final chunk: finish_reason + usage
    usage_data = {
        "prompt_tokens": response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
        "total_tokens": response.usage.total_tokens,
    }
    yield "data: " + json.dumps({
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": finish_reason,
        }],
        "usage": usage_data,
    }) + "\n\n"

    # [DONE] sentinel — required by OpenAI spec
    yield "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Gemini Streaming — direct SSE streaming from Gemini backend
# ---------------------------------------------------------------------------

async def stream_gemini(req: ChatCompletionRequest) -> AsyncIterator[str]:
    """Stream from Google Gemini via the OpenAI-compatible endpoint using SSE.
    
    Instead of collecting the full response and converting, this streams
    directly from Gemini's SSE endpoint to minimize latency.
    """
    api_key = _gemini_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY not configured")

    url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

    # Build messages — inject cached thought_signatures for Gemini
    messages = []
    for m in req.messages:
        msg_dict: Dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls:
            # Deep-copy tool_calls and inject cached thought_signatures
            patched_tcs = []
            for tc in m.tool_calls:
                tc_dict = dict(tc) if isinstance(tc, dict) else tc
                tc_id = tc_dict.get("id", "")
                if tc_id and tc_id in _thought_sig_cache:
                    # Inject the thought_signature Gemini expects
                    tc_dict = dict(tc_dict)  # ensure mutable copy
                    ec = tc_dict.get("extra_content", {})
                    if not isinstance(ec, dict):
                        ec = {}
                    google = ec.get("google", {})
                    if not isinstance(google, dict):
                        google = {}
                    google["thought_signature"] = _thought_sig_cache[tc_id]
                    ec["google"] = google
                    tc_dict["extra_content"] = ec
                    logger.info(f"🔀 Injected thought_signature for tool_call {tc_id}")
                patched_tcs.append(tc_dict)
            msg_dict["tool_calls"] = patched_tcs
        if m.function_call:
            msg_dict["function_call"] = m.function_call
        extras = m.model_extra or {}
        for k, v in extras.items():
            if k not in msg_dict:
                msg_dict[k] = v
        messages.append(msg_dict)

    payload: Dict[str, Any] = {
        "model": req.model,
        "messages": messages,
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    extras = req.model_extra or {}
    if "tools" in extras:
        payload["tools"] = extras["tools"]
    if "tool_choice" in extras:
        payload["tool_choice"] = extras["tool_choice"]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Log the request details for debugging
    tool_names = [t.get("function", {}).get("name", "?") for t in payload.get("tools", []) if isinstance(t, dict)]
    logger.info(f"🔀 Gemini SSE request: model={payload.get('model')} tools={tool_names} msg_count={len(payload.get('messages', []))}")
    # Log system prompt size
    for msg in payload.get("messages", []):
        if msg.get("role") == "system":
            sys_content = msg.get("content", "")
            logger.info(f"🔀 Gemini system prompt size: {len(sys_content)} chars")

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        collected_lines: list[str] = []
        malformed = False

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    if resp.status_code != 200:
                        # Read the error body for diagnostics
                        error_body = ""
                        async for chunk in resp.aiter_bytes():
                            error_body += chunk.decode("utf-8", errors="replace")
                        logger.error(f"❌ Gemini API returned {resp.status_code} (attempt {attempt}/{max_retries}): {error_body[:2000]}")

                        if attempt < max_retries:
                            import asyncio
                            await asyncio.sleep(0.5 * attempt)
                            continue

                        # Exhausted retries — yield an error as an SSE event so
                        # the caller gets a proper response instead of a crash.
                        error_chunk = {
                            "id": f"chatcmpl-err-{uuid.uuid4().hex[:8]}",
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": req.model,
                            "choices": [{
                                "index": 0,
                                "delta": {"role": "assistant", "content": f"[LLM_ERROR] Gemini returned HTTP {resp.status_code}: {error_body[:500]}"},
                                "finish_reason": "stop",
                            }],
                        }
                        yield f"data: {json.dumps(error_chunk)}\n\n"
                        yield "data: [DONE]\n\n"
                        return

                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            # Debug log tool_calls chunks and finish_reason
                            if "tool_calls" in line or "finish_reason" in line:
                                logger.info(f"🔀 Gemini SSE chunk (attempt {attempt}): {line[:500]}")
                            collected_lines.append(line)
                            # Detect MALFORMED_FUNCTION_CALL in finish_reason
                            if "MALFORMED_FUNCTION_CALL" in line:
                                malformed = True
                        elif line.strip() == "":
                            continue
        except httpx.HTTPStatusError as exc:
            error_body = exc.response.text if hasattr(exc.response, 'text') else str(exc)
            logger.error(f"❌ Gemini HTTPStatusError (attempt {attempt}/{max_retries}): {error_body[:2000]}")
            if attempt < max_retries:
                import asyncio
                await asyncio.sleep(0.5 * attempt)
                continue
            # Return error as SSE
            error_chunk = {
                "id": f"chatcmpl-err-{uuid.uuid4().hex[:8]}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": req.model,
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant", "content": f"[LLM_ERROR] Gemini error: {error_body[:500]}"},
                    "finish_reason": "stop",
                }],
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"
            return
        except Exception as exc:
            logger.error(f"❌ Gemini unexpected error (attempt {attempt}/{max_retries}): {exc}")
            if attempt < max_retries:
                import asyncio
                await asyncio.sleep(0.5 * attempt)
                continue
            error_chunk = {
                "id": f"chatcmpl-err-{uuid.uuid4().hex[:8]}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": req.model,
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant", "content": f"[LLM_ERROR] Gemini error: {str(exc)[:500]}"},
                    "finish_reason": "stop",
                }],
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"
            return

        if malformed and attempt < max_retries:
            logger.warning(f"⚠️ Gemini MALFORMED_FUNCTION_CALL on attempt {attempt}/{max_retries}, retrying...")
            import asyncio
            await asyncio.sleep(0.5 * attempt)  # brief back-off
            continue

        if malformed:
            logger.error(f"❌ Gemini MALFORMED_FUNCTION_CALL persisted after {max_retries} attempts")

        # Extract and cache thought_signatures from collected SSE lines
        for line in collected_lines:
            if "thought_signature" in line and "tool_calls" in line:
                try:
                    payload_str = line[len("data: "):] if line.startswith("data: ") else line
                    if payload_str.strip() == "[DONE]":
                        continue
                    chunk_data = json.loads(payload_str)
                    for choice in chunk_data.get("choices", []):
                        delta = choice.get("delta", {})
                        for tc in delta.get("tool_calls", []):
                            tc_id = tc.get("id", "")
                            sig = (tc.get("extra_content", {}) or {}).get("google", {}).get("thought_signature", "")
                            if tc_id and sig:
                                _thought_sig_cache[tc_id] = sig
                                logger.info(f"🔀 Cached thought_signature for tool_call {tc_id} ({len(sig)} chars)")
                except Exception as e:
                    logger.debug(f"Could not parse thought_signature from SSE line: {e}")

        # Yield all collected lines
        for line in collected_lines:
            yield line + "\n\n"
        break  # done — either success or exhausted retries


# ---------------------------------------------------------------------------
# Ollama backend
# ---------------------------------------------------------------------------

async def call_ollama(req: ChatCompletionRequest) -> ChatCompletionResponse:
    """Forward to Ollama /api/chat endpoint."""
    messages = []
    for m in req.messages:
        msg_dict: Dict[str, Any] = {"role": m.role, "content": m.content or ""}
        if m.tool_calls:
            msg_dict["tool_calls"] = m.tool_calls
        extras = m.model_extra or {}
        for k, v in extras.items():
            if k not in msg_dict:
                msg_dict[k] = v
        messages.append(msg_dict)
    ollama_url = _ollama_url()

    payload: Dict[str, Any] = {
        "model": req.model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": req.temperature or 0.7,
            "num_predict": req.max_tokens or 4096,
        },
    }
    # Pass through tools if provided
    extras = req.model_extra or {}
    if "tools" in extras:
        payload["tools"] = extras["tools"]

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(
            f"{ollama_url}/api/chat",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    content = data.get("message", {}).get("content", "")
    tool_calls = data.get("message", {}).get("tool_calls")
    finish_reason = "stop"
    if tool_calls:
        finish_reason = "tool_calls"
    return ChatCompletionResponse(
        created=int(time.time()),
        model=req.model,
        choices=[ChatCompletionChoice(
            message=ChatMessage(role="assistant", content=content, tool_calls=tool_calls),
            finish_reason=finish_reason,
        )],
        usage=UsageInfo(
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            total_tokens=data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
        ),
    )


# ---------------------------------------------------------------------------
# Gemini backend (uses Google's OpenAI-compatible endpoint)
# ---------------------------------------------------------------------------

async def call_gemini(req: ChatCompletionRequest) -> ChatCompletionResponse:
    """Forward to Google Gemini via the OpenAI-compatible endpoint.
    
    Gemini's OpenAI-compat endpoint returns full OpenAI-format responses
    including tool_calls. We pass them through verbatim.
    """
    api_key = _gemini_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY not configured — add it via /llm-providers in the UI")

    url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

    # Build messages — pass through tool_calls/tool role messages as-is
    # Inject cached thought_signatures for Gemini
    messages = []
    for m in req.messages:
        msg_dict: Dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls:
            # Deep-copy and inject cached thought_signatures
            patched_tcs = []
            for tc in m.tool_calls:
                tc_dict = dict(tc) if isinstance(tc, dict) else tc
                tc_id = tc_dict.get("id", "")
                if tc_id and tc_id in _thought_sig_cache:
                    tc_dict = dict(tc_dict)
                    ec = tc_dict.get("extra_content", {})
                    if not isinstance(ec, dict):
                        ec = {}
                    google = ec.get("google", {})
                    if not isinstance(google, dict):
                        google = {}
                    google["thought_signature"] = _thought_sig_cache[tc_id]
                    ec["google"] = google
                    tc_dict["extra_content"] = ec
                patched_tcs.append(tc_dict)
            msg_dict["tool_calls"] = patched_tcs
        if m.function_call:
            msg_dict["function_call"] = m.function_call
        # Pass through any extra fields (tool_call_id, name, etc.)
        extras = m.model_extra or {}
        for k, v in extras.items():
            if k not in msg_dict:
                msg_dict[k] = v
        messages.append(msg_dict)

    payload: Dict[str, Any] = {
        "model": req.model,
        "messages": messages,
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
        "stream": False,
    }
    # Pass through tools/tool_choice if provided by the client
    extras = req.model_extra or {}
    if "tools" in extras:
        payload["tools"] = extras["tools"]
    if "tool_choice" in extras:
        payload["tool_choice"] = extras["tool_choice"]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    # Debug: log raw Gemini response structure
    import json as _json
    for i, c in enumerate(data.get("choices", [])):
        msg = c.get("message", {})
        tc = msg.get("tool_calls")
        logger.info(
            f"🔍 Gemini raw choice[{i}] | finish_reason={c.get('finish_reason')} "
            f"| content_len={len(msg.get('content') or '')} "
            f"| tool_calls={len(tc) if tc else 0} "
            f"| tool_names={[t.get('function',{}).get('name','?') for t in (tc or [])]}"
        )
        if tc:
            logger.info(f"🔍 Gemini tool_calls detail: {_json.dumps(tc)[:500]}")
            # Cache thought_signatures from non-streaming responses
            for t in tc:
                tc_id = t.get("id", "")
                sig = (t.get("extra_content", {}) or {}).get("google", {}).get("thought_signature", "")
                if tc_id and sig:
                    _thought_sig_cache[tc_id] = sig
                    logger.info(f"🔀 Cached thought_signature for tool_call {tc_id} ({len(sig)} chars) [non-stream]")

    # Pass through the raw response — preserving tool_calls, finish_reason, etc.
    choices = []
    for c in data.get("choices", []):
        msg = c.get("message", {})
        tool_calls = msg.get("tool_calls")
        # Gemini's OpenAI-compat endpoint sometimes returns finish_reason="stop"
        # even when tool_calls are present. OpenAI clients (including OpenClaw)
        # check finish_reason to decide whether to process tool_calls.
        finish_reason = c.get("finish_reason", "stop")
        if tool_calls and finish_reason != "tool_calls":
            logger.info(f"🔧 Gemini fix: finish_reason '{finish_reason}' → 'tool_calls' (has {len(tool_calls)} tool_calls)")
            finish_reason = "tool_calls"
        choices.append(ChatCompletionChoice(
            index=c.get("index", 0),
            message=ChatMessage(
                role=msg.get("role", "assistant"),
                content=msg.get("content"),
                tool_calls=tool_calls,
                function_call=msg.get("function_call"),
            ),
            finish_reason=finish_reason,
        ))

    usage = data.get("usage", {})
    return ChatCompletionResponse(
        id=data.get("id", "chatcmpl-gemini"),
        created=data.get("created", int(time.time())),
        model=req.model,
        choices=choices,
        usage=UsageInfo(
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        ),
    )


# ---------------------------------------------------------------------------
# Anthropic backend
# ---------------------------------------------------------------------------

async def call_anthropic(req: ChatCompletionRequest) -> ChatCompletionResponse:
    """Forward to Anthropic Messages API with tool_use support.
    
    Translates OpenAI-format tools/tool_calls to Anthropic format and back.
    """
    api_key = _anthropic_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured — add it via /llm-providers in the UI")

    url = "https://api.anthropic.com/v1/messages"

    system_text = ""
    anthropic_messages = []
    for m in req.messages:
        if m.role == "system":
            system_text += (m.content or "") + "\n"
        elif m.role == "tool":
            # OpenAI tool result → Anthropic tool_result
            extras = m.model_extra or {}
            anthropic_messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": extras.get("tool_call_id", ""),
                    "content": m.content or "",
                }],
            })
        elif m.role == "assistant" and m.tool_calls:
            # Reconstruct Anthropic content with tool_use blocks
            content_blocks = []
            if m.content:
                content_blocks.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                import json as _json
                try:
                    args = _json.loads(fn.get("arguments", "{}"))
                except Exception:
                    args = {}
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", "") if isinstance(tc, dict) else "",
                    "name": fn.get("name", ""),
                    "input": args,
                })
            anthropic_messages.append({"role": "assistant", "content": content_blocks})
        else:
            anthropic_messages.append({"role": m.role, "content": m.content or ""})

    payload: Dict[str, Any] = {
        "model": req.model,
        "messages": anthropic_messages,
        "max_tokens": req.max_tokens or 4096,
        "temperature": req.temperature,
    }
    if system_text.strip():
        payload["system"] = system_text.strip()

    # Translate OpenAI tools format to Anthropic format
    extras = req.model_extra or {}
    if "tools" in extras:
        anthropic_tools = []
        for tool in extras["tools"]:
            if tool.get("type") == "function":
                fn = tool.get("function", {})
                anthropic_tools.append({
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                })
        if anthropic_tools:
            payload["tools"] = anthropic_tools

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    content_blocks = data.get("content", [])
    text_parts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
    text = " ".join(text_parts) if text_parts else None

    # Convert Anthropic tool_use blocks back to OpenAI tool_calls format
    import json as _json
    tool_calls = []
    for b in content_blocks:
        if b.get("type") == "tool_use":
            tool_calls.append({
                "id": b.get("id", ""),
                "type": "function",
                "function": {
                    "name": b.get("name", ""),
                    "arguments": _json.dumps(b.get("input", {})),
                },
            })

    # Map Anthropic stop_reason to OpenAI finish_reason
    stop_reason = data.get("stop_reason", "end_turn")
    finish_reason = "tool_calls" if stop_reason == "tool_use" else "stop"

    usage = data.get("usage", {})

    return ChatCompletionResponse(
        id=data.get("id", "chatcmpl-anthropic"),
        created=int(time.time()),
        model=req.model,
        choices=[ChatCompletionChoice(
            message=ChatMessage(
                role="assistant",
                content=text,
                tool_calls=tool_calls if tool_calls else None,
            ),
            finish_reason=finish_reason,
        )],
        usage=UsageInfo(
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        ),
    )


# ---------------------------------------------------------------------------
# OpenAI backend
# ---------------------------------------------------------------------------

async def call_openai(req: ChatCompletionRequest) -> ChatCompletionResponse:
    """Forward to OpenAI Chat Completions API, preserving tool_calls."""
    api_key = _openai_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not configured — add it via /llm-providers in the UI")

    url = "https://api.openai.com/v1/chat/completions"

    # Build messages — pass through tool_calls/tool role messages as-is
    messages = []
    for m in req.messages:
        msg_dict: Dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls:
            msg_dict["tool_calls"] = m.tool_calls
        if m.function_call:
            msg_dict["function_call"] = m.function_call
        extras = m.model_extra or {}
        for k, v in extras.items():
            if k not in msg_dict:
                msg_dict[k] = v
        messages.append(msg_dict)

    payload: Dict[str, Any] = {
        "model": req.model,
        "messages": messages,
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
        "stream": False,
    }
    extras = req.model_extra or {}
    if "tools" in extras:
        payload["tools"] = extras["tools"]
    if "tool_choice" in extras:
        payload["tool_choice"] = extras["tool_choice"]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    choices = []
    for c in data.get("choices", []):
        msg = c.get("message", {})
        choices.append(ChatCompletionChoice(
            index=c.get("index", 0),
            message=ChatMessage(
                role=msg.get("role", "assistant"),
                content=msg.get("content"),
                tool_calls=msg.get("tool_calls"),
                function_call=msg.get("function_call"),
            ),
            finish_reason=c.get("finish_reason", "stop"),
        ))

    usage = data.get("usage", {})
    return ChatCompletionResponse(
        id=data.get("id", "chatcmpl-openai"),
        created=data.get("created", int(time.time())),
        model=req.model,
        choices=choices,
        usage=UsageInfo(
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        ),
    )


# ---------------------------------------------------------------------------
# Provider dispatch map
# ---------------------------------------------------------------------------

PROVIDER_HANDLERS = {
    "ollama": call_ollama,
    "gemini": call_gemini,
    "anthropic": call_anthropic,
    "openai": call_openai,
}


# ===================================================================
# ROUTES
# ===================================================================

@router.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest, request: Request):
    """
    OpenAI-compatible chat completions endpoint.

    The agent inside the container calls this as if it were OpenAI.
    The router detects the provider from the model name and dispatches.
    
    Supports both streaming (stream=true → SSE) and non-streaming responses.
    OpenClaw ALWAYS sends stream=true, so SSE is the primary path.
    """
    # Ensure config is loaded from DB
    await _load_config_from_db()

    # Extract task_id from Authorization header for interaction tracking
    task_id = _extract_task_id_from_auth(request)

    provider = detect_provider(req.model)
    handler = PROVIDER_HANDLERS.get(provider)

    if not handler:
        raise HTTPException(status_code=400, detail=f"Unknown provider for model: {req.model}")

    want_stream = req.stream or False
    # Also check model_extra for stream (OpenAI SDK sends it there sometimes)
    extras = req.model_extra or {}
    if extras.get("stream"):
        want_stream = True

    logger.info(f"🔀 LLM Router | model={req.model} → provider={provider} | messages={len(req.messages)} | stream={want_stream}")

    # Debug: log incoming request details
    tools_in = extras.get("tools")
    tool_choice_in = extras.get("tool_choice")
    if tools_in:
        tool_names = [t.get("function", {}).get("name", "?") for t in tools_in if isinstance(t, dict)]
        logger.info(f"🔧 Incoming tools={len(tools_in)} | names={tool_names[:10]} | tool_choice={tool_choice_in}")
    # Log message roles for debugging multi-turn tool conversations
    msg_summary = [(m.role, bool(m.tool_calls), bool(m.model_extra.get("tool_call_id") if m.model_extra else False)) for m in req.messages]
    logger.info(f"📨 Messages: {msg_summary}")

    if want_stream:
        # --- STREAMING PATH ---
        # For Gemini, stream directly from the backend SSE endpoint
        # For others, call non-streaming backend and convert to SSE
        if provider == "gemini":
            logger.info(f"🌊 Streaming directly from Gemini SSE")
            try:
                async def _tracked_stream_gemini():
                    """Wrap stream_gemini to record interaction after streaming."""
                    collected_content = []
                    collected_tool_calls: Dict[int, Dict] = {}  # index → {id, name, arguments}
                    finish_reason = None
                    usage_data = None

                    async for chunk_line in stream_gemini(req):
                        yield chunk_line
                        # Parse SSE lines to extract response info for tracking
                        if chunk_line.startswith("data: ") and task_id:
                            payload_str = chunk_line[6:].strip()
                            if payload_str == "[DONE]":
                                continue
                            try:
                                chunk_data = json.loads(payload_str)
                                for choice in chunk_data.get("choices", []):
                                    delta = choice.get("delta", {})
                                    if delta.get("content"):
                                        collected_content.append(delta["content"])
                                    if choice.get("finish_reason"):
                                        finish_reason = choice["finish_reason"]
                                    for tc in delta.get("tool_calls", []):
                                        idx = tc.get("index", 0)
                                        if idx not in collected_tool_calls:
                                            collected_tool_calls[idx] = {
                                                "id": tc.get("id", ""),
                                                "type": "function",
                                                "function": {"name": tc.get("function", {}).get("name", ""), "arguments": ""},
                                            }
                                        else:
                                            if tc.get("id"):
                                                collected_tool_calls[idx]["id"] = tc["id"]
                                            if tc.get("function", {}).get("name"):
                                                collected_tool_calls[idx]["function"]["name"] = tc["function"]["name"]
                                        if tc.get("function", {}).get("arguments"):
                                            collected_tool_calls[idx]["function"]["arguments"] += tc["function"]["arguments"]
                                    if chunk_data.get("usage"):
                                        usage_data = chunk_data["usage"]
                            except Exception:
                                pass

                    # Stream finished — record the interaction
                    if task_id:
                        full_content = "".join(collected_content) if collected_content else None
                        tc_list = list(collected_tool_calls.values()) if collected_tool_calls else None
                        _record_interaction(
                            task_id=task_id, req=req,
                            response_content=full_content,
                            response_tool_calls=tc_list,
                            finish_reason=finish_reason,
                            usage=usage_data,
                            provider=provider, is_streaming=True,
                        )

                return StreamingResponse(
                    _tracked_stream_gemini(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                )
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"❌ LLM Router stream | provider={provider} | {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"LLM stream failed: {e}")
        else:
            # Call backend non-streaming, then convert to SSE chunks
            try:
                response = await handler(req)
                msg0 = response.choices[0].message if response.choices else None
                tool_info = f" | tool_calls={len(msg0.tool_calls)}" if msg0 and msg0.tool_calls else ""
                content_preview = (msg0.content or "")[:100] if msg0 else ""
                logger.info(
                    f"✅ LLM Router (→SSE) | model={req.model} | provider={provider} "
                    f"| tokens={response.usage.total_tokens}{tool_info} | preview={content_preview!r}..."
                )
                # Record interaction for task tracking
                if task_id and msg0:
                    _record_interaction(
                        task_id=task_id, req=req,
                        response_content=msg0.content,
                        response_tool_calls=msg0.tool_calls,
                        finish_reason=response.choices[0].finish_reason if response.choices else None,
                        usage={"prompt_tokens": response.usage.prompt_tokens,
                               "completion_tokens": response.usage.completion_tokens,
                               "total_tokens": response.usage.total_tokens},
                        provider=provider, is_streaming=False,
                    )
                return StreamingResponse(
                    _generate_sse_chunks(response, req.model),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                )
            except httpx.HTTPStatusError as e:
                logger.error(f"❌ LLM Router | provider={provider} | HTTP {e.response.status_code}: {e.response.text[:300]}")
                raise HTTPException(
                    status_code=e.response.status_code,
                    detail=f"Provider {provider} error: {e.response.text[:500]}",
                )
            except httpx.ConnectError as e:
                logger.error(f"❌ LLM Router | provider={provider} | Connection failed: {e}")
                raise HTTPException(status_code=503, detail=f"Cannot connect to {provider}: {e}")
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"❌ LLM Router | provider={provider} | {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"LLM call failed: {e}")
    else:
        # --- NON-STREAMING PATH ---
        try:
            response = await handler(req)
            msg0 = response.choices[0].message if response.choices else None
            content_preview = (msg0.content or "")[:100] if msg0 else ""
            tool_info = ""
            if msg0 and msg0.tool_calls:
                tool_info = f" | tool_calls={len(msg0.tool_calls)}"
                resp_dict = response.model_dump(exclude_none=True)
                logger.info(f"🔬 Serialized response (first 1000): {json.dumps(resp_dict)[:1000]}")
            logger.info(
                f"✅ LLM Router | model={req.model} | provider={provider} "
                f"| tokens={response.usage.total_tokens}{tool_info} | preview={content_preview!r}..."
            )
            # Record interaction for task tracking
            if task_id and msg0:
                _record_interaction(
                    task_id=task_id, req=req,
                    response_content=msg0.content,
                    response_tool_calls=msg0.tool_calls,
                    finish_reason=response.choices[0].finish_reason if response.choices else None,
                    usage={"prompt_tokens": response.usage.prompt_tokens,
                           "completion_tokens": response.usage.completion_tokens,
                           "total_tokens": response.usage.total_tokens},
                    provider=provider, is_streaming=False,
                )
            return response
        except httpx.HTTPStatusError as e:
            logger.error(f"❌ LLM Router | provider={provider} | HTTP {e.response.status_code}: {e.response.text[:300]}")
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Provider {provider} error: {e.response.text[:500]}",
            )
        except httpx.ConnectError as e:
            logger.error(f"❌ LLM Router | provider={provider} | Connection failed: {e}")
            raise HTTPException(status_code=503, detail=f"Cannot connect to {provider}: {e}")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"❌ LLM Router | provider={provider} | {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"LLM call failed: {e}")


# -------------------------------------------------------------------
# Per-task interaction log endpoints
# -------------------------------------------------------------------

@router.get("/interactions/{task_id}")
async def get_task_interactions(task_id: str):
    """Get all recorded LLM interactions for a task.
    
    Returns the full trace of every LLM turn: what tool results came in,
    what tool calls the LLM made, text responses, usage stats.
    Called by the worker after the agent container finishes.
    """
    interactions = _task_interactions.get(task_id, [])
    return {
        "task_id": task_id,
        "count": len(interactions),
        "interactions": interactions,
    }


@router.delete("/interactions/{task_id}")
async def clear_task_interactions(task_id: str):
    """Clear recorded interactions for a task (called after worker stores them)."""
    removed = _task_interactions.pop(task_id, [])
    return {"task_id": task_id, "cleared": len(removed)}


# -------------------------------------------------------------------
# Utility endpoints
# -------------------------------------------------------------------

@router.get("/health")
async def llm_health():
    """Check backend provider connectivity."""
    status_map = {}
    ollama_url = _ollama_url()

    # Check Ollama
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            status_map["ollama"] = {"status": "healthy", "url": ollama_url, "models": models}
    except Exception as e:
        status_map["ollama"] = {"status": "unhealthy", "error": str(e)}

    status_map["gemini"] = {
        "status": "configured" if _gemini_key() else "not_configured",
        "models": ["gemini-2.0-flash-exp", "gemini-1.5-pro", "gemini-1.5-flash", "gemini-3-flash-preview", "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.5-flash-lite"],
    }
    status_map["anthropic"] = {
        "status": "configured" if _anthropic_key() else "not_configured",
        "models": ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-3-5-haiku-20241022"],
    }
    status_map["openai"] = {
        "status": "configured" if _openai_key() else "not_configured",
        "models": ["gpt-4o", "gpt-4o-mini", "o1-preview"],
    }

    return {"providers": status_map}


@router.get("/providers")
async def get_llm_providers():
    """Return available providers for the frontend / agent."""
    providers = []

    # Ollama — always present if reachable
    ollama_url = _ollama_url()
    ollama_models: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            if resp.status_code == 200:
                ollama_models = [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        pass

    providers.append({
        "name": "ollama", "type": "ollama", "url": ollama_url,
        "available": len(ollama_models) > 0, "models": ollama_models,
    })
    providers.append({
        "name": "gemini", "type": "gemini",
        "available": bool(_gemini_key()),
        "models": ["gemini-2.0-flash-exp", "gemini-1.5-pro", "gemini-1.5-flash", "gemini-3-flash-preview", "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.5-flash-lite"],
    })
    providers.append({
        "name": "anthropic", "type": "anthropic",
        "available": bool(_anthropic_key()),
        "models": ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-3-5-haiku-20241022"],
    })
    providers.append({
        "name": "openai", "type": "openai",
        "available": bool(_openai_key()),
        "models": ["gpt-4o", "gpt-4o-mini", "o1-preview"],
    })

    default = "ollama"
    for p in providers:
        if p["available"]:
            default = p["name"]
            break

    return {"providers": providers, "default_provider": default}


@router.get("/models")
async def list_all_models():
    """List models across all configured providers."""
    all_models = []

    ollama_url = _ollama_url()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            if resp.status_code == 200:
                for m in resp.json().get("models", []):
                    all_models.append({"id": m["name"], "provider": "ollama"})
    except Exception:
        pass

    if _gemini_key():
        for m in ["gemini-2.0-flash-exp", "gemini-1.5-pro", "gemini-1.5-flash", "gemini-3-flash-preview", "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.5-flash-lite"]:
            all_models.append({"id": m, "provider": "gemini"})
    if _anthropic_key():
        for m in ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-3-5-haiku-20241022"]:
            all_models.append({"id": m, "provider": "anthropic"})
    if _openai_key():
        for m in ["gpt-4o", "gpt-4o-mini", "o1-preview"]:
            all_models.append({"id": m, "provider": "openai"})

    return {"models": all_models}


# Keep backward compatibility with old /chat endpoint
class LegacyChatRequest(BaseModel):
    prompt: str
    model: str = "gemma3:4b"
    stream: bool = False


@router.post("/chat")
async def legacy_chat(request: LegacyChatRequest):
    """Legacy chat endpoint — converts to unified format and dispatches."""
    completion_req = ChatCompletionRequest(
        model=request.model,
        messages=[ChatMessage(role="user", content=request.prompt)],
    )
    result = await chat_completions(completion_req)
    return {
        "model": result.model,
        "response": result.choices[0].message.content if result.choices else "",
        "done": True,
    }


# -------------------------------------------------------------------
# Runtime configuration endpoints
# -------------------------------------------------------------------

def _mask_key(key: str) -> str:
    """Mask an API key for display — show first 4 and last 4 chars."""
    if not key:
        return ""
    if len(key) <= 10:
        return key[:2] + "*" * (len(key) - 2)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


class ProviderConfigUpdate(BaseModel):
    ollama_url: Optional[str] = None
    gemini_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None


@router.get("/config")
async def get_config():
    """Return current provider configuration (keys are masked)."""
    await _load_config_from_db()
    return {
        "ollama_url": _config["OLLAMA_URL"],
        "gemini_api_key": _mask_key(_config["GEMINI_API_KEY"]),
        "anthropic_api_key": _mask_key(_config["ANTHROPIC_API_KEY"]),
        "openai_api_key": _mask_key(_config["OPENAI_API_KEY"]),
        "gemini_configured": bool(_config["GEMINI_API_KEY"]),
        "anthropic_configured": bool(_config["ANTHROPIC_API_KEY"]),
        "openai_configured": bool(_config["OPENAI_API_KEY"]),
    }


@router.post("/config")
async def update_config(update: ProviderConfigUpdate):
    """Update provider configuration at runtime — persisted to database."""
    changes: list[str] = []

    if update.ollama_url is not None and update.ollama_url.strip():
        _config["OLLAMA_URL"] = update.ollama_url.strip()
        await _save_config_to_db("OLLAMA_URL", _config["OLLAMA_URL"])
        changes.append(f"OLLAMA_URL → {_config['OLLAMA_URL']}")

    if update.gemini_api_key is not None:
        _config["GEMINI_API_KEY"] = update.gemini_api_key.strip()
        await _save_config_to_db("GEMINI_API_KEY", _config["GEMINI_API_KEY"])
        changes.append(f"GEMINI_API_KEY → {'set' if _config['GEMINI_API_KEY'] else 'cleared'}")

    if update.anthropic_api_key is not None:
        _config["ANTHROPIC_API_KEY"] = update.anthropic_api_key.strip()
        await _save_config_to_db("ANTHROPIC_API_KEY", _config["ANTHROPIC_API_KEY"])
        changes.append(f"ANTHROPIC_API_KEY → {'set' if _config['ANTHROPIC_API_KEY'] else 'cleared'}")

    if update.openai_api_key is not None:
        _config["OPENAI_API_KEY"] = update.openai_api_key.strip()
        await _save_config_to_db("OPENAI_API_KEY", _config["OPENAI_API_KEY"])
        changes.append(f"OPENAI_API_KEY → {'set' if _config['OPENAI_API_KEY'] else 'cleared'}")

    if not changes:
        return {"status": "no_changes", "message": "No fields provided"}

    logger.info(f"🔧 LLM Config updated: {', '.join(changes)}")

    return {
        "status": "updated",
        "changes": changes,
        "config": await get_config(),
    }
