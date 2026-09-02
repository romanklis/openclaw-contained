# Docling RAG — sample PDF skill

Interact with the Docling RAG service at `http://docling-rag:8080`.

## Endpoints
- **Upload + index a PDF**
  `POST http://docling-rag:8080/api/upload` — multipart form with the file
  (field name `file`). A successful upload returns an ingestion receipt with a
  `document_id`. Wait a moment for indexing (poll `GET /api/documents` until
  the document's status is `indexed`).
- **List documents**
  `GET http://docling-rag:8080/api/documents` — returns the indexed documents
  with their status.
- **Search (hybrid retrieval)**
  `POST http://docling-rag:8080/api/search` with JSON
  `{"query": "...", "mode": "hybrid", "k": 5}` and header `X-Api-Key: docling-rag`.
- **Query a specific document**
  `POST http://docling-rag:8080/api/documents/{document_id}/query` with
  `{"query": "..."}` (uses the Ollama embeddings/LLM configured for docling).

## Guidance
- Use `curl` (or `httpx`) for these calls; verify each with the HTTP status
  and, for upload, that the returned `document_id` is present.
- After uploading, confirm the document reached `indexed` status before
  searching it; if not indexed, retry/wait.
- Prefer `mode: "hybrid"` and a small `k` (5–10) for the sample dataset.
- Do NOT fabricate search results: only report facts present in the returned
  chunks/snippets, and cite them.
