"""Agent sandbox: build, run, test, expose, and clean up solution containers.

Runs against a dedicated Docker host (SANDBOX_DOCKER_HOST), never the platform
dind. Jobs are workspace/context tarballs; no host path mounts. The API is
agent-facing; the HTTP gateway at /sandbox/{job}/... is the only exposed route.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shlex
import sqlite3
import tarfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import docker
import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

DATA_DIR = Path(os.getenv("SANDBOX_DATA_DIR", "/data"))
JOBS_DIR = DATA_DIR / "jobs"
DB_PATH = DATA_DIR / "sandbox.db"
DOCKER_HOST = os.getenv("SANDBOX_DOCKER_HOST", "tcp://sandbox-dind:2375")
DIND_HOST = os.getenv("SANDBOX_DIND_HOST", "sandbox-dind")
PUBLIC_URL = os.getenv("SANDBOX_PUBLIC_URL", "http://localhost:8090").rstrip("/")
CONTROL_PLANE_URL = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
ADMIN_TOKEN = os.getenv("SANDBOX_ADMIN_TOKEN", "sandbox-admin")
MAX_JOBS = int(os.getenv("SANDBOX_MAX_JOBS", "2"))
DEFAULT_TTL = int(os.getenv("SANDBOX_DEFAULT_TTL_SECONDS", "900"))
ALLOWED_BASES = [
    item.strip()
    for item in os.getenv(
        "SANDBOX_ALLOWED_BASE_IMAGES",
        "python:,python@,node:,node@,openclaw-agent:,openclaw-agent@,ubuntu:,debian:,alpine:",
    ).split(",")
    if item.strip()
]

app = FastAPI(title="TaskForge Sandbox", version="0.1.0")
_db_lock = threading.Lock()
_semaphore: Optional[asyncio.Semaphore] = None
_running: Dict[str, asyncio.Task] = {}


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    with _db_lock, _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY,
              task_id TEXT,
              workspace_id TEXT,
              owner TEXT,
              job_token TEXT,
              status TEXT,
              mode TEXT,
              dockerfile TEXT,
              command TEXT,
              tests TEXT,
              artifacts TEXT,
              ports TEXT,
              ttl_seconds INTEGER,
              image TEXT,
              container_id TEXT,
              service_port INTEGER,
              host_port INTEGER,
              url TEXT,
              logs TEXT,
              test_results TEXT,
              error TEXT,
              created_at REAL,
              started_at REAL,
              finished_at REAL
            )
            """
        )


@app.on_event("startup")
async def _startup() -> None:
    global _semaphore
    _init_db()
    _semaphore = asyncio.Semaphore(MAX_JOBS)
    with _db_lock, _db() as conn:
        conn.execute(
            "UPDATE jobs SET status='failed', error='sandbox service restarted' "
            "WHERE status IN ('queued','building','running')"
        )


def _job_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    for key in ("tests", "artifacts", "ports", "test_results"):
        try:
            d[key] = json.loads(d.get(key) or "[]")
        except Exception:
            d[key] = []
    d.pop("job_token", None)
    return d


def _update(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    columns = ", ".join(f"{key}=?" for key in fields)
    with _db_lock, _db() as conn:
        conn.execute(
            f"UPDATE jobs SET {columns} WHERE id=?",
            [*fields.values(), job_id],
        )


def _get(job_id: str) -> Optional[Dict[str, Any]]:
    with _db_lock, _db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _job_dict(row) if row else None


def _get_raw(job_id: str) -> Optional[sqlite3.Row]:
    with _db_lock, _db() as conn:
        return conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def _safe_extract(tar_path: Path, dest: Path) -> None:
    with tarfile.open(tar_path, "r:*") as archive:
        for member in archive.getmembers():
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise ValueError("context archive contains an unsafe path")
        archive.extractall(dest)


async def _verify_task(task_id: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{CONTROL_PLANE_URL}/api/tasks/{task_id}")
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="unknown task")
    return resp.json()


async def _owner(request: Request, task_id: str = "") -> Dict[str, Any]:
    token = request.headers.get("X-Agent-Token", "")
    if request.headers.get("X-Sandbox-Token", "") == ADMIN_TOKEN:
        return {"task_id": task_id, "workspace_id": "", "admin": True}
    if token.startswith("task:"):
        token_task = token.split(":", 1)[1]
        if task_id and token_task != task_id:
            raise HTTPException(status_code=403, detail="task token does not own this job")
        task = await _verify_task(token_task)
        return {
            "task_id": token_task,
            "workspace_id": task.get("workspace_id") or "",
            "admin": False,
        }
    raise HTTPException(status_code=401, detail="missing sandbox credentials")


def _allowed_base(dockerfile_path: Path) -> None:
    text = dockerfile_path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        match = re.match(r"\s*FROM\s+(\S+)", line, re.IGNORECASE)
        if not match:
            continue
        base = match.group(1)
        if not any(base.startswith(prefix) for prefix in ALLOWED_BASES):
            raise ValueError(f"base image '{base}' is not allowed in the sandbox")
        return
    raise ValueError("Dockerfile has no FROM instruction")


def _collect_artifacts(container: Any, job_id: str, paths: List[str]) -> List[str]:
    out_dir = JOBS_DIR / job_id / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    collected: List[str] = []
    for path in paths:
        try:
            stream, stat = container.get_archive(path)
            payload = b"".join(stream)
            size = int(stat.get("size") or 0)
            if size > 100 * 1024 * 1024:
                continue
            blob = tarfile.open(fileobj=io.BytesIO(payload), mode="r:*")
            for member in blob.getmembers():
                target = (out_dir / member.name).resolve()
                if not str(target).startswith(str(out_dir.resolve())):
                    continue
                blob.extract(member, out_dir)
                if member.isfile():
                    collected.append(str(target.relative_to(out_dir)))
        except Exception:
            continue
    return collected


async def _execute(job_id: str, data: Dict[str, Any]) -> None:
    assert _semaphore is not None
    async with _semaphore:
        client = docker.DockerClient(base_url=DOCKER_HOST)
        container = None
        network = None
        image = f"sandbox-{job_id}"
        try:
            _update(job_id, status="building", started_at=time.time())
            context_dir = JOBS_DIR / job_id / "context"
            dockerfile = context_dir / (data["dockerfile"] or "Dockerfile")
            if not dockerfile.is_file():
                raise ValueError(f"Dockerfile not found: {data['dockerfile']}")
            _allowed_base(dockerfile)

            def build() -> None:
                client.images.build(
                    path=str(context_dir),
                    dockerfile=data["dockerfile"] or "Dockerfile",
                    tag=image,
                    rm=True,
                    forcerm=True,
                )

            await asyncio.to_thread(build)
            _update(job_id, status="running", image=image)

            network = await asyncio.to_thread(
                client.networks.create,
                f"sandbox-{job_id}",
                driver="bridge",
                internal=(data["mode"] == "job"),
                check_duplicate=True,
            )
            run_kwargs = dict(
                detach=True,
                network=network.name,
                mem_limit=f"{data['memory_mb']}m",
                nano_cpus=int(float(data["cpu"]) * 1_000_000_000),
                pids_limit=int(data["pids"]),
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                tmpfs={"/tmp": "size=512m,mode=1777"},
            )

            if data["mode"] == "service":
                port = int((data["ports"] or [8000])[0])
                host_port = int(os.getenv("SANDBOX_PORT_BASE", "19000")) + (abs(hash(job_id)) % 900)
                run_kwargs.update(
                    ports={f"{port}/tcp": host_port},
                    command=data["command"] or None,
                )
                container = await asyncio.to_thread(
                    client.containers.run, image, **run_kwargs
                )
                _update(
                    job_id,
                    status="healthy",
                    container_id=container.id,
                    service_port=port,
                    host_port=host_port,
                    url=f"/sandbox/{job_id}/",
                )
                logs = await asyncio.to_thread(
                    container.logs, stdout=True, stderr=True, tail=200
                )
                _update(job_id, logs=logs.decode("utf-8", errors="replace"))
                # Service jobs stay up until cancelled/TTL.
                await asyncio.sleep(int(data["ttl_seconds"]))
                _update(job_id, status="completed", finished_at=time.time())
                return

            container = await asyncio.to_thread(
                client.containers.run,
                image,
                command=data["command"] or None,
                **run_kwargs,
            )
            _update(job_id, container_id=container.id)
            try:
                result = await asyncio.to_thread(container.wait, timeout=int(data["ttl_seconds"]))
                exit_code = int(result.get("StatusCode", 1))
            except Exception:
                await asyncio.to_thread(container.kill)
                exit_code = 124
            logs = await asyncio.to_thread(
                container.logs, stdout=True, stderr=True, tail=4000
            )
            artifacts = await asyncio.to_thread(
                _collect_artifacts, container, job_id, data["artifacts"] or []
            )
            test_results = []
            for test in data["tests"] or []:
                test_container = await asyncio.to_thread(
                    client.containers.run,
                    image,
                    command=f"sh -lc {shlex.quote(str(test))}",
                    detach=True,
                    network=network.name,
                    mem_limit=f"{data['memory_mb']}m",
                    nano_cpus=int(float(data["cpu"]) * 1_000_000_000),
                    pids_limit=int(data["pids"]),
                    cap_drop=["ALL"],
                    security_opt=["no-new-privileges"],
                    tmpfs={"/tmp": "size=256m,mode=1777"},
                )
                try:
                    res = await asyncio.to_thread(test_container.wait, timeout=300)
                    code = int(res.get("StatusCode", 1))
                except Exception:
                    await asyncio.to_thread(test_container.kill)
                    code = 124
                tlogs = await asyncio.to_thread(
                    test_container.logs, stdout=True, stderr=True, tail=1000
                )
                test_results.append(
                    {
                        "command": str(test),
                        "exit_code": code,
                        "logs": tlogs.decode("utf-8", errors="replace")[-4000:],
                    }
                )
                await asyncio.to_thread(test_container.remove, force=True)
            _update(
                job_id,
                status="completed" if exit_code == 0 else "failed",
                finished_at=time.time(),
                logs=logs.decode("utf-8", errors="replace")[-20000:],
                artifacts=json.dumps(artifacts),
                test_results=json.dumps(test_results),
                error="" if exit_code == 0 else f"command exited {exit_code}",
            )
        except Exception as exc:
            _update(
                job_id,
                status="failed",
                finished_at=time.time(),
                error=str(exc)[:2000],
            )
        finally:
            try:
                if container is not None:
                    await asyncio.to_thread(container.remove, force=True)
            except Exception:
                pass
            try:
                if network is not None:
                    await asyncio.to_thread(network.remove)
            except Exception:
                pass


@app.get("/v1/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "docker_host": DOCKER_HOST, "max_jobs": MAX_JOBS}


@app.post("/v1/jobs", status_code=201)
async def create_job(
    request: Request,
    context: UploadFile = File(...),
    task_id: str = Form(""),
    workspace_id: str = Form(""),
    mode: str = Form("job"),
    dockerfile: str = Form("Dockerfile"),
    command: str = Form(""),
    tests: str = Form("[]"),
    artifacts: str = Form("[]"),
    ports: str = Form("[]"),
    ttl_seconds: int = Form(DEFAULT_TTL),
    cpu: float = Form(2.0),
    memory_mb: int = Form(4096),
    pids: int = Form(512),
):
    owner = await _owner(request, task_id)
    task_id = owner["task_id"]
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")
    try:
        tests_list = json.loads(tests)
        artifacts_list = json.loads(artifacts)
        ports_list = json.loads(ports)
    except Exception:
        raise HTTPException(status_code=400, detail="tests/artifacts/ports must be JSON arrays")
    if mode not in ("job", "service"):
        raise HTTPException(status_code=400, detail="mode must be job or service")

    job_id = f"sbx-{uuid.uuid4().hex[:10]}"
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    tar_path = job_dir / "context.tar"
    with tar_path.open("wb") as handle:
        while chunk := await context.read(1024 * 1024):
            handle.write(chunk)
    try:
        _safe_extract(tar_path, job_dir / "context")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid context archive: {exc}")

    job_token = uuid.uuid4().hex
    now = time.time()
    with _db_lock, _db() as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, task_id, workspace_id, owner, job_token, status, mode,
              dockerfile, command, tests, artifacts, ports, ttl_seconds, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                job_id,
                task_id,
                workspace_id or owner.get("workspace_id") or "",
                task_id,
                job_token,
                "queued",
                mode,
                dockerfile,
                command,
                json.dumps(tests_list),
                json.dumps(artifacts_list),
                json.dumps(ports_list),
                max(30, min(int(ttl_seconds), 3600)),
                now,
            ),
        )
    data = {
        "dockerfile": dockerfile,
        "command": command,
        "tests": tests_list,
        "artifacts": artifacts_list,
        "ports": ports_list,
        "ttl_seconds": max(30, min(int(ttl_seconds), 3600)),
        "cpu": max(0.25, min(float(cpu), 8.0)),
        "memory_mb": max(256, min(int(memory_mb), 16384)),
        "pids": max(64, min(int(pids), 4096)),
        "mode": mode,
    }
    task = asyncio.create_task(_execute(job_id, data))
    _running[job_id] = task
    task.add_done_callback(lambda _: _running.pop(job_id, None))
    job = _get(job_id) or {}
    job["job_token"] = job_token
    return job


@app.get("/v1/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> Dict[str, Any]:
    job = _get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    await _owner(request, job["task_id"])
    if job.get("url"):
        job["url"] = f"/sandbox/{job_id}/?token={_get_raw(job_id)['job_token']}"
    job["artifact_list"] = _list_artifacts(job_id)
    job["external_url"] = (
        f"{PUBLIC_URL}/sandbox/{job_id}/?token={_get_raw(job_id)['job_token']}"
        if job.get("host_port")
        else None
    )
    return job


def _list_artifacts(job_id: str) -> List[str]:
    root = JOBS_DIR / job_id / "artifacts"
    if not root.is_dir():
        return []
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())


@app.get("/v1/jobs/{job_id}/logs")
async def get_logs(job_id: str, request: Request) -> Response:
    job = _get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    await _owner(request, job["task_id"])
    return Response(job.get("logs") or "", media_type="text/plain")


@app.get("/v1/jobs/{job_id}/artifacts/{path:path}")
async def get_artifact(job_id: str, path: str, request: Request):
    job = _get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    await _owner(request, job["task_id"])
    root = (JOBS_DIR / job_id / "artifacts").resolve()
    target = (root / path).resolve()
    if not str(target).startswith(str(root)) or not target.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(target)


@app.post("/v1/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request) -> Dict[str, Any]:
    job = _get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    await _owner(request, job["task_id"])
    task = _running.get(job_id)
    if task:
        task.cancel()
    _update(job_id, status="cancelled", finished_at=time.time(), error="cancelled")
    return {"id": job_id, "status": "cancelled"}


@app.delete("/v1/jobs/{job_id}")
async def delete_job(job_id: str, request: Request) -> Dict[str, Any]:
    await cancel_job(job_id, request)
    _update(job_id, status="cancelled")
    return {"id": job_id, "status": "cancelled"}


@app.api_route("/sandbox/{job_id}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def sandbox_gateway(job_id: str, path: str, request: Request):
    job = _get_raw(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    token = request.query_params.get("token", "") or request.headers.get("X-Sandbox-Job-Token", "")
    if token != (job["job_token"] or ""):
        raise HTTPException(status_code=401, detail="invalid sandbox job token")
    if not job["host_port"] or job["status"] not in ("healthy", "running", "completed"):
        return JSONResponse(status_code=409, content={"detail": "service is not running"})
    target = f"http://{DIND_HOST}:{int(job['host_port'])}/{path}"
    body = await request.body()
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        resp = await client.request(
            request.method,
            target,
            content=body,
            headers={
                key: value
                for key, value in request.headers.items()
                if key.lower() not in ("host", "content-length")
            },
        )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers={
            key: value
            for key, value in resp.headers.items()
            if key.lower() not in ("content-encoding", "transfer-encoding", "connection")
        },
    )
