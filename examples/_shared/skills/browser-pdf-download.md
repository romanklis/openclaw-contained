# Browser PDF discovery & download (curl_cffi)

Find PDF links on a web page and download them into the node workspace using **curl_cffi**
(TLS-impersonating HTTP), which is baked into the **browser_v4** image (`curl_cffi[cli]`,
plus `beautifulsoup4`, `lxml`, `html5lib`, `trafilatura`).

## Hard rules

- Connect **only** to the target site/domains provided in the step inputs; never scan or
  rediscover hosts.
- Download only files that are actually referenced by the page (or its same-site linked
  pages if the step allows) — never fabricate URLs, PDFs or metadata.
- A file counts as a PDF only if it is served as `application/pdf` **and** starts with the
  `%PDF-` magic bytes; otherwise record it as skipped with the reason.
- Write all outputs under `__node_dir__` (auto-collected as deliverables) and return a small
  JSON manifest; never embed file blobs in the return value.

## Inputs (typical)

- `page_url` (required): page to scan for PDF links.
- `allowed_domains` (optional): list of hostnames permitted for downloads (defaults to the
  `page_url` host). Off-domain links are skipped.
- `max_pdfs` (optional, default 10), `same_site_depth` (optional, default 0 = page only).

## Python API (preferred)

```python
import os, json, hashlib
from urllib.parse import urljoin, urlparse
from curl_cffi import requests          # TLS-impersonating HTTP client
from bs4 import BeautifulSoup

def fetch(url, session):
    # curl_cffi impersonates a real browser TLS/HTTP fingerprint
    return session.get(url, timeout=60, allow_redirects=True)

def discover_pdfs(html, base_url, sample=None):
    soup = BeautifulSoup(html, "lxml")
    urls = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        text = (a.get_text() or "").strip().lower()
        if href.lower().split("?")[0].endswith(".pdf") or ("pdf" in text and href.startswith("http")):
            urls.append(href)
    return list(dict.fromkeys(urls))     # de-dup, keep order

def download_pdf(url, out_dir, session, allowed):
    host = urlparse(url).hostname or ""
    if allowed and host not in allowed:
        return {"url": url, "skipped": f"host {host} not in allowed_domains"}
    r = session.get(url, timeout=120, allow_redirects=True)
    if r.status_code != 200:
        return {"url": url, "skipped": f"HTTP {r.status_code}"}
    ctype = (r.headers.get("content-type") or "").lower()
    body = r.content
    if "application/pdf" not in ctype or not body.startswith(b"%PDF-"):
        return {"url": url, "skipped": "not a PDF (content-type/magic mismatch)"}
    name = os.path.basename(urlparse(url).path) or "document.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    dest = os.path.join(out_dir, name)
    with open(dest, "wb") as fh:
        fh.write(body)
    return {"url": url, "file": name, "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "content_type": ctype}

def main(inputs):
    page_url = inputs["page_url"]
    out_dir = inputs.get("__node_dir__") or "/tmp"
    os.makedirs(out_dir, exist_ok=True)
    allowed = set(inputs.get("allowed_domains") or [urlparse(page_url).hostname or ""])

    # 1) Fetch the page with Chrome impersonation; fall back to other browsers on 403/503.
    sess = requests.Session(impersonate="chrome")
    page = fetch(page_url, sess)
    if page.status_code in (403, 429, 503):
        for imp in ("chrome110", "firefox", "safari"):
            page = requests.get(page_url, impersonate=imp, timeout=60)
            if page.status_code == 200:
                break
    if page.status_code != 200:
        raise RuntimeError(f"page fetch failed: HTTP {page.status_code}")

    # 2) Discover + download PDFs (cap at max_pdfs).
    pdfs, skipped = [], []
    for url in discover_pdfs(page.text, page_url)[: int(inputs.get("max_pdfs", 10))]:
        res = download_pdf(url, out_dir, sess, allowed)
        (skipped if res.get("skipped") else pdfs).append(res)

    manifest = {"source_url": page_url, "pdfs": pdfs, "skipped": skipped,
                "downloaded": len(pdfs)}
    with open(os.path.join(out_dir, "pdf_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    return manifest
```

## CLI fallback

`curl_cffi` ships with a CLI in this image:

```bash
curl_cffi --impersonate chrome -L -o out.pdf "<pdf-url>"
```

## When plain HTTP is not enough

browser_v4 has **no browser binary**. For JS-only pages, logins, or bot-protected sites,
call the credential-gateway / `agent_web` driver endpoints (credentialed web session) instead
of trying to render locally, then hand the returned HTML/URLs to the curl_cffi downloader
above. Never bypass a site's access controls.
