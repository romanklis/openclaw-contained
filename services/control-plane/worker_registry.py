"""In-memory registry of live Temporal workers.

The compose file bind-mounts worker.py into the container. A running Python
process keeps the old activity signatures until restarted, while the Temporal
sandbox can re-import updated workflow code from disk. That mismatch caused
dag-90e1501e to fail with "start_agent_container() takes from 2 to 7 positional
arguments but 8 were given".

Workers POST a heartbeat with their code revision. DAG start refuses to launch
when no worker for the queue is heartbeating, so the failure is explicit instead
of a silent 30-minute stall.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

TASK_QUEUE = "openclaw-tasks"
STALE_AFTER_SECONDS = 45
_WORKERS: Dict[str, Dict[str, Any]] = {}


class WorkerUnavailable(RuntimeError):
    """Raised when no fresh worker is registered for a task queue."""


def record_heartbeat(payload: Dict[str, Any]) -> None:
    worker_id = str(payload.get("worker_id") or "").strip()
    if not worker_id:
        return
    now = time.time()
    _WORKERS[worker_id] = {
        "worker_id": worker_id,
        "revision": str(payload.get("revision") or "unknown"),
        "task_queue": str(payload.get("task_queue") or TASK_QUEUE),
        "activity_count": int(payload.get("activity_count") or 0),
        "pid": payload.get("pid"),
        "last_seen": now,
    }


def snapshot() -> Dict[str, Any]:
    now = time.time()
    workers: List[Dict[str, Any]] = []
    for worker in _WORKERS.values():
        item = dict(worker)
        item["age_seconds"] = round(now - worker["last_seen"], 1)
        item["stale"] = item["age_seconds"] > STALE_AFTER_SECONDS
        workers.append(item)
    workers.sort(key=lambda w: w["last_seen"], reverse=True)
    return {"stale_after_seconds": STALE_AFTER_SECONDS, "workers": workers}


def fresh_workers(task_queue: str = TASK_QUEUE) -> List[Dict[str, Any]]:
    now = time.time()
    return [
        worker
        for worker in _WORKERS.values()
        if worker["task_queue"] == task_queue
        and (now - worker["last_seen"]) <= STALE_AFTER_SECONDS
    ]


def assert_worker_ready(task_queue: str = TASK_QUEUE) -> Dict[str, Any]:
    workers = fresh_workers(task_queue)
    if not workers:
        known = snapshot()["workers"]
        revisions = sorted({w["revision"] for w in known}) or ["none"]
        raise WorkerUnavailable(
            f"No fresh Temporal worker is heartbeating for queue '{task_queue}'. "
            f"Known revisions: {', '.join(revisions)}. "
            "Restart the temporal-worker container after editing worker.py "
            "(the source is bind-mounted, but a running Python process keeps "
            "its old activity signatures)."
        )
    return workers[0]


async def wait_for_worker(
    task_queue: str = TASK_QUEUE, timeout_seconds: float = 15.0
) -> Dict[str, Any]:
    """Wait one heartbeat interval for a worker, then fail.

    A control-plane restart clears the in-memory registry; waiting avoids
    rejecting the first DAG start during that brief window.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        workers = fresh_workers(task_queue)
        if workers:
            return workers[0]
        if time.monotonic() >= deadline:
            assert_worker_ready(task_queue)
        await asyncio.sleep(1)
