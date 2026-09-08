# Google Photos Album — task example

Downloads every photo from a **public Google Photos shared album** by using a
real stealth browser (Camoufox) only to resolve the album and extract the image
URLs, then fetching each image with **curl_cffi** (the `lh3.googleusercontent.com`
CDN needs no authentication).

## Why this hybrid
Probing the short link `https://photos.app.goo.gl/GjN5cVQbmtrdAUHJ7` with plain
`curl_cffi` returns a JS bootstrap page with **no server-side redirect, no share
key, and no image URLs** (`AF1Qip…` / `lh3.googleusercontent.com` / `og:image` are
all absent). Google Photos resolves the album client-side, so a browser is needed
for discovery — but once you have the direct image URLs, curl_cffi downloads them
fast and reliably.

## Run
1. Platform up, `openclaw-agent:browser_v4` built (`make build-browser-v4`).
2. Import the template (`/dags` → New DAG from template → **Google Photos Album**,
   or `POST /api/dags -d @google-photos-album.dag.json`).
3. Run. The single `browser_v4` node:
   - opens the album in Camoufox and waits for the `photos.google.com/share/…` page,
   - scrolls to load all thumbnails and extracts the `lh3.googleusercontent.com`
     image URLs,
   - downloads each with curl_cffi into `/workspace/download-album-photos/`,
   - writes `manifest.json` ({album_url, resolved_page, files, downloaded}).
4. Inspect the deliverables (photos + manifest) in the node panel.

## Notes / caveats
- Google may present a consent/CAPTCHA wall from datacenter IPs; the agent is
  instructed to stop and report the blocker rather than fabricate URLs.
- Photo count depends on the album being public; private/restricted albums are out
  of scope for this example.
- Files are images served by Google's CDN — downloading a public album you have
  access to is for your own content.
