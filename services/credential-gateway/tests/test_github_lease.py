"""Tests for repo/branch-scoped GitHub credential leases."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from github_lease import (  # noqa: E402
    GitHubLeaseError,
    build_github_lease,
    normalize_repository,
)


class GitHubLeaseTests(unittest.TestCase):
    profile = {
        "provider": "github",
        "repository": "romanklis/fake-video-detection",
        "branch": "main",
        "username": "x-access-token",
        "token": "github_pat_test",
    }

    def test_valid_lease(self):
        lease = build_github_lease(
            self.profile,
            "https://github.com/romanklis/fake-video-detection.git",
            "main",
        )
        self.assertEqual(lease["username"], "x-access-token")
        self.assertEqual(lease["password"], "github_pat_test")
        self.assertEqual(lease["repository"], "romanklis/fake-video-detection")
        self.assertEqual(lease["branch"], "main")
        self.assertIsNone(lease["expires_at"])

    def test_repository_normalization(self):
        self.assertEqual(
            normalize_repository("git@github.com:Owner/Repo.git"),
            "owner/repo",
        )
        self.assertEqual(
            normalize_repository("https://github.com/Owner/Repo/"),
            "owner/repo",
        )

    def test_wrong_repository_denied(self):
        with self.assertRaises(GitHubLeaseError) as ctx:
            build_github_lease(self.profile, "other/private-repo", "main")
        self.assertEqual(ctx.exception.code, "GITHUB_REPOSITORY_DENIED")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_wrong_branch_denied(self):
        with self.assertRaises(GitHubLeaseError) as ctx:
            build_github_lease(self.profile, self.profile["repository"], "dev")
        self.assertEqual(ctx.exception.code, "GITHUB_BRANCH_DENIED")

    def test_non_github_credential_denied(self):
        with self.assertRaises(GitHubLeaseError) as ctx:
            build_github_lease(
                {"provider": "basic", "password": "x"},
                self.profile["repository"],
                "main",
            )
        self.assertEqual(ctx.exception.code, "GITHUB_CREDENTIAL_PROVIDER")

    def test_missing_token_denied(self):
        profile = dict(self.profile)
        profile.pop("token")
        with self.assertRaises(GitHubLeaseError) as ctx:
            build_github_lease(profile, self.profile["repository"], "main")
        self.assertEqual(ctx.exception.code, "GITHUB_CREDENTIAL_TOKEN")


if __name__ == "__main__":
    unittest.main()
