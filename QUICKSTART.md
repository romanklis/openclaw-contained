# Quick Start Guide

Get TaskForge running in under 10 minutes.

## Prerequisites

- **Docker 24+** with Docker Compose v1 (`docker-compose`)
- **16GB+ RAM** recommended (base agent image is ~1.8GB)
- **20GB+ free disk** space
- At least one LLM provider: local [Ollama](https://ollama.ai), or a cloud API key (Gemini, Anthropic, OpenAI)

Verify Docker is installed:

```bash
docker --version
docker-compose --version
```

## 1. Clone and Configure

```bash
git clone <repo-url> openclaw-contained
cd openclaw-contained
cp .env.example .env
```

Edit `.env` to configure your LLM provider. At minimum, set one of:

```bash
# Option A: Local Ollama (default, no API key needed)
OLLAMA_URL=http://host.docker.internal:11434

# Option B: Cloud provider (pick one or more)
GEMINI_API_KEY=your-key-here
ANTHROPIC_API_KEY=your-key-here
OPENAI_API_KEY=your-key-here
```

## 2. Start the Platform

```bash
make up
```

This starts **10 services**. On first boot, the image-builder automatically builds the base
agent image and pushes it to the internal Docker registry. This one-time build takes a few minutes.

Watch the build progress:

```bash
make logs-service SERVICE=image-builder
```

## 3. Verify Everything Is Running

```bash
make health
```

Expected output:

```
  TaskForge Health Check
  ─────────────────────
  ✅  Control Plane    http://localhost:8000
  ✅  Frontend         http://localhost:3000
  ✅  Temporal UI      http://localhost:8088
  ✅  Docker-in-Docker
  ✅  Base agent image (in registry)
```

## 4. Open the UI

| Service | URL |
|---------|-----|
| **Frontend** | http://localhost:3000 |
| **API Docs** | http://localhost:8000/docs |
| **Temporal UI** | http://localhost:8088 |

## 5. Configure LLM Provider (UI)

Navigate to http://localhost:3000/llm-providers to set API keys and verify
which models are available.

## 6. Create Your First Task

**Via the UI:** Go to http://localhost:3000/tasks and click "New Task".

**Via the API:**

```bash
# Create a task
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Hello World",
    "description": "Write a Python function that prints hello world",
    "llm_model": "gemini-2.5-flash"
  }'

# Start it (replace {task_id} with the returned ID)
curl -X POST http://localhost:8000/api/tasks/{task_id}/start
```

## 7. Monitor Execution

- **Frontend:** http://localhost:3000/tasks/{task_id} — outputs, audit log, timeline
- **Temporal UI:** http://localhost:8088 — workflow execution details

If the agent requests a new capability (e.g., needs `pandas`), it will appear in the
**Approvals** page at http://localhost:3000/approvals. Approve it, and the platform
will rebuild the agent image with the new package and resume execution.

---

## Common Commands

```bash
make up              # Start all services
make down            # Stop and remove containers
make health          # Quick health check
make ps              # Show running services
make logs            # Follow all logs
make logs-service SERVICE=control-plane   # Follow one service
make restart         # Restart all services
make clean           # Remove everything including volumes (destructive!)
make backup          # Backup database and workspaces
make check-base      # Verify base agent image exists
make build-base      # Force rebuild base agent image
make scale-workers WORKERS=3  # Scale temporal workers
```

## Troubleshooting

### First boot takes a long time

Normal — the base agent image (~1.8GB) is being built. Watch with:

```bash
make logs-service SERVICE=image-builder
```

### Services aren't healthy

```bash
docker-compose ps      # Check container states
docker-compose logs    # Check for errors
```

### Agent containers fail

```bash
# Check temporal worker logs
docker-compose logs temporal-worker

# Check containers inside DinD
docker exec openclaw-docker-dind docker ps -a
```

### ContainerConfig error on frontend rebuild

Docker Compose v1 bug. Workaround:

```bash
docker rm -f openclaw-frontend
docker-compose up -d --no-deps frontend
```

### Port conflicts

Change ports in `docker-compose.yml`:

```yaml
ports:
  - "3001:3000"  # Change frontend port
```

---

## What's Next

- **Architecture:** [ARCHITECTURE.md](ARCHITECTURE.md) — full system design
- **Policy Reference:** [docs/POLICY_SCHEMA.md](docs/POLICY_SCHEMA.md)
- **Deployment Guide:** [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)

## Cleanup

To completely remove TaskForge:

```bash
make clean
docker images | grep openclaw | awk '{print $3}' | xargs docker rmi -f
```
