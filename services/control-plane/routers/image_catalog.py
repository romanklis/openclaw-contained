"""Platform Image Catalog API — git-backed bundles, versions, capabilities."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import tarfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import capability_resolver as resolver
import image_catalog
from config import settings
from database import get_db
from models import (
    AgentImageBinding,
    CapabilityRequest,
    EnvironmentImageVersion,
    ImageAlias,
    ImageBuild,
    ImageCapability,
    ImageDefinition,
    ImageEvent,
    ImageVersion,
    ImageVersionSBOM,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class DefinitionCreate(BaseModel):
    id: str
    name: str
    description: str = ""
    runtime_family: str = "openclaw"
    adapter_type: str = ""
    entrypoint: str = ""
    enabled: bool = True
    tags: List[str] = Field(default_factory=list)


class DefinitionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    runtime_family: Optional[str] = None
    adapter_type: Optional[str] = None
    entrypoint: Optional[str] = None
    enabled: Optional[bool] = None
    tags: Optional[List[str]] = None


class PromoteRequest(BaseModel):
    alias: str = "stable"


class BuildResultRequest(BaseModel):
    image_digest: str = ""
    image_tag: str = ""
    sbom: Optional[Dict[str, Any]] = None
    packages: Optional[List[Any]] = None
    build_id: str = ""
    logs: str = ""
    error: str = ""


class ResolveRequest(BaseModel):
    runtime_family: str = "openclaw"
    packages: List[Any] = Field(default_factory=list)
    adapter_type: str = ""
    base_image: str = ""
    categories: List[str] = Field(default_factory=list)
    modalities: List[str] = Field(default_factory=list)
    binaries: List[str] = Field(default_factory=list)
    definition_id: Optional[str] = None
    create_if_missing: bool = True
    task_id: Optional[str] = None
    node_id: Optional[str] = None
    trigger: str = "capability_request"
    capability_request_id: Optional[int] = None
    build_id: str = ""
    track_build: bool = False


class EditorFile(BaseModel):
    content: str = ""
    encoding: str = "utf-8"  # utf-8|base64


class EditorVersionRequest(BaseModel):
    base_version_id: Optional[str] = None
    version: str = ""
    message: str = ""
    files: Dict[str, EditorFile] = Field(default_factory=dict)
    deleted: List[str] = Field(default_factory=list)


class BuildVersionRequest(BaseModel):
    requested_by: str = "user"


class SpecRequest(BaseModel):
    spec: Dict[str, Any]


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _version_dict(v: ImageVersion, aliases: Optional[Dict[str, str]] = None) -> dict:
    return {
        "id": v.id,
        "definition_id": v.definition_id,
        "version": v.version,
        "commit_message": v.commit_message or "",
        "git_tag": v.git_tag,
        "git_commit": v.git_commit,
        "bundle_hash": v.bundle_hash,
        "dockerfile_hash": v.dockerfile_hash,
        "base_image": v.base_image,
        "base_digest": v.base_digest,
        "runtime_family": v.runtime_family,
        "adapter_type": v.adapter_type,
        "adapter_sha": v.adapter_sha,
        "runner_sha": v.runner_sha,
        "component_manifest": v.component_manifest,
        "entrypoint": v.entrypoint,
        "runtime_description": v.runtime_description,
        "capability_fingerprint": v.capability_fingerprint,
        "capabilities": v.capabilities or [],
        "image_tag": v.image_tag,
        "image_digest": v.image_digest,
        "parent_version_id": v.parent_version_id,
        "sbom_id": v.sbom_id,
        "status": v.status,
        "source_available": v.source_available,
        "created_by": v.created_by,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "aliases": aliases or {},
    }


def _build_dict(b: ImageBuild) -> dict:
    return {
        "id": b.id,
        "version_id": b.version_id,
        "definition_id": b.definition_id,
        "build_id": b.build_id,
        "trigger_type": b.trigger_type,
        "capability_request_id": b.capability_request_id,
        "task_id": b.task_id,
        "node_id": b.node_id,
        "requested_by": b.requested_by,
        "approved_by": b.approved_by,
        "status": b.status,
        "image_tag": b.image_tag,
        "image_digest": b.image_digest,
        "error": b.error,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "finished_at": b.finished_at.isoformat() if b.finished_at else None,
    }


def _definition_dict(d: ImageDefinition, versions: List[ImageVersion], aliases: Dict[str, str]) -> dict:
    latest = None
    if versions:
        latest = versions[0]
    return {
        "id": d.id,
        "name": d.name,
        "description": d.description,
        "runtime_family": d.runtime_family,
        "adapter_type": d.adapter_type,
        "entrypoint": d.entrypoint,
        "enabled": d.enabled,
        "tags": d.tags or [],
        "created_by": d.created_by,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "version_count": len(versions),
        "latest": _version_dict(latest, aliases) if latest else None,
        "aliases": aliases,
    }


async def _aliases_for(db: AsyncSession, definition_id: str) -> Dict[str, str]:
    result = await db.execute(select(ImageAlias).where(ImageAlias.definition_id == definition_id))
    return {a.alias: a.version_id for a in result.scalars().all()}


async def _get_definition_or_404(db: AsyncSession, definition_id: str) -> ImageDefinition:
    definition = await db.get(ImageDefinition, image_catalog._safe_id(definition_id))
    if not definition:
        raise HTTPException(status_code=404, detail=f"Definition '{definition_id}' not found")
    return definition


async def _get_version_or_404(db: AsyncSession, version_id: str) -> ImageVersion:
    version = await db.get(ImageVersion, version_id)
    if not version:
        raise HTTPException(status_code=404, detail=f"Version '{version_id}' not found")
    return version


# ---------------------------------------------------------------------------
# Definitions
# ---------------------------------------------------------------------------

@router.get("/definitions")
async def list_definitions(
    enabled_only: bool = False,
    detected_only: bool = False,
    exclude_detected: bool = False,
    limit: int = 200,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    query = select(ImageDefinition)
    if enabled_only:
        query = query.where(ImageDefinition.enabled.is_(True))
    if detected_only:
        query = query.where(ImageDefinition.id.like("detected-%"))
    if exclude_detected:
        query = query.where(~ImageDefinition.id.like("detected-%"))
    query = query.order_by(ImageDefinition.name).offset(max(0, offset)).limit(max(1, min(limit, 1000)))
    result = await db.execute(query)
    defs = list(result.scalars().all())
    if not defs:
        return []

    ids = [d.id for d in defs]
    versions_result = await db.execute(
        select(ImageVersion)
        .where(ImageVersion.definition_id.in_(ids))
        .order_by(ImageVersion.created_at.desc())
    )
    versions_by: Dict[str, List[ImageVersion]] = {}
    for version in versions_result.scalars().all():
        versions_by.setdefault(version.definition_id, []).append(version)

    aliases_result = await db.execute(
        select(ImageAlias).where(ImageAlias.definition_id.in_(ids))
    )
    aliases_by: Dict[str, Dict[str, str]] = {}
    for alias in aliases_result.scalars().all():
        aliases_by.setdefault(alias.definition_id, {})[alias.alias] = alias.version_id

    return [
        _definition_dict(d, versions_by.get(d.id, []), aliases_by.get(d.id, {}))
        for d in defs
    ]


@router.post("/definitions", status_code=201)
async def create_definition(data: DefinitionCreate, db: AsyncSession = Depends(get_db)):
    definition = await image_catalog.create_definition(
        db,
        data.id,
        name=data.name,
        description=data.description,
        runtime_family=data.runtime_family,
        adapter_type=data.adapter_type,
        entrypoint=data.entrypoint,
        enabled=data.enabled,
        tags=data.tags,
    )
    await db.commit()
    return _definition_dict(definition, [], {})


@router.patch("/definitions/{definition_id}")
async def update_definition(
    definition_id: str,
    data: DefinitionUpdate,
    db: AsyncSession = Depends(get_db),
):
    definition = await _get_definition_or_404(db, definition_id)
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(definition, field, value)
    await db.commit()
    versions = await db.execute(
        select(ImageVersion)
        .where(ImageVersion.definition_id == definition.id)
        .order_by(ImageVersion.created_at.desc())
    )
    return _definition_dict(definition, list(versions.scalars().all()), await _aliases_for(db, definition.id))


@router.get("/definitions/{definition_id}")
async def get_definition(definition_id: str, db: AsyncSession = Depends(get_db)):
    definition = await _get_definition_or_404(db, definition_id)
    versions = await db.execute(
        select(ImageVersion)
        .where(ImageVersion.definition_id == definition.id)
        .order_by(ImageVersion.created_at.desc())
    )
    return _definition_dict(definition, list(versions.scalars().all()), await _aliases_for(db, definition.id))


# ---------------------------------------------------------------------------
# Bundle upload / version creation
# ---------------------------------------------------------------------------

def _extract_tar_bytes(data: bytes) -> Dict[str, bytes]:
    files: Dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                name = member.name.replace("\\", "/")
                if name.startswith("/") or ".." in Path(name).parts or name.startswith(".git/"):
                    raise HTTPException(status_code=400, detail=f"Unsafe path in bundle: {name}")
                handle = tar.extractfile(member)
                if handle:
                    files[name] = handle.read()
    except tarfile.TarError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid tar archive: {exc}")
    # Strip a single common top-level directory (e.g. my-image/Dockerfile).
    if files and all("/" in name for name in files):
        prefixes = {name.split("/", 1)[0] for name in files}
        if len(prefixes) == 1:
            prefix = prefixes.pop()
            files = {name.split("/", 1)[1]: data for name, data in files.items()}
    return files


async def _collect_bundle(
    *,
    bundle: Optional[UploadFile],
    files: List[UploadFile],
    spec: Optional[str],
) -> Dict[str, bytes]:
    collected: Dict[str, bytes] = {}
    if bundle is not None and bundle.filename:
        collected.update(_extract_tar_bytes(await bundle.read()))
    for upload in files or []:
        name = os.path.basename(upload.filename or "")
        if name:
            collected[name] = await upload.read()
    if spec:
        try:
            parsed = json.loads(spec)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail=f"spec must be valid JSON: {exc}")
        spec_obj = resolver.make_spec(
            parsed.get("runtime_family", "openclaw"),
            parsed.get("packages", []),
            adapter_type=parsed.get("adapter_type", ""),
            base_image=parsed.get("base_image", ""),
            categories=parsed.get("categories"),
            modalities=parsed.get("modalities"),
            binaries=parsed.get("binaries"),
        )
        collected["Dockerfile"] = resolver.generated_dockerfile(spec_obj, spec_obj.get("base_image", "")).encode()
        collected["image.yaml"] = json.dumps({
            "runtime_family": spec_obj["runtime_family"],
            "adapter_type": spec_obj["adapter_type"],
            "base_image": spec_obj["base_image"],
            "generated": True,
        }, indent=2).encode()
    return collected


@router.post("/definitions/{definition_id}/versions", status_code=201)
async def create_version(
    definition_id: str,
    bundle: Optional[UploadFile] = File(None),
    files: List[UploadFile] = File(default=[]),
    spec: Optional[str] = Form(None),
    version: str = Form(""),
    message: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    definition = await _get_definition_or_404(db, definition_id)
    collected = await _collect_bundle(bundle=bundle, files=files, spec=spec)
    if not collected:
        raise HTTPException(status_code=422, detail="Upload a bundle (tar.gz), individual files, or a capability spec")
    try:
        image_version, lint = await image_catalog.create_version_from_bundle(
            db,
            definition,
            collected,
            version=version,
            message=message or f"Add {definition.id} version",
            status="draft",
            created_by="api",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    await db.refresh(image_version)
    return {"version": _version_dict(image_version), "lint": lint}


@router.post("/definitions/{definition_id}/lint")
async def lint_bundle(
    definition_id: str,
    bundle: Optional[UploadFile] = File(None),
    files: List[UploadFile] = File(default=[]),
    spec: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    await _get_definition_or_404(db, definition_id)
    collected = await _collect_bundle(bundle=bundle, files=files, spec=spec)
    return image_catalog.bundle_lint(collected)


@router.post("/definitions/{definition_id}/versions/editor", status_code=201)
async def create_version_from_editor(
    definition_id: str,
    data: EditorVersionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new draft from a version clone + browser text edits/uploads."""
    definition = await _get_definition_or_404(db, definition_id)
    files: Dict[str, bytes] = {}
    base_version: Optional[ImageVersion] = None

    if data.base_version_id:
        base_version = await _get_version_or_404(db, data.base_version_id)
        if base_version.definition_id != definition.id:
            raise HTTPException(status_code=400, detail="Base version belongs to another definition")
        ref = base_version.git_tag or base_version.git_commit or "HEAD"
        try:
            for entry in image_catalog.read_tree_sync(definition.id, ref):
                path = entry["path"]
                if path == "bundle.manifest.json":
                    continue
                files[path] = image_catalog.read_file_sync(definition.id, ref, path)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Could not read base version: {exc}")

    for path in data.deleted:
        files.pop(str(path).replace("\\", "/").lstrip("/"), None)

    for path, file in data.files.items():
        safe = str(path).replace("\\", "/").lstrip("/")
        if not safe or ".." in Path(safe).parts or safe.startswith(".git/"):
            raise HTTPException(status_code=400, detail=f"Unsafe path: {path}")
        try:
            if file.encoding == "base64":
                files[safe] = base64.b64decode(file.content)
            else:
                files[safe] = file.content.encode("utf-8")
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Could not decode {path}: {exc}")

    if "Dockerfile" not in files:
        raise HTTPException(status_code=422, detail="Dockerfile is required")

    version = data.version.strip() or await image_catalog.next_version_for_definition(db, definition.id)
    message = data.message.strip() or "Update image source"
    try:
        image_version, lint = await image_catalog.create_version_from_bundle(
            db,
            definition,
            files,
            version=version,
            message=message,
            status="draft",
            created_by="editor",
            parent_version_id=base_version.id if base_version else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    await db.commit()
    await db.refresh(image_version)
    return {"version": _version_dict(image_version), "lint": lint}


@router.get("/definitions/{definition_id}/versions")
async def list_versions(
    definition_id: str,
    limit: int = 200,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    definition = await _get_definition_or_404(db, definition_id)
    result = await db.execute(
        select(ImageVersion)
        .where(ImageVersion.definition_id == definition.id)
        .order_by(ImageVersion.created_at.desc())
        .offset(max(0, offset))
        .limit(max(1, min(limit, 1000)))
    )
    aliases = await _aliases_for(db, definition.id)
    version_list = list(result.scalars().all())
    latest_builds: Dict[str, ImageBuild] = {}
    if version_list:
        builds_result = await db.execute(
            select(ImageBuild)
            .where(ImageBuild.version_id.in_([v.id for v in version_list]))
            .order_by(ImageBuild.created_at.desc())
        )
        for build in builds_result.scalars().all():
            latest_builds.setdefault(build.version_id, build)
    return [
        {
            **_version_dict(v, aliases),
            "latest_build": _build_dict(latest_builds[v.id]) if v.id in latest_builds else None,
        }
        for v in version_list
    ]


@router.get("/versions/{version_id}")
async def get_version(version_id: str, db: AsyncSession = Depends(get_db)):
    version = await _get_version_or_404(db, version_id)
    alias_map = await _aliases_for(db, version.definition_id)
    build_result = await db.execute(
        select(ImageBuild)
        .where(ImageBuild.version_id == version.id)
        .order_by(ImageBuild.created_at.desc())
        .limit(1)
    )
    latest_build = build_result.scalars().first()
    parent = (
        await db.get(ImageVersion, version.parent_version_id)
        if version.parent_version_id
        else None
    )
    return {
        **_version_dict(version, alias_map),
        "latest_build": _build_dict(latest_build) if latest_build else None,
        "capability_delta": image_catalog.capability_delta(version, parent),
    }


@router.get("/versions/{version_id}/tree")
async def get_version_tree(version_id: str, ref: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    version = await _get_version_or_404(db, version_id)
    if not version.source_available:
        raise HTTPException(status_code=409, detail="Source bundle is not available for this version")
    target = ref or version.git_tag or version.git_commit or "HEAD"
    try:
        return {"ref": target, "files": image_catalog.read_tree_sync(version.definition_id, target)}
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Could not read bundle at {target}: {exc}")


@router.get("/versions/{version_id}/source")
async def get_version_source(version_id: str, ref: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    version = await _get_version_or_404(db, version_id)
    if not version.source_available:
        raise HTTPException(status_code=409, detail="Source bundle is not available for this version")
    target = ref or version.git_tag or version.git_commit or "HEAD"
    try:
        data = image_catalog.source_tarball_sync(version.definition_id, target)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Could not archive bundle: {exc}")
    return Response(
        content=data,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{version.definition_id}-{target}.tar.gz"'},
    )


@router.get("/versions/{version_id}/file")
async def get_version_file(
    version_id: str,
    path: str = Query(...),
    ref: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    version = await _get_version_or_404(db, version_id)
    if not version.source_available:
        raise HTTPException(status_code=409, detail="Source bundle is not available for this version")
    target = ref or version.git_tag or version.git_commit or "HEAD"
    try:
        data = image_catalog.read_file_sync(version.definition_id, target, path)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Could not read {path}: {exc}")
    return Response(content=data, media_type="text/plain; charset=utf-8")


@router.get("/versions/{version_id}/diff/{other_id}")
async def diff_versions(version_id: str, other_id: str, db: AsyncSession = Depends(get_db)):
    left = await _get_version_or_404(db, version_id)
    right = await _get_version_or_404(db, other_id)
    if left.definition_id != right.definition_id:
        raise HTTPException(status_code=400, detail="Versions belong to different definitions")
    try:
        diff = image_catalog.diff_sync(
            left.definition_id,
            left.git_tag or left.git_commit or "HEAD",
            right.git_tag or right.git_commit or "HEAD",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"from": left.id, "to": right.id, "diff": diff}


@router.post("/versions/{version_id}/promote")
async def promote_version(version_id: str, data: PromoteRequest, db: AsyncSession = Depends(get_db)):
    version = await _get_version_or_404(db, version_id)
    await image_catalog._upsert_alias(db, version.definition_id, data.alias, version.id)
    db.add(ImageEvent(
        definition_id=version.definition_id,
        version_id=version.id,
        event_type="promoted",
        message=f"Alias '{data.alias}' -> {version.version}",
        data={"alias": data.alias},
    ))
    await db.commit()
    return {"ok": True, "alias": data.alias, "version_id": version.id}


@router.post("/versions/{version_id}/rollback")
async def rollback_version(version_id: str, data: PromoteRequest, db: AsyncSession = Depends(get_db)):
    return await promote_version(version_id, data, db)


@router.post("/versions/{version_id}/scan-capabilities")
async def scan_capabilities(version_id: str, db: AsyncSession = Depends(get_db)):
    """Generate an SBOM for a version's image and index its capabilities."""
    version = await _get_version_or_404(db, version_id)
    if not version.image_tag:
        raise HTTPException(status_code=409, detail="Version has no image tag to scan")
    image_ref = version.image_tag
    if not image_ref.startswith(("registry:5000/", "localhost:5000/")):
        image_ref = f"registry:5000/{image_ref}"
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.post(
                f"{settings.IMAGE_BUILDER_URL}/scan/sbom-raw",
                json={"image_ref": image_ref},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SBOM scan failed: {exc}")

    result = await image_catalog.ingest_build_result(
        db,
        version.id,
        image_digest=version.image_digest or "",
        image_tag=version.image_tag,
        sbom_document=data.get("sbom"),
    )
    await db.commit()
    return result


@router.post("/versions/{version_id}/build-result")
async def build_result(version_id: str, data: BuildResultRequest, db: AsyncSession = Depends(get_db)):
    await _get_version_or_404(db, version_id)
    try:
        result = await image_catalog.ingest_build_result(
            db,
            version_id,
            image_digest=data.image_digest,
            image_tag=data.image_tag,
            sbom_document=data.sbom,
            packages=data.packages,
            build_id=data.build_id,
            logs=data.logs,
            error=data.error,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    await db.commit()
    return result


# ---------------------------------------------------------------------------
# Import / reconcile / capabilities / resolution
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Builds / history / git
# ---------------------------------------------------------------------------

async def _resolve_build(db: AsyncSession, build_ref: str) -> ImageBuild:
    if build_ref.isdigit():
        build = await db.get(ImageBuild, int(build_ref))
    else:
        result = await db.execute(
            select(ImageBuild).where(ImageBuild.build_id == build_ref).limit(1)
        )
        build = result.scalars().first()
    if not build:
        raise HTTPException(status_code=404, detail=f"Build '{build_ref}' not found")
    return build


async def _refresh_build_from_builder(db: AsyncSession, build: ImageBuild) -> ImageBuild:
    if build.status not in ("pending", "building") or not build.build_id:
        return build
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{settings.IMAGE_BUILDER_URL}/builds/{build.build_id}")
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return build

    builder_status = data.get("status")
    if builder_status == "success":
        await image_catalog.update_build_record(
            db,
            build,
            status="succeeded",
            logs=data.get("logs") or build.logs,
            image_tag=data.get("image_tag") or build.image_tag,
            image_digest=data.get("digest") or build.image_digest,
        )
        if build.version_id:
            version = await db.get(ImageVersion, build.version_id)
            if version:
                version.status = "ready"
                version.image_tag = build.image_tag or version.image_tag
                version.image_digest = build.image_digest or version.image_digest
    elif builder_status == "failed":
        await image_catalog.update_build_record(
            db,
            build,
            status="failed",
            logs=data.get("logs") or build.logs,
            error=data.get("error") or build.error,
        )
        if build.version_id:
            version = await db.get(ImageVersion, build.version_id)
            if version:
                version.status = "failed"
    elif builder_status in ("pending", "building"):
        await image_catalog.update_build_record(
            db,
            build,
            status=builder_status,
            logs=data.get("logs") or build.logs,
        )
    await db.commit()
    return build


@router.post("/versions/{version_id}/build", status_code=201)
async def build_version(
    version_id: str,
    data: BuildVersionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Build a committed version explicitly (manual build)."""
    version = await _get_version_or_404(db, version_id)
    if not version.source_available:
        raise HTTPException(status_code=409, detail="Source bundle is not available for this version")
    definition = await _get_definition_or_404(db, version.definition_id)
    registry_tag = f"{settings.REGISTRY_URL}/{definition.id}:{version.version}"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{settings.IMAGE_BUILDER_URL}/build-catalog",
                json={
                    "version_id": version.id,
                    "definition_id": definition.id,
                    "version": version.version,
                    "registry_tag": registry_tag,
                },
            )
            resp.raise_for_status()
            result = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not start build: {exc}")

    build = await image_catalog.create_build_record(
        db,
        version=version,
        definition_id=definition.id,
        build_id=result.get("build_id", ""),
        trigger_type="manual",
        requested_by=data.requested_by,
        status="pending",
        image_tag=registry_tag,
    )
    version.status = "building"
    version.image_tag = registry_tag
    await db.commit()
    return _build_dict(build)


@router.get("/builds")
async def list_builds(
    definition_id: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    trigger_type: Optional[str] = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    query = select(ImageBuild)
    if definition_id:
        query = query.where(ImageBuild.definition_id == definition_id)
    if status_filter:
        query = query.where(ImageBuild.status == status_filter)
    if trigger_type:
        query = query.where(ImageBuild.trigger_type == trigger_type)
    query = query.order_by(ImageBuild.created_at.desc()).limit(max(1, min(limit, 500)))
    result = await db.execute(query)
    return [_build_dict(b) for b in result.scalars().all()]


@router.get("/builds/{build_ref}")
async def get_build(build_ref: str, db: AsyncSession = Depends(get_db)):
    build = await _resolve_build(db, build_ref)
    build = await _refresh_build_from_builder(db, build)
    version = await db.get(ImageVersion, build.version_id) if build.version_id else None
    return {
        **_build_dict(build),
        "logs": build.logs or "",
        "version": _version_dict(version) if version else None,
    }


@router.post("/builds/{build_ref}/retry")
async def retry_build(build_ref: str, db: AsyncSession = Depends(get_db)):
    build = await _resolve_build(db, build_ref)
    if build.status == "succeeded":
        raise HTTPException(status_code=409, detail="Build already succeeded")
    build = await image_catalog.retry_catalog_build(db, build, requested_by="user")
    return _build_dict(build)


@router.get("/builds/{build_ref}/logs")
async def get_build_logs(build_ref: str, db: AsyncSession = Depends(get_db)):
    build = await _resolve_build(db, build_ref)
    build = await _refresh_build_from_builder(db, build)
    return {
        "id": build.id,
        "build_id": build.build_id,
        "status": build.status,
        "logs": build.logs or "",
        "error": build.error or "",
    }


@router.get("/history")
async def image_history(
    definition_id: Optional[str] = None,
    trigger_type: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    exclude_detected: bool = True,
    limit: int = 200,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Global version/commit history across definitions."""
    query = select(ImageVersion, ImageDefinition).join(
        ImageDefinition, ImageVersion.definition_id == ImageDefinition.id
    )
    if definition_id:
        query = query.where(ImageVersion.definition_id == definition_id)
    if status_filter:
        query = query.where(ImageVersion.status == status_filter)
    if exclude_detected:
        query = query.where(ImageVersion.status != "detected")
    if trigger_type:
        version_ids = select(ImageBuild.version_id).where(
            ImageBuild.trigger_type == trigger_type,
            ImageBuild.version_id.isnot(None),
        )
        query = query.where(ImageVersion.id.in_(version_ids))
    query = (
        query.order_by(ImageVersion.created_at.desc())
        .offset(max(0, offset))
        .limit(max(1, min(limit, 500)))
    )
    rows = (await db.execute(query)).all()
    version_ids = [v.id for v, _ in rows]
    if not version_ids:
        return []

    builds_result = await db.execute(
        select(ImageBuild)
        .where(ImageBuild.version_id.in_(version_ids))
        .order_by(ImageBuild.created_at.desc())
    )
    latest_builds: Dict[str, ImageBuild] = {}
    for build in builds_result.scalars().all():
        latest_builds.setdefault(build.version_id, build)

    parent_ids = {v.parent_version_id for v, _ in rows if v.parent_version_id}
    parents: Dict[str, ImageVersion] = {}
    if parent_ids:
        parent_result = await db.execute(
            select(ImageVersion).where(ImageVersion.id.in_(parent_ids))
        )
        parents = {p.id: p for p in parent_result.scalars().all()}

    return [
        {
            "version": _version_dict(version),
            "definition": {
                "id": definition.id,
                "name": definition.name,
                "runtime_family": definition.runtime_family,
                "enabled": definition.enabled,
            },
            "build": _build_dict(latest_builds[version.id])
            if version.id in latest_builds
            else None,
            "capability_delta": image_catalog.capability_delta(
                version, parents.get(version.parent_version_id)
            ),
        }
        for version, definition in rows
    ]


@router.get("/definitions/{definition_id}/git-log")
async def get_git_log(
    definition_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    definition = await _get_definition_or_404(db, definition_id)
    commits = await asyncio.to_thread(image_catalog.git_log_sync, definition.id, limit)
    return {"definition_id": definition.id, "commits": commits}


@router.get("/versions/{version_id}/events")
async def get_version_events(
    version_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    version = await _get_version_or_404(db, version_id)
    result = await db.execute(
        select(ImageEvent)
        .where(ImageEvent.version_id == version.id)
        .order_by(ImageEvent.created_at.desc())
        .limit(max(1, min(limit, 500)))
    )
    return [
        {
            "id": event.id,
            "event_type": event.event_type,
            "message": event.message,
            "data": event.data,
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }
        for event in result.scalars().all()
    ]


@router.post("/import")
async def import_images(db: AsyncSession = Depends(get_db)):
    return await image_catalog.import_from_agent_images(db)


@router.post("/seed")
async def seed_predefined_images(db: AsyncSession = Depends(get_db)):
    """Seed catalog bundles + planner metadata from the reference agent-images/ folder.

    Imports each image directory as a full source bundle (Dockerfile, Python
    scripts, adapters/runners, requirements, tests) and upserts the planner's
    AgentImage rows from agent_profiles.yaml. Idempotent; does not build images.
    """
    from routers.agent_images import sync_agent_images_from_yaml

    agent_images: Dict[str, Any] = {}
    try:
        sync_result = await sync_agent_images_from_yaml(db)
        agent_images = (
            sync_result.model_dump()
            if hasattr(sync_result, "model_dump")
            else dict(sync_result)
        )
    except HTTPException as exc:
        await db.rollback()
        agent_images = {"error": exc.detail}
    except Exception as exc:
        await db.rollback()
        agent_images = {"error": str(exc)}

    catalog = await image_catalog.import_from_agent_images(db)
    return {"catalog": catalog, "agent_images": agent_images}


@router.get("/bootstrap-status")
async def bootstrap_status(db: AsyncSession = Depends(get_db)):
    return await image_catalog.catalog_bootstrap_status(db)


@router.post("/sync-agent-images")
async def sync_agent_images(force: bool = False, db: AsyncSession = Depends(get_db)):
    return await image_catalog.sync_built_agent_images(db, force=force)


@router.post("/reconcile")
async def reconcile(db: AsyncSession = Depends(get_db)):
    return await image_catalog.reconcile_registry(db)


@router.get("/capabilities")
async def search_capabilities(
    query: str = Query("", description="Package/capability substring"),
    manager: Optional[str] = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(ImageCapability, ImageVersion, ImageDefinition)
        .join(ImageVersion, ImageCapability.version_id == ImageVersion.id)
        .join(ImageDefinition, ImageVersion.definition_id == ImageDefinition.id)
    )
    if query:
        stmt = stmt.where(ImageCapability.name.ilike(f"%{query}%"))
    if manager:
        stmt = stmt.where(ImageCapability.manager == manager)
    stmt = stmt.order_by(ImageCapability.name).limit(max(1, min(limit, 500)))
    result = await db.execute(stmt)
    return [
        {
            "manager": cap.manager,
            "name": cap.name,
            "version": cap.version,
            "definition_id": definition.id,
            "definition_name": definition.name,
            "version_id": version.id,
            "image_version": version.version,
            "image_tag": version.image_tag,
            "status": version.status,
        }
        for cap, version, definition in result.all()
    ]


@router.post("/resolve")
async def resolve(
    data: ResolveRequest,
    db: AsyncSession = Depends(get_db),
):
    spec = resolver.make_spec(
        data.runtime_family,
        data.packages,
        adapter_type=data.adapter_type,
        base_image=data.base_image,
        categories=data.categories,
        modalities=data.modalities,
        binaries=data.binaries,
    )
    capability_request = None
    if data.capability_request_id:
        capability_request = await db.get(CapabilityRequest, data.capability_request_id)
    elif data.task_id and (data.track_build or data.trigger == "capability_request"):
        capability_request = await image_catalog.latest_approved_capability_request(
            db, data.task_id
        )

    if data.create_if_missing:
        result = await image_catalog.ensure_capability_version(
            db, spec, definition_id=data.definition_id, base_image=data.base_image
        )
    else:
        result = await image_catalog.resolve_spec(db, spec)

    if data.track_build and result.get("version_id"):
        version = await db.get(ImageVersion, result["version_id"])
        if version:
            build = await image_catalog.create_build_record(
                db,
                version=version,
                definition_id=result.get("definition_id", ""),
                build_id=data.build_id,
                trigger_type=data.trigger or "capability_request",
                capability_request=capability_request,
                task_id=data.task_id or "",
                node_id=data.node_id or "",
                requested_by="agent",
                status="succeeded" if result.get("reused") else "pending",
                image_tag=result.get("image_tag", "") if result.get("reused") else "",
                logs="Reused existing image" if result.get("reused") else "",
            )
            result["build_record_id"] = build.id

    await db.commit()
    return result


@router.post("/resolve-spec")
async def resolve_spec_endpoint(data: SpecRequest, db: AsyncSession = Depends(get_db)):
    spec = resolver.make_spec(
        data.spec.get("runtime_family", "openclaw"),
        data.spec.get("packages", []),
        adapter_type=data.spec.get("adapter_type", ""),
        base_image=data.spec.get("base_image", ""),
        categories=data.spec.get("categories"),
        modalities=data.spec.get("modalities"),
        binaries=data.spec.get("binaries"),
    )
    result = await image_catalog.resolve_spec(db, spec)
    await db.commit()
    return result


@router.delete("/definitions/{definition_id}", status_code=204)
async def delete_definition(definition_id: str, db: AsyncSession = Depends(get_db)):
    definition = await _get_definition_or_404(db, definition_id)
    version_rows = await db.execute(
        select(ImageVersion.id).where(ImageVersion.definition_id == definition.id)
    )
    version_ids = [row[0] for row in version_rows.all()]

    # Aliases reference versions, so remove them first.
    await db.execute(delete(ImageAlias).where(ImageAlias.definition_id == definition.id))
    if version_ids:
        await db.execute(
            update(ImageVersion)
            .where(ImageVersion.definition_id == definition.id)
            .values(parent_version_id=None)
        )
        await db.execute(delete(ImageCapability).where(ImageCapability.version_id.in_(version_ids)))
        await db.execute(delete(ImageVersionSBOM).where(ImageVersionSBOM.version_id.in_(version_ids)))
        await db.execute(delete(AgentImageBinding).where(AgentImageBinding.image_version_id.in_(version_ids)))
        await db.execute(delete(EnvironmentImageVersion).where(EnvironmentImageVersion.image_version_id.in_(version_ids)))
        await db.execute(delete(ImageVersion).where(ImageVersion.id.in_(version_ids)))
    await db.execute(delete(ImageEvent).where(ImageEvent.definition_id == definition.id))
    await db.delete(definition)
    await db.commit()

    import shutil
    shutil.rmtree(image_catalog._repo_dir(definition.id), ignore_errors=True)
    return Response(status_code=204)


@router.get("/definitions/{definition_id}/events")
async def list_events(definition_id: str, limit: int = 100, db: AsyncSession = Depends(get_db)):
    definition = await _get_definition_or_404(db, definition_id)
    result = await db.execute(
        select(ImageEvent)
        .where(ImageEvent.definition_id == definition.id)
        .order_by(ImageEvent.created_at.desc())
        .limit(max(1, min(limit, 500)))
    )
    return [
        {
            "id": e.id,
            "version_id": e.version_id,
            "event_type": e.event_type,
            "message": e.message,
            "data": e.data,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in result.scalars().all()
    ]


@router.get("/definitions/{definition_id}/drift")
async def check_drift(definition_id: str, db: AsyncSession = Depends(get_db)):
    """Compare vendored adapter/runner files against canonical shared sources."""
    definition = await _get_definition_or_404(db, definition_id)
    versions = await db.execute(
        select(ImageVersion)
        .where(ImageVersion.definition_id == definition.id)
        .order_by(ImageVersion.created_at.desc())
        .limit(1)
    )
    version = versions.scalars().first()
    if not version:
        return {"definition_id": definition.id, "drift": [], "note": "No versions yet"}
    shared_dir = Path(settings.SHARED_COMPONENTS_DIR)
    drift = []
    for path, digest in (version.component_manifest or {}).items():
        basename = Path(path).name
        candidates = list(shared_dir.glob(basename)) if shared_dir.exists() else []
        if not candidates:
            continue
        canonical = candidates[0].read_bytes()
        canonical_digest = hashlib.sha256(canonical).hexdigest()
        if canonical_digest != digest:
            drift.append({
                "file": path,
                "bundled_sha": digest,
                "canonical_path": str(candidates[0]),
                "canonical_sha": canonical_digest,
            })
    return {
        "definition_id": definition.id,
        "version_id": version.id,
        "shared_dir": str(shared_dir),
        "drift": drift,
        "ok": not drift,
    }
