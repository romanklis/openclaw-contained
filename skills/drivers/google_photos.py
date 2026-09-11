"""Google Photos shared-album driver (verified working, no re-discovery).

Resolution happens in the credential GATEWAY (Camoufox runs there, outside the
gVisor agent sandbox): the agent calls the gateway's browser-driver endpoint and
only downloads the returned URLs with curl_cffi. Falls back to local Camoufox
only when CREDENTIAL_GATEWAY_URL is not set.

Usage from an agent:
    from google_photos import resolve_album_urls, download_photo
    result = resolve_album_urls("https://photos.app.goo.gl/GjN5cVQbmtrdAUHJ7")
    for i, url in enumerate(result["urls"], 1):
        ok = download_photo(url, f"/workspace/<node_id>/media_{i:03d}.jpg")
"""
from __future__ import annotations

import os
from typing import Any, Dict, List


def resolve_album_urls(album_url: str, headless: bool = True,
                       max_scrolls: int = 6) -> Dict[str, Any]:
    """Resolve the album via the credential gateway's browser driver, returning
    {resolved_page, urls: [direct image urls]}."""
    gateway = os.environ.get("CREDENTIAL_GATEWAY_URL", "").rstrip("/")
    if gateway:
        import httpx

        headers = {"Content-Type": "application/json"}
        # Worker-run blocks authenticate with the internal admin token; agents
        # (which do not have it set) use their task token instead.
        admin = os.environ.get("CREDENTIAL_GATEWAY_ADMIN_TOKEN", "")
        task_id = os.environ.get("TASK_ID", "")
        if admin:
            headers["X-Admin-Token"] = admin
        elif task_id:
            headers["X-Agent-Token"] = f"task:{task_id}"
        resp = httpx.post(
            f"{gateway}/v1/driver/browser/resolve-album",
            json={"url": album_url, "headless": headless, "max_scrolls": max_scrolls},
            headers=headers,
            timeout=240,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"gateway album resolution failed HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    # Fallback: resolve locally with Camoufox (only when no gateway is reachable).
    return _resolve_local(album_url, headless=headless, max_scrolls=max_scrolls)


def _resolve_local(album_url: str, headless: bool = True, max_scrolls: int = 6) -> Dict[str, Any]:
    from camoufox.sync_api import Camoufox

    with Camoufox(headless=headless) as browser:
        page = browser.new_page()
        page.goto(album_url, timeout=90000, wait_until="domcontentloaded")
        try:
            page.wait_for_url("**photos.google.com/share/**", timeout=60000)
        except Exception:
            pass
        for _ in range(max_scrolls):
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass
            page.wait_for_timeout(2500)

        urls = page.evaluate(
            """() => {
              const out = new Set();
              document.querySelectorAll('img[srcset], img[src], a[href]').forEach(el => {
                [el.src, ...(el.srcset ? el.srcset.split(',') : [])].forEach(s => {
                  const u = (s || '').trim().split(' ')[0];
                  if (u && /^https?:\\/\\//.test(u) && /(googleusercontent|media|cdn|images|static|photo)/i.test(u)) out.add(u);
                });
                if (el.href) out.add(el.href);
              });
              return Array.from(out);
            }"""
        )
        page_url = page.url

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


def download_photo(url: str, dest: str, timeout: int = 90) -> bool:
    """Download a single image URL with curl_cffi (impersonated TLS)."""
    from curl_cffi import requests

    r = requests.get(url, impersonate="chrome", timeout=timeout, allow_redirects=True)
    if r.status_code == 200 and r.content and len(r.content) > 1000:
        with open(dest, "wb") as f:
            f.write(r.content)
        return True
    return False


def download_album(album_url: str, out_dir: str) -> Dict[str, Any]:
    """One-call driver: resolve the album via the gateway and download every
    photo into out_dir, writing manifest.json. Returns the manifest dict.

    Run exactly once per album; do not re-implement or inspect internals.
    """
    import os
    import json as _json

    os.makedirs(out_dir, exist_ok=True)
    result = resolve_album_urls(album_url)
    manifest: Dict[str, Any] = {
        "source_url": album_url,
        "resolved_page": result.get("resolved_page", ""),
        "files": [],
        "downloaded": 0,
        "skipped": [],
    }
    downloaded = 0
    for i, url in enumerate(result.get("urls") or [], 1):
        if "lh3.googleusercontent.com" not in url:
            continue
        ext = ".jpg"
        try:
            import httpx as _hx

            probe = _hx.get(url, timeout=20, follow_redirects=True, headers={"Range": "bytes=0-64"})
            ctype = (probe.headers.get("content-type") or "").lower()
            if "png" in ctype:
                ext = ".png"
            elif "webp" in ctype:
                ext = ".webp"
            elif "gif" in ctype:
                ext = ".gif"
            dest = os.path.join(out_dir, f"media_{i:03d}{ext}")
            ok = download_photo(url, dest)
        except Exception as exc:
            manifest["skipped"].append({"url": url, "reason": str(exc)})
            continue
        if ok:
            size = os.path.getsize(dest)
            manifest["files"].append({"file": os.path.basename(dest), "url": url, "bytes": size})
            downloaded += 1
        else:
            manifest["skipped"].append({"url": url, "reason": "download returned no image data"})
    manifest["downloaded"] = downloaded
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        _json.dump(manifest, f, indent=2, ensure_ascii=False)
    return manifest
