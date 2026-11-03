"""Gate behavior for in-place modification nodes."""
from __future__ import annotations

import asyncio
import unittest

from worker import evaluate_node_gate


class InPlaceGateTests(unittest.TestCase):
    def test_in_place_passes_without_deliverables(self):
        output = {"status": "completed", "completed": "true", "deliverables": None}
        config = {
            "deliverable_mode": "in_place",
            "verification_mode": "deterministic",
            "deliverable_gate": {
                "enabled": True,
                "require_deliverables": False,
                "require_real_sources": False,
            },
        }
        result = asyncio.run(evaluate_node_gate("task-test", output, config))
        self.assertTrue(result["valid"], result.get("reason"))

    def test_default_still_requires_deliverables(self):
        output = {"status": "completed", "completed": "true", "deliverables": None}
        config = {
            "verification_mode": "deterministic",
            "deliverable_gate": {"enabled": True},
        }
        result = asyncio.run(evaluate_node_gate("task-test", output, config))
        self.assertFalse(result["valid"])
        self.assertIn("no deliverables", result.get("reason", ""))


if __name__ == "__main__":
    unittest.main()
