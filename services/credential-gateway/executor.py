"""curl_cffi HTTP execution for the credential gateway.

Owns all credentialed sessions. Secrets and cookie jars stay in this process;
responses sent to the agent are redacted (no authorization/cookie headers, no
secret material) and body-size bounded.
"""
from __future__ import annotations

import base64
import threading
import time
import uuid
from typing import Any, Dict, Optional

from curl_cffi import requests as cffi_requests

from store import CredentialNotFound, CredentialStore
from approvals import GrantStore, ensure_grant, GrantError, ApprovalRequired

MAX_BODY_TEXT = int(__import__("os").getenv("GATEWAY_MAX_BODY_TEXT", "4000000"))
SAFE_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie"}

_IMPERSONATE_DEFAULT = "chrome"


class ExecutionDenied(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ExecutionUpstreamError(Exception):
    """Expected upstream failure (bot challenge, 2FA, bad login, network)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class SessionStore:
    """Gateway-owned curl_cffi sessions (cookie jars never leave the gateway)."""

    def __init__(self):
        self._sessions: Dict[str, cffi_requests.Session] = {}
        self._meta: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create(self, impersonate: str = _IMPERSONATE_DEFAULT, credential: Optional[str] = None) -> str:
        sid = uuid.uuid4().hex[:12]
        with self._lock:
            self._sessions[sid] = cffi_requests.Session(impersonate=impersonate)
            self._meta[sid] = {"impersonate": impersonate, "created": time.time(),
                               "credential": credential, "authed": False}
        return sid

    def meta(self, sid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            m = self._meta.get(sid)
            return dict(m) if m else None

    def set_meta(self, sid: str, **updates: Any) -> None:
        with self._lock:
            if sid in self._meta:
                self._meta[sid].update(updates)

    def get(self, sid: str) -> Optional[cffi_requests.Session]:
        with self._lock:
            return self._sessions.get(sid)

    def drop(self, sid: str) -> bool:
        with self._lock:
            s = self._sessions.pop(sid, None)
            self._meta.pop(sid, None)
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
        return s is not None


def origin_of(url: str) -> str:
    """Return scheme://netloc for a URL (policy scope)."""
    from urllib.parse import urlparse

    p = urlparse(url)
    if not p.scheme or not p.netloc:
        raise ExecutionDenied(f"invalid URL: {url}")
    return f"{p.scheme}://{p.netloc}"


def _challenge_hint(resp: Any, body: str = "") -> str:
    """Classify an upstream login/anti-bot response into a human message."""
    if not body:
        body = (getattr(resp, "text", "") or "")[:4000].lower()
    checks = [
        ("reCAPTCHA", ("recaptcha", "g-recaptcha", "cf-turnstile")),
        ("CAPTCHA", ("captcha",)),
        ("two-step verification / 2FA", ("two-step", "2fa", "twofactor", "two factor")),
        ("device-approval", ("approve this device", "device approval", "new device")),
        ("bot / fingerprint challenge", ("challenge", "verify you", "fingerprint")),
    ]
    for label, needles in checks:
        if any(n in body for n in needles):
            return label
    if getattr(resp, "status_code", 0) and resp.status_code >= 400:
        return f"HTTP {resp.status_code}"
    return ""


class Executor:
    def __init__(self, store: CredentialStore, grants: GrantStore, sessions: SessionStore, agent: str = ""):
        self.store = store
        self.grants = grants
        self.sessions = sessions
        self.agent = agent

    # -- credential use -------------------------------------------------------
    def _credential(self, name: str) -> Dict[str, Any]:
        try:
            cred = self.store.get(name)
        except CredentialNotFound:
            raise ExecutionDenied(f"unknown credential reference: {name}")
        return cred

    def _authorize(self, cred: Dict[str, Any], origin: str, method: str, reason: str) -> None:
        allowed_origins = cred.get("allowed_origins") or []
        if allowed_origins and origin not in allowed_origins:
            raise ExecutionDenied(f"credential '{cred['name']}' not allowed for origin {origin}")
        allowed = cred.get("allowed_methods") or ["GET", "POST"]
        if method not in allowed:
            raise ExecutionDenied(f"method {method} not allowed for credential '{cred['name']}'")
        try:
            ensure_grant(self.grants, self.agent, cred["name"], origin, method, reason)
        except GrantError as e:
            raise ExecutionDenied(str(e))

    # -- session setup --------------------------------------------------------
    def _prepare_session(self, session_id: str, credential: Optional[str]) -> str:
        """Return a session id, creating it (optionally bound to a credential)."""
        if not session_id:
            session_id = self.sessions.create(_IMPERSONATE_DEFAULT, credential=credential)
        elif credential:
            # Bind a credential to an existing/agent-created session for lazy auth.
            meta = self.sessions.meta(session_id) or {}
            meta_cred = meta.get("credential")
            if meta_cred and meta_cred != credential:
                raise ExecutionDenied("session already bound to a different credential")
            if not meta_cred:
                self.sessions.set_meta(session_id, credential=credential)
        return session_id

    def _apply_auth(self, sess: cffi_requests.Session, cred: Dict[str, Any]) -> None:
        kind = cred.get("kind", "basic")
        username = cred.get("username", "")
        password = cred.get("password", "")
        if kind == "basic":
            sess.auth = (username, password or "")
        elif kind == "bearer":
            sess.headers.update({"Authorization": f"Bearer {cred.get('token', '')}"})
        elif kind == "header":
            name = cred.get("header_name", "Authorization")
            sess.headers.update({name: cred.get("header_value", cred.get("token", ""))})
        elif kind == "cookie":
            name = cred.get("cookie_name", "")
            value = cred.get("cookie_value", cred.get("token", ""))
            if name:
                sess.cookies.set(name, value)

    def _ensure_authenticated(self, sess: cffi_requests.Session, cred: Dict[str, Any], session_id: str) -> None:
        """Authenticate a session for the credential — plain curl_cffi form login
        by default, or a real Camoufox browser login (with stored-cookie reuse)
        when the credential sets ``browser_login``."""
        import os as _os

        kind = cred.get("kind", "basic")
        if kind != "form":
            self._apply_auth(sess, cred)
            return
        meta = self.sessions.meta(session_id) or {}
        if meta.get("authed"):
            return

        if cred.get("browser_login"):
            from store import load_cookie_jar
            from browser import InteractiveLoginRequired, BrowserManager

            jar = load_cookie_jar(_os.getenv("CREDENTIAL_DIR", "/data/credentials"), cred.get("name", ""))
            if jar and jar.get("cookies"):
                for c in jar["cookies"]:
                    try:
                        sess.cookies.set(c.get("name", ""), c.get("value", ""), domain=c.get("domain", ""))
                    except Exception:
                        pass
                self.sessions.set_meta(session_id, authed=True)
                return
            bm = BrowserManager()
            bm.interactive_login(sess, cred)  # raises InteractiveLoginRequired on challenges
            self.sessions.set_meta(session_id, authed=True)
            return

        self._login(sess, cred)
        self.sessions.set_meta(session_id, authed=True)

    def _login(self, sess: cffi_requests.Session, cred: Dict[str, Any]) -> None:
        login_url = cred.get("login_url", "")
        if not login_url:
            return
        u_field = cred.get("username_field", "username")
        p_field = cred.get("password_field", "password")
        try:
            resp = sess.post(
                login_url,
                data={u_field: cred.get("username", ""), p_field: cred.get("password", "")},
                timeout=60,
            )
        except Exception as exc:
            raise ExecutionUpstreamError(f"login request failed: {exc}") from exc
        body = (resp.text or "")[:4000].lower()
        if resp.status_code >= 400:
            raise ExecutionUpstreamError(
                f"upstream login failed: HTTP {resp.status_code} — {_challenge_hint(resp, body)}"
            )
        # Some sites return 200 with a challenge/error page instead of an auth redirect.
        hint = _challenge_hint(resp, body)
        if hint:
            raise ExecutionUpstreamError(f"login blocked by {hint}")

    # -- execution ------------------------------------------------------------
    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        method = str(payload.get("method") or "GET").upper()
        url = payload.get("url") or ""
        credential = payload.get("credential")
        session_id = payload.get("session_id") or ""
        impersonate = payload.get("impersonate") or _IMPERSONATE_DEFAULT

        # Explicit warm-up: LOGIN <credential> (optionally on a session).
        if method == "LOGIN":
            if not credential:
                raise ExecutionDenied("credential required for LOGIN")
            cred = self._credential(credential)
            origin = origin_of(cred.get("login_url") or url or "https://login")
            self._authorize(cred, origin, "POST", "warm-up login")
            sid = self._prepare_session(session_id, credential)
            sess = self.sessions.get(sid)
            if sess is None:
                raise ExecutionDenied("unknown session_id")
            self._ensure_authenticated(sess, cred, sid)
            return {"ok": True, "session_id": sid}

        if not url:
            raise ExecutionDenied("url required")
        origin = origin_of(url)

        # A session may already carry its credential reference.
        if not credential:
            meta = self.sessions.meta(session_id) if session_id else None
            if meta and meta.get("credential"):
                credential = meta["credential"]

        cred: Optional[Dict[str, Any]] = None
        if credential:
            cred = self._credential(credential)
            self._authorize(cred, origin, method, payload.get("reason") or f"{method} {origin}")

        session_id = self._prepare_session(session_id, credential)
        sess = self.sessions.get(session_id)
        if sess is None:
            raise ExecutionDenied("unknown session_id")

        if cred:
            self._ensure_authenticated(sess, cred, session_id)

        kwargs: Dict[str, Any] = {
            "timeout": float(payload.get("timeout_seconds", 90)),
        }
        if payload.get("params"):
            kwargs["params"] = payload["params"]
        if payload.get("data") is not None:
            kwargs["data"] = payload["data"]
        if payload.get("json_data") is not None:
            kwargs["json"] = payload["json_data"]
        if payload.get("headers"):
            kwargs["headers"] = payload["headers"]

        try:
            resp = sess.request(method, url, **kwargs)
        except ExecutionDenied:
            raise
        except ExecutionUpstreamError:
            raise
        except Exception as exc:  # curl/network errors
            raise ExecutionUpstreamError(f"upstream request failed: {exc}") from exc

        return self._build_response(resp, session_id)

    def _build_response(self, resp: Any, session_id: str) -> Dict[str, Any]:
        headers = {k: v for k, v in resp.headers.items() if k.lower() not in SAFE_HEADERS}
        content_type = resp.headers.get("content-type", "").lower()
        result: Dict[str, Any] = {
            "status_code": resp.status_code,
            "headers": headers,
            "content_type": content_type,
            "session_id": session_id,
            "url": str(resp.url),
        }
        raw = resp.content or b""
        if "text" in content_type or content_type in ("", "application/json", "application/xml", "text/html"):
            text = raw.decode("utf-8", errors="replace")
            if len(text) > MAX_BODY_TEXT:
                text = text[:MAX_BODY_TEXT] + "\n... (truncated by gateway)"
            result["text"] = text
        else:
            result["content_b64"] = base64.b64encode(raw[:MAX_BODY_TEXT]).decode("ascii")
        return result
