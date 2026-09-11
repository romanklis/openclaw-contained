"""Credential management facade for the frontend.

The gateway owns the credential store; this router proxies list/add/update/delete
to it (internal network, admin token) so the browser only talks to control-plane.
Secret values are only ever sent TO the gateway for writes and are masked on reads.
"""
from fastapi import APIRouter, HTTPException, Body
from typing import List, Dict, Any
import os
import httpx

logger = __import__("logging").getLogger(__name__)
router = APIRouter(prefix="/api/credentials", tags=["credentials"])

GATEWAY_URL = os.getenv("CREDENTIAL_GATEWAY_URL", "http://credential-gateway:8083").rstrip("/")
ADMIN_TOKEN = os.getenv("CREDENTIAL_GATEWAY_ADMIN_TOKEN", "")


def _headers() -> Dict[str, str]:
    return {"Content-Type": "application/json", "X-Admin-Token": ADMIN_TOKEN}


def _call_gateway(method: str, path: str, json_body: Any = None) -> Dict[str, Any]:
    try:
        resp = httpx.request(method, f"{GATEWAY_URL}{path}", json=json_body, headers=_headers(), timeout=15)
    except Exception:
        raise HTTPException(status_code=503, detail="credential gateway unreachable")
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text[:200])
        except Exception:
            detail = resp.text[:200]
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return resp.json()


@router.get("")
async def list_credentials() -> List[Dict[str, Any]]:
    return _call_gateway("GET", "/v1/credentials")


@router.post("/{name}")
async def upsert_credential(name: str, payload: dict = Body(...)) -> Dict[str, Any]:
    return _call_gateway("POST", f"/v1/credentials/{name}", payload)


@router.delete("/{name}")
async def delete_credential(name: str) -> Dict[str, Any]:
    return _call_gateway("DELETE", f"/v1/credentials/{name}")


@router.get("/{name}/cookies/status")
async def cookie_status(name: str) -> Dict[str, Any]:
    return _call_gateway("GET", f"/v1/credentials/{name}/cookies/status")


@router.post("/{name}/cookies/import")
async def import_cookies(name: str, payload: dict = Body(...)) -> Dict[str, Any]:
    return _call_gateway("POST", f"/v1/credentials/{name}/cookies/import", payload)


@router.delete("/{name}/cookies")
async def delete_cookies(name: str) -> Dict[str, Any]:
    return _call_gateway("DELETE", f"/v1/credentials/{name}/cookies")
