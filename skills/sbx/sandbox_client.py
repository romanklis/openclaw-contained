"""Small stdlib client for the TaskForge agent sandbox."""
from __future__ import annotations

import io
import json
import os
import tarfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


class SandboxError(RuntimeError):
    pass


class SandboxClient:
    def __init__(self, base_url: Optional[str] = None, task_id: Optional[str] = None,
                 token: Optional[str] = None, admin_token: Optional[str] = None,
                 timeout: float = 30.0) -> None:
        self.base_url = (base_url or os.getenv("SANDBOX_URL", "http://sandbox:8080")).rstrip("/")
        self.task_id = task_id or os.getenv("TASK_ID", "")
        self.token = token or (f"task:{self.task_id}" if self.task_id else "")
        self.admin_token = admin_token or ""
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        if self.admin_token:
            return {"X-Sandbox-Token": self.admin_token}
        if not self.token:
            raise SandboxError("set TASK_ID or pass token")
        return {"X-Agent-Token": self.token}

    def _request(self, method: str, path: str, data: Optional[bytes] = None,
                 content_type: Optional[str] = None) -> bytes:
        req = urllib.request.Request(
            f"{self.base_url}{path}", data=data, method=method, headers=self._headers()
        )
        if content_type:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            raise SandboxError(f"{method} {path}: {exc.code} {exc.read().decode('utf-8', 'replace')}") from exc

    def _multipart(self, fields: Dict[str, str], context_tar: Path) -> tuple[bytes, str]:
        boundary = f"----sbx{uuid.uuid4().hex}"
        chunks: List[bytes] = []
        for name, value in fields.items():
            chunks.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
            )
        payload = context_tar.read_bytes()
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"context\"; filename=\"context.tar\"\r\n"
            "Content-Type: application/x-tar\r\n\r\n".encode()
        )
        chunks.append(payload)
        chunks.append(f"\r\n--{boundary}--\r\n".encode())
        return b"".join(chunks), f"multipart/form-data; boundary={boundary}"

    def submit(self, context_dir: str | Path, command: str = "", mode: str = "job",
               tests: Optional[List[str]] = None, artifacts: Optional[List[str]] = None,
               dockerfile: str = "Dockerfile", ttl_seconds: int = 900,
               cpu: float = 2, memory_mb: int = 4096, ports: Optional[List[int]] = None) -> Dict[str, Any]:
        root = Path(context_dir)
        if not (root / dockerfile).is_file():
            raise SandboxError(f"{root / dockerfile} is missing")
        with io.BytesIO() as buf:
            with tarfile.open(fileobj=buf, mode="w") as archive:
                for item in root.iterdir():
                    archive.add(item, arcname=item.name)
            upload = Path("/tmp") / f".sbx-{uuid.uuid4().hex}.tar"
            upload.write_bytes(buf.getvalue())
        fields = {
            "task_id": self.task_id,
            "mode": mode,
            "dockerfile": dockerfile,
            "command": command,
            "tests": json.dumps(tests or []),
            "artifacts": json.dumps(artifacts or []),
            "ports": json.dumps(ports or []),
            "ttl_seconds": str(ttl_seconds),
            "cpu": str(cpu),
            "memory_mb": str(memory_mb),
        }
        try:
            body, ctype = self._multipart(fields, upload)
            return json.loads(self._request("POST", "/v1/jobs", body, ctype))
        finally:
            upload.unlink(missing_ok=True)

    def get(self, job_id: str) -> Dict[str, Any]:
        return json.loads(self._request("GET", f"/v1/jobs/{job_id}"))

    def wait(self, job_id: str, timeout: float = 900.0, interval: float = 2.0) -> Dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.get(job_id)
            if job.get("status") in ("completed", "failed", "cancelled"):
                return job
            time.sleep(interval)
        raise SandboxError(f"job {job_id} did not finish in {timeout}s")

    def download(self, job_id: str, path: str, dest: str | Path) -> Path:
        data = self._request("GET", f"/v1/jobs/{job_id}/artifacts/{path.lstrip('/')}")
        out = Path(dest)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return out

    def cancel(self, job_id: str) -> Dict[str, Any]:
        return json.loads(self._request("POST", f"/v1/jobs/{job_id}/cancel"))
