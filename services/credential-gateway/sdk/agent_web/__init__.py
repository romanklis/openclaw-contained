"""agent_web — credentialed web access for agents, via the credential gateway.

Agent-facing API only. There is intentionally NO API to read a raw secret,
password, cookie or token: the agent works with *credential references* and the
gateway performs authenticated requests and returns response bodies.

Usage::

    from agent_web import web

    page = web.get("https://example.com/account", credential="example-account")
    print(page.status_code, page.text)

    s = web.session(credential="example-account", impersonate="chrome")
    r = s.get("https://example.com/dashboard")

    # For login-page discovery, request WITHOUT a credential:
    login_page = web.get("https://example.com/login")
"""
from __future__ import annotations

import base64
import os
from typing import Any, Dict, List, Optional

GATEWAY_URL = os.environ.get("CREDENTIAL_GATEWAY_URL", "").rstrip("/")
TASK_ID = os.environ.get("TASK_ID", "")

DEFAULT_TIMEOUT = 180


class CredentialDenied(Exception):
    """The human declined (or TTL-expired) credential use for this request."""


class CredentialGatewayUnavailable(Exception):
    """The credential gateway could not be reached or rejected the agent."""


def _headers() -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if TASK_ID:
        h["X-Agent-Token"] = f"task:{TASK_ID}"
    return h


def _call(method: str, url: str, *, gateway_url: str = "", timeout: Optional[float] = None) -> Dict[str, Any]:
    gw = gateway_url or GATEWAY_URL
    if not gw:
        raise CredentialGatewayUnavailable("CREDENTIAL_GATEWAY_URL is not set in this container")
    import httpx

    payload = {"method": method.upper(), "url": url}
    try:
        resp = httpx.post(
            f"{gw}/v1/web",
            json=payload,
            headers=_headers(),
            timeout=timeout if timeout is not None else DEFAULT_TIMEOUT,
        )
    except Exception as exc:  # network/timeouts
        raise CredentialGatewayUnavailable(f"gateway unreachable: {exc}") from exc
    if resp.status_code == 401:
        raise CredentialGatewayUnavailable("gateway rejected the agent token")
    if resp.status_code == 403:
        detail = _detail(resp)
        raise CredentialDenied(detail or "credential use denied")
    if resp.status_code >= 500:
        raise CredentialGatewayUnavailable(f"gateway error HTTP {resp.status_code}")
    resp.raise_for_status()
    return resp.json()


def _detail(resp: Any) -> str:
    try:
        return str(resp.json().get("detail", ""))
    except Exception:
        return resp.text[:300]


class Response:
    """A response from the credential gateway (secrets already stripped)."""

    def __init__(self, data: Dict[str, Any]):
        self._d = data

    @property
    def status_code(self) -> int:
        return int(self._d.get("status_code", 0))

    @property
    def headers(self) -> Dict[str, str]:
        return dict(self._d.get("headers") or {})

    @property
    def text(self) -> str:
        t = self._d.get("text")
        if t is not None:
            return t
        b = self._d.get("content_b64")
        if b:
            return base64.b64decode(b).decode("utf-8", errors="replace")
        return ""

    @property
    def content(self) -> bytes:
        b = self._d.get("content_b64")
        if b:
            return base64.b64decode(b)
        return self.text.encode("utf-8", errors="replace")

    @property
    def session_id(self) -> str:
        return self._d.get("session_id", "")

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    @property
    def url(self) -> str:
        return self._d.get("url", "")

    def json(self) -> Any:
        import json as _json

        return _json.loads(self.text)

    def raise_for_status(self) -> None:
        if not self.ok:
            raise CredentialDenied(f"HTTP {self.status_code} from {self.url}")

    def __repr__(self) -> str:  # pragma: no cover - debug aid, never includes secrets
        return f"<Response {self.status_code} {self.url}>"


class Session:
    """A gateway-owned HTTP session (cookies/credentials stay in the gateway)."""

    def __init__(self, session_id: str):
        self.session_id = session_id

    def request(self, method: str, url: str, **kw: Any) -> Response:
        payload = {
            "method": method.upper(),
            "url": url,
            "session_id": self.session_id,
        }
        if kw.get("params"):
            payload["params"] = kw["params"]
        if kw.get("data") is not None:
            payload["data"] = kw["data"]
        if kw.get("json") is not None:
            payload["json_data"] = kw["json"]
        if kw.get("headers"):
            payload["headers"] = kw["headers"]
        if kw.get("credential"):
            payload["credential"] = kw["credential"]
        return Response(_post_payload(payload))

    def get(self, url: str, **kw: Any) -> Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> Response:
        return self.request("POST", url, **kw)

    def close(self) -> None:
        try:
            _post_payload({"session_id": self.session_id, "close": True})
        except Exception:
            pass


def _post_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    gw = GATEWAY_URL
    if not gw:
        raise CredentialGatewayUnavailable("CREDENTIAL_GATEWAY_URL is not set in this container")
    import httpx

    try:
        resp = httpx.post(f"{gw}/v1/web", json=payload, headers=_headers(), timeout=DEFAULT_TIMEOUT)
    except Exception as exc:
        raise CredentialGatewayUnavailable(f"gateway unreachable: {exc}") from exc
    if resp.status_code == 202:
        data = resp.json()
        print(f"\nCREDENTIAL_APPROVAL_REQUEST:{data.get('request_id')}\n", flush=True)
        return {"status_code": 202, "text": data.get("detail", "awaiting credential approval")}
    if resp.status_code == 401:
        raise CredentialGatewayUnavailable("gateway rejected the agent token")
    if resp.status_code == 403:
        raise CredentialDenied(_detail(resp) or "credential use denied")
    resp.raise_for_status()
    return resp.json()


class _Web:
    """Facade: web.get / web.post / web.session / web.login."""

    @staticmethod
    def get(url: str, *, credential: Optional[str] = None, session_id: str = "", params: Optional[Dict[str, Any]] = None, impersonate: Optional[str] = None) -> Response:
        return _web_request("GET", url, credential=credential, session_id=session_id, params=params, impersonate=impersonate)

    @staticmethod
    def post(url: str, *, credential: Optional[str] = None, session_id: str = "", data: Any = None, json: Any = None, params: Optional[Dict[str, Any]] = None, impersonate: Optional[str] = None) -> Response:
        return _web_request("POST", url, credential=credential, session_id=session_id, data=data, json_data=json, params=params, impersonate=impersonate)

    @staticmethod
    def session(*, credential: Optional[str] = None, impersonate: str = "chrome") -> Session:
        payload: Dict[str, Any] = {"impersonate": impersonate}
        if credential:
            payload["credential"] = credential
        gw = GATEWAY_URL
        if not gw:
            raise CredentialGatewayUnavailable("CREDENTIAL_GATEWAY_URL is not set in this container")
        import httpx

        try:
            resp = httpx.post(f"{gw}/v1/session", json=payload, headers=_headers(), timeout=DEFAULT_TIMEOUT)
        except Exception as exc:
            raise CredentialGatewayUnavailable(f"gateway unreachable: {exc}") from exc
        if resp.status_code == 403:
            raise CredentialDenied(_detail(resp) or "credential use denied")
        if resp.status_code == 401:
            raise CredentialGatewayUnavailable("gateway rejected the agent token")
        resp.raise_for_status()
        return Session(str(resp.json().get("session_id", "")))

    @staticmethod
    def login(credential: str) -> None:
        """Explicitly warm up a credential (runs the login recipe in the gateway)."""
        _post_payload({"method": "LOGIN", "credential": credential})


def _web_request(method: str, url: str, *, credential: Optional[str], session_id: str, params: Optional[Dict[str, Any]], impersonate: Optional[str], data: Any = None, json_data: Any = None) -> Response:
    payload: Dict[str, Any] = {"method": method, "url": url}
    if credential:
        payload["credential"] = credential
    if session_id:
        payload["session_id"] = session_id
    if params:
        payload["params"] = params
    if data is not None:
        payload["data"] = data
    if json_data is not None:
        payload["json_data"] = json_data
    if impersonate:
        payload["impersonate"] = impersonate
    return Response(_post_payload(payload))


web = _Web()

__all__ = ["web", "Response", "Session", "CredentialDenied", "CredentialGatewayUnavailable"]
