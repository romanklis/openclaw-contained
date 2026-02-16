<p align="center">
  <img src="docs/images/thumbnail-taskforge-platform.png" alt="TaskForge Platform" width="720" />
</p>

# TaskForge

**Auditable Agent Orchestration for [OpenClaw](https://github.com/openclaw/openclaw)**

> **Disclaimer:** This project is not affiliated with or endorsed by the OpenClaw project.
> TaskForge is an independent orchestration layer that wraps the OpenClaw agent runtime.

---

## What It Does

TaskForge runs AI agents in sandboxed Docker containers with **capability-based security**.
Agents start with minimal permissions and must request new capabilities (packages, network access,
tools) through a human-in-the-loop approval process. Every approval triggers a container image
rebuild, and every LLM interaction is logged for audit.

![TaskForge Main Dashboard](docs/images/task-forge-main-dashboard.png)

**Key features:**

- **Sandboxed execution** — each agent runs in an isolated Docker container inside Docker-in-Docker
- **Capability gating** — agents start with a base image; new packages require explicit human approval
- **Image rebuilds** — approved capabilities are baked into a new Docker image (immutable infrastructure)
- **Multi-provider LLM routing** — Ollama, Gemini, Anthropic, and OpenAI via a unified OpenAI-compatible proxy
- **Full audit trail** — every LLM call logged with request/response, token counts, and provider info
- **Temporal workflows** — durable execution that survives crashes, with pause/resume for approvals
- **Deployment support** — agents can produce deployable applications served on ports 9100-9120

## Quick Start

### Prerequisites

- **Docker 24+** with Docker Compose v1 (`docker-compose`)
- **16GB+ RAM** recommended (base agent image build uses ~1.8GB)
- At least one LLM provider configured (Ollama local, or a cloud API key)

### 1. Clone and configure

```bash
git clone <repo-url>
cd openclaw-contained
cp .env.example .env
```

Edit `.env` with at least one LLM provider:

```bash
# Local Ollama (default — no key needed if Ollama is running on host)
OLLAMA_URL=http://host.docker.internal:11434

# Or cloud providers (set one or more)
GEMINI_API_KEY=your-key-here
ANTHROPIC_API_KEY=your-key-here
OPENAI_API_KEY=your-key-here
```

### 2. Start the platform

```bash
make up
```

This starts all 10 services. On first boot, the **image-builder** will automatically build
and push the base agent image (`openclaw-agent:openclaw`) to the internal registry. This takes
several minutes on the first run (~1.8GB image).

### 3. Verify everything is running

```bash
make health
```

### 4. Access the UI

| Service | URL | Purpose |
|---------|-----|---------|
| **Frontend** | http://localhost:3000 | Dashboard, tasks, approvals, LLM config |
| **Control Plane API** | http://localhost:8000 | REST API (FastAPI auto-docs at `/docs`) |
| **Temporal UI** | http://localhost:8088 | Workflow inspector |

### 5. Create and run a task

![Task Dashboard](docs/images/task-forge-task-dashboard.png)

Via the Frontend at http://localhost:3000/tasks, or via API:

```bash
# Create a task
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Analyze data",
    "description": "Process the CSV file and generate a summary report",
    "llm_model": "gemini-2.5-flash"
  }'

# Start it (replace {task_id} with the returned ID)
curl -X POST http://localhost:8000/api/tasks/{task_id}/start
```

![Create Task](docs/images/Create-task-dashboard.png)

The agent will execute inside a sandboxed container. If it needs additional packages,
a capability request will appear in the **Approvals** tab at http://localhost:3000/approvals.

![Capability Request Example](docs/images/capability-request-example.png)

---

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system design, including:
- 10-service Docker Compose topology
- Data flow diagrams
- Database schema
- Temporal workflow details
- Security model

### Capability Approval & Image Rebuild

When an agent requests a new capability, the workflow pauses for human review.
On approval, the image builder creates a new Docker image with the approved packages and the agent resumes.

![Rebuild Agent Image on Approval](docs/images/rebuild-agent-execution-docker-on-approval-with-new-capabilities.png)

### Execution Traceability

Every LLM interaction, tool call, and agent step is logged with full detail.

![Task Execution Traceability](docs/images/task-execution-tracebility.png)

### Services at a Glance

| Service | Port | Description |
|---------|------|-------------|
| **control-plane** | 8000 | FastAPI — tasks, policies, capabilities, LLM proxy |
| **image-builder** | *(internal)* | Builds agent Docker images, auto-bootstraps base image |
| **temporal-worker** | *(internal)* | Executes Temporal workflows (3 workflows, 11 activities) |
| **frontend** | 3000 | Next.js 14 dashboard |
| **postgres** | 5432 | Primary database (PostgreSQL 15) |
| **temporal** | *(internal)* | Temporal workflow engine |
| **temporal-postgres** | *(internal)* | Temporal's own database |
| **temporal-ui** | 8088 | Temporal workflow inspector |
| **docker-dind** | 9100-9120 | Docker-in-Docker for agent and deployment containers |
| **registry** | *(internal)* | Docker Registry v2 for built agent images |

### Deliverables

Agents produce deliverables — either as standalone files or as running applications deployed on ports 9100–9120.

![Deliverable — Single File](docs/images/deliverable-in-form-of-single-delivery.png)

![Deliverable — Running Application](docs/images/deliverable-in-form-of-running-application.png)

### LLM Providers

TaskForge includes a built-in LLM router that agents call via an OpenAI-compatible endpoint.
Configure providers via the UI at http://localhost:3000/llm-providers or via environment variables:

| Provider | Models | Config |
|----------|--------|--------|
| **Ollama** | Any local model (gemma3, qwen3, llama3, mistral, etc.) | `OLLAMA_URL` |
| **Gemini** | `gemini-2.5-flash`, `gemini-2.5-pro`, etc. | `GEMINI_API_KEY` |
| **Anthropic** | `claude-sonnet-4-20250514`, etc. | `ANTHROPIC_API_KEY` |
| **OpenAI** | `gpt-4o`, `o3`, `o4-mini`, etc. | `OPENAI_API_KEY` |

---

## Makefile Commands

```
make up              Start all services (first run builds base image)
make down            Stop and remove all containers
make build           Build all service images
make restart         Restart all services
make logs            Follow logs from all services
make logs-service    Follow logs from one service (SERVICE=control-plane)
make health          Check health of all services
make ps              Show running services
make clean           Stop everything and remove volumes
make backup          Backup database and workspaces
make scale-workers   Scale temporal workers (WORKERS=5)
```

---

## Development

### Project Structure

```
services/
├── control-plane/      # FastAPI API server (tasks, policies, LLM router)
├── image-builder/      # Docker image builder + auto-bootstrap
├── temporal-worker/    # Temporal workflows & activities
└── agent-executor/     # Code that runs INSIDE agent containers

frontend/               # Next.js 14 dashboard (6 routes)
agent-images/base/      # Base agent image definition
openclaw/               # OpenClaw CLI (mounted into agents)
workspaces/             # Per-task workspace directories
```

### Running a Service Locally

```bash
cd services/control-plane
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Database

PostgreSQL 15 with 9 tables. Schema is auto-created by SQLAlchemy on startup.
Connection: `postgresql://openclaw:openclaw_pass@localhost:5432/openclaw`

---

## Troubleshooting

### First boot takes a long time

The image-builder automatically builds the base agent image (~1.8GB) on first startup.
Watch progress with:

```bash
docker-compose logs -f image-builder
```

### ContainerConfig error on rebuild

Docker Compose v1 can hit a `KeyError: 'ContainerConfig'` when recreating containers.
Workaround:

```bash
docker rm -f openclaw-frontend
docker-compose up -d --no-deps frontend
```

### Services won't start

```bash
docker-compose logs          # check all logs
docker-compose ps            # check container states
make health                  # quick health check
```

### Agent container fails

```bash
# Check temporal worker logs for agent execution details
docker-compose logs temporal-worker

# Check DinD for container state
docker exec openclaw-docker-dind docker ps -a
```

---

## Environment Variables

| Variable | Default | Required | Purpose |
|----------|---------|----------|---------|
| `POSTGRES_PASSWORD` | `openclaw_pass` | No | PostgreSQL password |
| `JWT_SECRET` | `change-me-in-production` | No | JWT signing secret |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | No | Ollama endpoint URL |
| `GEMINI_API_KEY` | — | No | Google Gemini API key |
| `ANTHROPIC_API_KEY` | — | No | Anthropic Claude API key |
| `OPENAI_API_KEY` | — | No | OpenAI API key |
| `API_URL` | `http://localhost:8000` | No | Frontend → API URL |

At least one LLM provider must be configured for agents to function.

---

## Author

**Roman Pawel Klis, Dr. sc. ETH Zurich**
[LinkedIn](https://www.linkedin.com/in/roman-pawel-klis-3811994/)

Senior Data Scientist (Corporate Engineering) and AI Program Leader operating at a Senior Manager level. With 12+ years of experience delivering secure, enterprise-scale AI solutions in manufacturing and R&D. Expert in Agentic AI, Generative AI, and complex data architectures under strict governance (FDA-preparatory, ISO). Recognized for strategic leadership and mentorship — most recently demonstrated by guiding the 1st Prize winning team at the NASA Space Apps Challenge 2025 (Zurich).

💬 *If you'd like to discuss how TaskForge could be used in your organization — feel free to [reach out on LinkedIn](https://www.linkedin.com/in/roman-pawel-klis-3811994/).*

---

## License

Copyright © 2025-2026 Roman Pawel Klis.

See [LICENSE](LICENSE) for details.
