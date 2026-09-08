"""Camoufox-based interactive login for the credential gateway.

Used when a credential profile sets ``browser_login: true`` and its site blocks
plain curl_cffi form logins (e.g. Dropbox). The real stealth browser runs HERE,
in the gateway; the resulting cookies are imported into the gateway-owned
curl_cffi session and optionally persisted. The agent never touches the browser
or the secrets.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, List, Optional

CREDENTIAL_DIR = os.getenv("CREDENTIAL_DIR", "/data/credentials")
HEADLESS = os.getenv("CAMOUFOX_HEADLESS", "true").strip().lower() not in ("0", "false", "no")
HUMANIZE = os.getenv("CAMOUFOX_HUMANIZE", "true").strip().lower() not in ("0", "false", "no")
LOGIN_TIMEOUT = int(os.getenv("CAMOUFOX_LOGIN_TIMEOUT", "90"))
LOGIN_RETRIES = int(os.getenv("CAMOUFOX_LOGIN_RETRIES", "2"))


class InteractiveLoginRequired(Exception):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint


class BrowserManager:
    """Lazy, thread-safe facade around Camoufox."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    # -- cookie jar helpers ---------------------------------------------------
    def _jar(self) -> Optional[Dict[str, Any]]:
        from store import load_cookie_jar
        return load_cookie_jar(CREDENTIAL_DIR, self._credential_name)

    def _persist(self, cookies: list, origin: str) -> None:
        from store import save_cookie_jar
        save_cookie_jar(CREDENTIAL_DIR, self._credential_name, cookies, origin)

    # -- login ---------------------------------------------------------------
    def interactive_login(self, sess: Any, cred: Dict[str, Any]) -> Dict[str, Any]:
        """Run the login attempts in a clean non-asyncio worker thread (Camoufox
        sync API refuses to run inside an asyncio loop), then import the cookie
        jar into the curl_cffi session."""
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(self._login_worker, sess, cred).result()

    def _login_worker(self, sess: Any, cred: Dict[str, Any]) -> Dict[str, Any]:
        self._credential_name = cred.get("name") or ""
        profile_dir = os.path.join(CREDENTIAL_DIR, "profiles", _safe_name(self._credential_name))
        os.makedirs(profile_dir, exist_ok=True)

        last_hint = ""
        attempts = 1 + LOGIN_RETRIES
        for attempt in range(attempts):
            hint = self._attempt_login(sess, cred, profile_dir)
            if not hint:
                return {"ok": True, "origin": cred.get("login_origin") or "", "attempts": attempt + 1}
            last_hint = hint
            # A fresh attempt after a short, human-like pause occasionally
            # passes a transient risk checkpoint (never loop forever).
            time.sleep(5 + 3 * attempt)
        raise InteractiveLoginRequired(
            f"login still blocked after {attempts} attempts on {cred.get('login_url', '')}: {last_hint}",
            hint=last_hint,
        )

    def _attempt_login(self, sess: Any, cred: Dict[str, Any], profile_dir: str) -> str:
        """One login attempt. Returns '' on success, or a challenge hint."""
        from camoufox.sync_api import Camoufox

        login_url = cred.get("login_url") or ""
        if not login_url:
            raise InteractiveLoginRequired("browser_login credential is missing login_url")
        u_field = cred.get("username_field", "username")
        p_field = cred.get("password_field", "password")
        username = cred.get("username", "")
        password = cred.get("password", "")
        origin = cred.get("login_origin") or _netloc_origin(login_url)

        # Non-persistent sync context: the most compatible mode for the gateway
        # (persistent/humanize sync modes conflict with the surrounding event
        # loop). Stealth comes from Camoufox's default fingerprinting.
        kwargs = dict(headless=HEADLESS)
        try:
            with Camoufox(**kwargs) as browser:
                page = browser.new_page()
                page.goto(login_url, timeout=60000, wait_until="domcontentloaded")
                # Identifier step (Dropbox reveals the password field later).
                try:
                    page.fill(f'input[name="{u_field}"]', username, timeout=20000)
                except Exception:
                    page.fill('input[type="email"],input[name*="user"],input[name*="email"]', username, timeout=20000)
                try:
                    page.keyboard.press("Enter")
                except Exception:
                    pass
                # Password step.
                pwd_sel = (
                    f'input[name="{p_field}"]'
                    if p_field and p_field != "password"
                    else 'input[type="password"]:not([id*="hint"]):not([name*="autofill"])'
                )
                try:
                    page.wait_for_selector(pwd_sel, timeout=25000)
                    page.fill(pwd_sel, password, timeout=20000)
                    page.keyboard.press("Enter")
                except Exception:
                    try:
                        page.fill('input[type="password"]', password, timeout=10000)
                        page.keyboard.press("Enter")
                    except Exception:
                        pass

                hint = self._wait_authenticated(page, origin)
                if hint:
                    return hint

                cookies: List[Dict[str, Any]] = page.context.cookies()
                for c in cookies:
                    try:
                        sess.cookies.set(c.get("name", ""), c.get("value", ""), domain=c.get("domain", ""))
                    except Exception:
                        pass
                try:
                    self._persist(cookies, origin)
                except Exception:
                    pass
                return ""  # success
        except InteractiveLoginRequired:
            raise
        except Exception as exc:
            return f"browser error: {exc}"

    def _wait_authenticated(self, page: Any, origin: str) -> str:
        """Wait up to LOGIN_TIMEOUT for an authenticated state. Returns a
        challenge hint string, or '' when the login succeeded."""
        deadline = time.time() + LOGIN_TIMEOUT
        challenge = ""
        while time.time() < deadline:
            challenge = _detect_challenge(page)
            if challenge:
                return challenge
            try:
                url = page.url
            except Exception:
                url = ""
            cookies = page.context.cookies()
            auth_cookie = any(
                c.get("name") in ("t", "session", "sessionid", "auth", "authToken")
                and c.get("value")
                for c in cookies
            )
            authed_url = "/login" not in url and _same_origin(url, origin)
            if auth_cookie or authed_url:
                # Give the SPA a moment to settle and confirm.
                try:
                    page.wait_for_timeout(3000)
                except Exception:
                    pass
                return ""  # success
            time.sleep(3)
        return challenge or "timed out waiting for login; a 2FA/device-approval or CAPTCHA may be required"


def _netloc_origin(url: str) -> str:
    from urllib.parse import urlparse
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _safe_name(name: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name or "credential")[:80]


def _same_origin(url: str, origin: str) -> bool:
    try:
        return _netloc_origin(url) == origin
    except Exception:
        return False


def _detect_challenge(page: Any) -> str:
    try:
        body = page.content()[:40000].lower()
    except Exception:
        return ""
    checks = [
        ("reCAPTCHA", ("recaptcha", "g-recaptcha")),
        ("CAPTCHA", ("captcha", "verify you are human")),
        ("two-step / 2FA", ("two-step", "2fa", "two factor", "twofactor")),
        ("device approval", ("approve this device", "device approval", "new device", "device login")),
        ("email/phone code", ("verification code", "enter the code", "security code")),
    ]
    for label, needles in checks:
        if any(n in body for n in needles):
            return label
    return ""


class BrowserDriverError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def resolve_album_urls(album_url: str, headless: bool = HEADLESS,
                       max_scrolls: int = 10, scroll_wait: int = 3000,
                       page_timeout: int = 120000) -> Dict[str, Any]:
    """Resolve a JS-only shared album (e.g. Google Photos) inside Camoufox and
    return the direct media URLs. Runs in the gateway (outside the gVisor
    agent sandbox) so the agent never has to launch a browser."""
    from concurrent.futures import ThreadPoolExecutor

    def _run() -> Dict[str, Any]:
        from camoufox.sync_api import Camoufox

        with Camoufox(headless=headless) as browser:
            page = browser.new_page()
            page.goto(album_url, timeout=page_timeout, wait_until="domcontentloaded")
            try:
                page.wait_for_url("**photos.google.com/share/**", timeout=60000)
            except Exception:
                pass
            for _ in range(max_scrolls):
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                except Exception:
                    pass
                page.wait_for_timeout(scroll_wait)

            urls = page.evaluate(
                """() => {
                  const out = new Set();
                  const collect = (s) => { if (s) { const u = s.trim().split(' ')[0]; if (u && /^https?:\\/\\//.test(u)) out.add(u); } };
                  document.querySelectorAll('img[srcset], img[src], img[data-src], [style*="url("], a[href]').forEach(el => {
                    if (el.srcset) el.srcset.split(',').forEach(s => collect(s));
                    collect(el.src); collect(el.getAttribute('data-src')); collect(el.getAttribute('data-url'));
                    const st = (el.getAttribute && el.getAttribute('style')) || '';
                    const m = st.match(/url\\(['"]?([^'")]+)['"]?\\)/g) || [];
                    m.forEach(x => collect(x.replace(/^url\\(['"]?|['"]?\\)$/g, '')));
                    if (el.href) out.add(el.href);
                  });
                  return Array.from(out);
                }"""
            )
            # Also scan the rendered DOM for googleusercontent URLs as a fallback.
            html = page.content()[:1500000]
            page_url = page.url

        import re as _re
        for u in _re.findall(r"https://lh3\.googleusercontent\.com/[A-Za-z0-9_=.\-/?&%]+", html):
            urls.append(u)

        normalized: List[str] = []
        seen = set()
        for u in urls:
            u = u.split("=")[0] if "googleusercontent" in u else u
            if "lh3.googleusercontent.com" in u and "=w" not in u:
                u = f"{u}=w2560"
            if u not in seen and "lh3.googleusercontent.com" in u:
                seen.add(u)
                normalized.append(u)
        return {"resolved_page": page_url, "urls": normalized}

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_run).result(timeout=page_timeout / 1000 + 120)
    except Exception as exc:
        raise BrowserDriverError(f"album resolution failed: {exc}") from exc
