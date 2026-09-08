"""Credential stores for the credential gateway.

Credential material never leaves this service. Two backends share one shape:

    {name, kind: form|basic|bearer|header|cookie,
     username?, password?, token?,
     header_name?, header_value?,       # kind=header
     cookie_name?, cookie_value?,        # kind=cookie
     login_url?, username_field?, password_field?,  # kind=form
     allowed_origins: [...], allowed_methods: [...]}

The OpenBao backend is used when OPENBAO_ENABLED=true and OPENBAO_TOKEN is set;
otherwise the local JSON-file store under CREDENTIAL_DIR is used.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

DEFAULT_ALLOWED_METHODS = ["GET", "POST"]


class CredentialNotFound(Exception):
    pass


class CredentialStore:
    def get(self, name: str) -> Dict[str, Any]:  # pragma: no cover - interface
        raise NotImplementedError

    def list_names(self) -> List[str]:  # pragma: no cover - interface
        raise NotImplementedError

    def save(self, name: str, profile: Dict[str, Any]) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def delete(self, name: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class FileCredentialStore(CredentialStore):
    """Loads ``{name}.json`` files from CREDENTIAL_DIR."""

    def __init__(self, directory: str):
        self._dir = directory

    def _path(self, name: str) -> str:
        return os.path.join(self._dir, f"{name}.json")

    def get(self, name: str) -> Dict[str, Any]:
        path = self._path(name)
        if not os.path.isfile(path):
            raise CredentialNotFound(name)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return self._normalize(name, data)

    def list_names(self) -> List[str]:
        if not os.path.isdir(self._dir):
            return []
        return sorted(fn[:-5] for fn in os.listdir(self._dir) if fn.endswith(".json"))

    def save(self, name: str, profile: Dict[str, Any]) -> None:
        os.makedirs(self._dir, exist_ok=True)
        data = dict(profile or {})
        data["name"] = name
        data.setdefault("kind", "basic")
        data.setdefault("allowed_methods", list(DEFAULT_ALLOWED_METHODS))
        data.setdefault("allowed_origins", [])
        with open(self._path(name), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def delete(self, name: str) -> None:
        path = self._path(name)
        if os.path.isfile(path):
            os.remove(path)

    @staticmethod
    def _normalize(name: str, data: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(data or {})
        out["name"] = out.get("name") or name
        out.setdefault("kind", out.get("authentication", {}).get("type", "basic"))
        out.setdefault("allowed_methods", list(DEFAULT_ALLOWED_METHODS))
        out.setdefault("allowed_origins", [])
        return out


class OpenBaoCredentialStore(CredentialStore):
    """Reads secrets from OpenBao KV v2 at secret/data/credentials/{name}."""

    def __init__(self, addr: str, token: str):
        self._addr = addr.rstrip("/")
        self._token = token

    def _read(self, name: str) -> Dict[str, Any]:
        import httpx

        url = f"{self._addr}/v1/secret/data/credentials/{name}"
        resp = httpx.get(url, headers={"X-Vault-Token": self._token}, timeout=15)
        if resp.status_code == 404:
            raise CredentialNotFound(name)
        if resp.status_code != 200:
            raise RuntimeError(f"OpenBao read failed HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json().get("data", {}).get("data", {})
        return dict(data or {})

    def get(self, name: str) -> Dict[str, Any]:
        data = self._read(name)
        out = dict(data)
        out["name"] = out.get("name") or name
        out.setdefault("kind", out.get("authentication", {}).get("type", "basic"))
        out.setdefault("allowed_methods", list(DEFAULT_ALLOWED_METHODS))
        out.setdefault("allowed_origins", [])
        return out

    def list_names(self) -> List[str]:
        import httpx

        resp = httpx.get(
            f"{self._addr}/v1/secret/metadata/credentials?list=true",
            headers={"X-Vault-Token": self._token},
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        keys = resp.json().get("data", {}).get("keys", [])
        return [k.rstrip("/") for k in keys if k.rstrip("/")]

    def save(self, name: str, profile: Dict[str, Any]) -> None:
        import httpx

        data = dict(profile or {})
        data["name"] = name
        url = f"{self._addr}/v1/secret/data/credentials/{name}"
        resp = httpx.post(url, headers={"X-Vault-Token": self._token}, json={"data": data}, timeout=15)
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"OpenBao write failed HTTP {resp.status_code}: {resp.text[:200]}")

    def delete(self, name: str) -> None:
        import httpx

        url = f"{self._addr}/v1/secret/data/credentials/{name}"
        resp = httpx.delete(url, headers={"X-Vault-Token": self._token}, timeout=15)
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"OpenBao delete failed HTTP {resp.status_code}: {resp.text[:200]}")


def build_store(env: Optional[Dict[str, str]] = None) -> CredentialStore:
    env = env if env is not None else os.environ
    if str(env.get("OPENBAO_ENABLED", "")).lower() in ("1", "true", "yes") and env.get("OPENBAO_TOKEN"):
        return OpenBaoCredentialStore(
            env.get("OPENBAO_ADDR", "http://openbao:8200"),
            env["OPENBAO_TOKEN"],
        )
    return FileCredentialStore(env.get("CREDENTIAL_DIR", "/data/credentials"))


# ---------------------------------------------------------------------------
# Cookie-jar persistence (captured by Camoufox logins or imported by a human).
# Jars are stored next to the credential file so browser logins can be reused
# until they expire. They are NEVER exposed to the agent.
# ---------------------------------------------------------------------------
def _jar_path(directory: str, name: str) -> str:
    return os.path.join(directory, f"{name}.cookies.json")


def load_cookie_jar(directory: str, name: str) -> Optional[Dict[str, Any]]:
    path = _jar_path(directory, name)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_cookie_jar(directory: str, name: str, cookies: list, origin: str, captured_at: Optional[str] = None) -> None:
    import time as _time

    data = {
        "origin": origin,
        "captured_at": captured_at or _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
        "cookies": cookies,
    }
    with open(_jar_path(directory, name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def cookie_jar_status(directory: str, name: str) -> Dict[str, Any]:
    jar = load_cookie_jar(directory, name)
    if not jar:
        return {"has_jar": False}
    return {"has_jar": True, "origin": jar.get("origin"), "captured_at": jar.get("captured_at"),
            "count": len(jar.get("cookies") or [])}


def delete_cookie_jar(directory: str, name: str) -> None:
    path = _jar_path(directory, name)
    if os.path.isfile(path):
        os.remove(path)
