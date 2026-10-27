"""Helpers for user-attached DAG input files.

Attachments are stored on the shared workspace volume at
``<WORKSPACE_ROOT>/<workspace_id>/attachments/`` so every node/task in the DAG
can read them from ``/workspace/attachments/``. Only metadata (names, MIME,
sizes, relative paths) is kept in ``dag_json`` — never the file bytes in the DB.

The planner receives a manifest (names/types/sizes), not file contents; agents
inspect the files themselves.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from fastapi import HTTPException, UploadFile

from config import settings

MAX_FILES = 20
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024

# Broad but deliberate: documents, data, images, code/text, archives, slides.
ALLOWED_EXTENSIONS = {
    ".pdf", ".txt", ".md", ".rst", ".doc", ".docx", ".odt", ".rtf",
    ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".xlsx", ".xls", ".parquet",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".sql", ".sh",
    ".ipynb", ".xml", ".toml", ".ini", ".cfg",
    ".zip", ".tar", ".gz", ".tgz", ".7z",
    ".ppt", ".pptx",
}

ALLOWED_EXTENSIONS_TEXT = ", ".join(sorted(ALLOWED_EXTENSIONS))


def _extension(name: str) -> str:
    return Path(str(name or "")).suffix.lower()


def human_size(value: object) -> str:
    try:
        size = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "unknown size"
    if size < 0:
        return "unknown size"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def sanitize_filename(name: str) -> str:
    """Return a safe basename (no directories, control chars, or traversal)."""
    base = os.path.basename(str(name or "")).replace("\\", "_").strip()
    base = re.sub(r"[\x00-\x1f\x7f]", "", base)
    if not base or base in (".", ".."):
        raise HTTPException(status_code=400, detail="A file has an empty or invalid name.")
    stem, ext = os.path.splitext(base)
    ext = ext.lower()[:16]
    if not ext or ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type '{ext or '(none)'}' for '{base}'. "
                f"Allowed: {ALLOWED_EXTENSIONS_TEXT}"
            ),
        )
    stem = re.sub(r"[^\w.\- ]+", "_", stem, flags=re.UNICODE).strip(" ._-") or "file"
    return f"{stem[:120]}{ext}"


def validate_uploads(files: Iterable[UploadFile]) -> None:
    file_list = list(files)
    if len(file_list) > MAX_FILES:
        raise HTTPException(
            status_code=413,
            detail=f"At most {MAX_FILES} files can be attached (got {len(file_list)}).",
        )
    total_known = 0
    for upload in file_list:
        sanitize_filename(upload.filename or "")  # raises on bad names/types
        declared = getattr(upload, "size", None)
        if isinstance(declared, int):
            if declared > MAX_FILE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"'{upload.filename}' is larger than {human_size(MAX_FILE_BYTES)}.",
                )
            total_known += declared
    if total_known > MAX_TOTAL_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Total attachment size exceeds {human_size(MAX_TOTAL_BYTES)}.",
        )


def _attachments_dir(workspace_id: str) -> Path:
    return Path(settings.WORKSPACE_ROOT) / workspace_id / "attachments"


def _write_manifest(base: Path, workspace_id: str, files: List[dict]) -> None:
    manifest = {
        "workspace_id": workspace_id,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }
    (base / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    lines = [
        "# Attached reference files",
        "",
        "These files were attached by the user to this DAG. They are read-only",
        "reference context, available to every step at `/workspace/attachments/`.",
        "",
    ]
    for item in files:
        lines.append(
            f"- `{item['stored_as']}` — {item['mime']}, {human_size(item['size'])} "
            f"(original: {item['name']})"
        )
    (base / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def save_attachments(workspace_id: str, files: Iterable[UploadFile]) -> List[dict]:
    """Stream uploads into the workspace; return the attachment manifest.

    Enforces count/size limits and cleans up partial files on failure.
    """
    file_list = list(files)
    validate_uploads(file_list)
    if not file_list:
        return []

    base = _attachments_dir(workspace_id)
    base.mkdir(parents=True, exist_ok=True)

    saved: List[dict] = []
    used_names: set[str] = set()
    written: List[Path] = []
    total = 0

    try:
        for upload in file_list:
            safe_name = sanitize_filename(upload.filename or "")
            stem, ext = os.path.splitext(safe_name)
            candidate = safe_name
            counter = 1
            while candidate.lower() in used_names:
                candidate = f"{stem}-{counter}{ext}"
                counter += 1
            used_names.add(candidate.lower())

            dest = base / candidate
            written.append(dest)
            size = 0
            with dest.open("wb") as out:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    total += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=f"'{upload.filename}' exceeds {human_size(MAX_FILE_BYTES)}.",
                        )
                    if total > MAX_TOTAL_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=f"Total attachment size exceeds {human_size(MAX_TOTAL_BYTES)}.",
                        )
                    out.write(chunk)

            mime = upload.content_type or ""
            if not mime or mime == "application/octet-stream":
                mime = mimetypes.guess_type(candidate)[0] or "application/octet-stream"

            saved.append({
                "name": os.path.basename(upload.filename or candidate),
                "stored_as": candidate,
                "path": f"attachments/{candidate}",
                "size": size,
                "mime": mime,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            })
    except Exception:
        for path in written:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        raise

    _write_manifest(base, workspace_id, saved)
    return saved


def attachments_block(attachments: List[dict]) -> str:
    """Manifest text appended to the planner objective (no file contents)."""
    if not attachments:
        return ""
    lines = [
        "--- ATTACHED REFERENCE FILES (user-provided) ---",
        "The user attached the following files. They are stored in the shared",
        "workspace at /workspace/attachments/ and are available to every step:",
    ]
    for item in attachments:
        lines.append(
            f"- /workspace/attachments/{item['stored_as']} "
            f"({item['mime']}, {human_size(item['size'])})"
        )
    lines.extend([
        "Use them as reference context where relevant. Do not assume their",
        "contents; instruct agent steps to inspect them.",
        "--- END ATTACHED FILES ---",
    ])
    return "\n".join(lines)


def copy_attachments(source_workspace_id: str, target_workspace_id: str) -> bool:
    """Copy a template's attachments into a new DAG workspace."""
    if not source_workspace_id or not target_workspace_id:
        return False
    source = _attachments_dir(source_workspace_id)
    if not source.is_dir():
        return False
    target = _attachments_dir(target_workspace_id)
    try:
        shutil.copytree(source, target, dirs_exist_ok=True)
        return True
    except Exception:
        return False


def strip_attachments(dag_json: dict) -> None:
    """Remove attachment references (used when files cannot be copied)."""
    if not isinstance(dag_json, dict):
        return
    dag_json.pop("input_attachments", None)
    for node in dag_json.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        config = node.get("config")
        if isinstance(config, dict):
            config.pop("input_attachments", None)
