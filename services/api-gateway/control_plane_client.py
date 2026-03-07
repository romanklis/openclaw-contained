"""
Control Plane HTTP Client
=========================
Thin async wrapper around the TaskForge / OpenClaw control-plane REST API.

All methods raise ``httpx.HTTPStatusError`` on non-2xx responses so callers
can handle them uniformly.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import httpx

logger = logging.getLogger(__name__)

# Reuse a single async client with connection pooling for efficiency.
_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)


class ControlPlaneClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    # ------------------------------------------------------------------
    # Liveness
    # ------------------------------------------------------------------

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(f"{self.base_url}/health")
                return r.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    async def create_task(
        self,
        name: str,
        description: str,
        llm_model: str,
        base_image: str | None = None,
        agent_profile: str | None = None,
    ) -> Dict[str, Any]:
        """POST /api/tasks — creates and auto-starts a new workflow."""
        payload: Dict[str, Any] = {
            "name": name,
            "description": description,
            "llm_model": llm_model,
        }
        if base_image:
            payload["base_image"] = base_image
        if agent_profile:
            payload["agent_profile"] = agent_profile
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(
                f"{self.base_url}/api/tasks",
                json=payload,
            )
            r.raise_for_status()
            return r.json()

    async def get_task(self, task_id: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"{self.base_url}/api/tasks/{task_id}")
            r.raise_for_status()
            return r.json()

    async def list_tasks(self, limit: int = 20) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"{self.base_url}/api/tasks", params={"limit": limit})
            r.raise_for_status()
            return r.json()

    async def continue_task(
        self,
        task_id: str,
        follow_up: str,
        llm_model: str,
    ) -> Dict[str, Any]:
        """POST /api/tasks/{id}/continue — starts a continuation workflow."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(
                f"{self.base_url}/api/tasks/{task_id}/continue",
                json={"follow_up": follow_up, "llm_model": llm_model},
            )
            r.raise_for_status()
            return r.json()

    # ------------------------------------------------------------------
    # Task outputs (per-iteration results)
    # ------------------------------------------------------------------

    async def get_task_outputs(self, task_id: str) -> Dict[str, Any]:
        """GET /api/tasks/{id}/outputs — returns all stored iteration results."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"{self.base_url}/api/tasks/{task_id}/outputs")
            r.raise_for_status()
            return r.json()

    # ------------------------------------------------------------------
    # Deployments
    # ------------------------------------------------------------------

    async def get_task_deployments(self, task_id: str) -> List[Dict[str, Any]]:
        """GET /api/deployments?task_id={id} — returns deployments for a task."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(
                f"{self.base_url}/api/deployments",
                params={"task_id": task_id},
            )
            r.raise_for_status()
            return r.json()

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    async def get_capability_requests(
        self, task_id: str | None = None, status_filter: str | None = None,
    ) -> List[Dict[str, Any]]:
        """GET /api/capabilities/requests — filter by task_id and/or status."""
        params: Dict[str, str] = {}
        if task_id:
            params["task_id"] = task_id
        if status_filter:
            params["status_filter"] = status_filter
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(
                f"{self.base_url}/api/capabilities/requests",
                params=params,
            )
            r.raise_for_status()
            return r.json()

    # ------------------------------------------------------------------
    # Image Builder (build status)
    # ------------------------------------------------------------------

    async def get_build_status(
        self, build_id: str, image_builder_url: str,
    ) -> Dict[str, Any] | None:
        """GET /builds/{build_id} on the image-builder service."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as c:
                r = await c.get(f"{image_builder_url}/builds/{build_id}")
                if r.status_code == 200:
                    return r.json()
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # LLM Interactions (per-turn tracing)
    # ------------------------------------------------------------------

    async def get_llm_interactions(
        self, task_id: str, since: int = 0,
    ) -> Dict[str, Any]:
        """GET /api/llm/interactions/{task_id}?since=N — returns LLM turn data."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(
                f"{self.base_url}/api/llm/interactions/{task_id}",
                params={"since": since},
            )
            r.raise_for_status()
            return r.json()

    # ------------------------------------------------------------------
    # LLM models (from the LLM router)
    # ------------------------------------------------------------------

    async def get_llm_models(self) -> List[Dict[str, Any]]:
        """GET /api/llm/models — returns all models from all configured providers."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"{self.base_url}/api/llm/models")
            r.raise_for_status()
            data = r.json()
            return data.get("models", [])

    # ------------------------------------------------------------------
    # LLM Router — direct chat completions (fast-path)
    # ------------------------------------------------------------------

    async def llm_chat_completions(
        self,
        payload: dict,
        stream: bool = False,
    ) -> httpx.Response:
        """POST /api/llm/v1/chat/completions — direct LLM call (no task/workflow).

        When ``stream=True`` the caller gets back the raw ``httpx.Response``
        whose body hasn't been consumed yet (use ``aiter_lines()``).
        """
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)) as c:
            r = await c.post(
                f"{self.base_url}/api/llm/v1/chat/completions",
                json=payload,
                # Don't read full body if streaming; caller iterates.
            )
            r.raise_for_status()
            return r

    async def llm_chat_completions_stream(
        self,
        payload: dict,
    ):
        """Streaming variant — yields raw SSE lines from the LLM router."""
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)) as c:
            async with c.stream(
                "POST",
                f"{self.base_url}/api/llm/v1/chat/completions",
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    yield line
