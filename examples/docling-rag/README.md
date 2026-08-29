# Docling RAG — showcase example

Demonstrates the platform driving an **external tool** end-to-end: the Docling
RAG service (moved out of the core compose into this example) is used to ingest
a PDF, search it with hybrid retrieval, and produce an approved report — with a
closed-loop approval (accept / reject → rework / cancel).

## What it shows
- Project-scoped DAG template + skill (`docling-rag` project).
- Agents using an **external tool**: a sample PDF served by `sample-docs`, then
  uploaded to Docling RAG, searched, and summarised into a report.
- A closed-loop approval gate on the report.
- Skill learning isolated to this project.

## Run
```bash
make example-up NAME=docling-rag
# open the frontend, pick project "docling-rag", instantiate the "rag-report" template
make example-down NAME=docling-rag
```

## Files
- `example.yaml` — manifest.
- `docker-compose.yml` — **Docling RAG + Ollama** (moved here from the core
  compose so the main `docker-compose.yml` stays clean), plus `sample-docs`
  (nginx) serving `data/sample.pdf` on the platform network.
- `config/dags/rag-report.json` — DAG template (fetch → upload RAG → search → report → approval loop).
- `config/skills/docling-rag.md` — skill describing the Docling RAG API usage.
- `data/sample.pdf` — the sample document ingested into the RAG.

