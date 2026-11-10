"""Worker liveness endpoints.

The temporal-worker posts its code revision every 10 seconds. The control plane
uses this registry to refuse DAG starts when the worker is missing or stale.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

import worker_registry

router = APIRouter()


class WorkerHeartbeat(BaseModel):
    worker_id: str
    revision: Optional[str] = "unknown"
    task_queue: Optional[str] = worker_registry.TASK_QUEUE
    activity_count: Optional[int] = 0
    pid: Optional[int] = None


@router.post("/heartbeat")
async def worker_heartbeat(payload: WorkerHeartbeat) -> Dict[str, Any]:
    worker_registry.record_heartbeat(payload.model_dump())
    return {"ok": True}


@router.get("/status")
async def worker_status() -> Dict[str, Any]:
    return worker_registry.snapshot()
