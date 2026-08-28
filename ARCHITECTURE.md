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
6. **Supply-Chain Transparency** — every agent image gets an automatic SBOM (Software Bill of Materials) for full package visibility
7. **Supply-Chain Governance** — per-image-type package allowlists gate every capability request; denied packages are stripped before build and the agent receives actionable feedback
8. **Agent Diversity** — four base image types (OpenClaw, NanoBot, PicoClaw, ZeroClaw) with native adapters, selectable via Agent Profiles

---

## System Architecture

```
┌─────────────────────────────────┐  ┌──────────────────────────────────┐
│    OPEN WEBUI (Chat UI)         │  │       FRONTEND (Next.js)         │
│    :3001                        │  │       :3000                      │
│  Any OpenAI-compatible client   │  │  Dashboard, Tasks, Approvals,    │
│  (Open WebUI, LibreChat, curl)  │  │  Audit, SBOM, Deployments        │
└───────────────┬─────────────────┘  └──────────────┬───────────────────┘
                │ OpenAI API                         │ HTTP (REST)
                ▼                                    │
┌──────────────────────────────────┐                 │
│  API GATEWAY (FastAPI)           │                 │
│  :8080                           │                 │
│  POST /v1/chat/completions (SSE) │                 │
│  GET  /v1/models                 │                 │
│  GET  /v1/files/{task}/{iter}/.. │                 │
│  Session mgmt (Redis / in-mem)   │                 │
│  Fast-path LLM proxy             │                 │
│  Turn-by-turn streaming          │                 │
└───────────────┬──────────────────┘                 │
                │ HTTP (REST)            ┌───────────┘
                ▼                        ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     CONTROL PLANE (FastAPI)                          │
│  :8000                                                              │
│                                                                      │
│  ┌──────────────┐ ┌────────────────┐ ┌────────────────────────────┐ │
│  │ Task CRUD    │ │ Capability     │ │ LLM Router / Proxy         │ │
│  │ + Lifecycle  │ │ Approval       │ │ (Ollama, Gemini,           │ │
│  │ + System Info│ │ + Policy Mgmt  │ │  Anthropic, OpenAI)        │ │
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
│  ┌─────────────────────────────┐                                    │
│  │ AgentTaskWorkflow           │ ← main loop (up to 50 iterations)  │
│  │  • initialize_task          │                                    │
│  │  • AgentStepWorkflow (child)│ ── runs container in DinD ──┐      │
│  │     ├ start_agent_container │                              │      │
│  │     ├ poll_agent_turns      │                              │      │
│  │     ├ record_agent_turn ×N  │                              │      │
│  │     └ collect_agent_result  │                              │      │
│  │  • request_capability       │                              │      │
│  │  • wait for approval        │                              │      │
│  │  • build_agent_image        │ ── calls Image Builder ──┐   │      │
│  │  • finalize_task            │                          │   │      │
│  ├─────────────────────────────┤                          │   │      │
│  │ DeploymentBuildWorkflow     │                          │   │      │
│  │ DeploymentRunWorkflow       │                          │   │      │
│  └─────────────────────────────┘                          │   │      │
└───────────────────────────────────────────────────────────┼───┼──────┘
                                                        │   │
               ┌────────────────────────────────────────┘   │
               ▼                                            ▼
┌──────────────────────────┐    ┌───────────────────────────────────┐
│  IMAGE BUILDER (FastAPI) │    │   DOCKER-IN-DOCKER (Custom DinD)  │
│  :8002                   │    │   + gVisor (runsc)                │
│                          │    │                                   │
│  • Auto-bootstraps base  │    │  ┌ ─ ─ docker0 bridge ─ ─ ─ ─ ┐  │
│    openclaw-agent image   │    │  │ ┌─────────────────────────┐ │  │
│    on startup            │    │  │ │  Agent Container        │ │  │
│  • Builds agent images   │    │    │  runtime=runsc (gVisor)  │    │
│    with approved caps    │    │  │ │  (openclaw-agent:X-vY)  │ │  │
│  • Supply-chain gate     │    │    │                          │    │
│  • Builds deployment     │    │  │ │  • Runs native adapter  │ │  │
│                          │    │    │  • Calls LLM via IP     │    │
│  Uses Jinja2 templates   │    │  │ │  • Writes deliverables  │ │  │
│  Pushes to Registry ─────┼──→ │    └─────────────────────────┘    │
│                          │    │  │                               │  │
└──────────────────────────┘    │    ┌─────────────────────────┐    │
                                │  │ │  Deployment Container   │ │  │
┌──────────────────────────┐    │    │  (ports 9100-9120)      │    │
│  DOCKER REGISTRY (v2)    │    │  │ └─────────────────────────┘ │  │
│  :5000                   │    │  └ ─ ─ NAT via eth0 ─ ─ ─ ─ ─ ┘  │
│                          │    │                                   │
│  Stores built images:    │    │  IP watchdog: entrypoint-wrapper  │
│  • openclaw-agent:openclaw│   │  guards eth0 + docker0 addresses  │
│  • openclaw-agent:X-vY   │    └───────────────────────────────────┘
└──────────────────────────┘
```

---

## Running Services

| Service | Image / Build | Port (Host) | Purpose |
|---------|---------------|-------------|---------|
| **control-plane** | `./services/control-plane` | 8000 | Central API — tasks, policies, capabilities, LLM proxy |
| **image-builder** | `./services/image-builder` | 8002 | Builds agent & deployment Docker images, supply-chain validation |
| **temporal-worker** | `./services/temporal-worker` | — | Executes Temporal workflows & activities |
| **frontend** | `./frontend` | 3000 | Next.js dashboard UI |
| **postgres** | `postgres:15-alpine` | 5432 | Primary database |
| **temporal** | `temporalio/auto-setup:1.22` | — (7233 internal) | Workflow engine |
| **temporal-postgres** | `postgres:13` | — | Temporal's own database |
| **temporal-ui** | `temporalio/ui:2.40.1` | 8088 | Temporal workflow inspector |
| **docker-dind** | `./docker-dind` (custom) | 9100-9120 | Docker-in-Docker with gVisor/runsc for sandboxed agent containers |
| **registry** | `registry:2` | 5000 | Internal Docker image registry |
| **api-gateway** | `./services/api-gateway` | 8080 | OpenAI-compatible chat completions gateway (SSE streaming) |
| **open-webui** | `ghcr.io/open-webui/open-webui` | 3001 | Chat UI wired to the API Gateway |
| **redis** | `redis:7-alpine` | — (6379 internal) | Session store for API Gateway |

**Total: 13 services** in `docker-compose.yml`.

**Base Image Types:**

| Image | Base | Runtime | Package Managers | Adapter |
|-------|------|---------|------------------|---------|
| **openclaw** | Debian + Python 3.11 venv + Node.js | Full | pip, apt, npm | `openclaw-wrapper.py` |
| **nanobot** | Alpine + Python 3.11 | Lightweight | pip, apk | `taskforge-adapter.py` |
| **picoclaw** | Alpine (BusyBox) | Shell-only | apk | `picoclaw-adapter.sh` |
| **zeroclaw** | Debian + Python 3.11 + Rust | Compiled | pip, apt | `taskforge-adapter.py` |

---

## Component Details

### 1. Control Plane (FastAPI)

The central API server. Handles all external and internal communication.

**Route Groups:**

| Router | Prefix | Key Endpoints |
|--------|--------|---------------|
| `auth` | `/api/auth` | `POST /login` (dev: accepts any credentials), `GET /me` |
| `tasks` | `/api/tasks` | CRUD, start, pause, resume, complete, fail, logs |
| `tasks_extended` | `/api/tasks` | Dockerfiles, execution-timeline, outputs, messages, current-state, audit-turns |
| *(root)* | `/api/system/info` | Sandbox mode, security posture, version (used by SecurityBanner) |
| `capabilities` | `/api/capabilities` | List requests, create, review (approve/deny/suggest alternative) |
| `policies` | `/api/policies` | List, get, create version, get current for task |
| `llm` | `/api/llm` | Chat completions proxy, provider config, model listing |
| deployments | `/api/deployments` | Create, list, approve, start, stop |
| `sbom` | `/api/sbom`, `/api/tasks/{id}/sbom` | SBOM ingest, retrieval, version listing, diff, cross-task package search |

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
- **Multi-image support:** builds and pushes four base image types — `openclaw` (Debian+Python venv+Node),
  `nanobot` (Alpine+Python), `picoclaw` (Alpine shell-only), and `zeroclaw` (Debian+Python+Rust).
- **Agent image builds:** `POST /build` — generates a Dockerfile from Jinja2 templates that
  layers approved capabilities (pip, apt/apk, npm packages) on top of the base image.
  Image-type-aware: uses `apk` for Alpine-based images, `apt-get` for Debian-based.
- **Supply-chain validation:** Before building, every requested capability is checked against
  a per-image-type allowlist (`config/supply-chain.yaml`). Denied packages are stripped;
  if all are denied the build is skipped entirely. Includes Debian↔Alpine package alias
  translation (e.g. `libssl-dev` → `openssl-dev`) and feedback messages for the agent.
- **Deployment image builds:** `POST /deployments/build` — builds minimal deployment images
  from workspace files. Base image is chosen per image type (Alpine for picoclaw/nanobot,
  `python:3.11-slim` for openclaw/zeroclaw). Import scanning detects third-party Python
  packages from the workspace source files.
- **Build status polling:** `GET /builds/{build_id}` — returns build status and logs.
- **SBOM generation:** After every successful image build, [Trivy](https://trivy.dev) scans the image
  and generates SBOMs in both **SPDX JSON** and **CycloneDX JSON** formats. The SBOMs are POSTed
  to the control plane for storage. Trivy is installed directly in the image-builder container
  with a pre-cached vulnerability database.
- **Vulnerability scanning:** `POST /scan/vulnerabilities` — on-demand Trivy vulnerability scan
  for any image in the registry. Returns a flat list of CVEs with severity, package, and fix version.
- **On-demand SBOM:** `POST /scan/sbom` — generate an SBOM for any existing image without rebuilding.

**Supply-chain endpoints:**

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/supply-chain/check` | POST | Pre-flight validation — check if capabilities would pass before building |
| `/supply-chain/config` | GET | Return the full allowlist config (for audit UI) |
| `/supply-chain/reload` | POST | Hot-reload `supply-chain.yaml` from disk without restart |

**Supply-chain config (`config/supply-chain.yaml`):**

The single source of truth for what packages agents can install. Structure:

```yaml
aliases:
  apt_to_apk: { libssl-dev: openssl-dev, ... }  # Debian→Alpine translation
  apk_to_apt: { openssl-dev: libssl-dev, ... }  # Alpine→Debian translation
openclaw:   { pip: [...], apt: [...], npm: [...] }
nanobot:    { pip: [...], apk: [...] }
picoclaw:   { apk: [...] }  # no pip, no npm
zeroclaw:   { pip: [...], apt: [...] }
```

The git log of this file IS the audit trail. To add a new package: verify it
exists in the target repo, add it under the correct image type, and commit.

### 3. Temporal Worker

Connects to Temporal and registers workflows + activities.

**Workflows:**

| Workflow | Purpose |
|----------|---------|
| `AgentTaskWorkflow` | Main agent execution loop — initialize → run steps (up to 50 iterations) → handle capability requests (pause for human approval signal, rebuild image) → handle deployments → finalize |
| `DeploymentBuildWorkflow` | Build a deployment image after approval |
| `DeploymentRunWorkflow` | Start or stop a deployment container |

**Activities (13 total):**

| Activity | Status | Description |
|----------|--------|-------------|
| `initialize_task` | Stub | Returns True; workspace setup is a TODO |
| `start_agent_container` | ✅ | Launches agent container in DinD (detached); applies `runtime=runsc` for gVisor or `privileged=true` for insecure-dind. Always pulls mutable base image tags; caches versioned (`-vN`) tags. Returns container_id, image, status, sandbox_mode |
| `poll_agent_turns` | ✅ | Polls LLM router for new turn data while agent container runs |
| `record_agent_turn` | ✅ | Records a single LLM turn as a separate Temporal activity (visible in UI) |
| `collect_agent_result` | ✅ | Reads final result from container stdout after exit |
| `store_agent_output` | ✅ | POSTs iteration output to control plane |
| `request_capability` | ✅ | Creates capability request via control plane |
| `build_agent_image` | ✅ | Calls image builder, polls until complete. Returns `{image, feedback, denied}` dict. Supply-chain feedback is injected into the agent's follow-up as a one-shot `SYSTEM NOTICE` |
| `update_task_policy` | ✅ | PATCHes the task's `current_image` on the control plane |
| `finalize_task` | ✅ | Marks task complete or failed |
| `create_deployment` | ✅ | Creates deployment record |
| `build_deployment_image` | ✅ | Calls image builder for deployments |
| `start_deployment_container` | ✅ | Runs deployment container, allocates host port 9100-9120 |
| `stop_deployment_container` | ✅ | Stops and removes deployment container |

**Supply-chain feedback loop:**

When the image builder denies packages (supply-chain violation) or a build fails,
the worker injects a one-shot feedback message into the agent's next iteration
via `_capability_feedback`. The agent sees a `--- SYSTEM NOTICE ---` block
telling it exactly which packages were denied and why, so it can adapt its
approach. This also fires when the user manually denies a capability request.

**Cached Docker Client:**

The worker uses a module-level cached Docker client (`get_docker_client()`) connected to DinD
via `DOCKER_HOST=tcp://docker-dind:2375`. The client pins API version 1.43 to skip the
`/version` round-trip, reconnects automatically on stale connections, and retries with
exponential backoff if DinD is not yet ready.

**Service Discovery for Agent Containers:**

Agent containers run on DinD's internal `docker0` bridge in their own network namespace —
not `network_mode="host"`. They reach Compose services (control-plane, LLM router) via
NAT through DinD's `eth0`. The worker pre-resolves all service DNS names to IP addresses
using a `_resolve()` helper and injects them as environment variables, eliminating any DNS
dependency inside the sandbox.

### 4. Agent Runtime (runs inside agent containers)

The code that runs **inside** agent containers spawned by DinD. Each base image type
has its own native adapter that speaks the same protocol:

**Adapters (per base image):**

| Image Type | Adapter | Language | Notes |
|------------|---------|----------|-------|
| **openclaw** | `openclaw-wrapper.py` | Python | Full-featured — invokes OpenClaw CLI |
| **nanobot** | `taskforge-adapter.py` | Python | Lightweight — direct LLM tool loop, `Popen` + `os.killpg()` for exec |
| **picoclaw** | `picoclaw-adapter.sh` | Shell | Zero-Python — pure bash/curl/jq, `setsid` + cmdfile for exec |
| **zeroclaw** | `taskforge-adapter.py` | Python | Same as nanobot adapter, shared codebase |

**Common protocol (all adapters):**

- Reads `TASK_ID`, `LLM_MODEL`, `LLM_ROUTER_URL`, `CONTROL_PLANE_URL` from environment
- Calls the LLM router at `POST /api/llm/v1/chat/completions` with tool_calls
- Executes tool calls locally: `write`, `read`, `exec`, `edit`
- Detects `CAPABILITY_REQUEST` / `DEPLOYMENT_REQUEST` markers
- Writes result JSON with `===OPENCLAW_RESULT_JSON_START===` markers
- Collects workspace deliverables

**Process tree management:**
- Python adapters: `subprocess.Popen(start_new_session=True)` + `os.killpg()` for clean exec timeout kills
- Shell adapter: `setsid` + writes command to a cmdfile to avoid quoting issues, kills process group via `kill -KILL -$pgid`

### 5. Frontend (Next.js 14)

| Route | Description |
|-------|-------------|
| `/` | Dashboard — task/deployment counts, pending approvals, recent activity |
| `/tasks` | Task list with status badges |
| `/tasks/[id]` | Task detail with 4 tabs: **Outputs** (deliverables, logs), **Audit Log** (LLM interactions, tool calls, token usage), **📦 Software Inventory** (SBOM packages, version diff, license info, raw download), **Timeline** (execution history) |
| `/approvals` | Capability approval queue |
| `/deployments` | Deployment management |
| `/llm-providers` | LLM provider configuration (API keys, Ollama URL) |

### 6. API Gateway (FastAPI)

OpenAI-compatible chat completions gateway that bridges stateless HTTP clients
(Open WebUI, LibreChat, curl) with TaskForge's stateful Temporal workflows.

**Key files:**
- `main.py` — SSE streaming engine, session resolution, fast-path detection
- `control_plane_client.py` — async HTTP client for all control-plane APIs
- `session_manager.py` — Redis-backed session store with deterministic ID derivation
- `schemas.py` — OpenAI-format Pydantic models
- `config.py` — Pydantic-settings configuration

**Endpoints:**

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/chat/completions` | POST | Main chat endpoint — creates/continues tasks, streams SSE |
| `/v1/models` | GET | Lists all LLM models + meta-models |
| `/v1/files/{task_id}/{iter}/{file}` | GET | Download deliverable files |
| `/v1/sessions/{id}` | GET/DELETE | Session inspection / reset |
| `/health` | GET | Health check |

**Streaming features:**
- Turn-by-turn progress with tool-call icons (⚡📝📖✏️🌐🔧)
- Capability approval lifecycle (request → approve → build → resume)
- **Streaming resumes after capability rebuild** — LLM turn probing detects agent resume immediately, so the user sees the new iteration's progress in real-time
- **Temporal workflow link** — clickable `[Track in Temporal UI]` link emitted at stream start
- **Deployment links** — running deployments show `[Open App]` with `localhost:{host_port}` URL; pending ones link to the dashboard for approval
- **Dashboard + Temporal links in footer** — terminal-state message includes `[Dashboard]` and `[Temporal]` links
- Deployment status summary at task completion with `[Manage Deployments]` link
- Fast-path proxy for Open WebUI meta-requests (title/tag/follow-up generation)

**Model & image selection:**
- `GET /v1/models` returns the configured LLM models (OpenAI-compatible).
- The planner assigns a **base image** (from the `agent_images` DB catalog, seeded from `agent-images/agent_profiles.yaml` → `base_images:`) and a **skill** to each DAG node.
- No agent-profile indirection: a model selects the LLM; a node selects its image.

**Session management:**
- Deterministic conversation ID from `model + system_prompt + first_user_message`
- Browser refresh reconnects to the same Temporal workflow
- Redis backend (optional, falls back to in-memory)

### 7. Database (PostgreSQL 15)

**10 tables:**

| Table | Purpose |
|-------|---------|
| `tasks` | Task definitions, status, workspace_id, current_image, llm_model, workflow references |
| `policies` | Versioned policy snapshots per task (tools, network, filesystem, database rules as JSON) |
| `capability_requests` | Capability requests with type, justification, status, decision notes |
| `task_outputs` | Per-iteration output: logs, deliverables, LLM response preview, model used, duration, raw_result JSON |
| `task_messages` | Conversation messages (agent/user/system roles) |
| `llm_provider_config` | Key-value store for LLM API keys and URLs |
| `deployments` | Deployment records with image, port, container_id, status |
| `sboms` | Software Bill of Materials per image version — SPDX/CycloneDX JSON documents, denormalized package list, generator info. Indexed by `task_id` and `(task_id, image_version)` |
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
4. AgentStepWorkflow (child workflow) per iteration:
   a. start_agent_container:
      - Resolves service IPs via _resolve() (service discovery)
      - Launches container in DinD with runtime=runsc (gVisor)
        or privileged=true (insecure-dind)
      - Agent runs on docker0 bridge, NAT via eth0
   b. poll_agent_turns → record_agent_turn (per turn):
      - Polls LLM router for new interactions
      - Each turn recorded as a separate Temporal activity
   c. openclaw-wrapper.py inside container:
      • Fetches task details from Control Plane (via IP)
      • Invokes OpenClaw CLI with configured model
      • OpenClaw calls LLM Router for inference (via IP)
      • LLM Router proxies to configured provider
      • Agent writes deliverables to /workspace
      • Outputs result JSON via stdout markers
   d. collect_agent_result:
      - Reads container exit, extracts stdout result
   e. Stores output via Control Plane API
      │
      ▼
5. If agent requests a capability:
   a. Worker creates capability request
   b. Workflow pauses — waits for approval signal
   c. Human reviews in Approvals UI
   d. On approve: Image Builder creates new image
      with approved packages, pushes to Registry
   d'. Trivy generates SPDX + CycloneDX SBOMs for the
       new image → POSTed to Control Plane → stored in DB
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
│   Trivy scans image → generates SPDX + CycloneDX SBOMs
│     ↓
│   SBOMs POSTed to Control Plane (stored in sboms table)
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
| `AGENT_SANDBOX_MODE` | `gvisor` | Agent container isolation: `gvisor` (recommended) or `insecure-dind` |
| `POSTGRES_PASSWORD` | `openclaw_pass` | PostgreSQL password |
| `JWT_SECRET` | `change-me-in-production` | JWT signing secret |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama endpoint |
| `GEMINI_API_KEY` | *(none)* | Google Gemini API key |
| `ANTHROPIC_API_KEY` | *(none)* | Anthropic Claude API key |
| `OPENAI_API_KEY` | *(none)* | OpenAI API key |
| `API_URL` | `http://localhost:8000` | Frontend → Control Plane URL |

---

## Security Model

### Sandbox Modes

| Mode | Runtime | `privileged` | Security | Use Case |
|------|---------|-------------|----------|----------|
| **`gvisor`** | `runsc` | `false` | ✅ Strong | Production & shared hosts |
| `insecure-dind` | runc (DinD) | `true` | ⚠️ Weak | Local development only |
| `dedicated-vm` | *(future)* | — | 🔒 Strongest | Firecracker microVMs |

### Current Implementation

| Layer | Mechanism |
|-------|-----------|
| **gVisor kernel isolation** | Each agent runs under `runtime=runsc` — a user-space kernel that intercepts syscalls and delivers VM-level isolation at container speed. No privileged mode required. |
| **Bridge network isolation** | Agent containers run on DinD's `docker0` bridge with NAT, not `network_mode="host"`. Each gets its own network namespace. |
| **Service discovery** | The worker pre-resolves Compose DNS names to IPs via `_resolve()` and injects them as env vars. No DNS dependency inside the sandbox. |
| **DinD network watchdog** | `entrypoint-wrapper.sh` snapshots eth0 and docker0 IPv4 addresses and restores them if flushed by dockerd or gVisor container launches. |
| **Capability gating** | Agents start with base image only; new packages require human approval + image rebuild |
| **Filesystem isolation** | Each task gets its own workspace directory |
| **LLM audit trail** | Every LLM call logged with full request/response, token counts, provider info |
| **Temporal history** | Complete workflow execution history, replayable and immutable |
| **SBOM supply-chain transparency** | Every agent image automatically scanned by Trivy; SPDX + CycloneDX SBOMs stored with denormalized package lists; cross-task package search enables rapid CVE triage; version diffs track what changed between builds |
| **Supply-chain allowlists** | Per-image-type package allowlists in `config/supply-chain.yaml` gate every capability request. Denied packages are stripped before build; the agent receives actionable feedback via `SYSTEM NOTICE`. Debian↔Alpine alias translation handles cross-distro package names. The git log of the config file serves as the audit trail |
| **Multi-layer insecure-mode warnings** | `make up` red banner, worker startup WARNING log, frontend SecurityBanner, `.env.example` comments |

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
├── docker-dind/                # Custom DinD image with gVisor
│   ├── Dockerfile              # docker:24-dind + runsc + iproute2
│   └── entrypoint-wrapper.sh   # Network watchdog (eth0/docker0 IP guard)
├── docker-dind-daemon.json     # DinD daemon config (insecure registry + runsc)
│
├── services/
│   ├── control-plane/          # FastAPI API server
│   │   ├── main.py             # App entry, CORS, health, startup
│   │   ├── models.py           # SQLAlchemy models (10 tables)
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
│   │       ├── sbom.py         # SBOM ingest, retrieval, diff, search
│   │       └── llm.py          # LLM router (~1500 lines)
│   │
│   ├── image-builder/          # Docker image builder + SBOM generator
│   │   ├── main.py             # Build API + auto-bootstrap + Trivy SBOM/vuln scanning
│   │   └── templates/          # Jinja2 Dockerfile templates
│   │
│   ├── temporal-worker/        # Temporal workflow worker
│   │   └── worker.py           # 3 workflows, 13 activities, cached Docker client
│   │
│   ├── api-gateway/            # OpenAI-compatible API Gateway
│   │   ├── main.py             # SSE streaming, session mgmt, fast-path
│   │   ├── control_plane_client.py # Async HTTP client
│   │   ├── session_manager.py  # Redis / in-memory session store
│   │   ├── schemas.py          # OpenAI-format Pydantic models
│   │   ├── config.py           # Pydantic-settings configuration
│   │   ├── Dockerfile          # Python 3.11-slim + uvicorn
│   │   └── requirements.txt    # FastAPI, httpx, redis, etc.
│   │
│   └── agent-executor/         # Code that runs INSIDE agent containers
│       ├── openclaw-wrapper.py # Primary agent entrypoint
│       ├── openclaw-wrapper.js # Alternative JS executor
│       ├── agent.py            # Fallback executor class
│       └── Dockerfile.openclaw # Base agent image definition
│
├── config/
│   └── supply-chain.yaml       # Per-image-type package allowlists (the supply-chain source of truth)
│
├── agent-images/
│   ├── profiles.yaml           # Agent Profiles registry (name, base_image, llm_model, tags, etc.)
│   ├── base/                   # Base agent runtime files (4 image types)
│   │   ├── Dockerfile          # openclaw (Debian + Python venv + Node.js)
│   │   ├── Dockerfile.nanobot  # nanobot (Alpine + Python)
│   │   ├── Dockerfile.picoclaw # picoclaw (Alpine, shell-only — no Python)
│   │   ├── Dockerfile.zeroclaw # zeroclaw (Debian + Python + Rust)
│   │   └── taskforge-adapter.py # Native Python adapter (nanobot, zeroclaw)
│   ├── picoclaw/
│   │   └── picoclaw-adapter.sh # Native shell adapter (bash/curl/jq — no Python)
│   └── task-*/                 # Generated Dockerfiles per task
│
├── frontend/                   # Next.js 14 dashboard
│   ├── app/
│   │   ├── layout.tsx          # Root layout (includes SecurityBanner)
│   │   ├── page.tsx            # Dashboard
│   │   ├── tasks/page.tsx      # Task list
│   │   ├── tasks/[id]/page.tsx # Task detail (outputs, audit, SBOM inventory, timeline)
│   │   ├── approvals/page.tsx  # Capability approvals
│   │   ├── deployments/        # Deployment management
│   │   ├── llm-providers/      # LLM provider config
│   │   ├── components/
│   │   │   └── SecurityBanner.tsx  # Amber warning when insecure-dind active
│   │   └── lib/api.ts          # API base URL helper
│   └── Dockerfile              # Multi-stage Next.js build
│
├── docs/
│   ├── GVISOR_SETUP.md         # gVisor installation & configuration guide
│   ├── DEPLOYMENT.md           # Deployment guide
│   └── POLICY_SCHEMA.md        # Policy schema reference
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
3. **`audit_logs` table exists but no code writes to it** — audit is via Temporal history + task_outputs
5. **`agent-images/base/agent_runtime.py`** is an unused prototype — the real agent code is `services/agent-executor/openclaw-wrapper.py`
6. **Base image is ~2.3GB** — first boot takes several minutes to build and push
8. **Docker Compose v1** — uses `docker-compose` (v1.29); may hit `ContainerConfig` KeyError on image rebuilds — workaround is to `docker rm -f` the container and re-run
9. **Docker SDK pinned to 6.1.3** — `docker==7.0.0` breaks with `requests>=2.32` (`urllib3` incompatibility). Worker pins `docker==6.1.3`, `requests<2.32.0`, `urllib3<2`
