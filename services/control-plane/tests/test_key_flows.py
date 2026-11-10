"""Deterministic key-flow tests: exploration, lock, templates, workflows, blocks.

Run only against a disposable test database:
    DATABASE_URL=.../openclaw_test python -m unittest tests.test_key_flows -v
"""
from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch

TEST_DB_URL = os.getenv("DATABASE_URL", "")
if "test" not in TEST_DB_URL.lower():
    raise RuntimeError(
        "test_key_flows requires DATABASE_URL to point at a test database "
        "(for example openclaw_test); refusing to touch the primary database"
    )

from fastapi.testclient import TestClient  # noqa: E402

import explorations  # noqa: E402
import workflow_groups  # noqa: E402
from main import app  # noqa: E402
from tests import key_flow_support as support  # noqa: E402


BLOCK_SOURCE = """def main(inputs):
    return {"value": int(inputs.get("value", 0)) + 1}
"""


class KeyFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.skill_id = None

    @classmethod
    def tearDownClass(cls):
        try:
            cls.client.__exit__(None, None, None)
        except Exception:
            pass

    def _skill(self) -> str:
        if not self.skill_id:
            import asyncio

            self.__class__.skill_id = asyncio.run(support.seed_skill())
        return self.skill_id

    def _create_exploration(self) -> dict:
        objective = f"Research Acme workflow {uuid.uuid4().hex[:8]}"
        fake = support.fake_cycle_dag(self._skill())
        with patch.object(explorations, "plan_dag", AsyncMock(return_value=fake)), patch.object(
            workflow_groups,
            "generate_workflow_title",
            AsyncMock(return_value=("Test workflow", False)),
        ):
            response = self.client.post(
                "/api/explorations",
                data={"objective": objective, "model": "test-model"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _lock(self, dag_id: str) -> dict:
        response = self.client.post(
            f"/api/dags/{dag_id}/lock",
            json={
                "parameters": [
                    {
                        "key": "company",
                        "label": "Company",
                        "type": "string",
                        "default": "Acme",
                    }
                ]
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _create_block(self) -> str:
        block_id = f"keyflow-echo-{uuid.uuid4().hex[:8]}"
        response = self.client.post(
            "/api/blocks",
            json={
                "id": block_id,
                "name": "Key flow echo",
                "description": "Test block",
                "entrypoint": "main",
                "status": "active",
                "inputs_schema": {"type": "object"},
                "outputs_schema": {"type": "object"},
                "code": {"files": {"main.py": BLOCK_SOURCE}, "entry_file": "main.py"},
                "conformance": {"sample_input": {"value": 1}, "expect": {"value": 2}},
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def test_exploration_creates_cycle_and_is_visible(self):
        data = self._create_exploration()
        self.assertEqual(data["session"]["status"], "awaiting_run")
        self.assertEqual(data["cycle"]["status"], "proposed")
        self.assertTrue(data["dag_id"].startswith("dag-"))

        listing = self.client.get("/api/explorations")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertIn(data["session"]["id"], [item["id"] for item in listing.json()])

        detail = self.client.get(f"/api/dags/{data['dag_id']}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(len(detail.json()["nodes"]), 2)

    def test_lock_parameterizes_and_generalizes_template_skill(self):
        data = self._create_exploration()
        dag_id = data["dag_id"]
        locked = self._lock(dag_id)

        self.assertTrue(locked["locked"])
        self.assertIn("{company}", locked["objective"])
        self.assertIn("{company}", locked["nodes"][0]["description"])
        self.assertEqual(locked["template_params"][0]["key"], "company")
        self.assertIn(
            "{company}",
            locked["nodes"][0]["config"]["template_skill_instructions"],
        )

        templates = self.client.get(
            f"/api/skill-learning/template-skills?dag_id={dag_id}"
        )
        self.assertEqual(templates.status_code, 200, templates.text)
        self.assertEqual(len(templates.json()), 1)
        self.assertIn("{company}", templates.json()[0]["instructions"])

        group_id = data["session"]["workflow_group_id"]
        workflows = self.client.get("/api/workflows?project_id=__general__")
        self.assertEqual(workflows.status_code, 200, workflows.text)
        summary = next(item for item in workflows.json() if item["id"] == group_id)
        self.assertEqual(summary["stage"], "locked")
        self.assertTrue(summary["locked"])
        self.assertEqual(summary["mode"], "exploration")

        unlocked = self.client.post(f"/api/dags/{dag_id}/unlock")
        self.assertEqual(unlocked.status_code, 200, unlocked.text)
        self.assertFalse(unlocked.json()["locked"])

    def test_workflow_detail_and_instantiation(self):
        data = self._create_exploration()
        dag_id = data["dag_id"]
        self._lock(dag_id)
        support_dag = self.client.get(f"/api/dags/{dag_id}").json()
        source_node = support_dag["nodes"][0]["node_id"]

        import asyncio

        asyncio.run(support.seed_node_guidance(dag_id, source_node))

        response = self.client.post(
            f"/api/dags/{dag_id}/instantiate",
            json={
                "parameters": {"company": "Globex"},
                "auto_start": False,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        instance = response.json()
        self.assertEqual(instance["template_source_dag_id"], dag_id)
        self.assertIn("Globex", instance["objective"])
        self.assertNotIn("Acme", instance["objective"])

        by_id = {node["node_id"]: node for node in instance["nodes"]}
        self.assertIn("research-globex", by_id)
        self.assertIn("summarize-globex", by_id)
        self.assertIn("Globex", by_id["research-globex"]["description"])
        self.assertIn(
            "Globex",
            by_id["research-globex"]["config"]["template_skill_instructions"],
        )
        self.assertIn(
            "Globex",
            by_id["research-globex"]["config"]["template_guidance"],
        )
        self.assertEqual(
            by_id["summarize-globex"]["depends_on"], ["research-globex"]
        )
        self.assertEqual(
            by_id["summarize-globex"]["input_mapping"]["source"],
            "research-globex.output",
        )

        group_id = data["session"]["workflow_group_id"]
        detail = self.client.get(f"/api/workflows/{group_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(len(detail.json()["runs"]), 2)
        self.assertEqual(detail.json()["stage"], "locked")

    def test_promote_task_to_block_and_run_conformance(self):
        import asyncio

        task_id = asyncio.run(support.seed_task_with_python_deliverable())
        response = self.client.post(
            "/api/blocks/promote-from-task", json={"task_id": task_id}
        )
        self.assertEqual(response.status_code, 201, response.text)
        block = response.json()
        self.assertEqual(block["status"], "draft")
        self.assertIn("main.py", block["code"]["files"])

        activated = self.client.put(
            f"/api/blocks/{block['id']}", json={"status": "active"}
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        tested = self.client.post(
            f"/api/blocks/{block['id']}/test", json={"inputs": {"value": 2}}
        )
        self.assertEqual(tested.status_code, 200, tested.text)
        self.assertTrue(tested.json()["ok"])
        self.assertEqual(tested.json()["result"], {"value": 3})

        self.client.delete(f"/api/blocks/{block['id']}")

    def test_learn_block_from_run_uses_driver_source(self):
        import asyncio

        task_id = asyncio.run(support.seed_task_with_python_deliverable())
        spec = {
            "name": "learned echo",
            "description": "Learned deterministic echo",
            "code": {
                "files": {
                    "block_main.py": (
                        "def main(inputs):\n"
                        "    return {'value': int(inputs.get('value', 0)) + 1}\n"
                    )
                }
            },
            "inputs_schema": {"value": {"type": "integer"}},
            "outputs_schema": {"value": {"type": "integer"}},
        }
        with patch(
            "routers.blocks._call_block_derive_llm", AsyncMock(return_value=spec)
        ):
            response = self.client.post(
                "/api/blocks/learn-from-run", json={"task_id": task_id}
            )
        self.assertEqual(response.status_code, 201, response.text)
        block = response.json()["block"]
        self.assertEqual(block["status"], "draft")
        self.assertIn("block_main.py", block["code"]["files"])
        self.assertIn("main.py", block["code"]["files"])
        self.assertIn(task_id, block["description"])
        self.client.delete(f"/api/blocks/{block['id']}")

    def test_learn_block_from_run_accepts_top_level_block_main(self):
        import asyncio

        task_id = asyncio.run(support.seed_task_with_python_deliverable())
        spec = {
            "name": "learned echo top level",
            "description": "Learned deterministic echo",
            "block_main": (
                "def main(inputs):\n"
                "    return {'value': int(inputs.get('value', 0)) + 1}\n"
            ),
            "inputs_schema": {"type": "object", "properties": {"value": {"type": "integer"}}},
            "outputs_schema": {"type": "object", "properties": {"value": {"type": "integer"}}},
        }
        with patch(
            "routers.blocks._call_block_derive_llm", AsyncMock(return_value=spec)
        ):
            response = self.client.post(
                "/api/blocks/learn-from-run", json={"task_id": task_id}
            )
        self.assertEqual(response.status_code, 201, response.text)
        block = response.json()["block"]
        self.assertIn("block_main.py", block["code"]["files"])
        self.assertIn("main.py", block["code"]["files"])
        self.client.delete(f"/api/blocks/{block['id']}")

    def test_learn_block_from_run_returns_502_on_llm_error(self):
        import asyncio

        task_id = asyncio.run(support.seed_task_with_python_deliverable())
        with patch(
            "routers.blocks._call_block_derive_llm",
            AsyncMock(return_value={"error": "LLM returned no parseable JSON block spec"}),
        ):
            response = self.client.post(
                "/api/blocks/learn-from-run", json={"task_id": task_id}
            )
        self.assertEqual(response.status_code, 502, response.text)

    def test_coerce_block_spec_merges_driver_files(self):
        from routers.blocks import _coerce_block_spec

        coerced = _coerce_block_spec(
            {
                "name": "merge test",
                "block_main": "def main(inputs):\n    return {'ok': True}\n",
                "inputs_schema": {"type": "object", "properties": {}},
                "outputs_schema": {"type": "object", "properties": {}},
            },
            "merge-test",
            driver_files={"driver.py": "def helper():\n    return 1\n"},
        )
        self.assertIn("driver.py", coerced["files"])
        self.assertIn("block_main.py", coerced["files"])

    def test_coerce_block_spec_requires_block_main_with_driver(self):
        from routers.blocks import _coerce_block_spec

        with self.assertRaises(ValueError):
            _coerce_block_spec(
                {"name": "missing wrapper"},
                "missing-wrapper",
                driver_files={"driver.py": "def helper():\n    return 1\n"},
            )

    def test_llm_content_text_handles_missing_choices(self):
        from routers.blocks import _llm_content_text

        self.assertEqual(_llm_content_text({"choices": []}), "")
        self.assertEqual(
            _llm_content_text({"choices": [{"message": {"content": '{"name":"x"}'}}]}),
            '{"name":"x"}',
        )

    def test_parse_block_derive_response_marker_format(self):
        from routers.blocks import _parse_block_derive_response

        content = """===META===
{"name":"x","description":"d","inputs_schema":{"type":"object","properties":{}},"outputs_schema":{"type":"object","properties":{}}}
===BLOCK_MAIN===
def main(inputs):
    value = inputs.get("value", 0)
    return {"value": value + 1, "note": "braces { } and quotes"}
===END===
"""
        parsed = _parse_block_derive_response(content)
        self.assertIsNotNone(parsed)
        self.assertIn("def main(inputs):", parsed["block_main"])
        self.assertIn('"value"', parsed["block_main"])

    def test_parse_block_derive_response_json_fallback(self):
        from routers.blocks import _parse_block_derive_response

        content = '{"name":"x","block_main":"def main(inputs):\\n    return {}\\n"}'
        parsed = _parse_block_derive_response(content)
        self.assertIsNotNone(parsed)
        self.assertIn("def main", parsed["block_main"])

    def _create_manual_chain(self) -> str:
        response = self.client.post(
            "/api/dags/manual",
            json={
                "objective": f"Editable chain {uuid.uuid4().hex[:8]}",
                "nodes": [
                    {
                        "node_id": "a",
                        "description": "A",
                        "depends_on": [],
                        "config": {"base_image": "openclaw"},
                    },
                    {
                        "node_id": "b",
                        "description": "B",
                        "depends_on": ["a"],
                        "config": {"base_image": "openclaw"},
                    },
                    {
                        "node_id": "c",
                        "description": "C",
                        "depends_on": ["b"],
                        "config": {"base_image": "openclaw"},
                    },
                ],
                "edges": [
                    {"from_node": "a", "to_node": "b"},
                    {"from_node": "b", "to_node": "c"},
                ],
                "default_image": "openclaw",
                "default_llm": "test-model",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        dag_id = response.json()["id"]
        patched = self.client.patch(f"/api/dags/{dag_id}", json={"status": "completed"})
        self.assertEqual(patched.status_code, 200, patched.text)
        for node_id in ("a", "b", "c"):
            node_patch = self.client.patch(
                f"/api/dags/{dag_id}/nodes/{node_id}", json={"status": "completed"}
            )
            self.assertEqual(node_patch.status_code, 200, node_patch.text)
        return dag_id

    def test_replace_step_with_block_on_completed_dag(self):
        dag_id = self._create_manual_chain()
        block_id = self._create_block()
        response = self.client.post(
            f"/api/dags/{dag_id}/nodes/b/replace-with-block",
            json={"block_id": block_id, "inputs": {"value": 7}},
        )
        self.assertEqual(response.status_code, 200, response.text)
        detail = response.json()
        self.assertEqual(detail["status"], "ready")
        nodes = {node["node_id"]: node for node in detail["nodes"]}
        self.assertEqual(nodes["a"]["status"], "completed")
        self.assertEqual(nodes["b"]["status"], "pending")
        self.assertEqual(nodes["c"]["status"], "pending")
        self.assertEqual(nodes["b"]["node_type"], "block")
        self.assertEqual(nodes["b"]["config"]["block"], block_id)
        self.assertEqual(nodes["b"]["config"]["inputs"], {"value": 7})
        self.assertIsNone(nodes["b"]["task_id"])
        self.assertIsNone(nodes["b"]["selected_skill_v2_id"])
        self.assertEqual(nodes["c"]["depends_on"], ["b"])
        self.client.post(f"/api/dags/{dag_id}/archive")
        self.client.delete(f"/api/blocks/{block_id}")

    def test_delete_step_on_completed_dag_resets_downstream(self):
        dag_id = self._create_manual_chain()
        response = self.client.delete(f"/api/dags/{dag_id}/nodes/b")
        self.assertEqual(response.status_code, 200, response.text)
        detail = response.json()
        self.assertEqual(detail["status"], "ready")
        nodes = {node["node_id"]: node for node in detail["nodes"]}
        self.assertNotIn("b", nodes)
        self.assertEqual(nodes["a"]["status"], "completed")
        self.assertEqual(nodes["c"]["status"], "pending")
        self.assertEqual(nodes["c"]["depends_on"], ["a"])
        self.client.post(f"/api/dags/{dag_id}/archive")

    def test_replace_step_with_missing_block_returns_404(self):
        dag_id = self._create_manual_chain()
        response = self.client.post(
            f"/api/dags/{dag_id}/nodes/b/replace-with-block",
            json={"block_id": "missing-block"},
        )
        self.assertEqual(response.status_code, 404, response.text)
        self.client.post(f"/api/dags/{dag_id}/archive")

    def test_add_block_node_to_dag(self):
        block_id = self._create_block()
        created = self.client.post(
            "/api/dags/manual",
            json={
                "objective": f"Block composition {uuid.uuid4().hex[:8]}",
                "nodes": [
                    {
                        "node_id": "entry",
                        "description": "Entry node",
                        "depends_on": [],
                        "config": {"base_image": "openclaw"},
                    }
                ],
                "edges": [],
                "default_image": "openclaw",
                "default_llm": "test-model",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        dag_id = created.json()["id"]
        response = self.client.post(
            f"/api/dags/{dag_id}/nodes",
            json={
                "node_id": "echo-block",
                "node_type": "block",
                "description": "Run the deterministic echo block",
                "depends_on": ["entry"],
                "config": {
                    "type": "block",
                    "block": block_id,
                    "inputs": {"value": 4},
                },
                "input_mapping": {},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        node = next(
            item for item in response.json()["nodes"] if item["node_id"] == "echo-block"
        )
        self.assertEqual(node["node_type"], "block")
        self.assertEqual(node["config"]["block"], block_id)

        self.client.post(f"/api/dags/{dag_id}/archive")
        self.client.delete(f"/api/blocks/{block_id}")


if __name__ == "__main__":
    unittest.main()
