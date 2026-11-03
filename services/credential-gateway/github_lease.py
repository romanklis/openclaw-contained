"""GitHub credential lease validation.

Agents never call this module. The temporal worker leases a GitHub credential
from the gateway for a specific authorized DAG node, then injects it as
GITHUB_TOKEN into that ephemeral agent container.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional


class GitHubLeaseError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def normalize_repository(value: str) -> str:
    ref = (value or "").strip()
    for prefix in (
        "https://github.com/",
        "http://github.com/",
        "git@github.com:",
        "ssh://git@github.com/",
    ):
        if ref.lower().startswith(prefix):
            ref = ref[len(prefix):]
            break
    if ref.lower().endswith(".git"):
        ref = ref[:-4]
    return ref.strip("/").lower()


def build_github_lease(
    profile: Dict[str, Any],
    repository: str,
    branch: str = "main",
) -> Dict[str, Any]:
    """Validate a stored GitHub credential and return a git credential tuple.

    The credential profile must be scoped to the requested repository and
    branch. The caller (the worker) is responsible for only leasing for an
    authorized node; this function enforces the credential's own allowlist.
    """
    if str(profile.get("provider") or "").strip().lower() != "github":
        raise GitHubLeaseError(
            "GITHUB_CREDENTIAL_PROVIDER",
            "credential is not a GitHub credential",
            status_code=400,
        )

    stored_repo = normalize_repository(str(profile.get("repository") or ""))
    requested_repo = normalize_repository(repository)
    repository_pattern = r"[a-z0-9_.-]+/[a-z0-9_.-]+"
    if not re.fullmatch(repository_pattern, stored_repo):
        raise GitHubLeaseError(
            "GITHUB_CREDENTIAL_REPOSITORY",
            "credential repository must look like owner/repo",
            status_code=400,
        )
    if not re.fullmatch(repository_pattern, requested_repo):
        raise GitHubLeaseError(
            "GITHUB_REPOSITORY_INVALID",
            "requested repository must look like owner/repo",
            status_code=400,
        )
    if stored_repo != requested_repo:
        raise GitHubLeaseError(
            "GITHUB_REPOSITORY_DENIED",
            f"credential is not allowed for repository '{repository}'",
            status_code=403,
        )

    stored_branch = str(profile.get("branch") or "main").strip()
    requested_branch = str(branch or stored_branch).strip()
    if requested_branch != stored_branch:
        raise GitHubLeaseError(
            "GITHUB_BRANCH_DENIED",
            f"credential is not allowed for branch '{requested_branch}'",
            status_code=403,
        )

    token = str(profile.get("token") or profile.get("password") or "").strip()
    if not token:
        raise GitHubLeaseError(
            "GITHUB_CREDENTIAL_TOKEN",
            "credential has no token",
            status_code=409,
        )

    username = str(profile.get("username") or "x-access-token").strip()
    return {
        "username": username,
        "password": token,
        "repository": stored_repo,
        "branch": stored_branch,
        # A static PAT has no expiry; the Phase 2 GitHub App lease will set this.
        "expires_at": None,
    }
