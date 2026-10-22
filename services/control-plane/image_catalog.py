"""Platform image catalog: git-backed source bundles + capability indexing.

An image is a *bundle* (Dockerfile + adapter/runner/runtime/policy/config +
requirements + tests), not just a Dockerfile. Each ImageDefinition owns a bare
git repo under ``settings.IMAGE_CATALOG_DIR``; each ImageVersion is an immutable
commit/tag of that bundle built into a registry image.

Capabilities are extracted from SBOMs and stored as normalized rows, so the
resolver can reuse an existing image whose capabilities are a superset of a
request instead of rebuilding.
"""
from __future__ import annotations

import asyncio
import glob as globmod
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx
import yaml
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import capability_resolver as resolver
from config import settings
from models import (
    AgentImage,
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
    NodeEnvironment,
    RequestStatus,
)

logger = logging.getLogger(__name__)

CATALOG_DIR = Path(settings.IMAGE_CATALOG_DIR)
AGENT_IMAGES_DIR = Path(settings.AGENT_IMAGES_DIR)
SKIP_DIRS = {"__pycache__", ".git", "node_modules", "deployments", "_base_rebuild"}


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _registry_ref(tag: str) -> str:
    registry = (settings.REGISTRY_URL or "registry:5000").rstrip("/")
    if not tag:
        return tag
    if tag.startswith(("registry:5000/", "localhost:5000/", f"{registry}/")):
        return tag
    return f"{registry}/{tag}"


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", (value or "").strip()).strip(".-")
    if not safe:
        raise ValueError("Invalid definition id")
    return safe


def _repo_dir(definition_id: str) -> Path:
    return CATALOG_DIR / f"{_safe_id(definition_id)}.git"


def _git(args: List[str], cwd: Optional[Path] = None, binary: bool = False) -> Any:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        check=True,
    )
    if binary:
        return result.stdout
    return result.stdout.decode("utf-8", errors="replace")


def _repo_has_commits(repo: Path) -> bool:
    result = subprocess.run(
        ["git", "--git-dir", str(repo), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
    )
    return result.returncode == 0


def ensure_repo(definition_id: str) -> Path:
    repo = _repo_dir(definition_id)
    repo.parent.mkdir(parents=True, exist_ok=True)
    if not repo.exists():
        subprocess.run(["git", "init", "--bare", "-q", str(repo)], check=True, capture_output=True)
        subprocess.run(
            ["git", "--git-dir", str(repo), "symbolic-ref", "HEAD", "refs/heads/main"],
            check=True,
            capture_output=True,
        )
    return repo


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_bundle_hash(files: Dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for path in sorted(files):
        if path == "bundle.manifest.json":
            continue
        data = files[path]
        h.update(path.encode())
        h.update(b"\0")
        h.update(_sha256_bytes(data).encode())
        h.update(b"\0")
        h.update(str(len(data)).encode())
        h.update(b"\n")
    return h.hexdigest()


def _component_manifest(files: Dict[str, bytes]) -> Dict[str, str]:
    return {path: _sha256_bytes(data) for path, data in sorted(files.items())}


def _find_component(files: Dict[str, bytes], patterns: Iterable[str]) -> Tuple[str, str]:
    for pattern in patterns:
        for path in sorted(files):
            if globmod.fnmatch.fnmatch(Path(path).name, pattern):
                return path, _sha256_bytes(files[path])
    return "", ""


# ---------------------------------------------------------------------------
# Dockerfile parsing / bundle lint
# ---------------------------------------------------------------------------

def parse_dockerfile(text: str) -> dict:
    joined = re.sub(r"\\\s*\n", " ", text or "")
    info = {
        "from": "",
        "entrypoint": "",
        "cmd": "",
        "labels": {},
        "copy_sources": [],
        "errors": [],
        "warnings": [],
    }
    for raw in joined.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        upper = line.upper()
        if upper.startswith("FROM "):
            info["from"] = line.split(None, 1)[1].split()[0]
        elif upper.startswith("LABEL "):
            for match in re.finditer(r'([A-Za-z0-9_.-]+)\s*=\s*("([^"]*)"|\'([^\']*)\'|\S+)', line[6:]):
                info["labels"][match.group(1)] = match.group(3) if match.group(3) is not None else (match.group(4) if match.group(4) is not None else match.group(2))
        elif upper.startswith("ENTRYPOINT "):
            body = line[len("ENTRYPOINT "):].strip()
            info["entrypoint"] = _parse_exec_form(body)
        elif upper.startswith("CMD "):
            body = line[len("CMD "):].strip()
            info["cmd"] = _parse_exec_form(body)
        elif upper.startswith("COPY ") or upper.startswith("ADD "):
            rest = line.split(None, 1)[1]
            if "--from=" in rest:
                continue
            sources = _parse_copy_sources(rest)
            info["copy_sources"].extend(sources)
    return info


def _parse_exec_form(body: str) -> str:
    body = body.strip()
    if body.startswith("["):
        try:
            parts = json.loads(body)
            if isinstance(parts, list):
                return " ".join(str(p) for p in parts)
        except json.JSONDecodeError:
            pass
    return body


def _parse_copy_sources(rest: str) -> List[str]:
    rest = rest.strip()
    if rest.startswith("["):
        try:
            parts = json.loads(rest)
            return [str(p) for p in parts[:-1]] if len(parts) > 1 else []
        except json.JSONDecodeError:
            pass
    try:
        tokens = shlex.split(rest)
    except ValueError:
        tokens = rest.split()
    tokens = [t for t in tokens if not t.startswith("--")]
    return tokens[:-1] if len(tokens) > 1 else []


def _source_exists(source: str, files: Dict[str, bytes]) -> bool:
    if not source or source in (".", "./"):
        return True
    if source.startswith("$") or source.startswith("http://") or source.startswith("https://"):
        return True
    clean = source.lstrip("./")
    if "*" in clean or "?" in clean:
        return any(globmod.fnmatch.fnmatch(path, clean) for path in files)
    return clean in files or any(path.startswith(clean.rstrip("/") + "/") for path in files)


def bundle_lint(files: Dict[str, bytes]) -> dict:
    """Validate a complete image bundle before commit/build."""
    files = {str(p).replace("\\", "/").lstrip("/"): data for p, data in files.items()}
    errors: List[str] = []
    warnings: List[str] = []
    if "Dockerfile" not in files:
        errors.append("Dockerfile is required")
    if any(path.startswith(".git/") or "/.git/" in path for path in files):
        errors.append("Bundle must not contain .git directories")
    if any(".." in Path(path).parts for path in files):
        errors.append("Bundle paths must not contain '..'")

    dockerfile = files.get("Dockerfile", b"").decode("utf-8", errors="replace")
    parsed = parse_dockerfile(dockerfile)
    for source in parsed["copy_sources"]:
        if not _source_exists(source, files):
            errors.append(f"Dockerfile COPY/ADD source is missing: {source}")

    image_yaml = {}
    if "image.yaml" in files:
        try:
            image_yaml = yaml.safe_load(files["image.yaml"].decode()) or {}
        except Exception as exc:
            errors.append(f"image.yaml is invalid YAML: {exc}")

    entrypoint = parsed["entrypoint"] or image_yaml.get("entrypoint") or ""
    if entrypoint:
        # Check a referenced file basename exists in the bundle.
        for token in shlex.split(entrypoint):
            if "/" in token:
                base = Path(token).name
                if base and not any(Path(path).name == base for path in files):
                    warnings.append(f"ENTRYPOINT references a file not found in the bundle: {token}")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "from": parsed["from"] or image_yaml.get("base_image", ""),
        "entrypoint": entrypoint,
        "labels": parsed["labels"],
        "image_yaml": image_yaml,
        "dockerfile_hash": _sha256_bytes(files.get("Dockerfile", b"")),
    }


def _infer_runtime_family(definition_id: str, lint: dict) -> str:
    labels = lint.get("labels") or {}
    labeled = labels.get("org.openclaw.image.type") or labels.get("org.openclaw.runtime_family")
    if labeled:
        return str(labeled).lower()
    yaml_runtime = (lint.get("image_yaml") or {}).get("runtime_family")
    if yaml_runtime:
        return str(yaml_runtime).lower()
    from_image = (lint.get("from") or "").lower()
    for fam in ("picoclaw", "nanobot", "zeroclaw", "octaveclaw", "browser", "openclaw"):
        if fam in from_image or fam in definition_id.lower():
            return fam
    return "openclaw"


# ---------------------------------------------------------------------------
# Commit / read bundles
# ---------------------------------------------------------------------------

def commit_bundle_sync(
    definition_id: str,
    files: Dict[str, bytes],
    *,
    version: str,
    message: str,
    author: str = "TaskForge Platform",
) -> dict:
    repo = ensure_repo(definition_id)
    files = {str(p).replace("\\", "/").lstrip("/"): data for p, data in files.items()}
    for path in files:
        if ".." in Path(path).parts or path.startswith(".git/"):
            raise ValueError(f"Unsafe path in bundle: {path}")

    bundle_hash = compute_bundle_hash(files)
    components = _component_manifest(files)
    dockerfile_hash = _sha256_bytes(files.get("Dockerfile", b""))
    manifest = {
        "definition_id": _safe_id(definition_id),
        "version": version,
        "bundle_hash": bundle_hash,
        "dockerfile_hash": dockerfile_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "components": components,
    }
    files_with_manifest = dict(files)
    files_with_manifest["bundle.manifest.json"] = json.dumps(manifest, indent=2).encode()

    with tempfile.TemporaryDirectory(prefix="imgcat-") as td:
        work = Path(td)
        if _repo_has_commits(repo):
            subprocess.run(["git", "clone", "-q", str(repo), str(work)], check=True, capture_output=True)
            for child in work.iterdir():
                if child.name == ".git":
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        else:
            subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", str(work)], check=True, capture_output=True)

        for rel, data in files_with_manifest.items():
            dest = work / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

        _git(["config", "user.name", author], cwd=work)
        _git(["config", "user.email", "platform@taskforge.local"], cwd=work)
        _git(["add", "-A"], cwd=work)
        _git(["commit", "-q", "-m", message], cwd=work)
        commit = _git(["rev-parse", "HEAD"], cwd=work).strip()
        safe_version = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(version)).strip(".-") or "1.0.0"
        tag = f"v{safe_version}"
        _git(["tag", "-f", tag], cwd=work)
        remotes = _git(["remote"], cwd=work).split()
        if "origin" not in remotes:
            _git(["remote", "add", "origin", str(repo)], cwd=work)
        _git(["push", "-q", "origin", "HEAD:refs/heads/main", "--tags"], cwd=work)

    return {
        "commit": commit,
        "tag": tag,
        "bundle_hash": bundle_hash,
        "dockerfile_hash": dockerfile_hash,
        "components": components,
    }


async def commit_bundle(definition_id: str, files: Dict[str, bytes], **kwargs) -> dict:
    return await asyncio.to_thread(commit_bundle_sync, definition_id, files, **kwargs)


def read_tree_sync(definition_id: str, ref: str = "HEAD") -> List[dict]:
    repo = ensure_repo(definition_id)
    output = _git(["--git-dir", str(repo), "ls-tree", "-r", "-l", ref])
    entries = []
    for line in output.splitlines():
        parts = line.split(maxsplit=4)
        if len(parts) >= 5:
            entries.append({"mode": parts[0], "type": parts[1], "sha": parts[2], "size": parts[3], "path": parts[4]})
    return entries


def read_file_sync(definition_id: str, ref: str, path: str) -> bytes:
    repo = ensure_repo(definition_id)
    return _git(["--git-dir", str(repo), "show", f"{ref}:{path}"], binary=True)


def source_tarball_sync(definition_id: str, ref: str = "HEAD") -> bytes:
    repo = ensure_repo(definition_id)
    return _git(["--git-dir", str(repo), "archive", "--format=tar.gz", ref], binary=True)


def diff_sync(definition_id: str, from_ref: str, to_ref: str) -> str:
    repo = ensure_repo(definition_id)
    return _git(["--git-dir", str(repo), "diff", from_ref, to_ref])


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _version_capabilities_for_storage(tags: Optional[Iterable[str]], packages: List[dict]) -> List[dict]:
    out = list(packages or [])
    for tag in tags or []:
        out.append({"manager": "capability", "name": str(tag), "version": ""})
    # De-dupe
    seen = set()
    deduped = []
    for item in out:
        key = (item.get("manager"), item.get("name"), item.get("version"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


async def create_definition(
    db: AsyncSession,
    definition_id: str,
    *,
    name: str,
    description: str = "",
    runtime_family: str = "openclaw",
    adapter_type: str = "",
    entrypoint: str = "",
    enabled: bool = True,
    tags: Optional[List[str]] = None,
    created_by: str = "system",
) -> ImageDefinition:
    definition_id = _safe_id(definition_id)
    existing = await db.get(ImageDefinition, definition_id)
    if existing:
        return existing
    definition = ImageDefinition(
        id=definition_id,
        name=name or definition_id,
        description=description,
        runtime_family=runtime_family,
        adapter_type=adapter_type,
        entrypoint=entrypoint,
        enabled=enabled,
        tags=tags or [],
        created_by=created_by,
    )
    db.add(definition)
    await db.flush()
    ensure_repo(definition_id)
    return definition


async def create_version_from_bundle(
    db: AsyncSession,
    definition: ImageDefinition,
    files: Dict[str, bytes],
    *,
    version: str,
    message: str,
    status: str = "draft",
    created_by: str = "system",
    parent_version_id: Optional[str] = None,
) -> Tuple[ImageVersion, dict]:
    lint = bundle_lint(files)
    if not lint["ok"]:
        raise ValueError("; ".join(lint["errors"]))
    if not version:
        version = datetime.now(timezone.utc).strftime("%Y.%m.%d-%H%M%S")

    committed = await commit_bundle(
        definition.id, files, version=version, message=message, author=created_by
    )

    image_yaml = lint.get("image_yaml") or {}
    runtime_family = str(image_yaml.get("runtime_family") or _infer_runtime_family(definition.id, lint)).lower()
    adapter_file, adapter_sha = _find_component(
        files, ["*adapter*.sh", "*adapter*.py", "taskforge-adapter.py", "*wrapper*.py"]
    )
    runner_file, runner_sha = _find_component(files, ["*runner*.sh", "*wrapper*.py"])
    entrypoint = lint.get("entrypoint") or image_yaml.get("entrypoint") or ""

    tags = image_yaml.get("capabilities") or definition.tags or []
    capabilities = _version_capabilities_for_storage(tags, [])
    fingerprint = resolver.capability_fingerprint(
        runtime_family,
        image_yaml.get("adapter_type", definition.adapter_type or ""),
        adapter_sha,
        runner_sha,
        capabilities,
    )

    version_id = f"imgv-{uuid.uuid4().hex[:8]}"
    image_version = ImageVersion(
        id=version_id,
        definition_id=definition.id,
        version=version,
        git_tag=committed["tag"],
        git_commit=committed["commit"],
        bundle_hash=committed["bundle_hash"],
        dockerfile_hash=committed["dockerfile_hash"],
        base_image=lint.get("from") or image_yaml.get("base_image", ""),
        runtime_family=runtime_family,
        adapter_type=image_yaml.get("adapter_type", definition.adapter_type or ""),
        adapter_sha=adapter_sha,
        runner_sha=runner_sha,
        component_manifest=committed["components"],
        entrypoint=entrypoint,
        runtime_description=image_yaml.get("runtime", definition.description or ""),
        capability_fingerprint=fingerprint,
        capabilities=capabilities,
        image_tag=image_yaml.get("tag", f"openclaw-agent:{definition.id}"),
        parent_version_id=parent_version_id,
        status=status,
        source_available=True,
        commit_message=message,
        created_by=created_by,
    )
    if parent_version_id:
        parent = await db.get(ImageVersion, parent_version_id)
        if parent:
            if not image_version.adapter_type:
                image_version.adapter_type = parent.adapter_type
            if not image_version.adapter_sha:
                image_version.adapter_sha = parent.adapter_sha
            if not image_version.runner_sha:
                image_version.runner_sha = parent.runner_sha
            if not image_version.entrypoint:
                image_version.entrypoint = parent.entrypoint
            if not image_version.base_digest:
                image_version.base_digest = parent.image_digest
            if not image_version.runtime_description:
                image_version.runtime_description = parent.runtime_description
            image_version.capability_fingerprint = resolver.capability_fingerprint(
                runtime_family,
                image_version.adapter_type or "",
                image_version.adapter_sha or "",
                image_version.runner_sha or "",
                capabilities,
            )

    db.add(image_version)
    await db.flush()
    for item in capabilities:
        db.add(ImageCapability(
            version_id=version_id,
            manager=item.get("manager", ""),
            name=item.get("name", ""),
            version=item.get("version", ""),
        ))

    # Bind the planner-facing AgentImage alias if one exists.
    binding = await db.execute(
        select(AgentImageBinding).where(AgentImageBinding.agent_image_id == definition.id)
    )
    if binding.scalars().first() is None:
        agent_image = await db.get(AgentImage, definition.id)
        if agent_image:
            db.add(AgentImageBinding(agent_image_id=definition.id, image_version_id=version_id))

    # Aliases: first version gets latest+stable.
    existing_alias = await db.execute(
        select(ImageAlias).where(ImageAlias.definition_id == definition.id)
    )
    if existing_alias.scalars().first() is None:
        db.add(ImageAlias(definition_id=definition.id, alias="latest", version_id=version_id))
        if status in ("ready", "detected"):
            db.add(ImageAlias(definition_id=definition.id, alias="stable", version_id=version_id))

    db.add(ImageEvent(
        definition_id=definition.id,
        version_id=version_id,
        event_type="version_created",
        message=message,
        data={
            "version": version,
            "bundle_hash": committed["bundle_hash"],
            "adapter_file": adapter_file,
            "runner_file": runner_file,
            "runtime_family": runtime_family,
        },
    ))
    return image_version, lint


async def ingest_build_result(
    db: AsyncSession,
    version_id: str,
    *,
    image_digest: str = "",
    image_tag: str = "",
    sbom_document: Optional[dict] = None,
    packages: Optional[List[dict]] = None,
    build_id: str = "",
    logs: str = "",
    error: str = "",
) -> dict:
    version = await db.get(ImageVersion, version_id)
    if not version:
        raise ValueError(f"ImageVersion {version_id} not found")

    build: Optional[ImageBuild] = None
    if build_id:
        build_result = await db.execute(
            select(ImageBuild).where(ImageBuild.build_id == build_id).limit(1)
        )
        build = build_result.scalars().first()
    if not build:
        build_result = await db.execute(
            select(ImageBuild)
            .where(
                ImageBuild.version_id == version.id,
                ImageBuild.status.in_(("pending", "building")),
            )
            .order_by(ImageBuild.created_at.desc())
            .limit(1)
        )
        build = build_result.scalars().first()

    if error:
        version.status = "failed"
        if build:
            await update_build_record(
                db, build, status="failed", error=error, logs=logs or None
            )
        await db.flush()
        return {"version_id": version.id, "status": "failed", "error": error}

    normalized = resolver.normalize_capabilities(packages or [])
    if not normalized and sbom_document:
        normalized = _packages_from_sbom(sbom_document)

    # Keep high-level capability tags from the bundle; add concrete packages.
    existing_caps = [c for c in (version.capabilities or []) if isinstance(c, dict)]
    existing_tags = [c for c in existing_caps if c.get("manager") == "capability"]
    # Keep the capabilities the version was created for; SBOM packages add to
    # (and version) that set rather than replacing it.
    existing_spec = [c for c in existing_caps if c.get("manager") != "capability"]
    capabilities = _version_capabilities_for_storage(
        [c["name"] for c in existing_tags], existing_spec + normalized
    )

    if sbom_document:
        sbom = ImageVersionSBOM(
            version_id=version.id,
            format="cyclonedx-json" if "components" in sbom_document else "spdx-json",
            document=sbom_document,
            packages=normalized,
        )
        db.add(sbom)
        await db.flush()
        version.sbom_id = sbom.id

    await db.execute(delete(ImageCapability).where(ImageCapability.version_id == version.id))
    for item in capabilities:
        db.add(ImageCapability(
            version_id=version.id,
            manager=item.get("manager", ""),
            name=item.get("name", ""),
            version=item.get("version", ""),
        ))

    parent_caps: List[dict] = []
    if version.parent_version_id:
        parent = await db.get(ImageVersion, version.parent_version_id)
        parent_caps = list(parent.capabilities or []) if parent else []

    previous_fingerprint = version.capability_fingerprint
    version.capabilities = capabilities
    version.image_digest = image_digest or version.image_digest
    version.image_tag = image_tag or version.image_tag
    version.status = "ready"
    version.capability_fingerprint = resolver.capability_fingerprint(
        version.runtime_family or "",
        version.adapter_type or "",
        version.adapter_sha or "",
        version.runner_sha or "",
        capabilities,
    )

    added = _capability_delta(parent_caps, capabilities)
    if added or previous_fingerprint != version.capability_fingerprint:
        db.add(ImageEvent(
            definition_id=version.definition_id,
            version_id=version.id,
            event_type="capability_added" if added else "capability_updated",
            message=f"{len(added)} new capability row(s)",
            data={
                "added": added,
                "fingerprint": version.capability_fingerprint,
                "previous_fingerprint": previous_fingerprint,
            },
        ))
    db.add(ImageEvent(
        definition_id=version.definition_id,
        version_id=version.id,
        event_type="built",
        message=f"Built {version.image_tag}",
        data={"image_digest": version.image_digest, "packages": len(normalized)},
    ))

    # Ensure latest points at a ready build.
    await _upsert_alias(db, version.definition_id, "latest", version.id)
    stable = await db.execute(
        select(ImageAlias).where(
            ImageAlias.definition_id == version.definition_id,
            ImageAlias.alias == "stable",
        )
    )
    if stable.scalars().first() is None:
        db.add(ImageAlias(definition_id=version.definition_id, alias="stable", version_id=version.id))

    if build:
        await update_build_record(
            db,
            build,
            status="succeeded",
            logs=logs or build.logs,
            image_tag=version.image_tag,
            image_digest=version.image_digest,
        )

    await db.flush()
    return {
        "version_id": version.id,
        "fingerprint": version.capability_fingerprint,
        "added": added,
        "build_id": build.id if build else None,
        "status": "succeeded",
    }


def _manager_from_purl(purl: str) -> str:
    purl = (purl or "").lower()
    if purl.startswith("pkg:pypi/"):
        return "pip"
    if purl.startswith("pkg:npm/"):
        return "npm"
    if purl.startswith("pkg:deb/"):
        return "apt"
    if purl.startswith("pkg:apk/"):
        return "apk"
    if purl.startswith(("pkg:golang/", "pkg:generic/")):
        return "binary"
    return ""


def _packages_from_sbom(document: dict) -> List[dict]:
    packages: List[dict] = []
    if isinstance(document.get("components"), list):  # CycloneDX
        for comp in document["components"]:
            if isinstance(comp, dict) and comp.get("name"):
                packages.append({
                    "name": comp.get("name"),
                    "version": comp.get("version") or "",
                    "type": _manager_from_purl(comp.get("purl") or "") or "binary",
                })
    elif isinstance(document.get("packages"), list):  # SPDX
        for pkg in document["packages"]:
            if not isinstance(pkg, dict) or not pkg.get("name"):
                continue
            purl = ""
            for ref in pkg.get("externalRefs") or []:
                if isinstance(ref, dict) and str(ref.get("referenceType", "")).lower() == "purl":
                    purl = ref.get("referenceLocator") or ""
                    break
            packages.append({
                "name": pkg.get("name"),
                "version": pkg.get("versionInfo") or pkg.get("version") or "",
                "type": _manager_from_purl(purl) or "binary",
            })
    return resolver.normalize_capabilities(packages)


def _capability_delta(previous: List[dict], current: List[dict]) -> List[dict]:
    prev = {(p.get("manager"), p.get("name"), p.get("version")) for p in previous if isinstance(p, dict)}
    return [
        p for p in current
        if (p.get("manager"), p.get("name"), p.get("version")) not in prev
    ]


async def _upsert_alias(db: AsyncSession, definition_id: str, alias: str, version_id: str) -> None:
    result = await db.execute(
        select(ImageAlias).where(
            ImageAlias.definition_id == definition_id,
            ImageAlias.alias == alias,
        )
    )
    row = result.scalars().first()
    if row:
        row.version_id = version_id
    else:
        db.add(ImageAlias(definition_id=definition_id, alias=alias, version_id=version_id))


async def alias_version(
    db: AsyncSession, definition_id: str, alias: str
) -> Optional[ImageVersion]:
    result = await db.execute(
        select(ImageAlias)
        .where(
            ImageAlias.definition_id == definition_id,
            ImageAlias.alias == alias,
        )
        .limit(1)
    )
    row = result.scalars().first()
    if not row:
        return None
    return await db.get(ImageVersion, row.version_id)


async def resolve_base_alias(db: AsyncSession, base_image: str) -> Optional[dict]:
    """Resolve a planner base_image id to its definition's stable/latest image."""
    definition = await db.get(ImageDefinition, _safe_id(base_image))
    if not definition or not definition.enabled:
        return None
    for alias in ("stable", "latest"):
        version = await alias_version(db, definition.id, alias)
        if version and version.status == "ready":
            return {"definition": definition, "version": version, "alias": alias}
    return None


# ---------------------------------------------------------------------------
# Build tracking / capability-request links
# ---------------------------------------------------------------------------

async def latest_approved_capability_request(
    db: AsyncSession, task_id: str
) -> Optional[CapabilityRequest]:
    if not task_id:
        return None
    result = await db.execute(
        select(CapabilityRequest)
        .where(
            CapabilityRequest.task_id == task_id,
            CapabilityRequest.status == RequestStatus.APPROVED,
        )
        .order_by(
            CapabilityRequest.reviewed_at.desc().nullslast(),
            CapabilityRequest.requested_at.desc(),
        )
        .limit(1)
    )
    return result.scalars().first()


async def create_build_record(
    db: AsyncSession,
    *,
    version: Optional[ImageVersion] = None,
    definition_id: str = "",
    build_id: str = "",
    trigger_type: str = "manual",
    capability_request: Optional[CapabilityRequest] = None,
    task_id: str = "",
    node_id: str = "",
    requested_by: str = "",
    approved_by: str = "",
    status: str = "pending",
    image_tag: str = "",
    image_digest: str = "",
    logs: str = "",
    error: str = "",
) -> ImageBuild:
    """Create (or return) an ImageBuild and its audit event."""
    if build_id:
        existing = await db.execute(
            select(ImageBuild).where(ImageBuild.build_id == build_id).limit(1)
        )
        existing_build = existing.scalars().first()
        if existing_build:
            return existing_build

    build = ImageBuild(
        version_id=version.id if version else None,
        definition_id=definition_id or (version.definition_id if version else ""),
        build_id=build_id,
        trigger_type=trigger_type,
        capability_request_id=capability_request.id if capability_request else None,
        task_id=task_id or (capability_request.task_id if capability_request else ""),
        node_id=node_id,
        requested_by=requested_by,
        approved_by=approved_by or (capability_request.reviewed_by if capability_request else ""),
        status=status,
        image_tag=image_tag,
        image_digest=image_digest,
        logs=logs,
        error=error,
        finished_at=datetime.utcnow()
        if status in ("succeeded", "failed", "denied")
        else None,
    )
    db.add(build)
    await db.flush()
    event_type = (
        "build_started"
        if status in ("pending", "building")
        else "build_succeeded" if status == "succeeded" else "build_failed"
    )
    db.add(ImageEvent(
        definition_id=build.definition_id,
        version_id=build.version_id,
        event_type=event_type,
        message=f"{trigger_type} build {status}",
        data={
            "build_id": build_id,
            "trigger": trigger_type,
            "capability_request_id": build.capability_request_id,
            "task_id": build.task_id,
        },
    ))
    return build


async def update_build_record(
    db: AsyncSession,
    build: ImageBuild,
    *,
    status: str,
    logs: Optional[str] = None,
    error: Optional[str] = None,
    image_tag: Optional[str] = None,
    image_digest: Optional[str] = None,
) -> ImageBuild:
    build.status = status
    if logs is not None:
        build.logs = logs
    if error is not None:
        build.error = error
    if image_tag is not None:
        build.image_tag = image_tag
    if image_digest is not None:
        build.image_digest = image_digest
    if status in ("succeeded", "failed", "denied"):
        build.finished_at = datetime.utcnow()
    db.add(ImageEvent(
        definition_id=build.definition_id,
        version_id=build.version_id,
        event_type=(
            "build_succeeded" if status == "succeeded"
            else "build_failed" if status == "failed"
            else "build_status"
        ),
        message=f"Build {status}",
        data={
            "build_id": build.build_id,
            "error": (build.error or "")[:500],
            "logs_tail": (build.logs or "")[-1000:],
        },
    ))
    return build


def git_log_sync(definition_id: str, limit: int = 100) -> List[dict]:
    repo = ensure_repo(definition_id)
    limit = max(1, min(limit, 500))
    fmt = "%H%x1f%an%x1f%ad%x1f%s"
    output = _git([
        "--git-dir", str(repo), "log", f"-{limit}",
        f"--pretty=format:{fmt}", "--date=iso",
    ])
    commits = []
    for line in output.splitlines():
        parts = line.split("\x1f")
        if len(parts) >= 4:
            commits.append({
                "commit": parts[0],
                "author": parts[1],
                "date": parts[2],
                "message": parts[3],
            })
    return commits


async def next_version_for_definition(db: AsyncSession, definition_id: str) -> str:
    result = await db.execute(
        select(ImageVersion.version)
        .where(ImageVersion.definition_id == definition_id)
        .order_by(ImageVersion.created_at.desc())
        .limit(50)
    )
    semvers = []
    for (value,) in result.all():
        match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", str(value or ""))
        if match:
            semvers.append(tuple(int(part) for part in match.groups()))
    if semvers:
        major, minor, patch = max(semvers)
        return f"{major}.{minor}.{patch + 1}"
    return "1.0.0"


def capability_delta(version: ImageVersion, parent: Optional[ImageVersion]) -> dict:
    previous = {
        (p.get("manager"), p.get("name"), p.get("version"))
        for p in ((parent.capabilities or []) if parent else [])
        if isinstance(p, dict)
    }
    current = {
        (p.get("manager"), p.get("name"), p.get("version"))
        for p in (version.capabilities or [])
        if isinstance(p, dict)
    }
    def rows(items):
        return [
            {"manager": m, "name": n, "version": v}
            for m, n, v in sorted(items)
        ]
    return {"added": rows(current - previous), "removed": rows(previous - current)}


# ---------------------------------------------------------------------------
# Import / registry reconciliation
# ---------------------------------------------------------------------------

def _read_bundle_dir(path: Path) -> Dict[str, bytes]:
    files: Dict[str, bytes] = {}
    for child in sorted(path.rglob("*")):
        if not child.is_file():
            continue
        rel_parts = child.relative_to(path).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        if child.suffix in (".pyc", ".pyo"):
            continue
        files["/".join(rel_parts)] = child.read_bytes()
    return files


async def import_from_agent_images(db: AsyncSession) -> dict:
    profiles = {}
    yaml_path = AGENT_IMAGES_DIR / "agent_profiles.yaml"
    if yaml_path.is_file():
        try:
            profiles = (yaml.safe_load(yaml_path.read_text()) or {}).get("base_images", {}) or {}
        except Exception as exc:
            logger.warning("Could not parse agent_profiles.yaml: %s", exc)

    created_defs = 0
    created_versions = 0
    skipped = 0
    children = sorted(AGENT_IMAGES_DIR.iterdir()) if AGENT_IMAGES_DIR.exists() else []
    for child in children:
        if not child.is_dir() or child.name in SKIP_DIRS or child.name.startswith("_"):
            continue
        # The base image directory maps to the planner's "openclaw" alias.
        profile_key = "openclaw" if child.name == "base" else child.name
        definition_id = _safe_id(profile_key)
        existing = await db.get(ImageDefinition, definition_id)
        if existing:
            skipped += 1
            continue
        files = _read_bundle_dir(child)
        if "Dockerfile" not in files:
            continue
        lint = bundle_lint(files)
        if not lint["ok"]:
            # Vendor shared adapter/runtime files that a Dockerfile copies but
            # that live outside this image directory (common for browser images).
            for err in list(lint["errors"]):
                if "COPY/ADD source is missing:" not in err:
                    continue
                missing = err.split("COPY/ADD source is missing:", 1)[1].strip()
                basename = Path(missing).name
                for candidate in (
                    Path(settings.SHARED_COMPONENTS_DIR) / basename,
                    AGENT_IMAGES_DIR / "base" / basename,
                ):
                    if candidate.is_file():
                        files[missing] = candidate.read_bytes()
                        logger.info("Vendored %s into %s from %s", basename, child.name, candidate)
                        break
            lint = bundle_lint(files)
        if not lint["ok"]:
            logger.warning("Skipping %s: %s", child.name, lint["errors"])
            continue
        meta = profiles.get(profile_key, {}) if isinstance(profiles, dict) else {}
        runtime_family = str(meta.get("runtime_family") or profile_key).lower()
        definition = await create_definition(
            db,
            definition_id,
            name=meta.get("name", child.name.capitalize()),
            description=meta.get("description", ""),
            runtime_family=runtime_family,
            adapter_type=meta.get("adapter_type", ""),
            entrypoint=lint.get("entrypoint", ""),
            enabled=bool(meta.get("enabled", True)),
            tags=list(meta.get("capabilities", []) or []),
            created_by="import",
        )
        created_defs += 1

        # Prefer the repo's declared version, then the YAML tag.
        version = "1.0.0"
        files.setdefault(
            "image.yaml",
            yaml.safe_dump({
                "name": definition.name,
                "description": definition.description,
                "runtime_family": runtime_family,
                "entrypoint": lint.get("entrypoint", ""),
                "base_image": lint.get("from", ""),
                "tag": meta.get("tag", f"openclaw-agent:{definition_id}"),
                "capabilities": meta.get("capabilities", []),
                "best_for": meta.get("best_for", []),
                "avoid_for": meta.get("avoid_for", []),
            }, sort_keys=False).encode(),
        )
        try:
            image_version, version_lint = await create_version_from_bundle(
                db,
                definition,
                files,
                version=version,
                message="Import existing agent image source bundle",
                status="ready",
                created_by="import",
            )
            # Derived images inherit their adapter/runner/entrypoint from the
            # base image named in FROM (e.g. ann2snn/banking -> openclaw).
            if not image_version.entrypoint:
                from_image = (version_lint.get("from") or "").lower()
                for candidate in ("octaveclaw", "nanobot", "picoclaw", "zeroclaw", "openclaw"):
                    if candidate in from_image:
                        parent_definition = await db.get(ImageDefinition, candidate)
                        if not parent_definition:
                            continue
                        parent_result = await db.execute(
                            select(ImageVersion)
                            .where(ImageVersion.definition_id == candidate)
                            .order_by(ImageVersion.created_at.desc())
                            .limit(1)
                        )
                        parent_version = parent_result.scalars().first()
                        if parent_version:
                            image_version.entrypoint = parent_version.entrypoint
                            image_version.adapter_type = parent_version.adapter_type
                            image_version.adapter_sha = parent_version.adapter_sha
                            image_version.runner_sha = parent_version.runner_sha
                            image_version.runtime_description = parent_version.runtime_description
                            image_version.parent_version_id = parent_version.id
                        break
            created_versions += 1
        except Exception as exc:
            logger.warning("Could not import version for %s: %s", definition_id, exc)

    # Backfill inherited adapter/entrypoint metadata for derived images.
    derived = await db.execute(
        select(ImageVersion).where(
            (ImageVersion.entrypoint == "")
            | (ImageVersion.entrypoint.is_(None))
        )
    )
    for version in derived.scalars().all():
        from_image = (version.base_image or "").lower()
        for candidate in ("octaveclaw", "nanobot", "picoclaw", "zeroclaw", "openclaw"):
            if candidate not in from_image:
                continue
            parent_result = await db.execute(
                select(ImageVersion)
                .where(
                    ImageVersion.definition_id == candidate,
                    ImageVersion.status == "ready",
                    ImageVersion.id != version.id,
                )
                .order_by(ImageVersion.created_at.desc())
                .limit(1)
            )
            parent = parent_result.scalars().first()
            if parent:
                if not version.entrypoint:
                    version.entrypoint = parent.entrypoint
                if not version.adapter_type:
                    version.adapter_type = parent.adapter_type
                if not version.adapter_sha:
                    version.adapter_sha = parent.adapter_sha
                if not version.runner_sha:
                    version.runner_sha = parent.runner_sha
                if not version.runtime_description:
                    version.runtime_description = parent.runtime_description
                if not version.parent_version_id:
                    version.parent_version_id = parent.id
            break

    # Backfill capability index rows for versions imported before indexing.
    all_versions = await db.execute(select(ImageVersion))
    for version in all_versions.scalars().all():
        has_rows = await db.execute(
            select(ImageCapability.id).where(ImageCapability.version_id == version.id).limit(1)
        )
        if has_rows.scalars().first():
            continue
        for item in version.capabilities or []:
            if isinstance(item, dict):
                db.add(ImageCapability(
                    version_id=version.id,
                    manager=item.get("manager", ""),
                    name=item.get("name", ""),
                    version=item.get("version", ""),
                ))

    await db.commit()
    return {"definitions": created_defs, "versions": created_versions, "skipped": skipped}


async def reconcile_registry(db: AsyncSession) -> dict:
    """Scan the registry for images not yet registered in the catalog."""
    registry = (settings.REGISTRY_URL or "registry:5000").replace("http://", "").replace("https://", "").rstrip("/")
    base = f"http://{registry}"
    created = 0
    scanned = 0
    errors: List[str] = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            catalog = await client.get(f"{base}/v2/_catalog")
            catalog.raise_for_status()
            repos = catalog.json().get("repositories", [])
            for repo in repos:
                tags_resp = await client.get(f"{base}/v2/{repo}/tags/list")
                if tags_resp.status_code != 200:
                    continue
                for tag in tags_resp.json().get("tags", []) or []:
                    scanned += 1
                    image_tag = f"{registry}/{repo}:{tag}"
                    existing = await db.execute(
                        select(ImageVersion).where(ImageVersion.image_tag == image_tag).limit(1)
                    )
                    if existing.scalars().first():
                        continue
                    digest = ""
                    try:
                        manifest = await client.get(
                            f"{base}/v2/{repo}/manifests/{tag}",
                            headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"},
                        )
                        digest = manifest.headers.get("Docker-Content-Digest", "")
                    except Exception:
                        pass
                    definition_id = _safe_id(f"detected-{repo}")
                    definition = await create_definition(
                        db,
                        definition_id,
                        name=f"Detected: {repo}",
                        description="Discovered in the registry; source bundle not yet imported",
                        runtime_family=_infer_runtime_family(repo, {"from": repo}),
                        enabled=False,
                        created_by="reconciler",
                    )
                    version = ImageVersion(
                        id=f"imgv-{uuid.uuid4().hex[:8]}",
                        definition_id=definition.id,
                        version=tag,
                        image_tag=image_tag,
                        image_digest=digest,
                        runtime_family=definition.runtime_family,
                        status="detected",
                        source_available=False,
                        created_by="reconciler",
                    )
                    db.add(version)
                    db.add(ImageEvent(
                        definition_id=definition.id,
                        version_id=version.id,
                        event_type="detected",
                        message=f"Detected registry image {image_tag}",
                        data={"image_tag": image_tag, "digest": digest},
                    ))
                    created += 1
    except Exception as exc:
        errors.append(str(exc))
        logger.warning("Registry reconcile failed: %s", exc)
    await db.commit()
    return {"scanned": scanned, "created": created, "errors": errors}


# ---------------------------------------------------------------------------
# Resolution helper used by the image-builder and DAG planner
# ---------------------------------------------------------------------------

async def resolve_spec(db: AsyncSession, spec: dict) -> dict:
    reuse = await resolver.find_reusable(db, spec)
    if reuse:
        version: ImageVersion = reuse["version"]
        return {
            "reused": True,
            "reason": reuse["reason"],
            "version_id": version.id,
            "definition_id": version.definition_id,
            "image_tag": version.image_tag,
            "image_digest": version.image_digest,
            "capabilities": version.capabilities,
            "fingerprint": version.capability_fingerprint,
            "runtime_family": version.runtime_family,
            "adapter_type": version.adapter_type,
        }
    env = await resolver.get_or_create_environment(db, spec)
    return {
        "reused": False,
        "reason": "no_superset",
        "environment_id": env.id,
        "fingerprint": spec["fingerprint"],
        "spec": spec,
    }


async def resolve_planner_capabilities(dag_json: dict, db: AsyncSession) -> None:
    """Resolve base images and planner `required_capabilities` to catalog pins."""
    from capability_resolver import spec_from_required

    # Resolve base_image -> the definition's promoted stable/latest image.
    for node_def in dag_json.get("nodes", []):
        config = node_def.setdefault("config", {})
        base = str(config.get("base_image") or "").strip()
        if not base:
            continue
        try:
            resolved_base = await resolve_base_alias(db, base)
        except Exception as exc:
            logger.warning("Base image resolution failed for %s: %s", base, exc)
            continue
        if not resolved_base:
            continue
        base_version = resolved_base["version"]
        image_tag = base_version.image_tag or ""
        if image_tag and not image_tag.startswith(("registry:5000/", "localhost:5000/")):
            image_tag = f"{settings.REGISTRY_URL}/{image_tag}"
        if image_tag:
            config["resolved_image"] = image_tag
        config["resolved_runtime"] = base_version.runtime_family or base
        config["capability_resolution"] = {
            "reused": True,
            "reason": f"base_alias:{resolved_base['alias']}",
            "version_id": base_version.id,
            "definition_id": base_version.definition_id,
            "image_tag": image_tag,
            "fingerprint": base_version.capability_fingerprint,
        }

    for node_def in dag_json.get("nodes", []):
        config = node_def.setdefault("config", {})
        if str(node_def.get("node_type") or config.get("type") or "").lower() == "block":
            continue
        required = config.get("required_capabilities")
        if not required:
            continue
        spec = spec_from_required(
            required,
            runtime_family=config.get("runtime_family") or config.get("base_image") or "openclaw",
            base_image=config.get("base_image", ""),
            adapter_type=config.get("adapter_type", ""),
        )
        if not spec:
            continue
        try:
            resolved = await ensure_capability_version(
                db,
                spec,
                definition_id=(required or {}).get("definition_id"),
                base_image=config.get("base_image", ""),
            )
            config["capability_resolution"] = {
                "fingerprint": resolved.get("fingerprint") or spec["fingerprint"],
                "reused": bool(resolved.get("reused")),
                "reason": resolved.get("reason"),
                "version_id": resolved.get("version_id"),
                "definition_id": resolved.get("definition_id"),
                "image_tag": resolved.get("image_tag"),
            }
            if resolved.get("reused") and resolved.get("image_tag"):
                resolved_image = resolved["image_tag"]
                if not resolved_image.startswith(("registry:5000/", "localhost:5000/")):
                    resolved_image = f"registry:5000/{resolved_image}"
                config["resolved_image"] = resolved_image
            config["resolved_runtime"] = (
                resolved.get("runtime_family")
                or config.get("runtime_family")
                or config.get("base_image")
                or "openclaw"
            )
        except Exception as exc:
            logger.warning(
                "Capability resolution failed for node %s: %s",
                node_def.get("node_id"),
                exc,
            )
            config["capability_resolution"] = {"error": str(exc)[:300]}


async def ensure_capability_version(
    db: AsyncSession,
    spec: dict,
    *,
    definition_id: Optional[str] = None,
    base_image: str = "",
) -> dict:
    """Resolve a spec; create a generated catalog version when no image covers it."""
    resolved = await resolve_spec(db, spec)
    if resolved.get("reused"):
        return resolved

    # A pending/draft version with the same fingerprint should be built, not
    # duplicated. The image-builder will consume this version_id.
    existing = await db.execute(
        select(ImageVersion)
        .where(
            ImageVersion.capability_fingerprint == spec["fingerprint"],
            ImageVersion.status.in_(("draft", "building", "ready")),
        )
        .order_by(ImageVersion.created_at.desc())
        .limit(1)
    )
    existing_version = existing.scalars().first()
    if existing_version:
        return {
            "reused": existing_version.status == "ready",
            "reason": "existing_version",
            "version_id": existing_version.id,
            "definition_id": existing_version.definition_id,
            "version": existing_version.version,
            "image_tag": existing_version.image_tag,
            "git_ref": existing_version.git_tag,
            "fingerprint": existing_version.capability_fingerprint,
            "spec": spec,
        }

    definition_id = _safe_id(definition_id or f"capability-{spec['runtime_family']}")
    definition = await create_definition(
        db,
        definition_id,
        name=f"{spec['runtime_family']} capability images",
        description="Generated by the capability resolver",
        runtime_family=spec["runtime_family"],
        enabled=True,
        created_by="capability-resolver",
    )
    # Inherit runtime/adapter identity from the base family's latest ready build.
    parent = None
    base_definition = await db.get(ImageDefinition, spec["runtime_family"])
    if base_definition:
        parent_result = await db.execute(
            select(ImageVersion)
            .where(
                ImageVersion.definition_id == base_definition.id,
                ImageVersion.status == "ready",
            )
            .order_by(ImageVersion.created_at.desc())
            .limit(1)
        )
        parent = parent_result.scalars().first()

    base = base_image or spec.get("base_image") or (
        _registry_ref(parent.image_tag) if parent and parent.image_tag else f"{settings.REGISTRY_URL}/openclaw-agent:{spec['runtime_family']}"
    )
    dockerfile = resolver.generated_dockerfile(spec, base)
    caps = [f"{p['manager']}:{p['name']}" for p in spec["packages"] if p["manager"] in ("pip", "npm", "apt", "apk")]
    image_yaml = yaml.safe_dump({
        "runtime_family": spec["runtime_family"],
        "adapter_type": (parent.adapter_type if parent else spec["adapter_type"]),
        "base_image": base,
        "capabilities": caps,
        "generated": True,
    }, sort_keys=False)
    files = {
        "Dockerfile": dockerfile.encode(),
        "image.yaml": image_yaml.encode(),
    }
    version = spec["fingerprint"][:12]
    image_version, lint = await create_version_from_bundle(
        db,
        definition,
        files,
        version=version,
        message=f"Generated capability image ({spec['fingerprint'][:12]})",
        status="draft",
        created_by="capability-resolver",
        parent_version_id=parent.id if parent else None,
    )
    if parent:
        image_version.adapter_type = parent.adapter_type
        image_version.adapter_sha = parent.adapter_sha
        image_version.runner_sha = parent.runner_sha
        image_version.entrypoint = parent.entrypoint
        image_version.base_digest = parent.image_digest
        image_version.runtime_description = parent.runtime_description
    normalized_spec_caps = resolver.normalize_capabilities(spec["packages"])
    image_version.capabilities = normalized_spec_caps
    await db.execute(delete(ImageCapability).where(ImageCapability.version_id == image_version.id))
    for item in normalized_spec_caps:
        db.add(ImageCapability(
            version_id=image_version.id,
            manager=item.get("manager", ""),
            name=item.get("name", ""),
            version=item.get("version", ""),
        ))
    image_version.capability_fingerprint = spec["fingerprint"]
    db.add(EnvironmentImageVersion(
        environment_id=resolved["environment_id"],
        image_version_id=image_version.id,
        is_current=True,
    ))
    await db.flush()
    return {
        "reused": False,
        "reason": "generated",
        "version_id": image_version.id,
        "definition_id": definition.id,
        "version": image_version.version,
        "image_tag": image_version.image_tag,
        "git_ref": image_version.git_tag,
        "fingerprint": spec["fingerprint"],
        "spec": spec,
        "dockerfile": dockerfile,
    }
