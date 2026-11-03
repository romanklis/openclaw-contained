"""Tests for package-aware FastAPI deployment entrypoints."""
from __future__ import annotations

import os
import tempfile
import unittest

from explorations_deploy import (
    _fastapi_entrypoint,
    normalize_uvicorn_entrypoint,
)


class EntrypointTests(unittest.TestCase):
    def _write(self, root: str, rel: str) -> None:
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write("")

    def test_package_app_uses_package_root(self):
        with tempfile.TemporaryDirectory() as root:
            self._write(root, "api-and-pipeline-wiring/app/__init__.py")
            self._write(root, "api-and-pipeline-wiring/app/main.py")
            entrypoint = _fastapi_entrypoint(
                root, "api-and-pipeline-wiring/app/main.py", 8000
            )
            self.assertEqual(
                entrypoint,
                'sh -c "cd api-and-pipeline-wiring && uvicorn app.main:app --host 0.0.0.0 --port 8000"',
            )

    def test_flat_app_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            self._write(root, "main.py")
            entrypoint = _fastapi_entrypoint(root, "main.py", 8000)
            self.assertEqual(
                entrypoint, "uvicorn main:app --host 0.0.0.0 --port 8000"
            )

    def test_nested_packages(self):
        with tempfile.TemporaryDirectory() as root:
            self._write(root, "backend/__init__.py")
            self._write(root, "backend/app/__init__.py")
            self._write(root, "backend/app/main.py")
            entrypoint = _fastapi_entrypoint(root, "backend/app/main.py", 8000)
            self.assertEqual(
                entrypoint, "uvicorn backend.app.main:app --host 0.0.0.0 --port 8000"
            )

    def test_directory_without_init_keeps_old_behavior(self):
        with tempfile.TemporaryDirectory() as root:
            self._write(root, "api/main.py")
            entrypoint = _fastapi_entrypoint(root, "api/main.py", 8000)
            self.assertEqual(
                entrypoint,
                'sh -c "cd api && uvicorn main:app --host 0.0.0.0 --port 8000"',
            )

    def test_normalizer_repairs_failed_deployment_entrypoint(self):
        with tempfile.TemporaryDirectory() as root:
            self._write(root, "api-and-pipeline-wiring/app/__init__.py")
            self._write(root, "api-and-pipeline-wiring/app/main.py")
            broken = (
                'sh -c "cd api-and-pipeline-wiring/app && uvicorn main:app '
                '--host 0.0.0.0 --port 8000"'
            )
            fixed = normalize_uvicorn_entrypoint(broken, root)
            self.assertEqual(
                fixed,
                'sh -c "cd api-and-pipeline-wiring && uvicorn app.main:app '
                '--host 0.0.0.0 --port 8000"',
            )

    def test_normalizer_leaves_flat_command(self):
        with tempfile.TemporaryDirectory() as root:
            self._write(root, "main.py")
            command = "uvicorn main:app --host 0.0.0.0 --port 8000"
            self.assertEqual(normalize_uvicorn_entrypoint(command, root), command)


if __name__ == "__main__":
    unittest.main()
