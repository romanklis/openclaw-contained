"""Read-only IMAP mailbox access for TaskForge agents.

The credential gateway holds the mailbox password. This module only references a
credential name; it never sees the password or connects to the mail server.

Usage::

    import email_imap as mail
    mail.folders()
    mail.list_messages(folder="INBOX", from_addr="billing@example.com", limit=10)
    msg = mail.read_message(uid="42")
    mail.download_attachment(uid="42", part="2", dest="/workspace/invoice.pdf")
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_TIMEOUT = 180
GATEWAY_URL = os.environ.get("CREDENTIAL_GATEWAY_URL", "").rstrip("/")
TASK_ID = os.environ.get("TASK_ID", "")
DEFAULT_CREDENTIAL = os.environ.get("EMAIL_CREDENTIAL", "taskforge-mail")


class MailError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class MailApprovalRequired(MailError):
    def __init__(self, request_id: Any, message: str):
        super().__init__("MAIL_APPROVAL_REQUIRED", message)
        self.request_id = request_id


class MailUnavailable(MailError):
    pass


def _headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if TASK_ID:
        headers["X-Agent-Token"] = f"task:{TASK_ID}"
    return headers


def _gateway() -> str:
    if not GATEWAY_URL:
        raise MailUnavailable("MAIL_GATEWAY_UNAVAILABLE", "CREDENTIAL_GATEWAY_URL is not set")
    return GATEWAY_URL


def _call(method: str, path: str, **kwargs: Any) -> Any:
    import httpx

    try:
        resp = httpx.request(
            method,
            f"{_gateway()}{path}",
            headers=_headers(),
            timeout=DEFAULT_TIMEOUT,
            **kwargs,
        )
    except Exception as exc:
        raise MailUnavailable("MAIL_GATEWAY_UNAVAILABLE", f"credential gateway unreachable: {exc}")

    if resp.status_code == 202:
        data = resp.json() if resp.content else {}
        print(f"\nMAIL_APPROVAL_REQUEST:{data.get('request_id')}\n", flush=True)
        raise MailApprovalRequired(
            data.get("request_id"),
            data.get("detail") or "mailbox access awaits human approval",
        )
    if resp.status_code == 401:
        raise MailUnavailable("MAIL_GATEWAY_UNAVAILABLE", "credential gateway rejected the agent token")
    if resp.status_code in (403, 404, 413, 400):
        try:
            detail = resp.json()
        except Exception:
            detail = {"code": "MAIL_ERROR", "detail": resp.text[:300]}
        raise MailError(str(detail.get("code") or "MAIL_ERROR"), str(detail.get("detail") or detail))
    if resp.status_code >= 500:
        raise MailUnavailable("MAIL_UPSTREAM", f"mail gateway error HTTP {resp.status_code}")
    try:
        return resp.json()
    except Exception:
        raise MailUnavailable("MAIL_UPSTREAM", f"invalid gateway response HTTP {resp.status_code}")


def folders(credential: str = DEFAULT_CREDENTIAL) -> list[str]:
    data = _call("GET", "/v1/mail/folders", params={"credential": credential})
    return list(data.get("folders") or [])


def list_messages(
    folder: str = "INBOX",
    from_addr: str = "",
    subject: str = "",
    text: str = "",
    since: str = "",
    before: str = "",
    unseen: bool = False,
    limit: int = 20,
    credential: str = DEFAULT_CREDENTIAL,
) -> list[dict]:
    params: Dict[str, Any] = {
        "credential": credential,
        "folder": folder,
        "subject": subject,
        "text": text,
        "since": since,
        "before": before,
        "unseen": unseen,
        "limit": limit,
    }
    if from_addr:
        params["from"] = from_addr
    data = _call("GET", "/v1/mail/messages", params=params)
    return list(data.get("messages") or [])


def read_message(
    uid: str,
    folder: str = "INBOX",
    credential: str = DEFAULT_CREDENTIAL,
) -> dict:
    data = _call(
        "GET",
        f"/v1/mail/messages/{uid}",
        params={"credential": credential, "folder": folder},
    )
    return data.get("message") or {}


def download_attachment(
    uid: str,
    part: str,
    folder: str = "INBOX",
    dest: Optional[str] = None,
    credential: str = DEFAULT_CREDENTIAL,
) -> str:
    import httpx

    try:
        resp = httpx.get(
            f"{_gateway()}/v1/mail/messages/{uid}/attachments/{part}",
            params={"credential": credential, "folder": folder},
            headers=_headers(),
            timeout=DEFAULT_TIMEOUT,
        )
    except Exception as exc:
        raise MailUnavailable("MAIL_GATEWAY_UNAVAILABLE", f"credential gateway unreachable: {exc}")

    if resp.status_code == 202:
        data = resp.json() if resp.content else {}
        print(f"\nMAIL_APPROVAL_REQUEST:{data.get('request_id')}\n", flush=True)
        raise MailApprovalRequired(data.get("request_id"), data.get("detail") or "approval required")
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except Exception:
            detail = {"code": "MAIL_ERROR", "detail": resp.text[:300]}
        raise MailError(str(detail.get("code") or "MAIL_ERROR"), str(detail.get("detail") or detail))

    filename = ""
    disposition = resp.headers.get("content-disposition", "")
    match = re.search(r'filename="?([^";]+)"?', disposition)
    if match:
        filename = match.group(1)
    safe_name = os.path.basename(filename or f"attachment-{uid}-{part}")
    target = Path(dest) if dest else Path("/workspace") / safe_name
    if not target.is_absolute():
        target = Path("/workspace") / target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(resp.content)
    return str(target)
