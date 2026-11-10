# Examples

Each directory under `examples/` is a self-contained showcase of the OpenClaw
platform: it may bring up its own infrastructure (databases, mock APIs,
simulators) on the platform's Docker network, and it imports its DAG templates,
skills and agent images **under a project namespace** so examples stay isolated.

## Layout

```
examples/
├── _shared/example.sh           # example tooling (up/down/status/list)
├── banking-architecture/        # bank landscape showcase (mock bank API → approved report)
├── docling-rag/                 # external RAG tool showcase (PDF ingest → search → approved report)
├── dropbox-upload/              # credentialed web upload showcase
├── google-photos-album/         # browser + CDN download showcase
└── github-programming-push/     # build a tested CLI and push it to GitHub
```

## Showcases

- [Docling RAG](docling-rag/README.md)
- [Banking Architecture](banking-architecture/README.md)
- [GitHub Programming & Push](github-programming-push/README.md) — an agent
  writes and tests a Python CLI, pushes it to GitHub with a task-scoped token,
  and verifies the remote tree.

## Per-example structure

```
examples/<name>/
├── example.yaml            # manifest: name, description, infra, dags, skills, seed
├── docker-compose.yml      # OPTIONAL extra infrastructure (joins the platform network)
├── infra/                  # infra source: mock services, Dockerfiles, sql
├── config/
│   ├── dags/               # dag_json files → locked DAG templates
│   ├── skills/             # skill definitions
│   └── agent-images.yaml   # optional example-specific images
├── *.xml                   # optional draw.io architecture diagrams (linked from README)
├── data/                   # seed data + seed scripts
└── README.md               # what it demonstrates + how to run
```

## Usage

```bash
# list examples
make examples

# bring up an example: start its infra, import DAG templates/skills, seed data
make example-up NAME=banking-architecture

# tear down only the example infra (platform stays up)
make example-down NAME=banking-architecture
```

Examples without a `docker-compose.yml` (for example
`github-programming-push`) skip the infrastructure step and only import their
DAG templates and skills.

## Example manifest (`example.yaml`)

```yaml
name: banking-architecture
description: Bank landscape — accounts, transactions, approval loop
infra: docker-compose.yml
services:
  - { name: bank-mock, url: http://bank-mock:8080 }
dags:
  - config/dags/bank-onboarding.json
skills:
  - config/skills/bank-query.md
seed:
  - data/bank_seed.sql
```

## Rules
- Examples never modify core platform files (`services/`, `frontend/`, `docker-compose.yml`).
- Example infra joins the shared network `openclaw-contained_openclaw-network` so agents can reach it by service name.
- Setup is idempotent (safe to re-run).
- Examples may ship draw.io (`.xml`) architecture diagrams — open them in
  diagrams.net and link them from the example's README.
