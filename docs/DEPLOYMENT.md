# Deployment Guide

## Local Development (Docker Compose)

### Prerequisites

- Docker 24+ with Docker Compose v1 (`docker-compose`)
- 16GB RAM recommended
- 20GB+ free disk space (base agent image is ~1.8GB)
- At least one LLM provider configured

### Start

```bash
cp .env.example .env    # configure LLM API keys
make up                 # starts all 10 services
make health             # verify everything is running
```

On first boot, the image-builder automatically builds the base agent image
(`openclaw-agent:openclaw`) and pushes it to the internal Docker registry.
This takes several minutes. Watch progress:

```bash
make logs-service SERVICE=image-builder
```

### Services & Ports

| Service | Host Port | Purpose |
|---------|-----------|---------|
| Frontend | 3000 | Next.js dashboard |
| Control Plane API | 8000 | REST API (docs at `/docs`) |
| Temporal UI | 8088 | Workflow inspector |
| PostgreSQL | 5432 | Primary database |
| DinD | 9100-9120 | Agent/deployment containers |

Internal-only services (no host port): image-builder (8002), temporal (7233),
temporal-postgres, registry (5000).

### Environment Variables

Set in `.env`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `POSTGRES_PASSWORD` | `openclaw_pass` | Database password |
| `JWT_SECRET` | `change-me-in-production` | JWT signing key |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama endpoint |
| `GEMINI_API_KEY` | — | Google Gemini |
| `ANTHROPIC_API_KEY` | — | Anthropic Claude |
| `OPENAI_API_KEY` | — | OpenAI |
| `API_URL` | `http://localhost:8000` | Frontend → API |

### Stopping

```bash
make down          # stop and remove containers (data preserved in volumes)
make clean         # stop, remove containers AND volumes (destructive)
```

---

## Volumes

| Volume | Contents |
|--------|----------|
| `postgres-data` | Primary database |
| `temporal-postgres-data` | Temporal's database |
| `registry-data` | Built Docker images |
| `docker-data` | DinD Docker state |
| `./workspaces/` | Per-task workspace files (bind mount) |
| `./agent-images/` | Generated Dockerfiles per task (bind mount) |

### Backup

```bash
make backup    # dumps DB + workspaces to ./backups/
```

### Restore

```bash
make restore BACKUP=backups/taskforge-20250216-120000.sql
```

---

## Scaling Workers

Scale Temporal workers for higher task throughput:

```bash
make scale-workers WORKERS=3
```

Each worker can execute one agent container at a time.

---

## Resource Considerations

| Component | Memory | CPU | Notes |
|-----------|--------|-----|-------|
| Control Plane | ~200MB | Low | Stateless FastAPI |
| Image Builder | ~200MB | High during builds | Calls Docker build API |
| Temporal Worker | ~150MB | Medium | Manages workflow execution |
| Temporal Server | ~500MB | Low | Workflow engine |
| PostgreSQL | ~200MB | Low | Primary state store |
| DinD | ~500MB + per container | Varies | Agent containers run here |
| Frontend | ~100MB | Low | Next.js SSR |
| Base image build | ~2GB temp | High | Only on first boot |

**Total steady state:** ~2GB. During agent execution, add ~500MB-1GB per active agent container.

---

## Monitoring (Optional)

Configuration files exist under `config/` for Prometheus, Grafana, Loki, and Promtail.
These are **not** included in the main `docker-compose.yml`. To activate them, create a
`docker-compose.override.yml` with the monitoring services.

---

## Known Issues

### Docker Compose v1 ContainerConfig Error

When rebuilding images, Docker Compose v1 may throw `KeyError: 'ContainerConfig'`.
Workaround:

```bash
docker rm -f openclaw-<service>
docker-compose up -d --no-deps <service>
```

### Slow First Boot

The base agent image (~1.8GB) is built on first startup. Subsequent starts are fast
because the image is cached in the internal registry.

### DinD Disk Usage

Agent images accumulate in the DinD Docker daemon. Periodically clean up:

```bash
docker exec openclaw-docker-dind docker system prune -f
```
