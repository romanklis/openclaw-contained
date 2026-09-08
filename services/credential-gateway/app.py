"""Credential gateway — agents get capabilities, never credentials.

Routes (platform-network only):
  GET  /v1/health
  POST /v1/web      {method, url, credential?, session_id?, ...}
  POST /v1/session  {credential?, impersonate?}
  DELETE /v1/session/{id}
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from approvals import GrantStore, GrantError, ApprovalRequired
from browser import InteractiveLoginRequired, BrowserDriverError, resolve_album_urls
from executor import ExecutionDenied, ExecutionUpstreamError, Executor, SessionStore
from store import build_store, CredentialNotFound

CONTROL_PLANE_URL = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000").rstrip("/")
GATEWAY_AUTH = os.getenv("CREDENTIAL_GATEWAY_AUTH", "")
ADMIN_TOKEN = os.getenv("CREDENTIAL_GATEWAY_ADMIN_TOKEN", "")

SECRET_FIELDS = {"password", "token", "header_value", "cookie_value"}
MASKED_FIELDS = {"name", "kind", "username", "login_url", "username_field", "password_field",
                 "header_name", "cookie_name", "allowed_origins", "allowed_methods"}


def _masked(profile: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in profile.items() if k in MASKED_FIELDS}
    for secret in SECRET_FIELDS:
        if profile.get(secret):
            out[f"has_{secret}"] = True
    return out


def _admin(request: Request) -> None:
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="credential management is disabled (no admin token configured)")
    token = request.headers.get("X-Admin-Token", "")
    import hmac
    if not hmac.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="invalid admin token")


class AgentAuth:
    """Validate X-Agent-Token: task:<id> against the control plane (cached)."""

    def __init__(self) -> None:
        self._valid: Dict[str, float] = {}
        self._cache_s = 300

    def validate(self, token: Optional[str]) -> str:
        if not token or not token.startswith("task:"):
            raise HTTPException(status_code=401, detail="missing agent token")
        task_id = token.split(":", 1)[1].strip()
        now = __import__("time").time()
        if self._valid.get(task_id, 0) > now:
            return task_id
        try:
            resp = httpx.get(f"{CONTROL_PLANE_URL}/api/tasks/{task_id}", timeout=8)
        except Exception:
            raise HTTPException(status_code=503, detail="control plane unreachable")
        if resp.status_code != 200:
            raise HTTPException(status_code=401, detail="unknown agent task")
        self._valid[task_id] = now + self._cache_s
        return task_id


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = build_store()
    app.state.grants = GrantStore()
    app.state.sessions = SessionStore()
    app.state.auth = AgentAuth()
    app.state.executor_factory = Executor
    yield


app = FastAPI(title="Credential Gateway", version="0.1.0", lifespan=lifespan)


@app.get("/v1/credentials")
def list_credentials(request: Request):
    _admin(request)
    st = request.app.state.store
    return [_masked(st.get(n)) for n in st.list_names()]


@app.post("/v1/credentials/{name}")
def upsert_credential(name: str, payload: Dict[str, Any], request: Request):
    _admin(request)
    try:
        existing = request.app.state.store.get(name)
    except CredentialNotFound:
        existing = {}
    merged = dict(existing or {})
    for k, v in (payload or {}).items():
        if v is None:
            continue
        if v == "" and k in SECRET_FIELDS:
            continue  # blank secret field = keep existing
        merged[k] = v
    merged["name"] = name
    request.app.state.store.save(name, merged)
    return {"ok": True, "name": name}


@app.delete("/v1/credentials/{name}")
def delete_credential(name: str, request: Request):
    _admin(request)
    try:
        request.app.state.store.delete(name)
    except CredentialNotFound:
        pass
    return {"ok": True, "name": name}


@app.get("/v1/credentials/{name}/cookies/status")
def cookie_status(name: str, request: Request):
    _admin(request)
    from store import cookie_jar_status
    return cookie_jar_status(os.getenv("CREDENTIAL_DIR", "/data/credentials"), name)


@app.post("/v1/credentials/{name}/cookies/import")
def import_cookies(name: str, payload: Dict[str, Any], request: Request):
    """Import a session cookie jar for a credential (human finished the login).

    Payload: {"cookies": [{name, value, domain, path?, expires?, secure?}], "origin": "https://..."}
    Cookie domains are constrained to the credential's allowed origins.
    """
    _admin(request)
    from store import save_cookie_jar
    try:
        cred = request.app.state.store.get(name)
    except CredentialNotFound:
        raise HTTPException(status_code=404, detail="unknown credential")
    allowed = [o.rstrip("/") for o in (cred.get("allowed_origins") or [])]
    cookies = payload.get("cookies") or []
    if not isinstance(cookies, list) or not cookies:
        raise HTTPException(status_code=400, detail="cookies list required")
    cleaned = []
    for c in cookies:
        dom = str(c.get("domain") or "").lstrip(".")
        origin = f"https://{dom}"
        if allowed and origin not in allowed and f"http://{dom}" not in allowed:
            raise HTTPException(status_code=403, detail=f"cookie domain {dom} not allowed for credential '{name}'")
        cleaned.append({
            "name": str(c.get("name") or ""),
            "value": str(c.get("value") or ""),
            "domain": dom,
            "path": c.get("path") or "/",
            "secure": bool(c.get("secure", True)),
            "expires": c.get("expires"),
        })
    save_cookie_jar(os.getenv("CREDENTIAL_DIR", "/data/credentials"), name, cleaned,
                    origin=payload.get("origin") or f"https://{cleaned[0]['domain']}")
    return {"ok": True, "name": name, "count": len(cleaned)}


@app.delete("/v1/credentials/{name}/cookies")
def delete_cookies(name: str, request: Request):
    _admin(request)
    from store import delete_cookie_jar
    delete_cookie_jar(os.getenv("CREDENTIAL_DIR", "/data/credentials"), name)
    return {"ok": True, "name": name}


def _agent(request: Request) -> str:
    token = request.headers.get("X-Agent-Token", "")
    return request.app.state.auth.validate(token)


def _executor(request: Request, agent: str) -> Executor:
    st = request.app.state
    return Executor(st.store, st.grants, st.sessions, agent=agent)


@app.get("/v1/health")
async def health():
    return {"status": "ok"}


@app.post("/v1/web")
def web_execute(payload: Dict[str, Any], request: Request):
    agent = _agent(request)
    ex = _executor(request, agent)
    try:
        result = ex.execute(payload)
    except InteractiveLoginRequired as e:
        return JSONResponse(
            status_code=202,
            content={
                "status": "interactive_login_required",
                "credential": payload.get("credential"),
                "detail": e.message,
                "hint": e.hint or "A human step (2FA / device approval / CAPTCHA) is needed. Finish the login in your own browser and import the session cookies on the Credentials page.",
            },
        )
    except ApprovalRequired as e:
        return JSONResponse(
            status_code=202,
            content={
                "status": "awaiting_approval",
                "request_id": e.request_id,
                "credential": e.credential,
                "origin": e.origin,
                "method": e.method,
                "detail": "This credential needs human approval. Approve it on the Approvals page; the workflow will resume and retry automatically.",
            },
        )
    except ExecutionDenied as e:
        return JSONResponse(status_code=403, content={"detail": e.message})
    except ExecutionUpstreamError as e:
        return JSONResponse(status_code=502, content={"detail": e.message})
    except Exception:
        import logging
        logging.getLogger(__name__).exception("credential gateway execution error")
        return JSONResponse(status_code=500, content={"detail": "gateway execution error"})
    return result


@app.post("/v1/session")
def create_session(payload: Dict[str, Any], request: Request):
    """Create a session. A credential may be bound for lazy, scoped auth on the
    first real request (no approval-gated LOGIN happens here)."""
    agent = _agent(request)
    st = request.app.state
    credential = payload.get("credential")
    if credential:
        try:
            st.store.get(credential)
        except Exception as e:
            raise HTTPException(status_code=404, detail=str(e))
    sid = st.sessions.create(payload.get("impersonate") or "chrome", credential=credential)
    return {"session_id": sid, "agent": agent}


@app.post("/v1/driver/browser/resolve-album")
def browser_resolve_album(payload: Dict[str, Any], request: Request):
    """Gateway-hosted browser driver: resolve a JS-only public album (e.g.
    Google Photos) and return direct media URLs. Runs Camoufox HERE (outside the
    gVisor agent sandbox); the agent just downloads the returned URLs.

    Auth: either a valid agent token (task:<id>) or the shared internal admin
    token (used by worker-run deterministic blocks)."""
    import hmac as _hmac

    agent_ok = True
    try:
        _agent(request)
    except HTTPException:
        agent_ok = False
    token = request.headers.get("X-Admin-Token", "")
    if not agent_ok and not (ADMIN_TOKEN and _hmac.compare_digest(token, ADMIN_TOKEN)):
        raise HTTPException(status_code=401, detail="invalid auth token")
    url = payload.get("url") or ""
    if not url:
        raise HTTPException(status_code=400, detail="url required")
    try:
        return resolve_album_urls(
            url,
            headless=bool(payload.get("headless", True)),
            max_scrolls=int(payload.get("max_scrolls") or 10),
            scroll_wait=int(payload.get("scroll_wait") or 3000),
            page_timeout=int(payload.get("page_timeout") or 120000),
        )
    except BrowserDriverError as e:
        return JSONResponse(status_code=502, content={"detail": e.message})
    except Exception:
        import logging
        logging.getLogger(__name__).exception("browser driver error")
        return JSONResponse(status_code=500, content={"detail": "browser driver error"})


@app.delete("/v1/session/{session_id}")
def drop_session(session_id: str, request: Request):
    _agent(request)
    removed = request.app.state.sessions.drop(session_id)
    if not removed:
        raise HTTPException(status_code=404, detail="unknown session_id")
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8083")))
