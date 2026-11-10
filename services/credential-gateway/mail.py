"""Read-only IMAP connector for the credential gateway.

Agents never receive IMAP credentials. They reference a credential name and the
gateway performs read-only IMAP operations:

    list_folders(credential)
    list_messages(credential, ...)
    read_message(credential, uid, ...)
    download_attachment(credential, uid, part, ...)

All operations use ``select(..., readonly=True)`` and ``BODY.PEEK[...]`` so a
read never changes the ``\\Seen`` flag. Send/delete/move/copy/append are not
implemented.
"""
from __future__ import annotations

import html as _html
import imaplib
import os
import re
import socket
import ssl
from contextlib import contextmanager
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_TIMEOUT = int(os.getenv("MAIL_READ_TIMEOUT_SECONDS", "20"))
DEFAULT_LIMIT = int(os.getenv("MAIL_MAX_MESSAGES", "50"))
DEFAULT_BODY_LIMIT = int(os.getenv("MAIL_MAX_BODY_BYTES", str(2 * 1024 * 1024)))
DEFAULT_ATTACHMENT_LIMIT = int(os.getenv("MAIL_MAX_ATTACHMENT_BYTES", str(10 * 1024 * 1024)))
DEFAULT_MESSAGE_LIMIT = int(os.getenv("MAIL_MAX_MESSAGE_BYTES", str(25 * 1024 * 1024)))


class MailError(Exception):
    """Stable error surfaced to the agent."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _as_uid(uid: Any) -> str:
    value = str(uid or "").strip()
    if not value.isdigit():
        raise MailError("MAIL_BAD_UID", "uid must be a numeric IMAP UID")
    return value


def _as_part(part: Any) -> str:
    value = str(part or "").strip()
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", value):
        raise MailError("MAIL_BAD_PART", "attachment part must look like 1 or 1.2")
    return value


def _limit(value: Any, fallback: int, maximum: int = 500) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(1, min(parsed, maximum))


def _credential_limits(cred: Dict[str, Any]) -> Tuple[int, int, int, int]:
    return (
        _limit(cred.get("max_messages"), DEFAULT_LIMIT),
        _limit(cred.get("max_body_bytes"), DEFAULT_BODY_LIMIT, 20 * 1024 * 1024),
        _limit(cred.get("max_attachment_bytes"), DEFAULT_ATTACHMENT_LIMIT, 50 * 1024 * 1024),
        _limit(cred.get("max_message_bytes"), DEFAULT_MESSAGE_LIMIT, 100 * 1024 * 1024),
    )


def _folder(cred: Dict[str, Any], folder: Optional[str]) -> str:
    requested = (folder or "INBOX").strip()
    if not requested or "\x00" in requested or "\r" in requested or "\n" in requested:
        raise MailError("MAIL_BAD_FOLDER", "invalid folder name")
    allowed = [str(item) for item in (cred.get("allowed_folders") or ["INBOX"]) if str(item).strip()]
    if not allowed:
        allowed = ["INBOX"]
    if requested.upper() == "INBOX":
        return "INBOX"
    match = next((item for item in allowed if item.upper() == requested.upper()), None)
    if not match:
        raise MailError(
            "MAIL_FOLDER_DENIED",
            f"folder '{requested}' is not in the credential's allowed_folders",
            status_code=403,
        )
    if ".." in match or match.startswith("/") or match.startswith("."):
        raise MailError("MAIL_BAD_FOLDER", "invalid folder name")
    return match


@contextmanager
def _connection(cred: Dict[str, Any], timeout: int = DEFAULT_TIMEOUT):
    host = str(cred.get("host") or "").strip()
    username = str(cred.get("username") or "").strip()
    password = str(cred.get("password") or "")
    if not host or not username or not password:
        raise MailError("MAIL_CONFIG", "credential is missing host, username, or password")

    use_tls = bool(cred.get("tls", True))
    port = int(cred.get("port") or (993 if use_tls else 143))
    conn = None
    try:
        if use_tls:
            conn = imaplib.IMAP4_SSL(
                host,
                port,
                ssl_context=ssl.create_default_context(),
                timeout=timeout,
            )
        else:
            conn = imaplib.IMAP4(host, port, timeout=timeout)
        conn.login(username, password)
        yield conn
    except MailError:
        raise
    except ssl.SSLError as exc:
        raise MailError("MAIL_TLS", f"TLS failure: {exc}", status_code=502)
    except (socket.timeout, TimeoutError) as exc:
        raise MailError("MAIL_TIMEOUT", f"IMAP timeout: {exc}", status_code=504)
    except imaplib.IMAP4.error as exc:
        text = str(exc)
        lowered = text.lower()
        if "auth" in lowered or "login" in lowered or "password" in lowered:
            raise MailError("MAIL_AUTH", "IMAP authentication failed", status_code=401)
        raise MailError("MAIL_UPSTREAM", f"IMAP error: {text[:300]}", status_code=502)
    except OSError as exc:
        raise MailError("MAIL_UPSTREAM", f"IMAP connection failed: {exc}", status_code=502)
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                try:
                    conn.shutdown()
                except Exception:
                    pass


def _select(conn: imaplib.IMAP4, cred: Dict[str, Any], folder: Optional[str]) -> str:
    selected = _folder(cred, folder)
    try:
        status, _ = conn.select(selected, readonly=True)
    except imaplib.IMAP4.error as exc:
        raise MailError("MAIL_UPSTREAM", f"select {selected} failed: {exc}", status_code=502)
    if status != "OK":
        raise MailError("MAIL_NOT_FOUND", f"folder '{selected}' not found", status_code=404)
    return selected


def _quote(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _imap_date(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(text[:19] if "T" in text else text, fmt).strftime("%d-%b-%Y")
        except ValueError:
            continue
    raise MailError("MAIL_BAD_QUERY", f"invalid date '{value}' (use YYYY-MM-DD)")


def _search_criteria(
    from_addr: str = "",
    subject: str = "",
    text: str = "",
    since: str = "",
    before: str = "",
    unseen: bool = False,
) -> List[str]:
    criteria: List[str] = []
    if from_addr:
        criteria += ["FROM", _quote(from_addr)]
    if subject:
        criteria += ["SUBJECT", _quote(subject)]
    if text:
        criteria += ["TEXT", _quote(text)]
    since_date = _imap_date(since) if since else ""
    before_date = _imap_date(before) if before else ""
    if since_date:
        criteria += ["SINCE", since_date]
    if before_date:
        criteria += ["BEFORE", before_date]
    if unseen:
        criteria += ["UNSEEN"]
    return criteria or ["ALL"]


def _fetch_bytes(data: Iterable[Any]) -> bytes:
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return b""


def _fetch_meta(data: Iterable[Any]) -> str:
    for item in data:
        if isinstance(item, tuple) and item and isinstance(item[0], (bytes, bytearray)):
            return item[0].decode("utf-8", errors="replace")
        if isinstance(item, (bytes, bytearray)):
            return bytes(item).decode("utf-8", errors="replace")
    return ""


def _decode_header(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        from email.header import decode_header, make_header

        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _part_text(part: Any) -> str:
    try:
        content = part.get_content()
        return content if isinstance(content, str) else str(content)
    except Exception:
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except Exception:
            return payload.decode("utf-8", errors="replace")


def _html_to_text(value: str) -> str:
    text = re.sub(r"(?is)<(script|style|head).*?>.*?</\1>", " ", value)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\s*>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = _html.unescape(text)
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", text)).strip()


def _parse_message(raw: bytes) -> Any:
    return BytesParser(policy=policy.default).parsebytes(raw)


def _attachment_entries(part: Any, path: str = "") -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    if part.is_multipart():
        for index, child in enumerate(part.iter_parts(), start=1):
            child_path = f"{path}.{index}" if path else str(index)
            entries.extend(_attachment_entries(child, child_path))
        return entries
    filename = part.get_filename()
    disposition = (part.get_content_disposition() or "").lower()
    if filename or disposition == "attachment":
        payload = part.get_payload(decode=True) or b""
        entries.append(
            {
                "part": path or "1",
                "filename": str(filename or "attachment"),
                "content_type": part.get_content_type(),
                "size": len(payload),
                "content": payload,
            }
        )
    return entries


def _find_attachment(part: Any, wanted: str, path: str = "") -> Optional[Dict[str, Any]]:
    if part.is_multipart():
        for index, child in enumerate(part.iter_parts(), start=1):
            child_path = f"{path}.{index}" if path else str(index)
            found = _find_attachment(child, wanted, child_path)
            if found:
                return found
        return None
    if (path or "1") == wanted:
        filename = part.get_filename()
        disposition = (part.get_content_disposition() or "").lower()
        if filename or disposition == "attachment":
            return {
                "part": path or "1",
                "filename": str(filename or "attachment"),
                "content_type": part.get_content_type(),
                "content": part.get_payload(decode=True) or b"",
            }
    return None


def list_folders(cred: Dict[str, Any]) -> List[str]:
    """Return the credential's allowed folders, verified against the server."""
    allowed = [str(item) for item in (cred.get("allowed_folders") or ["INBOX"]) if str(item).strip()]
    if not allowed:
        allowed = ["INBOX"]
    with _connection(cred) as conn:
        try:
            status, data = conn.list()
        except imaplib.IMAP4.error:
            status, data = "NO", []
        if status != "OK":
            return allowed
        names: List[str] = []
        for item in data or []:
            if isinstance(item, (bytes, bytearray)):
                text = bytes(item).decode("utf-8", errors="replace")
                match = re.search(r'"([^"]+)"\s*$', text)
                if match:
                    names.append(match.group(1))
        configured = {item.upper(): item for item in allowed}
        return [configured[name.upper()] for name in names if name.upper() in configured] or allowed


def list_messages(
    cred: Dict[str, Any],
    folder: str = "INBOX",
    from_addr: str = "",
    subject: str = "",
    text: str = "",
    since: str = "",
    before: str = "",
    unseen: bool = False,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    max_messages, _, _, _ = _credential_limits(cred)
    count = _limit(limit, 20, max_messages)
    criteria = _search_criteria(from_addr, subject, text, since, before, unseen)
    with _connection(cred) as conn:
        selected = _select(conn, cred, folder)
        try:
            status, data = conn.uid("search", None, *criteria)
        except imaplib.IMAP4.error as exc:
            raise MailError("MAIL_UPSTREAM", f"search failed: {exc}", status_code=502)
        if status != "OK":
            raise MailError("MAIL_UPSTREAM", "search failed", status_code=502)
        raw_uids = data[0] if data else b""
        uids = raw_uids.split() if isinstance(raw_uids, (bytes, bytearray)) else []
        selected_uids = [uid.decode() for uid in uids[-count:]]
        selected_uids.reverse()

        messages: List[Dict[str, Any]] = []
        for uid in selected_uids:
            try:
                status, fetched = conn.uid(
                    "fetch",
                    uid,
                    "(FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID CONTENT-TYPE)])",
                )
            except imaplib.IMAP4.error as exc:
                raise MailError("MAIL_UPSTREAM", f"fetch {uid} failed: {exc}", status_code=502)
            if status != "OK":
                continue
            raw = _fetch_bytes(fetched)
            meta = _fetch_meta(fetched)
            message = _parse_message(raw)
            flags_match = re.search(r"FLAGS \(([^)]*)\)", meta, re.IGNORECASE)
            size_match = re.search(r"RFC822\.SIZE (\d+)", meta, re.IGNORECASE)
            content_type = str(message.get("Content-Type") or "").lower()
            date_header = message.get("Date")
            try:
                date_iso = parsedate_to_datetime(date_header).isoformat() if date_header else ""
            except Exception:
                date_iso = str(date_header or "")
            messages.append(
                {
                    "uid": uid,
                    "folder": selected,
                    "from": _decode_header(message.get("From")),
                    "to": _decode_header(message.get("To")),
                    "cc": _decode_header(message.get("Cc")),
                    "subject": _decode_header(message.get("Subject")),
                    "date": date_iso,
                    "message_id": str(message.get("Message-ID") or "").strip(),
                    "flags": flags_match.group(1).split() if flags_match else [],
                    "size": int(size_match.group(1)) if size_match else len(raw),
                    "has_attachments": "multipart/mixed" in content_type or "name=" in content_type,
                }
            )
        return messages


def read_message(cred: Dict[str, Any], uid: Any, folder: str = "INBOX") -> Dict[str, Any]:
    uid_value = _as_uid(uid)
    _, max_body, _, max_message = _credential_limits(cred)
    with _connection(cred) as conn:
        selected = _select(conn, cred, folder)
        try:
            status, data = conn.uid("fetch", uid_value, "(FLAGS RFC822.SIZE BODY.PEEK[])")
        except imaplib.IMAP4.error as exc:
            raise MailError("MAIL_UPSTREAM", f"fetch {uid_value} failed: {exc}", status_code=502)
        if status != "OK":
            raise MailError("MAIL_NOT_FOUND", f"message {uid_value} not found", status_code=404)
        meta = _fetch_meta(data)
        size_match = re.search(r"RFC822\.SIZE (\d+)", meta, re.IGNORECASE)
        reported_size = int(size_match.group(1)) if size_match else 0
        if reported_size and reported_size > max_message:
            raise MailError(
                "MAIL_TOO_LARGE",
                f"message is {reported_size} bytes (limit {max_message})",
                status_code=413,
            )
        raw = _fetch_bytes(data)
        if not raw:
            raise MailError("MAIL_NOT_FOUND", f"message {uid_value} has no content", status_code=404)

    message = _parse_message(raw)
    text_parts: List[str] = []
    html_parts: List[str] = []

    def walk(part: Any) -> None:
        if part.is_multipart():
            for child in part.iter_parts():
                walk(child)
            return
        ctype = part.get_content_type()
        if ctype == "text/plain":
            text_parts.append(_part_text(part))
        elif ctype == "text/html":
            html_parts.append(_part_text(part))

    walk(message)
    text = "\n\n".join(part.strip() for part in text_parts if part and part.strip())
    html = "\n\n".join(part.strip() for part in html_parts if part and part.strip())
    truncated = False
    if not text and html:
        text = _html_to_text(html)
    if len(text) > max_body:
        text = text[:max_body]
        truncated = True
    attachments = [
        {key: value for key, value in entry.items() if key != "content"}
        for entry in _attachment_entries(message)
    ]
    flags_match = re.search(r"FLAGS \(([^)]*)\)", meta, re.IGNORECASE)
    date_header = message.get("Date")
    try:
        date_iso = parsedate_to_datetime(date_header).isoformat() if date_header else ""
    except Exception:
        date_iso = str(date_header or "")
    return {
        "uid": uid_value,
        "folder": selected,
        "from": _decode_header(message.get("From")),
        "to": _decode_header(message.get("To")),
        "cc": _decode_header(message.get("Cc")),
        "subject": _decode_header(message.get("Subject")),
        "date": date_iso,
        "message_id": str(message.get("Message-ID") or "").strip(),
        "flags": flags_match.group(1).split() if flags_match else [],
        "text": text,
        "html": "" if truncated else html[:max_body],
        "truncated": truncated,
        "attachments": attachments,
    }


def download_attachment(
    cred: Dict[str, Any],
    uid: Any,
    part: Any,
    folder: str = "INBOX",
) -> Dict[str, Any]:
    uid_value = _as_uid(uid)
    part_value = _as_part(part)
    _, _, max_attachment, max_message = _credential_limits(cred)
    with _connection(cred) as conn:
        selected = _select(conn, cred, folder)
        try:
            status, data = conn.uid("fetch", uid_value, "(RFC822.SIZE BODY.PEEK[])")
        except imaplib.IMAP4.error as exc:
            raise MailError("MAIL_UPSTREAM", f"fetch {uid_value} failed: {exc}", status_code=502)
        if status != "OK":
            raise MailError("MAIL_NOT_FOUND", f"message {uid_value} not found", status_code=404)
        meta = _fetch_meta(data)
        size_match = re.search(r"RFC822\.SIZE (\d+)", meta, re.IGNORECASE)
        reported_size = int(size_match.group(1)) if size_match else 0
        if reported_size and reported_size > max_message:
            raise MailError(
                "MAIL_TOO_LARGE",
                f"message is {reported_size} bytes (limit {max_message})",
                status_code=413,
            )
        raw = _fetch_bytes(data)
        if not raw:
            raise MailError("MAIL_NOT_FOUND", f"message {uid_value} has no content", status_code=404)

    message = _parse_message(raw)
    found = _find_attachment(message, part_value)
    if not found:
        raise MailError(
            "MAIL_NOT_FOUND",
            f"attachment part {part_value} not found in message {uid_value}",
            status_code=404,
        )
    content = found["content"]
    if len(content) > max_attachment:
        raise MailError(
            "MAIL_TOO_LARGE",
            f"attachment is {len(content)} bytes (limit {max_attachment})",
            status_code=413,
        )
    return {
        "uid": uid_value,
        "folder": selected,
        "part": part_value,
        "filename": found["filename"],
        "content_type": found["content_type"],
        "size": len(content),
        "content": content,
    }
