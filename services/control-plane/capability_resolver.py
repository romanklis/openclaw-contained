"""Capability resolution for platform-managed agent images.

A CapabilitySpec describes what a task/node needs to run: a runtime/adapter
family, normalized package specs, and optional binaries/modalities. The resolver
reuses an existing image version whose capability set is a superset of the
request (within a compatible runtime/adapter family), or creates a reusable
environment for a single build.

This module is deliberately dependency-light so it can be unit-tested without a
running stack.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    ImageAlias,
    ImageDefinition,
    ImageVersion,
    NodeEnvironment,
)


# Runtime families grouped by base/distro compatibility. Reuse never crosses
# families with different runtime semantics (e.g. OpenClaw -> PicoClaw).
RUNTIME_FAMILIES = {
    "openclaw": "debian",
    "octaveclaw": "debian",
    "zeroclaw": "debian",
    "browser": "debian",
    "browser_v2": "debian",
    "browser_v3": "debian",
    "browser_v4": "debian",
    "banking": "debian",
    "ann2snn": "debian",
    "nanobot": "alpine",
    "picoclaw": "alpine",
}


def base_family(runtime_family: str) -> str:
    key = (runtime_family or "").strip().lower()
    if key in RUNTIME_FAMILIES:
        return RUNTIME_FAMILIES[key]
    # Unknown/capability-generated families inherit their prefix (e.g. openclaw-cap).
    for name, fam in RUNTIME_FAMILIES.items():
        if key.startswith(name):
            return fam
    return "debian"


def _normalize_package(item: Any, default_manager: str = "pip") -> Optional[dict]:
    if isinstance(item, str):
        raw = item.strip()
        if not raw:
            return None
        manager = default_manager
        if ":" in raw and not raw.startswith(("=", ">", "<", "~")):
            maybe_manager, rest = raw.split(":", 1)
            if maybe_manager in ("pip", "npm", "apt", "apk", "binary"):
                manager, raw = maybe_manager, rest
        match = re.match(r"^([A-Za-z0-9_.@/+~-]+)\s*(.*)$", raw)
        if not match:
            return None
        name, version = match.group(1), match.group(2).strip()
        return {"manager": manager, "name": name.lower(), "version": version}
    if isinstance(item, dict):
        name = item.get("name") or item.get("package")
        if not name:
            return None
        manager = item.get("manager") or item.get("type") or default_manager
        manager = {
            "python": "pip", "pip_package": "pip", "python_packages": "pip",
            "python-pkg": "pip", "npm_package": "npm", "node-pkg": "npm",
            "apt_package": "apt", "apk_package": "apk", "deb": "apt",
            "gobinary": "binary", "go": "binary",
        }.get(str(manager).lower(), str(manager).lower())
        return {
            "manager": manager,
            "name": str(name).lower(),
            "version": str(item.get("version") or "").strip(),
        }
    return None


def normalize_capabilities(items: Iterable[Any], default_manager: str = "pip") -> List[dict]:
    out: Dict[Tuple[str, str], dict] = {}
    for item in items or []:
        pkg = _normalize_package(item, default_manager)
        if pkg:
            out[(pkg["manager"], pkg["name"])] = pkg
    return [out[k] for k in sorted(out)]


def make_spec(
    runtime_family: str,
    packages: Iterable[Any],
    *,
    adapter_type: str = "",
    adapter_sha: str = "",
    runner_sha: str = "",
    base_image: str = "",
    categories: Optional[Iterable[str]] = None,
    modalities: Optional[Iterable[str]] = None,
    binaries: Optional[Iterable[str]] = None,
) -> dict:
    normalized = normalize_capabilities(packages)
    # Categories/modalities/binaries are matched as pseudo-capabilities against
    # the high-level capability tags stored for imported images.
    for value in list(categories or []) + list(modalities or []):
        if value:
            normalized.append({"manager": "capability", "name": str(value).lower(), "version": ""})
    for value in binaries or []:
        if value:
            normalized.append({"manager": "binary", "name": str(value).lower(), "version": ""})
    deduped = {}
    for item in normalized:
        deduped[(item["manager"], item["name"])] = item
    normalized = [deduped[k] for k in sorted(deduped)]

    fam = (runtime_family or "openclaw").strip().lower()
    spec = {
        "runtime_family": fam,
        "base_family": base_family(fam),
        "adapter_type": (adapter_type or "").strip().lower(),
        "adapter_sha": adapter_sha or "",
        "runner_sha": runner_sha or "",
        "base_image": base_image or "",
        "categories": sorted({str(c) for c in (categories or []) if c}),
        "modalities": sorted({str(m) for m in (modalities or []) if m}),
        "binaries": sorted({str(b) for b in (binaries or []) if b}),
        "packages": normalized,
    }
    spec["fingerprint"] = capability_fingerprint(
        fam, adapter_type, adapter_sha, runner_sha, normalized
    )
    return spec


def capability_fingerprint(
    runtime_family: str,
    adapter_type: str,
    adapter_sha: str,
    runner_sha: str,
    packages: List[dict],
) -> str:
    payload = {
        "runtime_family": (runtime_family or "").strip().lower(),
        "base_family": base_family(runtime_family),
        "adapter_type": (adapter_type or "").strip().lower(),
        "adapter_sha": adapter_sha or "",
        "runner_sha": runner_sha or "",
        "packages": sorted(
            (p.get("manager", ""), p.get("name", ""), p.get("version", ""))
            for p in (packages or [])
        ),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]


def _version_ok(candidate: str, requirement: str) -> bool:
    candidate = (candidate or "").strip()
    requirement = (requirement or "").strip()
    if not requirement:
        return True
    if not candidate:
        return False
    if requirement.startswith("=="):
        return candidate == requirement[2:].strip()
    if requirement.startswith(">="):
        try:
            return tuple(map(int, re.findall(r"\d+", candidate)[:3])) >= tuple(map(int, re.findall(r"\d+", requirement)[:3]))
        except Exception:
            return candidate >= requirement[2:].strip()
    if requirement.startswith("<="):
        try:
            return tuple(map(int, re.findall(r"\d+", candidate)[:3])) <= tuple(map(int, re.findall(r"\d+", requirement)[:3]))
        except Exception:
            return candidate <= requirement[2:].strip()
    # Bare version constraint or compatible release: require an exact prefix.
    return candidate == requirement or candidate.startswith(requirement)


def _covers(candidate_caps: Optional[List[dict]], required: List[dict]) -> bool:
    by_key = {
        (str(c.get("manager", "")).lower(), str(c.get("name", "")).lower()): str(c.get("version", ""))
        for c in (candidate_caps or [])
        if isinstance(c, dict)
    }
    for req in required:
        key = (req["manager"], req["name"])
        if key not in by_key:
            return False
        if not _version_ok(by_key[key], req.get("version", "")):
            return False
    return True


def _compatible(version: ImageVersion, spec: dict) -> bool:
    if (version.runtime_family or "").strip().lower() != spec["runtime_family"]:
        return False
    if base_family(version.runtime_family) != spec["base_family"]:
        return False
    if spec.get("adapter_type") and (version.adapter_type or "").strip().lower() != spec["adapter_type"]:
        return False
    return True


def spec_from_required(
    required: Any,
    *,
    runtime_family: str = "openclaw",
    base_image: str = "",
    adapter_type: str = "",
) -> Optional[dict]:
    """Build a spec from a planner `required_capabilities` object."""
    if not isinstance(required, dict):
        return None
    packages = required.get("packages") or required.get("pip") or []
    if isinstance(packages, dict):
        flat = []
        for manager, names in packages.items():
            for name in names or []:
                flat.append({"manager": manager, "name": name})
        packages = flat
    return make_spec(
        required.get("runtime_family") or runtime_family,
        packages,
        adapter_type=required.get("adapter_type") or adapter_type,
        base_image=base_image,
        categories=required.get("categories"),
        modalities=required.get("modalities"),
        binaries=required.get("binaries") or required.get("tools"),
    )


async def _definition_enabled(db: AsyncSession, definition_id: str) -> bool:
    result = await db.execute(
        select(ImageDefinition.enabled).where(ImageDefinition.id == definition_id)
    )
    value = result.scalar_one_or_none()
    return bool(value) if value is not None else False


async def find_reusable(db: AsyncSession, spec: dict) -> Optional[dict]:
    """Return {version, reason, reused: True} if an existing image covers spec."""
    if not spec.get("packages") and not spec.get("categories") and not spec.get("binaries"):
        return None

    # Exact fingerprint first.
    exact = await db.execute(
        select(ImageVersion)
        .where(
            ImageVersion.status == "ready",
            ImageVersion.capability_fingerprint == spec["fingerprint"],
        )
        .order_by(ImageVersion.created_at.desc())
        .limit(1)
    )
    version = exact.scalars().first()
    if version and await _definition_enabled(db, version.definition_id):
        return {"reused": True, "reason": "exact_fingerprint", "version": version}

    # Superset within a compatible runtime/adapter family.
    candidates = await db.execute(
        select(ImageVersion)
        .where(
            ImageVersion.status == "ready",
            ImageVersion.runtime_family == spec["runtime_family"],
        )
        .order_by(ImageVersion.created_at.desc())
        .limit(300)
    )
    best: Optional[ImageVersion] = None
    best_score: Optional[Tuple[int, int, float]] = None
    for candidate in candidates.scalars().all():
        if not _compatible(candidate, spec):
            continue
        if not await _definition_enabled(db, candidate.definition_id):
            continue
        if not _covers(candidate.capabilities, spec["packages"]):
            continue
        # Prefer fewer capabilities (smaller/specialized), then bytes, then newest.
        score = (
            len(candidate.capabilities or []),
            len(candidate.image_digest or ""),
            -(candidate.created_at.timestamp() if candidate.created_at else 0.0),
        )
        if best is None or score < best_score:
            best, best_score = candidate, score

    if best is not None:
        return {"reused": True, "reason": "capability_superset", "version": best}
    return None


async def get_or_create_environment(db: AsyncSession, spec: dict) -> NodeEnvironment:
    """Find/create a reusable environment keyed by the capability fingerprint."""
    result = await db.execute(
        select(NodeEnvironment).where(
            NodeEnvironment.capability_fingerprint == spec["fingerprint"]
        )
    )
    env = result.scalars().first()
    if env:
        return env

    caps = [f"{p['manager']}:{p['name']}{('==' + p['version']) if p['version'] else ''}" for p in spec["packages"]]
    env = NodeEnvironment(
        id=f"env-{uuid.uuid4().hex[:8]}",
        name=f"{spec['runtime_family']} capabilities",
        description="Auto-created by the capability resolver",
        base_image=spec["runtime_family"],
        capabilities=caps,
        capability_fingerprint=spec["fingerprint"],
        current_image_tag="",
        version=0,
    )
    db.add(env)
    await db.flush()
    return env


def generated_dockerfile(spec: dict, base_image: str) -> str:
    """Generate a Dockerfile for a capability-only image version."""
    fam = spec["runtime_family"]
    if not base_image:
        base_image = f"registry:5000/openclaw-agent:{fam}"
    packages = spec["packages"]
    pip = [p["name"] + (p["version"] if p["version"].startswith(("=", ">", "<", "~")) else (f"=={p['version']}" if p["version"] else "")) for p in packages if p["manager"] == "pip"]
    npm = [p["name"] + (f"@{p['version']}" if p["version"] else "") for p in packages if p["manager"] == "npm"]
    apt = [p["name"] for p in packages if p["manager"] == "apt"]
    apk = [p["name"] for p in packages if p["manager"] == "apk"]

    lines = [
        f"FROM {base_image}",
        "",
        f"LABEL org.openclaw.runtime_family=\"{fam}\"",
        f"LABEL org.openclaw.capability_fingerprint=\"{spec['fingerprint']}\"",
        "",
    ]
    if apt:
        lines += ["USER root", "RUN apt-get update && apt-get install -y \\"]
        lines += [f"    {p} \\" for p in apt]
        lines += ["    && rm -rf /var/lib/apt/lists/*", ""]
    if apk:
        lines += ["USER root", "RUN apk add --no-cache \\"]
        lines += [f"    {p} \\" for p in apk]
        lines += ["    && rm -rf /var/cache/apk/*", ""]
    if pip:
        lines += ["USER root", "ENV PIP_BREAK_SYSTEM_PACKAGES=1"]
        if fam in ("nanobot", "picoclaw"):
            lines.append("RUN pip install --no-cache-dir " + " ".join(pip))
        else:
            lines.append("RUN pip install --no-cache-dir --break-system-packages " + " ".join(pip))
        lines.append("")
    if npm:
        lines += ["USER root", "RUN npm install -g " + " ".join(npm), ""]
    lines += ["WORKDIR /workspace", ""]
    return "\n".join(lines)
