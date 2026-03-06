"""
Session Manager
===============
Maps an opaque ``conversation_id`` → ``{task_id, status}``.

Primary backend: Redis (optional).
Fallback:        plain in-memory dict (lost on restart; fine for dev/test).

The ``derive_id`` static method produces a *deterministic* conversation ID
from the model name + system prompt + first user message.  This lets a user
refresh their browser tab and automatically reconnect to the same Temporal
workflow without passing any explicit state from the client side.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class SessionStore:
    def __init__(self, redis_url: str = "") -> None:
        self._store: Dict[str, dict] = {}
        self._redis_url = redis_url
        self._redis = None  # lazy-initialised

    # ------------------------------------------------------------------
    # Redis backend (lazy connect)
    # ------------------------------------------------------------------

    async def _get_redis(self):
        if not self._redis_url:
            return None
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as aioredis  # type: ignore[import]

            client = aioredis.from_url(self._redis_url, decode_responses=True)
            await client.ping()
            self._redis = client
            logger.info("✅ Redis session backend connected at %s", self._redis_url)
        except Exception as exc:
            logger.warning("Redis unavailable (%s) — falling back to in-memory sessions", exc)
            self._redis = None
        return self._redis

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def get(self, conversation_id: str) -> Optional[dict]:
        r = await self._get_redis()
        if r:
            try:
                raw = await r.get(f"gw:sess:{conversation_id}")
                if raw:
                    return json.loads(raw)
            except Exception as exc:
                logger.debug("Redis GET error: %s", exc)
        return self._store.get(conversation_id)

    async def set(self, conversation_id: str, data: dict, ttl: int = 86400) -> None:
        r = await self._get_redis()
        if r:
            try:
                await r.setex(f"gw:sess:{conversation_id}", ttl, json.dumps(data))
                return
            except Exception as exc:
                logger.debug("Redis SET error: %s", exc)
        self._store[conversation_id] = data

    async def delete(self, conversation_id: str) -> None:
        r = await self._get_redis()
        if r:
            try:
                await r.delete(f"gw:sess:{conversation_id}")
                return
            except Exception as exc:
                logger.debug("Redis DEL error: %s", exc)
        self._store.pop(conversation_id, None)

    # ------------------------------------------------------------------
    # Deterministic ID derivation
    # ------------------------------------------------------------------

    @staticmethod
    def derive_id(messages: List[dict], model: str = "") -> str:
        """Return a 24-char hex digest that uniquely identifies a conversation.

        Stability guarantee: given the same model + system prompt + first user
        message, this always returns the same ID — even across gateway restarts
        — so a browser refresh reconnects to the same Temporal workflow.
        """
        system_prompt = next(
            (m.get("content", "") for m in messages if m.get("role") == "system"), ""
        )
        first_user = next(
            (m.get("content", "") for m in messages if m.get("role") == "user"), ""
        )
        # Normalise content lists (vision messages) to plain text
        if isinstance(first_user, list):
            first_user = " ".join(
                p.get("text", "") for p in first_user if isinstance(p, dict)
            )
        if isinstance(system_prompt, list):
            system_prompt = " ".join(
                p.get("text", "") for p in system_prompt if isinstance(p, dict)
            )
        seed = f"{model}|{system_prompt[:200]}|{first_user[:400]}"
        return hashlib.sha256(seed.encode()).hexdigest()[:24]
