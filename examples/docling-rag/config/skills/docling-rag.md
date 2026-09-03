# Docling RAG — sample PDF skill

Interact with the Docling RAG service. Its base URL is provided to you (env
`RAG_TOOL_URL`, otherwise `http://docling-rag:8080`). Authenticate with
`X-Api-Key: <key>` — for the bundled example the key is `my-local-secret-key`.

Indexing is **asynchronous**: upload returns fast with a `document_id`, and the
document is only searchable after Docling parses + chunks + embeds it
(`processing: true → false`). Wait for that to finish in ONE blocking step —
do not poll with many separate turns and do not re-upload.

## Endpoints
- **Health** — `GET {base}/health`.
- **List documents (status)** — `GET {base}/api/documents`. Each document has
  `id`, `source_uri`, `title`, `status`, `processing`, `has_markdown`.
- **Upload + index a PDF** — `POST {base}/api/upload` — multipart form with the
  file (field name `file`) and header `X-Api-Key`. Returns an ingestion receipt
  with a `document_id`. If the response says the content was already present
  (`skipped`, content-addressed dedup), use the returned existing document id —
  it is already indexed, so do NOT upload again.
- **Search (hybrid retrieval)** — `POST {base}/api/search` with JSON
  `{"query": "...", "mode": "hybrid", "k": 5}` and header `X-Api-Key`.
- **Query a specific document** — `POST {base}/api/documents/{document_id}/query`
  with `{"query": "..."}`.

## Upload + wait (single blocking step)
1. Upload once with curl/httpx; capture `document_id` from the receipt.
2. Wait until that document is indexed by calling the **`wait` tool** (blocks in
   one turn, no extra LLM calls) with a command that exits 0 only when the
   document shows `processing == false`. Example predicate:
   ```bash
   K=my-local-secret-key; B=http://docling-rag:8080; ID=<document_id>
   python3 - "$K" "$B" "$ID" <<'PY'
   import json, sys, urllib.request
   key, base, did = sys.argv[1], sys.argv[2], sys.argv[3]
   req = urllib.request.Request(f"{base}/api/documents", headers={"X-Api-Key": key})
   docs = json.load(urllib.request.urlopen(req, timeout=15))
   items = docs.get("documents", docs if isinstance(docs, list) else [])
   m = [d for d in items if d.get("id") == did]
   ok = bool(m) and not m[0].get("processing", True)
   sys.exit(0 if ok else 1)
   PY
   ```
   Call `wait` with `{"command": "<predicate>", "timeout_seconds": 600}`. If it
   returns "timed out", call `wait` again with the same command — the job is
   simply still running.
3. Fallback (no `wait` tool): run one `exec` with a bounded sleep-poll loop
   (`for i in $(seq 1 45); do ...; sleep 5; done` ≈ 240s max), then a single
   quick re-check if still processing.

## Guidance
- Use `curl` (or `httpx`) for these calls; verify each with the HTTP status
  and, for upload, that the returned `document_id` is present.
- Never upload the same content twice — if `skipped`, proceed with the existing
  document.
- Only report a document as searchable after `processing == false`.
- Prefer `mode: "hybrid"` and a small `k` (5–10) for the sample dataset.
- Do NOT fabricate search results: only report facts present in the returned
  chunks/snippets, and cite them.
- Do NOT re-discover the API by exploring the service UI — the endpoints above
  are authoritative.
