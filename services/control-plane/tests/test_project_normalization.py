"""Project sentinel normalization for DAG persistence."""
from __future__ import annotations

import asyncio
import unittest

from models import MasterDAG
from workflow_groups import normalize_project_id, resolve_project_id


class _FakeProjectDB:
    async def get(self, _model, project_id):
        return {"id": project_id} if project_id == "real-project" else None


class NormalizeProjectIdTests(unittest.TestCase):
    def test_empty_becomes_none(self):
        self.assertIsNone(normalize_project_id(""))
        self.assertIsNone(normalize_project_id(None))

    def test_general_sentinel_becomes_none(self):
        self.assertIsNone(normalize_project_id("__general__"))

    def test_real_project_is_preserved(self):
        self.assertEqual(normalize_project_id("real-project"), "real-project")

    def test_model_rejects_sentinel(self):
        dag = MasterDAG(project_id="__general__")
        self.assertIsNone(dag.project_id)

    def test_model_preserves_real_project(self):
        dag = MasterDAG(project_id="real-project")
        self.assertEqual(dag.project_id, "real-project")


class ResolveProjectIdTests(unittest.TestCase):
    def test_missing_project_becomes_unassigned(self):
        self.assertIsNone(
            asyncio.run(resolve_project_id("deleted-project", _FakeProjectDB()))
        )

    def test_existing_project_is_preserved(self):
        self.assertEqual(
            asyncio.run(resolve_project_id("real-project", _FakeProjectDB())),
            "real-project",
        )

    def test_sentinel_does_not_query(self):
        self.assertIsNone(
            asyncio.run(resolve_project_id("__general__", _FakeProjectDB()))
        )


if __name__ == "__main__":
    unittest.main()
