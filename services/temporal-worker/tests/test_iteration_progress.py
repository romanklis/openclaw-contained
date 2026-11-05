"""Iteration handoff and stagnation-signature helpers."""
from __future__ import annotations

import unittest

from worker import (
    _build_iteration_handoff,
    _find_repo_checkout,
    _iteration_failure_signature,
    _node_needs_repo_access,
    _scrub_git_credentials,
    _valid_capability_resource,
)


class IterationSignatureTests(unittest.TestCase):
    def test_timestamps_are_normalized(self):
        a = {"error": "2026-11-04 21:32:04 ERROR: Command timed out after 60s"}
        b = {"error": "2026-11-04 21:42:37 ERROR: Command timed out after 60s"}
        self.assertEqual(
            _iteration_failure_signature(a), _iteration_failure_signature(b)
        )

    def test_different_errors_do_not_collide(self):
        a = {"error": "pytest failed: assertion error"}
        b = {"error": "pytest failed: import error"}
        self.assertNotEqual(
            _iteration_failure_signature(a), _iteration_failure_signature(b)
        )

    def test_logs_fallback(self):
        result = {"error": "", "agent_logs": "line one\nlast line"}
        self.assertIn("last line", _iteration_failure_signature(result))


class IterationHandoffTests(unittest.TestCase):
    def test_handoff_preserves_workspace_and_timeout_guidance(self):
        result = {
            "error": "ERROR: Command timed out after 60s (process tree killed)",
            "agent_logs": "",
        }
        text = _build_iteration_handoff(result, 2)
        self.assertIn("PREVIOUS ITERATION 2", text)
        self.assertIn("persistent", text)
        self.assertIn("timeout_seconds=900", text)
        self.assertIn("timed out after 60s", text)

    def test_handoff_is_bounded(self):
        result = {"error": "x" * 10000}
        self.assertLess(len(_build_iteration_handoff(result, 1)), 2200)


class RepoAccessTests(unittest.TestCase):
    def test_capability_placeholders_are_rejected(self):
        self.assertFalse(_valid_capability_resource("<type>/<pkg>"))
        self.assertFalse(_valid_capability_resource("pip/<pkg>"))
        self.assertTrue(_valid_capability_resource("pip/fastapi"))
        self.assertTrue(_valid_capability_resource("apt/libglib2.0-0"))
        self.assertTrue(_valid_capability_resource("npm/@scope/pkg"))

    def test_git_capability_requires_repo_access(self):
        self.assertTrue(
            _node_needs_repo_access(
                {"required_capabilities": {"binaries": ["git"]}}, "Do the work"
            )
        )
        self.assertFalse(_node_needs_repo_access({}, "Write a local report"))

    def test_missing_checkout_is_rejected(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            result = _find_repo_checkout("test", "acme/widgets", root=tmp)
        self.assertFalse(result["ok"])
        self.assertIn("no_checkout_for_", result["error"])

    def test_matching_checkout_is_accepted_and_token_is_not_returned(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            git_dir = os.path.join(tmp, "repo", ".git")
            os.makedirs(git_dir)
            with open(os.path.join(git_dir, "config"), "w") as fh:
                fh.write(
                    "[remote \"origin\"]\n"
                    "\turl = https://x-access-token:secret@github.com/acme/widgets.git\n"
                )
            with open(os.path.join(git_dir, "HEAD"), "w") as fh:
                fh.write("ref: refs/heads/main\n")
            result = _find_repo_checkout("test", "acme/widgets", root=tmp)
        self.assertTrue(result["ok"])
        self.assertNotIn("secret", str(result))
        self.assertTrue(result["checkout"].endswith("repo"))


class CredentialScrubTests(unittest.TestCase):
    def test_git_remote_token_is_removed(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            git_dir = os.path.join(tmp, "repo", ".git")
            os.makedirs(git_dir)
            config = os.path.join(git_dir, "config")
            with open(config, "w") as fh:
                fh.write(
                    "[remote \"origin\"]\n"
                    "\turl = https://x-access-token:secret@github.com/acme/widgets.git\n"
                )
            self.assertEqual(_scrub_git_credentials(tmp), 1)
            with open(config) as fh:
                text = fh.read()
        self.assertNotIn("secret", text)
        self.assertIn("https://github.com/acme/widgets.git", text)


if __name__ == "__main__":
    unittest.main()
