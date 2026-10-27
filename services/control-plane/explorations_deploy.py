"""Detect deployable web applications in an exploration workspace."""
import json
import os
import re
from dataclasses import dataclass, asdict
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    DAGNode,
    Deployment,
    ExplorationCycle,
    ExplorationMessage,
    ExplorationSession,
    Task,
)

APP_FILES = {
    "app.py": "Flask",
    "api.py": "Flask",
    "main.py": "FastAPI",
    "server.py": "Express",
    "asgi.py": "FastAPI",
    "wsgi.py": "Flask",
    "manage.py": "Django",
    "index.js": "Express",
    "server.js": "Express",
    "app.js": "Express",
    "package.json": "Express",
}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".cache", ".next", "build", "dist"}
MAX_READ = 256 * 1024


@dataclass
class DeployableApp:
    cycle_number: int
    dag_id: str
    task_id: str
    app_path: str
    framework: str
    entrypoint: str
    port: int
    name: str
    source_node: Optional[str] = None
    agent_image: Optional[str] = None
    existing_deployment: Optional[dict[str, Any]] = None


def _workspace_root(session: ExplorationSession) -> str:
    return os.path.realpath(f"/workspaces/{session.workspace_id}")


def _workspace_files(root: str) -> list[str]:
    files: list[str] = []
    if not os.path.isdir(root):
        return files
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        for name in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")
            files.append(rel)
    return files


def _read_text(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_READ)
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _resolve_path(root: str, workspace_files: list[str], deliverable: str) -> Optional[str]:
    normalized = str(deliverable or "").lstrip("/")
    if normalized in workspace_files:
        return normalized
    matches = [path for path in workspace_files if path.endswith("/" + normalized) or path.split("/")[-1] == normalized]
    if not matches:
        return None
    return sorted(matches, key=lambda path: (len(path), path))[0]


def _fastapi_entrypoint(path: str, port: int) -> str:
    """Build a cwd-safe uvicorn command for app files in subdirectories."""
    if "/" in path:
        directory, filename = path.rsplit("/", 1)
        module = filename[:-3] if filename.endswith(".py") else filename
        return f'sh -c "cd {directory} && uvicorn {module}:app --host 0.0.0.0 --port {port}"'
    module = path[:-3] if path.endswith(".py") else path
    return f"uvicorn {module}:app --host 0.0.0.0 --port {port}"


def _flask_port(text: str) -> int:
    match = re.search(r"app\.run\([^)]*port\s*=\s*(\d+)", text, re.DOTALL)
    if match:
        return int(match.group(1))
    match = re.search(r"port\s*=\s*(\d+)", text)
    return int(match.group(1)) if match else 5000


def _is_express_server(text: str) -> bool:
    if any(marker in text for marker in ("document.", "window.", "addEventListener(")):
        return False
    return bool(
        re.search(r"require\(\s*[\"']express[\"']\s*\)", text)
        or re.search(r"from\s+[\"']express[\"']", text)
        or re.search(r"\bexpress\s*\(", text)
    )


def _imported_flask_target(root: str, rel: str, text: str) -> Optional[str]:
    """Follow a thin app.py -> api.py import to find the real app module."""
    match = re.search(
        r"from\s+([A-Za-z_][\w.]*)\s+import\s+.*\b(?:app|create_app)\b",
        text,
    )
    if not match:
        return None
    module = match.group(1).replace(".", "/")
    base_dir = os.path.dirname(rel)
    target = f"{base_dir}/{module}.py" if base_dir else f"{module}.py"
    return target if os.path.isfile(os.path.join(root, target)) else None


def _detect_app(root: str, workspace_files: list[str], deliverable: str) -> Optional[DeployableApp]:
    rel = _resolve_path(root, workspace_files, deliverable)
    if not rel:
        return None
    parts = rel.split("/")
    base = parts[-1].lower()
    if base.startswith("test_") or base == "conftest.py":
        return None
    if any(part.lower() in ("test", "tests", "__tests__") for part in parts[:-1]):
        return None
    if base not in APP_FILES:
        return None
    full = os.path.join(root, rel)
    text = _read_text(full)
    framework = None
    entrypoint = ""
    port = 5000

    if base == "manage.py" or "DJANGO_SETTINGS_MODULE" in text:
        framework = "Django"
        port = 8000
        entrypoint = f"python {rel} runserver 0.0.0.0:{port}"
    elif base == "package.json":
        try:
            package = json.loads(text)
        except Exception:
            package = {}
        deps = {**(package.get("dependencies") or {}), **(package.get("devDependencies") or {})}
        if "express" not in deps:
            return None
        package_dir = os.path.dirname(rel)
        server = next(
            (
                path for path in workspace_files
                if os.path.dirname(path) == package_dir and os.path.basename(path) in ("index.js", "server.js", "app.js")
            ),
            None,
        )
        if not server:
            return None
        rel = server
        text = _read_text(os.path.join(root, rel))
        framework = "Express"
        match = re.search(r"listen\(\s*(\d+)", text)
        port = int(match.group(1)) if match else 3000
        entrypoint = f"node {rel}"
    elif base in ("app.py", "api.py", "wsgi.py", "asgi.py"):
        marker_text = text
        if base in ("app.py", "wsgi.py") and not ("Flask(" in text or "from flask" in text or "FastAPI(" in text or "from fastapi" in text):
            imported = _imported_flask_target(root, rel, text)
            if imported:
                marker_text = _read_text(os.path.join(root, imported))
        if "Flask(" in marker_text or "from flask" in marker_text or "flask import" in marker_text:
            framework = "Flask"
            port = _flask_port(marker_text)
            entrypoint = f"python {rel}"
        elif "FastAPI(" in marker_text or "from fastapi" in marker_text:
            framework = "FastAPI"
            port = 8000
            entrypoint = _fastapi_entrypoint(rel, port)
    elif base == "main.py":
        marker_text = text
        if not ("FastAPI(" in text or "from fastapi" in text or "Flask(" in text or "from flask" in text):
            imported = _imported_flask_target(root, rel, text)
            if imported:
                marker_text = _read_text(os.path.join(root, imported))
        if "FastAPI(" in marker_text or "from fastapi" in marker_text:
            framework = "FastAPI"
            port = 8000
            entrypoint = _fastapi_entrypoint(rel, port)
        elif "Flask(" in marker_text or "from flask" in marker_text:
            framework = "Flask"
            port = _flask_port(marker_text)
            entrypoint = f"python {rel}"
    elif base in ("server.py", "index.js", "server.js", "app.js"):
        if base == "server.py" and ("Flask(" in text or "from flask" in text):
            framework = "Flask"
            port = _flask_port(text)
            entrypoint = f"python {rel}"
        elif base == "server.py" and ("FastAPI(" in text or "from fastapi" in text):
            framework = "FastAPI"
            port = 8000
            entrypoint = _fastapi_entrypoint(rel, port)
        elif _is_express_server(text):
            framework = "Express"
            match = re.search(r"listen\(\s*(\d+)", text)
            port = int(match.group(1)) if match else 3000
            entrypoint = f"node {rel}"
    if not framework:
        return None
    return DeployableApp(
        cycle_number=0,
        dag_id="",
        task_id="",
        app_path=rel,
        framework=framework,
        entrypoint=entrypoint,
        port=port,
        name=rel.split("/")[-1],
    )


async def detect_cycle_deployables(session: ExplorationSession, db: AsyncSession) -> list[DeployableApp]:
    root = _workspace_root(session)
    workspace_files = _workspace_files(root)
    if not workspace_files:
        return []

    cycles_result = await db.execute(
        select(ExplorationCycle)
        .where(ExplorationCycle.session_id == session.id)
        .order_by(ExplorationCycle.cycle_number.asc())
    )
    cycles = list(cycles_result.scalars().all())

    by_path: dict[str, DeployableApp] = {}
    for cycle in cycles:
        if str(getattr(cycle.status, "value", cycle.status)) not in ("completed", "finalized"):
            continue
        result_msg = await db.execute(
            select(ExplorationMessage).where(
                ExplorationMessage.cycle_id == cycle.id,
                ExplorationMessage.kind == "cycle_result",
            )
        )
        message = result_msg.scalars().first()
        if not message:
            continue
        deliverables = (message.metadata_json or {}).get("deliverables") or []
        dag_nodes_result = await db.execute(select(DAGNode).where(DAGNode.dag_id == cycle.dag_id))
        dag_nodes = list(dag_nodes_result.scalars().all())
        task_ids = {node.node_id: node.task_id for node in dag_nodes if node.task_id}
        deliverable_set = set(str(item) for item in deliverables)
        for node in dag_nodes:
            deliverable_set.update(
                path for path in workspace_files
                if path.startswith(f"{node.node_id}/") and "/static/" not in f"/{path}"
            )
        for deliverable in sorted(deliverable_set):
            candidate = _detect_app(root, workspace_files, str(deliverable))
            if not candidate:
                continue
            candidate.cycle_number = cycle.cycle_number
            candidate.dag_id = cycle.dag_id
            owner = next(
                (
                    node for node in dag_nodes
                    if node.task_id and str(deliverable).startswith(node.node_id + "/")
                ),
                None,
            )
            completed = [
                node for node in dag_nodes
                if node.task_id and str(getattr(node.status, "value", node.status)) == "completed"
            ]
            chosen = owner or (completed[-1] if completed else next((node for node in dag_nodes if node.task_id), None))
            candidate.source_node = chosen.node_id if chosen else None
            candidate.task_id = chosen.task_id if chosen else ""
            candidate.name = f"{session.title} — Cycle {cycle.cycle_number}"
            by_path[candidate.app_path] = candidate

    candidates = list(by_path.values())
    non_verify = [
        candidate for candidate in candidates
        if not candidate.app_path.startswith("verify-")
        and "/app-snapshot/" not in candidate.app_path
    ]
    if non_verify:
        candidates = non_verify
    grouped: dict[str, list[DeployableApp]] = {}
    for candidate in candidates:
        grouped.setdefault(os.path.dirname(candidate.app_path), []).append(candidate)
    canonical: list[DeployableApp] = []
    for group in grouped.values():
        group.sort(key=lambda item: (
            0 if os.path.basename(item.app_path) == "app.py" else 1,
            0 if "api.py" not in item.app_path else 2,
            item.app_path,
        ))
        canonical.append(group[0])

    deployables: list[DeployableApp] = []
    for candidate in canonical:
        if not candidate.task_id:
            continue
        task_result = await db.execute(select(Task).where(Task.id == candidate.task_id))
        task = task_result.scalar_one_or_none()
        if task:
            candidate.agent_image = task.current_image
        deployment_result = await db.execute(
            select(Deployment)
            .where(Deployment.task_id == candidate.task_id)
            .order_by(Deployment.created_at.desc())
        )
        deployments = list(deployment_result.scalars().all())
        active_statuses = {"pending_approval", "approved", "building", "built", "running"}
        deployment = next(
            (
                item for item in deployments
                if str(getattr(item.status, "value", item.status)) in active_statuses
            ),
            deployments[0] if deployments else None,
        )
        if deployment:
            candidate.existing_deployment = {
                "id": deployment.id,
                "status": str(getattr(deployment.status, "value", deployment.status)),
                "url": deployment.url,
                "host_port": deployment.host_port,
                "error": deployment.error,
            }
        deployables.append(candidate)
    deployables.sort(key=lambda item: (item.cycle_number, item.app_path))
    return deployables
