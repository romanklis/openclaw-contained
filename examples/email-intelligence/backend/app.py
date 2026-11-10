"""Email Intelligence scheduler + dashboard API.

The scheduler runs in one of two explicit modes:

- ``manual`` (default): nothing runs automatically; the dashboard's Run now button
  starts the target DAG.
- ``interval``: the target DAG is started automatically on the configured interval.

Both modes start the target DAG in place (``POST /api/dags/{id}/start``), matching
the backoffice Run (all) action. The scheduler never calls the planner.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
import psycopg2
import psycopg2.pool
from fastapi import FastAPI, HTTPException, Query
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("email-scheduler")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://email_app:email_app_pwd_demo@email-postgres:5432/email_intelligence",
)
CONTROL_PLANE_URL = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000").rstrip("/")
INTERVAL_SECONDS = _env_int("INTERVAL_SECONDS", 60)
SCHEDULER_POLL_SECONDS = max(2, _env_int("SCHEDULER_POLL_SECONDS", 5))
SCHEDULER_MODE_ENV = os.getenv("SCHEDULER_MODE", "manual").strip().lower()
ENV_TARGET_DAG_ID = os.getenv("TARGET_DAG_ID", "").strip()

VALID_MODES = {"manual", "interval"}
MIN_INTERVAL_SECONDS = 10
MAX_INTERVAL_SECONDS = 86400

_pool: Optional[psycopg2.pool.SimpleConnectionPool] = None
_trigger_lock = asyncio.Lock()

STATUS_COLUMNS = [
    ("new", "New"),
    ("classified", "Classified"),
    ("parsed", "Parsed"),
    ("extracted", "Extracted"),
    ("stored", "Stored"),
    ("failed", "Failed"),
]
ACTIVE_CONTROL_PLANE_STATUSES = {"planning", "ready", "running"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _connect_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.SimpleConnectionPool(1, 8, dsn=DATABASE_URL)
    return _pool


def _db_query(sql: str, params: Any = None) -> list[dict[str, Any]]:
    conn = _connect_pool().getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or ())
            rows = [dict(row) for row in cur.fetchall()] if cur.description else []
            conn.commit()
            return rows
    except Exception:
        conn.rollback()
        raise
    finally:
        _connect_pool().putconn(conn)


def _db_execute(sql: str, params: Any = None) -> list[dict[str, Any]]:
    conn = _connect_pool().getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or ())
            rows = [dict(row) for row in cur.fetchall()] if cur.description else []
            conn.commit()
            return rows
    except Exception:
        conn.rollback()
        raise
    finally:
        _connect_pool().putconn(conn)


def _init_pool() -> None:
    last_error: Exception | None = None
    for attempt in range(1, 31):
        try:
            _connect_pool()
            _db_query("SELECT 1")
            log.info("database ready")
            return
        except Exception as exc:  # noqa: BLE001 - startup retry
            last_error = exc
            log.warning("database not ready (%s/30): %s", attempt, exc)
            time.sleep(2)
    raise RuntimeError(f"database unavailable: {last_error}")


def _get_setting(key: str, default: str = "") -> str:
    rows = _db_query(
        "SELECT setting_value FROM email_intelligence.settings WHERE setting_key = %s",
        (key,),
    )
    return str(rows[0]["setting_value"]) if rows else default


def _set_setting(key: str, value: str) -> None:
    _db_execute(
        """
        INSERT INTO email_intelligence.settings (setting_key, setting_value)
        VALUES (%s, %s)
        ON CONFLICT (setting_key) DO UPDATE
        SET setting_value = EXCLUDED.setting_value, updated_at = now()
        """,
        (key, value),
    )


def _target_dag_id() -> str:
    return (_get_setting("target_dag_id", ENV_TARGET_DAG_ID) or "").strip()


def _scheduler_mode() -> str:
    mode = (_get_setting("scheduler_mode", SCHEDULER_MODE_ENV) or "manual").strip().lower()
    return mode if mode in VALID_MODES else "manual"


def _interval_seconds() -> int:
    raw = _get_setting("interval_seconds", str(INTERVAL_SECONDS))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, value))


def _next_run_at() -> Optional[datetime]:
    raw = (_get_setting("next_run_at", "") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _schedule_next(interval_seconds: int) -> str:
    next_run = datetime.now(timezone.utc) + timedelta(seconds=interval_seconds)
    _set_setting("next_run_at", next_run.isoformat())
    return next_run.isoformat()


def _clear_next_run() -> None:
    _set_setting("next_run_at", "")


def _active_run(source_dag_id: str) -> Optional[dict[str, Any]]:
    rows = _db_query(
        """
        SELECT id, source_dag_id, instance_dag_id, status, started_at
        FROM email_intelligence.scheduler_runs
        WHERE status IN ('triggered', 'running') AND source_dag_id = %s
          AND instance_dag_id = source_dag_id
        ORDER BY started_at DESC
        LIMIT 1
        """,
        (source_dag_id,),
    )
    return rows[0] if rows else None


def _scheduler_status() -> dict[str, Any]:
    target = _target_dag_id()
    active = _active_run(target) if target else None
    last_run: list[dict[str, Any]] = []
    if target:
        last_run = _db_query(
            """
            SELECT id, source_dag_id, instance_dag_id, trigger, status, error,
                   started_at, finished_at
            FROM email_intelligence.scheduler_runs
            WHERE source_dag_id = %s AND instance_dag_id = %s
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (target, target),
        )
    next_run = _next_run_at()
    return {
        "mode": _scheduler_mode(),
        "interval_seconds": _interval_seconds(),
        "target_dag_id": target,
        "next_run_at": next_run.isoformat() if next_run else None,
        "active_instance_id": active.get("instance_dag_id") if active else None,
        "last_run": last_run[0] if last_run else None,
        "now": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Control-plane helpers
# ---------------------------------------------------------------------------

async def _cp_request(
    method: str,
    path: str,
    payload: Optional[dict[str, Any]] = None,
    timeout: float = 60.0,
) -> Any:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(
            method, f"{CONTROL_PLANE_URL}{path}", json=payload
        )
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail") or response.text[:300]
        except Exception:
            detail = response.text[:300]
        raise RuntimeError(
            f"control-plane {method} {path} -> {response.status_code}: {detail}"
        )
    return response.json() if response.content else {}


def _map_run_status(cp_status: str) -> str:
    if cp_status in ("planning", "ready", "running"):
        return "running"
    if cp_status in TERMINAL_STATUSES:
        return cp_status
    return cp_status or "unknown"


def _normalize_status(value: Any) -> str:
    """Handle enum-like values such as 'DAGStatus.RUNNING' as well as plain strings."""
    return str(value or "").split(".")[-1].strip().lower()


async def _refresh_runs() -> None:
    rows = _db_query(
        """
        SELECT id, instance_dag_id, status
        FROM email_intelligence.scheduler_runs
        WHERE status IN ('triggered', 'running') AND instance_dag_id IS NOT NULL
          AND source_dag_id = instance_dag_id
        ORDER BY started_at DESC
        LIMIT 20
        """
    )
    for row in rows:
        instance_id = row["instance_dag_id"]
        try:
            detail = await _cp_request(
                "GET", f"/api/dags/{instance_id}", timeout=15.0
            )
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            log.warning("could not refresh run %s: %s", instance_id, exc)
            continue

        cp_status = _normalize_status(detail.get("status"))
        error = None
        if cp_status == "ready":
            mapped = "failed"
            error = "DAG is ready but did not enter running state"
        else:
            mapped = _map_run_status(cp_status)

        if mapped in TERMINAL_STATUSES:
            _db_execute(
                """
                UPDATE email_intelligence.scheduler_runs
                SET status = %s, finished_at = COALESCE(finished_at, now()),
                    error = COALESCE(%s, error)
                WHERE id = %s
                """,
                (mapped, error, row["id"]),
            )
        elif mapped != row["status"]:
            _db_execute(
                "UPDATE email_intelligence.scheduler_runs SET status = %s WHERE id = %s",
                (mapped, row["id"]),
            )


def _record_run(
    source_dag_id: str,
    instance_dag_id: Optional[str],
    trigger: str,
    status: str,
    error: Optional[str] = None,
) -> Optional[str]:
    rows = _db_execute(
        """
        INSERT INTO email_intelligence.scheduler_runs
            (source_dag_id, instance_dag_id, trigger, status, error)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (source_dag_id, instance_dag_id, trigger, status, error),
    )
    return str(rows[0]["id"]) if rows else None


async def _trigger(trigger: str) -> dict[str, Any]:
    async with _trigger_lock:
        target = await asyncio.to_thread(_target_dag_id)
        if not target:
            return {
                "ok": False,
                "error": "No target DAG id configured. Set it in Settings or TARGET_DAG_ID.",
            }

        try:
            detail = await _cp_request("GET", f"/api/dags/{target}", timeout=15.0)
        except Exception as exc:  # noqa: BLE001 - record and surface
            run_id = _record_run(target, None, trigger, "failed", str(exc))
            return {"ok": False, "error": str(exc), "run_id": run_id}

        target_status = _normalize_status(detail.get("status"))
        if detail.get("locked"):
            return {
                "ok": False,
                "error": (
                    "Target DAG is locked (a frozen template). Use the backoffice "
                    "template run or choose an unlocked working DAG."
                ),
                "source_dag_id": target,
            }
        if target_status in ("running", "planning"):
            return {
                "ok": False,
                "skipped": True,
                "reason": f"target DAG is already {target_status}",
                "source_dag_id": target,
            }
        if target_status not in ("ready", "failed", "completed", "cancelled"):
            return {
                "ok": False,
                "error": f"Target DAG is in '{target_status or 'unknown'}' state and cannot be started.",
                "source_dag_id": target,
            }

        await _refresh_runs()
        try:
            started = await _cp_request(
                "POST", f"/api/dags/{target}/start", timeout=60.0
            )
        except Exception as exc:  # noqa: BLE001 - record and surface
            run_id = _record_run(target, None, trigger, "failed", str(exc))
            return {"ok": False, "error": str(exc), "run_id": run_id}

        cp_status = _normalize_status(started.get("status")) or "running"
        if cp_status == "ready":
            run_id = _record_run(
                target,
                target,
                trigger,
                "failed",
                "DAG did not enter running state after start",
            )
            return {
                "ok": False,
                "error": "DAG did not enter running state after start",
                "run_id": run_id,
                "source_dag_id": target,
                "instance_dag_id": target,
            }

        run_status = (
            "running"
            if cp_status in ("running", "planning")
            else _map_run_status(cp_status)
        )
        run_id = _record_run(target, target, trigger, run_status)
        return {
            "ok": True,
            "run_id": run_id,
            "source_dag_id": target,
            "instance_dag_id": target,
            "status": run_status,
        }


async def _scheduler_loop() -> None:
    while True:
        try:
            await _refresh_runs()
            mode = await asyncio.to_thread(_scheduler_mode)
            if mode == "manual":
                if await asyncio.to_thread(_next_run_at) is not None:
                    await asyncio.to_thread(_clear_next_run)
            else:
                target = await asyncio.to_thread(_target_dag_id)
                if not target:
                    if await asyncio.to_thread(_next_run_at) is not None:
                        await asyncio.to_thread(_clear_next_run)
                else:
                    interval = await asyncio.to_thread(_interval_seconds)
                    next_run = await asyncio.to_thread(_next_run_at)
                    now = datetime.now(timezone.utc)
                    if next_run is None:
                        await asyncio.to_thread(_schedule_next, interval)
                    elif now >= next_run:
                        result = await _trigger("auto")
                        await asyncio.to_thread(_schedule_next, interval)
                        if result.get("ok"):
                            log.info(
                                "auto-started target DAG %s",
                                result.get("source_dag_id"),
                            )
                        elif result.get("skipped"):
                            log.info(
                                "auto trigger skipped: %s",
                                result.get("reason"),
                            )
                        else:
                            log.warning(
                                "auto trigger failed: %s",
                                result.get("error"),
                            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never kill the loop
            log.exception("scheduler loop error")
        await asyncio.sleep(SCHEDULER_POLL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(_init_pool)
    task = asyncio.create_task(_scheduler_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Email Intelligence Scheduler", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class TargetRequest(BaseModel):
    dag_id: str


class SchedulerConfig(BaseModel):
    mode: Optional[str] = None
    interval_seconds: Optional[int] = None
    target_dag_id: Optional[str] = None


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    db_ok = True
    cp_ok = True
    try:
        await run_in_threadpool(_db_query, "SELECT 1")
    except Exception:  # noqa: BLE001
        db_ok = False

    try:
        await _cp_request("GET", "/health", timeout=5.0)
    except Exception:  # noqa: BLE001
        cp_ok = False

    scheduler: dict[str, Any] = {
        "mode": SCHEDULER_MODE_ENV if SCHEDULER_MODE_ENV in VALID_MODES else "manual",
        "interval_seconds": INTERVAL_SECONDS,
        "target_dag_id": "",
        "next_run_at": None,
        "active_instance_id": None,
        "last_run": None,
    }
    if db_ok:
        try:
            scheduler = await run_in_threadpool(_scheduler_status)
        except Exception:  # noqa: BLE001
            db_ok = False

    return {
        "ok": db_ok and cp_ok,
        "db": db_ok,
        "control_plane": cp_ok,
        "mode": scheduler.get("mode"),
        "interval_seconds": scheduler.get("interval_seconds"),
        "target_dag_id": scheduler.get("target_dag_id"),
        "next_run_at": scheduler.get("next_run_at"),
        "active_instance_id": scheduler.get("active_instance_id"),
        "last_run": scheduler.get("last_run"),
    }


@app.get("/api/summary")
async def summary() -> dict[str, Any]:
    def load() -> dict[str, Any]:
        by_status = {key: 0 for key, _ in STATUS_COLUMNS}
        for row in _db_query(
            "SELECT status, count(*)::int AS count FROM email_intelligence.emails GROUP BY status"
        ):
            by_status[str(row["status"])] = int(row["count"])

        by_intent: dict[str, int] = {}
        for row in _db_query(
            "SELECT intent, count(*)::int AS count FROM email_intelligence.emails "
            "WHERE intent IS NOT NULL GROUP BY intent"
        ):
            by_intent[str(row["intent"])] = int(row["count"])

        target = _target_dag_id()
        last_run = []
        if target:
            last_run = _db_query(
                """
                SELECT id, source_dag_id, instance_dag_id, trigger, status, error,
                       started_at, finished_at
                FROM email_intelligence.scheduler_runs
                WHERE source_dag_id = %s AND instance_dag_id = %s
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (target, target),
            )
        active = _active_run(target) if target else None

        next_run = _next_run_at()
        return {
            "total": sum(by_status.values()),
            "by_status": by_status,
            "by_intent": by_intent,
            "target_dag_id": target,
            "mode": _scheduler_mode(),
            "next_run_at": next_run.isoformat() if next_run else None,
            "active_instance_id": active.get("instance_dag_id") if active else None,
            "last_run": last_run[0] if last_run else None,
            "interval_seconds": _interval_seconds(),
        }

    return await run_in_threadpool(load)


def _email_list_sql(filters: dict[str, str], limit: int) -> tuple[str, list[Any]]:
    where = ["1=1"]
    params: list[Any] = []
    if filters.get("status"):
        where.append("e.status = %s")
        params.append(filters["status"])
    if filters.get("intent"):
        where.append("e.intent = %s")
        params.append(filters["intent"])
    if filters.get("search"):
        needle = f"%{filters['search']}%"
        where.append(
            "(e.subject ILIKE %s OR e.from_addr ILIKE %s OR x.company_name ILIKE %s)"
        )
        params.extend([needle, needle, needle])
    params.append(limit)
    sql = f"""
        SELECT e.id, e.message_id, e.folder, e.from_addr, e.to_addr, e.subject,
               e.received_at, e.intent, e.status, e.error, e.updated_at,
               x.company_name, x.stage, x.capital_raise
        FROM email_intelligence.emails e
        LEFT JOIN email_intelligence.extractions x ON x.email_id = e.id
        WHERE {' AND '.join(where)}
        ORDER BY e.received_at DESC NULLS LAST, e.created_at DESC
        LIMIT %s
    """
    return sql, params


@app.get("/api/emails")
async def list_emails(
    status: str = Query("", max_length=40),
    intent: str = Query("", max_length=40),
    search: str = Query("", max_length=200),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    sql, params = _email_list_sql(
        {"status": status, "intent": intent, "search": search}, limit
    )
    rows = await run_in_threadpool(_db_query, sql, params)
    return {"emails": rows}


@app.get("/api/emails/{email_id}")
async def email_detail(email_id: str) -> dict[str, Any]:
    try:
        normalized = str(uuid.UUID(email_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="email not found")

    def load() -> dict[str, Any]:
        emails = _db_query(
            """
            SELECT id, message_id, folder, imap_uid, from_addr, to_addr, subject,
                   received_at, body_text, intent, status, error, created_at, updated_at
            FROM email_intelligence.emails
            WHERE id = %s
            """,
            (normalized,),
        )
        if not emails:
            return {}
        attachments = _db_query(
            """
            SELECT id, filename, content_type, size_bytes, storage_path,
                   parse_status, error, created_at,
                   LEFT(COALESCE(extracted_text, ''), 4000) AS extracted_text_preview
            FROM email_intelligence.attachments
            WHERE email_id = %s
            ORDER BY filename
            """,
            (normalized,),
        )
        extractions = _db_query(
            """
            SELECT company_name, website, sector, stage, capital_raise, valuation,
                   thesis, traction_metrics, links, confidence, model, raw_json, created_at
            FROM email_intelligence.extractions
            WHERE email_id = %s
            """,
            (normalized,),
        )
        return {
            "email": emails[0],
            "attachments": attachments,
            "extraction": extractions[0] if extractions else None,
        }

    result = await run_in_threadpool(load)
    if not result:
        raise HTTPException(status_code=404, detail="email not found")
    return result


@app.get("/api/board")
async def board() -> dict[str, Any]:
    sql, params = _email_list_sql({}, 500)
    rows = await run_in_threadpool(_db_query, sql, params)
    groups: dict[str, list[dict[str, Any]]] = {key: [] for key, _ in STATUS_COLUMNS}
    for row in rows:
        groups.setdefault(str(row["status"]), []).append(row)
    return {
        "columns": [
            {"status": key, "label": label, "cards": groups.get(key, [])}
            for key, label in STATUS_COLUMNS
        ]
    }


@app.get("/api/runs")
async def list_runs(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    await _refresh_runs()
    target = await run_in_threadpool(_target_dag_id)
    if not target:
        return {"runs": []}
    rows = await run_in_threadpool(
        _db_query,
        """
        SELECT id, source_dag_id, instance_dag_id, trigger, status, error,
               started_at, finished_at
        FROM email_intelligence.scheduler_runs
        WHERE source_dag_id = %s AND instance_dag_id = %s
        ORDER BY started_at DESC
        LIMIT %s
        """,
        (target, target, limit),
    )
    return {"runs": rows}


async def _validate_target(dag_id: str) -> dict[str, Any]:
    try:
        return await _cp_request("GET", f"/api/dags/{dag_id}", timeout=15.0)
    except RuntimeError as exc:
        message = str(exc)
        if "404" in message:
            raise HTTPException(
                status_code=404, detail="DAG not found in control-plane"
            ) from exc
        raise HTTPException(status_code=502, detail=message) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503, detail=f"control-plane unreachable: {exc}"
        ) from exc


@app.get("/api/scheduler")
async def get_scheduler() -> dict[str, Any]:
    return await run_in_threadpool(_scheduler_status)


@app.patch("/api/scheduler")
async def update_scheduler(body: SchedulerConfig) -> dict[str, Any]:
    mode = body.mode
    if mode is not None:
        mode = mode.strip().lower()
        if mode not in VALID_MODES:
            raise HTTPException(
                status_code=422, detail="mode must be 'manual' or 'interval'"
            )

    interval = body.interval_seconds
    if interval is not None and not (
        MIN_INTERVAL_SECONDS <= interval <= MAX_INTERVAL_SECONDS
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"interval_seconds must be between {MIN_INTERVAL_SECONDS} "
                f"and {MAX_INTERVAL_SECONDS}"
            ),
        )

    target = body.target_dag_id
    if target is not None:
        target = target.strip()
        if target:
            await _validate_target(target)

    def apply() -> dict[str, Any]:
        if mode is not None:
            _set_setting("scheduler_mode", mode)
        if interval is not None:
            _set_setting("interval_seconds", str(interval))
        if target is not None:
            _set_setting("target_dag_id", target)

        current_mode = _scheduler_mode()
        current_target = _target_dag_id()
        current_interval = _interval_seconds()
        if current_mode == "interval" and current_target:
            _schedule_next(current_interval)
        else:
            _clear_next_run()
        return _scheduler_status()

    return await run_in_threadpool(apply)


@app.get("/api/target")
async def get_target() -> dict[str, Any]:
    scheduler = await run_in_threadpool(_scheduler_status)
    return {
        "target_dag_id": scheduler["target_dag_id"],
        "interval_seconds": scheduler["interval_seconds"],
        "mode": scheduler["mode"],
        "next_run_at": scheduler["next_run_at"],
    }


@app.post("/api/target")
async def set_target(body: TargetRequest) -> dict[str, Any]:
    dag_id = body.dag_id.strip()
    if not dag_id:
        raise HTTPException(status_code=400, detail="dag_id is required")

    detail = await _validate_target(dag_id)

    def apply() -> dict[str, Any]:
        _set_setting("target_dag_id", dag_id)
        if _scheduler_mode() == "interval":
            _schedule_next(_interval_seconds())
        else:
            _clear_next_run()
        return _scheduler_status()

    scheduler = await run_in_threadpool(apply)
    return {
        "ok": True,
        "target_dag_id": dag_id,
        "dag_status": _normalize_status(detail.get("status")),
        "objective": (detail.get("objective") or "")[:300],
        "mode": scheduler["mode"],
        "next_run_at": scheduler["next_run_at"],
    }


@app.post("/api/run-now")
async def run_now() -> dict[str, Any]:
    result = await _trigger("manual")
    if result.get("ok"):
        def reschedule() -> None:
            if _scheduler_mode() == "interval" and _target_dag_id():
                _schedule_next(_interval_seconds())
            else:
                _clear_next_run()

        await run_in_threadpool(reschedule)
    return result
