"""Tests for in-place node normalization (exp-ca99e842 cycle 2)."""
from __future__ import annotations

import unittest

from planner import _looks_like_web_fetch, _normalize_deliverable_mode


def _node(description: str, **config):
    return {"node_id": "n", "description": description, "config": dict(config)}


class DeliverableModeTests(unittest.TestCase):
    def test_in_place_fix_is_marked(self):
        dag = {
            "nodes": [
                _node(
                    "Fix the UI-serving gap in api-and-pipeline-wiring "
                    "(reuse existing assets, no rewrite). Edit app/main.py and "
                    "rewrite the frontend fetch URLs, then run pytest."
                )
            ]
        }
        _normalize_deliverable_mode(dag)
        config = dag["nodes"][0]["config"]
        self.assertEqual(config["deliverable_mode"], "in_place")
        self.assertFalse(config["deliverable_gate"]["require_deliverables"])
        self.assertFalse(config["deliverable_gate"]["require_real_sources"])

    def test_new_artifact_stays_strict(self):
        dag = {"nodes": [_node("Create a new PDF report and plot the results.")]}
        _normalize_deliverable_mode(dag)
        self.assertNotIn("deliverable_mode", dag["nodes"][0]["config"])

    def test_create_from_scratch_is_not_in_place(self):
        dag = {"nodes": [_node("Create a new app from scratch and edit main.py.")]}
        _normalize_deliverable_mode(dag)
        self.assertNotIn("deliverable_mode", dag["nodes"][0]["config"])

    def test_explicit_planner_flag_is_honored(self):
        dag = {
            "nodes": [
                _node(
                    "Apply the changes.",
                    deliverable_mode="in_place",
                    deliverable_gate={"enabled": True, "require_deliverables": True},
                )
            ]
        }
        _normalize_deliverable_mode(dag)
        gate = dag["nodes"][0]["config"]["deliverable_gate"]
        self.assertFalse(gate["require_deliverables"])
        self.assertFalse(gate["require_real_sources"])


class FetchDetectionTests(unittest.TestCase):
    def test_ui_fetch_urls_are_not_research(self):
        self.assertFalse(
            _looks_like_web_fetch("Rewrite the frontend fetch URLs in app.js")
        )

    def test_research_phrases_are_fetch(self):
        self.assertTrue(_looks_like_web_fetch("Scrape the web for auction prices"))
        self.assertTrue(_looks_like_web_fetch("Fetch data from the annual report"))

    def test_browser_image_is_fetch(self):
        self.assertTrue(_looks_like_web_fetch("Open the page", "browser_v4"))


if __name__ == "__main__":
    unittest.main()
