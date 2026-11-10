"""Regression tests for task-vs-sibling output scoping (dag-f92f1254)."""
from __future__ import annotations

import unittest

from worker import _own_task_outputs


class OutputScopingTests(unittest.TestCase):
    def test_only_own_task_outputs_are_kept(self):
        outputs = [
            {"iteration": 1, "source_task_id": "task-a", "completed": "false"},
            {"iteration": 2, "source_task_id": "task-a", "completed": "true"},
            {"iteration": 5, "source_task_id": "task-b", "completed": "true"},
            {"iteration": 3, "task_id": "task-c", "completed": "true"},
        ]
        own = _own_task_outputs("task-b", outputs)
        self.assertEqual([o["iteration"] for o in own], [5])

    def test_fresh_task_has_no_outputs(self):
        outputs = [
            {"iteration": 5, "source_task_id": "task-sibling", "completed": "true"}
        ]
        self.assertEqual(_own_task_outputs("task-fresh", outputs), [])

    def test_missing_source_is_not_claimed(self):
        outputs = [{"iteration": 4, "completed": "true"}]
        self.assertEqual(_own_task_outputs("task-fresh", outputs), [])

    def test_task_id_fallback(self):
        outputs = [{"iteration": 2, "task_id": "task-own", "completed": "true"}]
        self.assertEqual(
            [o["iteration"] for o in _own_task_outputs("task-own", outputs)], [2]
        )


if __name__ == "__main__":
    unittest.main()
