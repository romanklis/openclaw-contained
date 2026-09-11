"""Human approval coordination between the gateway and the control plane.

When an agent first wants to use a credential against an origin, the gateway
registers a pending request on the control plane and blocks (polling) until a
human approves or denies it. Approved grants are cached in the gateway for a TTL
so repeated calls within the window do not re-prompt.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Optional

import httpx

DEFAULT_TTL_MINUTES = int(os.getenv("DEFAULT_GRANT_TTL_MIN", "5"))
DEFAULT_MAX_REQUESTS = int(os.getenv("DEFAULT_GRANT_MAX_REQUESTS", "20"))
APPROVAL_WAIT_MAX = int(os.getenv("APPROVAL_WAIT_MAX", "300"))
POLL_EVERY_SECONDS = float(os.getenv("APPROVAL_POLL_EVERY_SECONDS", "2"))

GATEWAY_AUTH = os.getenv("CREDENTIAL_GATEWAY_AUTH", "")  # shared secret with control-plane, optional


def _cp_url() -> str:
    return os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000").rstrip("/")


def _headers() -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if GATEWAY_AUTH:
        h["X-Gateway-Token"] = GATEWAY_AUTH
    return h


class GrantError(Exception):
    pass


class ApprovalRequired(Exception):
    """Raised when a credentialed request needs a human decision first."""

    def __init__(self, request_id, credential: str, origin: str, method: str):
        super().__init__(f"credential '{credential}' awaits approval for {origin} ({method})")
        self.request_id = request_id
        self.credential = credential
        self.origin = origin
        self.method = method


class Grant:
    def __init__(self, credential: str, origin: str, agent: str,
                 expires_at: float, max_requests: int, methods: list):
        self.credential = credential
        self.origin = origin
        self.agent = agent
        self.expires_at = expires_at
        self.remaining = max_requests
        self.methods = set(methods)

    @property
    def active(self) -> bool:
        return self.remaining > 0 and time.time() < self.expires_at

    def allow(self, method: str) -> bool:
        if not self.active:
            return False
        if method not in self.methods:
            return False
        return True

    def consume(self) -> None:
        self.remaining -= 1


class GrantStore:
    """In-memory grants keyed by (agent, credential, origin)."""

    def __init__(self, ttl_minutes: int = DEFAULT_TTL_MINUTES, max_requests: int = DEFAULT_MAX_REQUESTS):
        self._grants: Dict[tuple, Grant] = {}
        self._lock = threading.Lock()
        self.ttl_minutes = ttl_minutes
        self.max_requests = max_requests

    def get(self, agent: str, credential: str, origin: str) -> Optional[Grant]:
        with self._lock:
            g = self._grants.get((agent, credential, origin))
            if g is None:
                return None
            if not g.active:
                self._grants.pop((agent, credential, origin), None)
                return None
            return g

    def add(self, agent: str, credential: str, origin: str, methods: list,
            ttl_minutes: Optional[int] = None, max_requests: Optional[int] = None) -> Grant:
        with self._lock:
            g = Grant(
                credential=credential,
                origin=origin,
                agent=agent,
                expires_at=time.time() + (ttl_minutes or self.ttl_minutes) * 60,
                max_requests=max_requests or self.max_requests,
                methods=methods,
            )
            self._grants[(agent, credential, origin)] = g
            return g


def _request_payload(agent: str, credential: str, origin: str, method: str, reason: str) -> Dict[str, Any]:
    return {
        "agent_session": agent,
        "credential_name": credential,
        "origin": origin,
        "method": method,
        "reason": reason,
    }


def create_or_get_request(agent: str, credential: str, origin: str, method: str, reason: str) -> Dict[str, Any]:
    resp = httpx.post(f"{_cp_url()}/api/credential-requests", json=_request_payload(agent, credential, origin, method, reason), headers=_headers(), timeout=15)
    if resp.status_code != 200:
        raise GrantError(f"control-plane request create failed HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def poll_request(request_id: int, wait_seconds: float = APPROVAL_WAIT_MAX) -> Dict[str, Any]:
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        resp = httpx.get(f"{_cp_url()}/api/credential-requests/{request_id}", headers=_headers(), timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") in ("approved", "denied", "cancelled"):
                return data
        time.sleep(POLL_EVERY_SECONDS)
    return {"status": "pending"}


def list_requests(agent: str = "", credential: str = "", origin: str = "", method: str = "") -> list:
    """Fetch credential requests from the control plane (any status)."""
    params = {}
    if agent:
        params["agent_session"] = agent
    if credential:
        params["credential_name"] = credential
    if origin:
        params["origin"] = origin
    if method:
        params["method"] = method
    resp = httpx.get(f"{_cp_url()}/api/credential-requests", params=params, headers=_headers(), timeout=15)
    if resp.status_code != 200:
        raise GrantError(f"control-plane list failed HTTP {resp.status_code}")
    data = resp.json()
    return data if isinstance(data, list) else []


def _is_within_ttl(row: dict, ttl_minutes: int) -> bool:
    reviewed = row.get("reviewed_at")
    if not reviewed:
        return False
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(str(reviewed).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=__import__("datetime").timezone.utc)
        return (__import__("datetime").datetime.now(__import__("datetime").timezone.utc) - dt).total_seconds() < ttl_minutes * 60
    except Exception:
        return False


def find_recent_approved(agent: str, credential: str, origin: str, method: str,
                         ttl_minutes: int = DEFAULT_TTL_MINUTES) -> Optional[dict]:
    """Return the most recent approved request for the scope if still within TTL."""
    rows = list_requests(agent=agent, credential=credential, origin=origin)
    approved = [r for r in rows if r.get("status") == "approved"]
    if not approved:
        return None
    approved.sort(key=lambda r: r.get("reviewed_at") or r.get("requested_at") or "", reverse=True)
    newest = approved[0]
    if not _is_within_ttl(newest, ttl_minutes):
        return None
    return newest


def ensure_grant(grants: GrantStore, agent: str, credential: str, origin: str, method: str, reason: str) -> Grant:
    """Return an active grant, minting from a previously approved request if one
    is still within TTL. If a decision is required, raise ApprovalRequired (the
    caller surfaces it to the agent; the Temporal workflow pauses for approval)."""
    existing = grants.get(agent, credential, origin)
    if existing and existing.allow(method):
        existing.consume()
        return existing

    # If a previous approval is still within TTL, reuse it — do not create or
    # block on a new decision.
    recent = find_recent_approved(agent, credential, origin, method)
    if recent is not None:
        grant = grants.add(agent, credential, origin, recent.get("allowed_methods") or ["GET", "POST"],
                           ttl_minutes=recent.get("ttl_minutes"))
        grant.consume()
        return grant

    req = create_or_get_request(agent, credential, origin, method, reason)
    if req.get("status") == "approved":
        methods = req.get("allowed_methods") or ["GET", "POST"]
        grant = grants.add(agent, credential, origin, methods, ttl_minutes=req.get("ttl_minutes"))
        grant.consume()
        return grant

    raise ApprovalRequired(
        request_id=int(req["id"]),
        credential=credential,
        origin=origin,
        method=method,
    )
