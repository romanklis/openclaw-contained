# TaskForge — Architecture
## Auditable Agent Orchestration for OpenClaw

## Overview

TaskForge is a policy-driven agent orchestration platform that enforces capability-based security
through human-in-the-loop approval workflows. Agents operate in sandboxed Docker containers and must
request capabilities (pip packages, apt packages, network access, etc.), which are granted through
a formal approval process and trigger an agent image rebuild.

Built on top of [OpenClaw](https://github.com/openclaw/openclaw).

## Core Principles

1. **Agent as Requesting Actor** — agents never self-authorize actions
2. **Policy-First Security** — all capabilities gated by enforced policies
3. **Immutable Infrastructure** — container images rebuilt when capabilities change
4. **Audit Everything** — complete history via Temporal workflows and LLM interaction logs
5. **Fail-Safe Defaults** — agents start maximally restricted, expand only when approved

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                          FRONTEND (Next.js)                         │
│  :3000                                                              │
│  ┌────────────┐ ┌─────────────┐ ┌──────────┐ ┌───────────────────┐ │
│  │ Dashboard  │ │ Task Detail │ │Approvals │ │ LLM Providers    │ │
│  │            │ │  + Audit    │ │          │ │ + Deployments    │ │
│  └────────────┘ └─────────────┘ └──────────┘ └───────────────────┘ │
└────────────────────────┬─────────────────────────────────────────────┘
                         │ HTTP (REST)
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     CONTROL PLANE (FastAPI)                          │
│  :8000                                                              │
│                                                                      │
│  ┌──────────────┐ ┌────────────────┐ ┌────────────────────────────┐ │
│  │ Task CRUD    │ │ Capability     │ │ LLM Router / Proxy         │ │
│  │ + Lifecycle  │ │ Approval       │ │ (Ollama, Gemini,           │ │
│  │              │ │ + Policy Mgmt  │ │  Anthropic, OpenAI)        │ │
│  └──────┬───────┘ └───────┬────────┘ └──────────────┬─────────────┘ │
│         │                 │                          │               │
│  ┌──────┴─────────────────┴──────────────────────────┴─────────────┐ │
│  │ PostgreSQL (tasks, policies, capabilities, outputs, llm_config) │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└──────────────┬───────────────────────────────────────────────────────┘
               │ gRPC
               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     TEMPORAL.IO (Workflow Engine)                     │
│  :7233 (internal)   UI :8088                                         │
│                                                                      │
│  ┌─────────────────────────┐                                        │
│  │ AgentTaskWorkflow       │ ← main loop (up to 50 iterations)      │
│  │  • initialize_task      │                                        │
│  │  • run_agent_step       │ ── runs container in DinD ──┐          │
│  │  • request_capability   │                              │          │
│  │  • wait for approval    │                              │          │
│  │  • build_agent_image    │ ── calls Image Builder ──┐   │          │
│  │  • finalize_task        │                          │   │          │
│  ├─────────────────────────┤                          │   │          │
│  │ DeploymentBuildWorkflow │                          │   │          │
│  │ DeploymentRunWorkflow   │                          │   │          │
│  └─────────────────────────┘                          │   │          │
└───────────────────────────────────────────────────────┼───┼──────────┘
                                                        │   │
               ┌────────────────────────────────────────┘   │
               ▼                                            ▼
┌──────────────────────────┐    ┌───────────────────────────────────┐
│  IMAGE BUILDER (FastAPI) │    │     DOCKER-IN-DOCKER (DinD)       │
│  :8002 (internal)        │    │                                   │
│                          │    │  ┌─────────────────────────────┐  │
│  • Auto-bootstraps base  │    │  │  Agent Container            │  │
│    openclaw-agent image   │    │  │  (openclaw-agent:task-X-vY) │  │
│    on startup            │    │  │                             │  │
│  • Builds agent images   │    │  │  • Runs openclaw CLI        │  │
│    with approved caps    │    │  │  • Calls LLM Router         │  │
│  • Builds deployment     │    │  │  • Writes deliverables      │  │
│    images from workspace │    │  │  • Reports output to API    │  │
│                          │    │  └─────────────────────────────┘  │
│  Uses Jinja2 templates   │    │                                   │
│  Pushes to Registry ─────┼──→ │  ┌─────────────────────────────┐  │
│                          │    │  │  Deployment Container       │  │
└──────────────────────────┘    │  │  (ports 9100-9120)          │  │
                                │  └─────────────────────────────┘  │
┌──────────────────────────┐    └───────────────────────────────────┘
│  DOCKER REGISTRY (v2)    │
│  :5000 (internal)        │
│                          │
│  Stores built images:    │
│  • openclaw-agent:openclaw (base)
│  • openclaw-agent:task-X-vY (per-task)
└──────────────────────────┘
```

---

## Running Services

| Service | Image / Build | Port (Host) | Purpose |
|---------|---------------|-------------|---------|
| **control-plane** | `./services/control-plane` | 8000 | Central API — tasks, policies, capabilities, LLM proxy |
| **image-builder** | `./services/image-builder` | — (8002 internal) | Builds agent & deployment Docker images |
| **temporal-worker** | `./services/temporal-worker` | — | Executes Temporal workflows & activities |
| **frontend** | `./frontend` | 3000 | Next.js dashboard UI |
| **postgres** | `postgres:15-alpine` | 5432 | Primary database |
| **temporal** | `temporalio/auto-setup:1.22` | — (7233 internal) | Workflow engine |
| **temporal-postgres** | `postgres:13` | — | Temporal's own database |
| **temporal-ui** | `temporalio/ui:2.40.1` | 8088 | Temporal workflow inspector |
| **docker-dind** | `docker:24-dind` | 9100-9120 | Docker-in-Docker for agent/deployment containers |
| **registry** | `registry:2` | — (5000 internal) | Internal Docker image registry |

**Total: 10 services** in `docker-compose.yml`.

---

## Component Details

### 1. Control Plane (FastAPI)

The central API server. Handles all external and internal communication.

**Route Groups:**

| Router | Prefix | Key Endpoints |
|--------|--------|---------------|
| `auth` | `/api/auth` | `POST /login` (dev: accepts any credentials), `GET /me` |
| `tasks` | `/api/tasks` | CRUD, start, pause, resume, complete, fail, logs |
| `tasks_extended` | `/api/tasks` | Dockerfiles, execution-timeline, outputs, messages, current-state |
| `capabilities` | `/api/capabilities` | List requests, create, review (approve/deny/suggest alternative) |
| `policies` | `/api/policies` | List, get, create version, get current for task |
| `llm` | `/api/llm` | Chat completions proxy, provider config, model listing |
| deployments | `/api/deployments` | Create, list, approve, start, stop |

**LLM Router / Proxy:**

The control plane includes a multi-provider LLM router that agents call via the
OpenAI-compatible endpoint `POST /api/llm/v1/chat/completions`. The router:

- Routes by model name prefix (`gemini*` → Google, `claude*` → Anthropic, `gpt-*`/`o1-*`/`o3-*`/`o4-*` → OpenAI, everything else → Ollama)
- Translates between OpenAI format and each provider's native format
- Logs every interaction (request + response + token counts) per task
- Supports SSE streaming for Gemini; other providers convert to SSE

**Supported LLM Providers:**

| Provider | Model Prefix | Config | Notes |
|----------|-------------|--------|-------|
| Ollama | *(default)* | `OLLAMA_URL` | Local inference, no API key needed |
| Gemini | `gemini*` | `GEMINI_API_KEY` | Native SSE streaming, thought caching |
| Anthropic | `claude*` | `ANTHROPIC_API_KEY` | Full format translation (tools, system prompts) |
| OpenAI | `gpt-*`, `o1-*`, `o3-*`, `o4-*` | `OPENAI_API_KEY` | Direct passthrough |

### 2. Image Builder

FastAPI service that builds Docker images inside DinD.

**Key behaviors:**

- **Auto-bootstrap on startup:** checks if `registry:5000/openclaw-agent:openclaw` exists in the
  internal registry. If not, builds it from `agent-images/base/Dockerfile` and pushes it. This
  makes the platform fully self-contained — no external image pulls needed after first boot.
  First build takes several minutes (~1.8GB image).
- **Agent image builds:** `POST /api/build` — generates a Dockerfile from Jinja2 templates that
  layers approved capabilities (pip, apt, npm packages) on top of the base image.
- **Deployment image builds:** `POST /api/deployments/build` — builds minimal Python images
  from workspace files for running deployed applications.
- **Build status polling:** `GET /api/build/{build_id}` — returns build status and logs.

### 3. Temporal Worker

Connects to Temporal and registers workflows + activities.

**Workflows:**

| Workflow | Purpose |
|----------|---------|
| `AgentTaskWorkflow` | Main agent execution loop — initialize → run steps (up to 50 iterations) → handle capability requests (pause for human approval signal, rebuild image) → handle deployments → finalize |
| `DeploymentBuildWorkflow` | Build a deployment image after approval |
| `DeploymentRunWorkflow` | Start or stop a deployment container |

**Activities (11 total):**

| Activity | Status | Description |
|----------|--------|-------------|
| `initialize_task` | Stub | Returns True; workspace setup is a TODO |
| `run_agent_step` | ✅ | Runs agent container in DinD, extracts result from stdout markers, fetches LLM interaction logs |
| `store_agent_output` | ✅ | POSTs iteration output to control plane |
| `request_capability` | ✅ | Creates capability request via control plane |
| `build_agent_image` | ✅ | Calls image builder, polls until complete |
| `update_task_policy` | Stub | Returns `{"updated": True}`; policy update is a TODO |
| `finalize_task` | ✅ | Marks task complete or failed |
| `create_deployment` | ✅ | Creates deployment record |
| `build_deployment_image` | ✅ | Calls image builder for deployments |
| `start_deployment` | ✅ | Runs container, allocates host port 9100-9120 |
| `stop_deployment` | ✅ | Stops and removes container |

### 4. Agent Runtime (runs inside agent containers)

The code that runs **inside** agent containers spawned by DinD:

- **`openclaw-wrapper.py`** (primary entrypoint) — fetches task details from control plane,
  configures the LLM router URL as the model endpoint, invokes the OpenClaw CLI, intercepts
  capability requests, writes deliverables, and outputs structured result JSON via stdout markers.
- **`openclaw-wrapper.js`** — alternative JavaScript executor.
- **`agent.py`** — fallback executor class.

The agent calls the control plane's LLM proxy at `POST /api/llm/v1/chat/completions`,
which routes to the configured provider.

### 5. Frontend (Next.js 14)

| Route | Description |
|-------|-------------|
| `/` | Dashboard — task/deployment counts, pending approvals, recent activity |
| `/tasks` | Task list with status badges |
| `/tasks/[id]` | Task detail with 3 tabs: **Outputs** (deliverables, logs), **Audit Log** (LLM interactions, tool calls, token usage), **Timeline** (execution history) |
| `/approvals` | Capability approval queue |
| `/deployments` | Deployment management |
| `/llm-providers` | LLM provider configuration (API keys, Ollama URL) |

### 6. Database (PostgreSQL 15)

**9 tables:**

| Table | Purpose |
|-------|---------|
| `tasks` | Task definitions, status, workspace_id, current_image, llm_model, workflow references |
| `policies` | Versioned policy snapshots per task (tools, network, filesystem, database rules as JSON) |
| `capability_requests` | Capability requests with type, justification, status, decision notes |
| `task_outputs` | Per-iteration output: logs, deliverables, LLM response preview, model used, duration, raw_result JSON |
| `task_messages` | Conversation messages (agent/user/system roles) |
| `llm_provider_config` | Key-value store for LLM API keys and URLs |
| `deployments` | Deployment records with image, port, container_id, status |
| `audit_logs` | Action audit trail (table exists, not yet populated by code) |

---

## Data Flow

### Task Execution

```
1. User creates task via Frontend (/tasks)
      │
      ▼
2. Control Plane stores task in PostgreSQL,
   starts Temporal AgentTaskWorkflow
      │
      ▼
3. Temporal Worker picks up workflow
      │
      ▼
4. run_agent_step activity:
   a. Pulls agent image from internal registry
   b. Runs container in DinD with task ID, API URLs, workspace mount
   c. openclaw-wrapper.py inside container:
      • Fetches task details from Control Plane
      • Invokes OpenClaw CLI with configured model
      • OpenClaw calls LLM Router for inference
      • LLM Router proxies to configured provider
      • Agent writes deliverables to /workspace
      • Outputs result JSON via stdout markers
   d. Worker extracts result, fetches LLM interaction log
   e. Stores output via Control Plane API
      │
      ▼
5. If agent requests a capability:
   a. Worker creates capability request
   b. Workflow pauses — waits for approval signal
   c. Human reviews in Approvals UI
   d. On approve: Image Builder creates new image
      with approved packages, pushes to Registry
   e. Workflow resumes with new image → back to step 4
      │
      ▼
6. Task completes → finalize_task marks done
   Deliverables persist in workspaces/{task_id}/
```

### Capability Approval

```
Agent needs pandas → requests capability
    ↓
Control Plane creates CapabilityRequest (status: pending)
    ↓
Temporal workflow pauses (wait_condition)
    ↓
Human sees request in Approvals UI
    ↓
├─ Approve → signal sent to workflow
│     ↓
│   Image Builder generates Dockerfile:
│     FROM openclaw-agent:openclaw
│     RUN pip install pandas
│     ↓
│   Builds → tags as openclaw-agent:task-X-v2
│     ↓
│   Pushes to internal registry
│     ↓
│   Workflow resumes with new image
│
├─ Deny → signal sent, workflow continues without capability
│
└─ Suggest Alternative → reviewer proposes different package
```

---

## Environment Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `POSTGRES_PASSWORD` | `openclaw_pass` | PostgreSQL password |
| `JWT_SECRET` | `change-me-in-production` | JWT signing secret |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama endpoint |
| `GEMINI_API_KEY` | *(none)* | Google Gemini API key |
| `ANTHROPIC_API_KEY` | *(none)* | Anthropic Claude API key |
| `OPENAI_API_KEY` | *(none)* | OpenAI API key |
| `API_URL` | `http://localhost:8000` | Frontend → Control Plane URL |

---

## Security Model

### Current Implementation

| Layer | Mechanism |
|-------|-----------|
| **Container isolation** | Each agent runs in a separate Docker container inside DinD |
| **Capability gating** | Agents start with base image only; new packages require human approval + image rebuild |
| **Network isolation** | Agent containers are on the DinD internal network; no direct internet access by default |
| **Filesystem isolation** | Each task gets its own workspace directory |
| **LLM audit trail** | Every LLM call logged with full request/response, token counts, provider info |
| **Temporal history** | Complete workflow execution history, replayable and immutable |

### Not Yet Implemented

- Seccomp / AppArmor profiles for agent containers
- Read-only root filesystems
- External secrets management (Vault)
- Multi-approver policies
- Network egress proxy with domain whitelisting
- Database access proxy

---

## Monitoring (Optional)

Configuration files exist under `config/` for an optional monitoring stack:

- **Prometheus** (`config/prometheus/prometheus.yml`)
- **Grafana** (`config/grafana/`)
- **Loki** (`config/loki/loki-config.yml`)
- **Promtail** (`config/promtail/promtail-config.yml`)

These are **not** included in the main `docker-compose.yml` and must be activated
separately if needed.

---

## Project Structure

```
openclaw-contained/
├── docker-compose.yml          # 10 services — the full platform
├── Makefile                    # Build, start, stop, health checks
├── .env.example                # Environment variable template
│
├── services/
│   ├── control-plane/          # FastAPI API server
│   │   ├── main.py             # App entry, CORS, health, startup
│   │   ├── models.py           # SQLAlchemy models (9 tables)
│   │   ├── schemas.py          # Pydantic request/response schemas
│   │   ├── database.py         # Async PostgreSQL session
│   │   ├── config.py           # Environment configuration
│   │   ├── temporal_client.py  # Temporal connection helper
│   │   └── routers/
│   │       ├── auth.py         # JWT auth (dev mode)
│   │       ├── tasks.py        # Task CRUD + lifecycle
│   │       ├── tasks_extended.py # Outputs, timeline, messages
│   │       ├── capabilities.py # Capability requests + review
│   │       ├── policies.py     # Policy versioning
│   │       └── llm.py          # LLM router (~1500 lines)
│   │
│   ├── image-builder/          # Docker image builder
│   │   ├── main.py             # Build API + auto-bootstrap
│   │   └── templates/          # Jinja2 Dockerfile templates
│   │
│   ├── temporal-worker/        # Temporal workflow worker
│   │   └── worker.py           # 3 workflows, 11 activities
│   │
│   └── agent-executor/         # Code that runs INSIDE agent containers
│       ├── openclaw-wrapper.py # Primary agent entrypoint
│       ├── openclaw-wrapper.js # Alternative JS executor
│       ├── agent.py            # Fallback executor class
│       └── Dockerfile.openclaw # Base agent image definition
│
├── agent-images/
│   ├── base/                   # Base agent runtime files
│   │   ├── Dockerfile          # Base image build definition
│   │   ├── agent_runtime.py    # Agent runtime (early prototype, unused)
│   │   └── config.py           # Agent config
│   └── task-*/                 # Generated Dockerfiles per task
│
├── frontend/                   # Next.js 14 dashboard
│   ├── app/
│   │   ├── page.tsx            # Dashboard
│   │   ├── tasks/page.tsx      # Task list
│   │   ├── tasks/[id]/page.tsx # Task detail (outputs, audit, timeline)
│   │   ├── approvals/page.tsx  # Capability approvals
│   │   ├── deployments/        # Deployment management
│   │   └── llm-providers/      # LLM provider config
│   └── Dockerfile              # Multi-stage Next.js build
│
├── openclaw/                   # OpenClaw CLI (mounted into agent images)
├── workspaces/                 # Per-task workspace directories
├── config/                     # Optional monitoring configs
└── scripts/
    └── init-db.sh              # Database initialization
```

---

## Known Limitations

1. **Auth is dev-mode only** — `POST /api/auth/login` accepts any credentials
2. **`initialize_task` is a stub** — returns True without workspace setup
3. **`update_task_policy` is a stub** — returns `{"updated": True}`
4. **`audit_logs` table exists but no code writes to it** — audit is via Temporal history + task_outputs
5. **`agent-images/base/agent_runtime.py`** is an unused prototype — the real agent code is `services/agent-executor/openclaw-wrapper.py`
6. **No API gateway** — control plane is exposed directly on port 8000
7. **Base image is ~1.8GB** — first boot takes several minutes to build and push
8. **Docker Compose v1** — uses `docker-compose` (v1.29); may hit `ContainerConfig` KeyError on image rebuilds — workaround is to `docker rm -f` the container and re-run
