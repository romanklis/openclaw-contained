"""Tests for GitHub push node normalization."""
from __future__ import annotations

import unittest

from planner import _normalize_github_push


def _node(description: str) -> dict:
    return {"node_id": "push", "description": description, "config": {}}


class GitHubPushNormalizationTests(unittest.TestCase):
    def test_push_node_gets_repo_scope(self):
        dag = {
            "nodes": [
                _node(
                    "Push the repo to https://github.com/romanklis/fake-video-detection.git "
                    "on branch main and verify the remote tree."
                )
            ]
        }
        _normalize_github_push(dag)
        push = dag["nodes"][0]["config"]["github_push"]
        self.assertTrue(push["enabled"])
        self.assertEqual(push["repository"], "romanklis/fake-video-detection")
        self.assertEqual(push["branch"], "main")
        self.assertEqual(push["credential"], "github")
        self.assertTrue(push["require_approval"])
        self.assertNotIn("token", push)

    def test_non_push_node_unchanged(self):
        dag = {"nodes": [_node("Implement the API and run pytest.")]}
        _normalize_github_push(dag)
        self.assertNotIn("github_push", dag["nodes"][0]["config"])

    def test_push_without_repo_does_not_guess(self):
        dag = {"nodes": [_node("Push the code to GitHub when ready.")]}
        _normalize_github_push(dag)
        self.assertNotIn("github_push", dag["nodes"][0]["config"])


if __name__ == "__main__":
    unittest.main()
