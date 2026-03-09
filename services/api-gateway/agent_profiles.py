"""
Agent Profiles Registry — loads and serves agent profile definitions.

Reads ``agent_profiles.yaml`` and exposes profiles as structured data
for the /v1/models endpoint and task creation.  Supports CRUD operations
that persist changes back to the YAML file.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger("api-gateway.profiles")

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class AgentProfileMetadata(BaseModel):
    runtime: str = ""
    strengths: List[str] = []


class BaseImageInfo(BaseModel):
    dockerfile: str = ""
    tag: str = ""
    runtime: str = ""
    description: str = ""
    size_estimate: str = ""


class AgentProfile(BaseModel):
    """A single agent profile — maps a user-facing ID to a (base_image, llm_model) pair."""

    id: str
    name: str
    description: str = ""
    base_image: str  # e.g. "openclaw", "nanobot", "picoclaw", "zeroclaw"
    llm_model: str  # e.g. "gemini-flash-latest", "claude-sonnet-4-20250514"
    tags: List[str] = []
    icon: str = "🤖"
    metadata: AgentProfileMetadata = Field(default_factory=AgentProfileMetadata)


# ---------------------------------------------------------------------------
# Registry singleton
# ---------------------------------------------------------------------------

_PROFILES: List[AgentProfile] = []
_BASE_IMAGES: Dict[str, BaseImageInfo] = {}
_LOADED = False
_YAML_PATH: Optional[Path] = None


def _find_profiles_yaml() -> Optional[Path]:
    """Search common locations for agent_profiles.yaml."""
    candidates = [
        # Mounted inside the container by docker-compose (agent-images volume)
        Path("/agent-images/agent_profiles.yaml"),
        # Relative to project root (local dev)
        Path(__file__).resolve().parent.parent.parent / "agent-images" / "agent_profiles.yaml",
        # Env override
        Path(os.getenv("AGENT_PROFILES_PATH", "")),
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def load_profiles(force: bool = False) -> None:
    """Load profiles from YAML. Called once at startup."""
    global _PROFILES, _BASE_IMAGES, _LOADED, _YAML_PATH

    if _LOADED and not force:
        return

    path = _find_profiles_yaml()
    _YAML_PATH = path

    if path is None:
        logger.warning("agent_profiles.yaml not found — using empty profile list")
        _PROFILES = []
        _BASE_IMAGES = {}
        _LOADED = True
        return

    logger.info("Loading agent profiles from %s", path)
    with open(path) as f:
        data: Dict[str, Any] = yaml.safe_load(f) or {}

    raw_profiles = data.get("profiles", [])
    _PROFILES = [AgentProfile(**p) for p in raw_profiles]
    logger.info("Loaded %d agent profiles", len(_PROFILES))

    raw_images = data.get("base_images", {})
    _BASE_IMAGES = {k: BaseImageInfo(**v) for k, v in raw_images.items()}
    logger.info("Loaded %d base image definitions", len(_BASE_IMAGES))

    _LOADED = True


def _save_profiles() -> None:
    """Persist current in-memory profiles back to agent_profiles.yaml."""
    if _YAML_PATH is None:
        raise RuntimeError("Cannot save — agent_profiles.yaml location unknown")

    data: Dict[str, Any] = {
        "profiles": [p.model_dump() for p in _PROFILES],
        "base_images": {k: v.model_dump() for k, v in _BASE_IMAGES.items()},
    }

    with open(_YAML_PATH, "w") as f:
        f.write("# ═══════════════════════════════════════════════════════════════════════════\n")
        f.write("# TaskForge Agent Profiles Registry  (auto-generated — edits via API)\n")
        f.write("# ═══════════════════════════════════════════════════════════════════════════\n\n")
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    logger.info("Saved %d profiles to %s", len(_PROFILES), _YAML_PATH)


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------


def get_profiles() -> List[AgentProfile]:
    """Return all registered agent profiles."""
    load_profiles()
    return list(_PROFILES)


def get_profile(profile_id: str) -> Optional[AgentProfile]:
    """Look up a single profile by ID."""
    load_profiles()
    for p in _PROFILES:
        if p.id == profile_id:
            return p
    return None


def get_base_image_info(image_key: str) -> Optional[BaseImageInfo]:
    """Return base image metadata by key (e.g. 'nanobot')."""
    load_profiles()
    return _BASE_IMAGES.get(image_key)


def get_base_images() -> Dict[str, BaseImageInfo]:
    """Return all base image definitions."""
    load_profiles()
    return dict(_BASE_IMAGES)


# ---------------------------------------------------------------------------
# Write operations (CRUD)
# ---------------------------------------------------------------------------


def create_profile(profile: AgentProfile) -> AgentProfile:
    """Add a new profile. Raises ValueError if ID already exists."""
    load_profiles()
    if any(p.id == profile.id for p in _PROFILES):
        raise ValueError(f"Profile '{profile.id}' already exists")
    _PROFILES.append(profile)
    _save_profiles()
    return profile


def update_profile(profile_id: str, updates: Dict[str, Any]) -> Optional[AgentProfile]:
    """Update an existing profile. Returns updated profile or None if not found."""
    load_profiles()
    for i, p in enumerate(_PROFILES):
        if p.id == profile_id:
            current = p.model_dump()
            # Handle nested metadata updates
            if "metadata" in updates and isinstance(updates["metadata"], dict):
                current_meta = current.get("metadata", {})
                current_meta.update(updates["metadata"])
                updates["metadata"] = current_meta
            current.update(updates)
            current["id"] = profile_id  # prevent ID change
            _PROFILES[i] = AgentProfile(**current)
            _save_profiles()
            return _PROFILES[i]
    return None


def delete_profile(profile_id: str) -> bool:
    """Delete a profile by ID. Returns True if deleted, False if not found."""
    global _PROFILES
    load_profiles()
    before = len(_PROFILES)
    _PROFILES = [p for p in _PROFILES if p.id != profile_id]
    if len(_PROFILES) < before:
        _save_profiles()
        return True
    return False
