# Changelog

All notable changes to OpenClaw Contained / TaskForge are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased] — 2026-03-04

### Added

- **Dockerfile preview in Audit tab** — each iteration in the audit log now has a
  "🐳 View Dockerfile" button that expands an inline syntax-highlighted view of the
  exact Dockerfile used to build that iteration's agent image.
  - Iteration 1 always shows the base image description (no custom Dockerfile).
  - Iteration N shows `Dockerfile.(N-1)` — the image built after the (N-1)th
    capability approval.
  - The control-plane container now mounts `./agent-images` read-only so it can
    serve Dockerfile content via the new `GET /api/tasks/{id}/dockerfiles` endpoint.
  - `docker-compose.yml`: added `./agent-images:/agent-images:ro` volume and
    `AGENT_IMAGES_DIR` env var to the control-plane service.

- **Rich capability-approval justifications** — the approvals queue now shows
  structured context for every capability request.
  - The `openclaw-wrapper.py` agent now captures the agent's own reason text from
    `CAPABILITY_REQUEST` markers and attaches error context when a package import
    failure triggered the request.
  - `_resolve_package_versions()` queries `pip index versions` / `npm view` to
    resolve the latest available version of each requested package at request time.
  - Task description is fetched from the control-plane API and embedded in the
    justification string.
  - `CapabilityRequestResponse` schema gained `details: Optional[Dict]` and
    `alternative_suggestion: Optional[str]` fields.
  - The approvals page now displays: task description, package type badges, resolved
    version strings, agent's justification text, and detection detail.

- **Software Bill of Materials (SBOM) generation & tracking** — each agent container
  image now gets a machine-readable inventory of every installed component.
  - Trivy installed in the image-builder container; generates both **SPDX JSON** and
    **CycloneDX JSON** SBOMs automatically after every image build.
  - New `sboms` database table stores full SBOM documents with a denormalised package
    list for fast cross-task searching.
  - **API endpoints**:
    - `POST /api/sbom` — ingest endpoint (called by image-builder).
    - `GET /api/tasks/{id}/sbom` — latest SBOM for a task (with optional `?version=N`
      and `?format=` filters).
    - `GET /api/tasks/{id}/sbom/all` — list all SBOM versions.
    - `GET /api/tasks/{id}/sbom/diff?from_version=1&to_version=2` — package diff
      between two image versions (added / removed / changed).
    - `GET /api/sbom/search?package=flask&version=3.0.0` — find all tasks using a
      specific package (CVE triage).
  - **Frontend "Software Inventory" tab** on the task detail page showing packages,
    versions, types (pip/apt/npm), and licenses with filtering, sorting, version
    selector, inter-version diff view, and raw SBOM download.
  - Image-builder exposes `POST /scan/vulnerabilities` (Trivy JSON vuln report) and
    `POST /scan/sbom` (on-demand SBOM generation for existing images).
  - SBOM generation is non-blocking — a build is marked successful before the scan
    runs, so failures in Trivy never break the build pipeline.

- **gVisor-inside-DinD architecture** — agent containers run under gVisor's `runsc`
  runtime *inside* the Docker-in-Docker sidecar. A custom DinD image
  (`docker-dind/Dockerfile`) extends `docker:24-dind` with `runsc`, `iproute2`,
  and a network watchdog entrypoint. No host-level gVisor installation required.
- **`AGENT_SANDBOX_MODE` environment variable** — controls how agent containers are
  launched. Supported values:
  - `gvisor` — (recommended) uses `runtime="runsc"` with `privileged=false`.
  - `insecure-dind` — (dev-only) uses `privileged=true` + `network_mode="host"`.
- **DinD network watchdog (`entrypoint-wrapper.sh`)** — background process that
  snapshots eth0 and docker0 IPv4 addresses before dockerd starts, then polls
  every second and restores any addresses flushed by dockerd or gVisor container
  launches. Prevents connectivity loss to the Compose network.
- **Bridge isolation for agent containers** — agents now run on DinD's default
  `docker0` bridge in their own network namespace (not `network_mode="host"`).
  Outbound traffic is NATed through DinD's eth0 to the Compose network.
- **Service discovery pattern** — worker pre-resolves all Compose service DNS names
  to IP addresses via `_resolve()` helper and injects them as environment variables.
  Eliminates DNS dependency inside the gVisor sandbox.
- **Cached Docker client** — module-level `get_docker_client()` with explicit
  API version pin (1.43), automatic reconnect on stale connections, and
  exponential-backoff retries if DinD is not yet ready.
- **`GET /api/system/info` endpoint** — returns `sandbox_mode`, `sandbox_secure`,
  and `version`. Used by the frontend SecurityBanner.
- **`SecurityBanner` component** — global amber warning banner shown in the frontend
  when `AGENT_SANDBOX_MODE` is not `gvisor`. Fetches `/api/system/info`, dismissible
  per session, links to `docs/GVISOR_SETUP.md`.
- **Audit log sandbox metadata** — container environment section in the audit tab now
  shows image name (full-width, selectable, wraps long hashes), colored status
  indicator (● running / ✓ completed), and a sandbox mode badge (🛡️ gVisor or
  ⚠️ insecure-dind). Previously the image name was truncated in a narrow grid column.
- **Preflight security warning in Makefile** — `make up` prints a colour-coded
  terminal banner: red warning for `insecure-dind`, green confirmation for `gvisor`.
- **Startup-time sandbox log in Temporal Worker** — the worker logs the active
  sandbox mode once at boot (`WARNING` for insecure, `INFO` for gvisor).
- **Graceful `runsc` missing error** — if `gvisor` mode is selected but `runsc` is
  not registered with Docker, the worker logs a clear message pointing to
  `docs/GVISOR_SETUP.md`.
- **`docs/GVISOR_SETUP.md`** — full guide for installing gVisor on Ubuntu/WSL2,
  registering it with Docker, enabling it in OpenClaw, daemonless image builders
  (Kaniko/Buildah), and multi-agent data-exchange best practices.
- **`CHANGELOG.md`** — this file.

### Changed

- **`docker-compose.yml`**:
  - `docker-dind` now builds from `./docker-dind` (custom Dockerfile) instead of
    using `docker:24-dind` directly. Added healthcheck.
  - `AGENT_SANDBOX_MODE` passed through to both `temporal-worker` and `control-plane`.
  - `temporal-worker` depends on `docker-dind: service_healthy` (was `service_started`).
  - `registry` now exposes port 5000 to host.
- **`docker-dind-daemon.json`** — added `runsc` runtime entry alongside the existing
  `insecure-registries` config.
- **`services/temporal-worker/worker.py`**:
  - Module-level `AGENT_SANDBOX_MODE` constant replaces per-activity `os.getenv` calls.
  - Cached Docker client (`get_docker_client()`) replaces `docker.from_env()` calls.
  - `start_agent_container` now returns `image`, `status`, and `sandbox_mode` fields.
  - Service discovery via `_resolve()` helper — all endpoints injected as IPs.
  - gVisor mode uses `runtime="runsc"` on default bridge; insecure-dind uses
    `privileged=True` + `network_mode="host"`.
- **`services/temporal-worker/requirements.txt`** — pinned `docker==6.1.3`,
  `requests>=2.31.0,<2.32.0`, `urllib3>=1.26,<2` to fix Docker SDK compatibility
  with newer `requests`/`urllib3` versions.
- **`services/control-plane/routers/tasks_extended.py`** — audit-turns endpoint now
  normalizes container_info fields: `agent_image`→`image`, adds `status` (defaults
  to "completed"), includes `sandbox_mode` and `workspace_dir`.
- **`services/control-plane/main.py`** — added `/api/system/info` endpoint.
- **`frontend/app/layout.tsx`** — imports and renders `SecurityBanner` globally.
- **`frontend/app/tasks/[id]/page.tsx`** — audit log container environment section
  reads `image || agent_image` (fallback), shows colored status indicators, and
  renders sandbox mode badge.
- **`.env.example`** — added the `AGENT_SANDBOX_MODE` section with security commentary.
- **`README.md`** — added Security: Agent Sandbox Modes section, `AGENT_SANDBOX_MODE`
  to environment variables table, gVisor key feature highlight.

---

## [0.4.0] — 2026-02-27

### Added

- **Per-turn audit visibility in Temporal and frontend** — each LLM turn is now a
  separate activity in the Temporal UI instead of a single monolithic blob.
- `AgentStepWorkflow` child workflow that orchestrates:
  `start_agent_container` → `poll_agent_turns` → `record_agent_turn` (per turn) →
  `collect_agent_result`.
- `GET /api/llm/interactions/{task_id}?since=N` — incremental polling endpoint for
  LLM interactions.
- `GET /api/tasks/{task_id}/audit-turns` — structured per-iteration, per-turn audit
  data with token totals fetched from Temporal UI API.
- Frontend audit tab rewritten to show turns grouped by iteration with container
  metadata, tool calls, token usage, provider info, and Temporal UI links.

### Removed

- Monolithic `run_agent_step` activity and post-hoc `record_agent_action` activity.

---

## [0.3.0] — 2026-02-26

### Changed

- Repository cleanup — removed stale files and unused assets.
- Updated `.gitignore` to cover generated/temporary artefacts.

---

## [0.2.0] — 2026-02-23

### Fixed

- **Binary deliverable collection & download** — agent-generated binary files (PNG
  charts, PDF reports, etc.) were silently dropped or corrupted.
  - Root causes: file size limit too low (50 KB), binary files read as text with
    `errors="replace"`, total size cap too tight (200 KB).
  - `openclaw-wrapper.py`: binary detection (extension list + null-byte sampling),
    base64 encoding with `base64:` prefix, per-file limit raised to 500 KB, total
    to 2 MB, deterministic file ordering.
  - Frontend: decode base64 binary deliverables, inline image preview for
    PNG/JPG/GIF/SVG, file type icons, human-readable sizes, per-file and
    "Download All" buttons.

---

## [0.1.0] — 2026-02-17

### Added

- **Continue / Iterate on completed tasks** — `POST /tasks/{id}/continue` lets users
  provide follow-up instructions; the agent resumes from its last built image with all
  packages and deliverables intact.
- Frontend "Continue / Iterate" button with follow-up textarea.
- Temporal continuation workflows with unique IDs.
- Agent wrapper prepends follow-up context with workspace file listing.

### Fixed

- Deployment `apt-get` regex handles multi-line Dockerfiles (`re.DOTALL`).
- `LABEL capabilities` parsing splits by comma and strips prefixes correctly.
- Agent image resolution falls back across name formats (localhost / registry / bare).
- Entrypoint smart-quote stripping for unbalanced JSON artefacts.
- Corrections to the image builder and worker for agent executor issues.

---

## [0.0.1] — 2026-02-16

### Added

- **Initial release** — TaskForge: Auditable Agent Orchestration Platform.
- 10-service Docker Compose stack: control-plane, image-builder, temporal-worker,
  frontend, postgres, temporal, temporal-postgres, temporal-ui, docker-dind, registry.
- Capability-based security with human-in-the-loop approval.
- Immutable image rebuilds on capability approval.
- Multi-provider LLM routing (Ollama, Gemini, Anthropic, OpenAI).
- Full audit trail for every LLM interaction.
- Temporal durable workflows with pause/resume.
- Deployment support on ports 9100–9120.
- Next.js 14 dashboard frontend.
