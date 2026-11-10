"""Tests for exploration deployment package parsing."""
from __future__ import annotations

import unittest

from deployment_service import _parse_package_text
from explorations_deploy import derive_app_path


class DeploymentPackageTests(unittest.TestCase):
    def test_derive_app_path_from_package_fields(self):
        package = {
            "app_dir": "api-and-pipeline-wiring",
            "app_module": "app.main:app",
            "entrypoint": 'sh -c "cd api-and-pipeline-wiring && uvicorn app.main:app"',
            "port": 8000,
        }
        self.assertEqual(
            derive_app_path(package), "api-and-pipeline-wiring/app/main.py"
        )

    def test_derive_app_path_from_entrypoint(self):
        package = {
            "entrypoint": 'sh -c "cd scaffold-backend/backend && uvicorn app.main:app --port 8000"',
            "port": 8000,
        }
        self.assertEqual(
            derive_app_path(package), "scaffold-backend/backend/app/main.py"
        )

    def test_parse_valid_package(self):
        package = _parse_package_text(
            '{"entrypoint":"uvicorn app.main:app","port":8000,"app":"demo"}'
        )
        self.assertIsNotNone(package)
        self.assertEqual(package["port"], 8000)

    def test_parse_rejects_missing_fields(self):
        self.assertIsNone(_parse_package_text('{"app":"demo"}'))
        self.assertIsNone(_parse_package_text("not json"))


if __name__ == "__main__":
    unittest.main()
