"""Planner propagation of DAG-level GitHub repository access."""
from __future__ import annotations

import unittest

from planner import _normalize_github_push


class GitHubAccessPlanningTests(unittest.TestCase):
    def test_upstream_repo_nodes_get_github_access(self):
        dag = {
            "nodes": [
                {
                    "node_id": "clone",
                    "description": "Clone https://github.com/acme/widgets and inspect it.",
                    "config": {"required_capabilities": {"binaries": ["git"]}},
                },
                {
                    "node_id": "integrate",
                    "description": "Edit the cloned repository in place.",
                    "config": {"deliverable_mode": "in_place"},
                },
                {
                    "node_id": "tests",
                    "description": "Run the repository test suite.",
                    "config": {"deliverable_mode": "in_place"},
                },
                {
                    "node_id": "push",
                    "description": "git push the result to https://github.com/acme/widgets.",
                    "config": {},
                },
            ],
            "edges": [
                {"from_node": "clone", "to_node": "integrate"},
                {"from_node": "integrate", "to_node": "tests"},
                {"from_node": "tests", "to_node": "push"},
            ],
        }
        normalized = _normalize_github_push(dag)
        nodes = {node["node_id"]: node for node in normalized["nodes"]}

        self.assertTrue(nodes["push"]["config"]["github_push"]["enabled"])
        for node_id in ("clone", "integrate", "tests"):
            access = nodes[node_id]["config"].get("github_access")
            self.assertIsNotNone(access, node_id)
            self.assertEqual(access["repository"], "acme/widgets")
            self.assertEqual(access["branch"], "main")
            self.assertEqual(nodes[node_id]["config"]["repo_dir"], "/workspace/repo")

    def test_does_not_guess_access_for_unrelated_nodes(self):
        dag = {
            "nodes": [
                {"node_id": "research", "description": "Write a report.", "config": {}},
                {
                    "node_id": "push",
                    "description": "Push https://github.com/acme/widgets.",
                    "config": {},
                },
            ],
            "edges": [{"from_node": "research", "to_node": "push"}],
        }
        normalized = _normalize_github_push(dag)
        self.assertNotIn("github_access", normalized["nodes"][0]["config"])

    def test_ambiguous_push_scopes_are_not_propagated(self):
        dag = {
            "nodes": [
                {
                    "node_id": "clone",
                    "description": "Clone https://github.com/acme/widgets.",
                    "config": {"required_capabilities": {"binaries": ["git"]}},
                },
                {
                    "node_id": "push-a",
                    "description": "Push https://github.com/acme/widgets.",
                    "config": {
                        "github_push": {
                            "enabled": True,
                            "repository": "acme/widgets",
                            "branch": "main",
                        }
                    },
                },
                {
                    "node_id": "push-b",
                    "description": "Push https://github.com/other/thing.",
                    "config": {
                        "github_push": {
                            "enabled": True,
                            "repository": "other/thing",
                            "branch": "main",
                        }
                    },
                },
            ],
            "edges": [
                {"from_node": "clone", "to_node": "push-a"},
                {"from_node": "clone", "to_node": "push-b"},
            ],
        }
        normalized = _normalize_github_push(dag)
        self.assertNotIn("github_access", normalized["nodes"][0]["config"])


if __name__ == "__main__":
    unittest.main()
