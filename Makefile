# Makefile for TaskForge
# Auditable Agent Orchestration for OpenClaw

.PHONY: help up down build restart stop logs logs-service ps health clean \
		backup restore scale-workers build-base build-octaveclaw build-nanobot build-picoclaw build-zeroclaw build-browser build-browser-v2 build-browser-v3 build-all-images \
		docker-clean-dag docker-clean-dag-dry-run

# ─────────────────────────────────────────────────────────
# Help
# ─────────────────────────────────────────────────────────

help: ## Show this help
	@echo ''
	@echo '  TaskForge — make targets'
	@echo ''
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ''

# ─────────────────────────────────────────────────────────
# Lifecycle
# ─────────────────────────────────────────────────────────

up: ## Start all services (first run auto-builds base agent image)
	@mkdir -p workspaces
	@SANDBOX=$$(grep -s '^AGENT_SANDBOX_MODE=' .env 2>/dev/null | cut -d= -f2); \
	 SANDBOX=$${SANDBOX:-insecure-dind}; \
	 if [ "$$SANDBOX" = "insecure-dind" ]; then \
	   echo ""; \
	   echo "  \033[0;31m======================================================================\033[0m"; \
	   echo "  \033[1;31m ⚠️  SECURITY WARNING: RUNNING IN INSECURE DIND MODE ⚠️ \033[0m"; \
	   echo "  \033[0;31m======================================================================\033[0m"; \
	   echo "  AGENT_SANDBOX_MODE=insecure-dind"; \
	   echo "  AI agents will execute in privileged containers with host-root access."; \
	   echo "  For production, install gVisor and set AGENT_SANDBOX_MODE=gvisor"; \
	   echo "  in your .env file.  See: docs/GVISOR_SETUP.md"; \
	   echo "  \033[0;31m======================================================================\033[0m"; \
	   echo ""; \
	   sleep 3; \
	 elif [ "$$SANDBOX" = "gvisor" ]; then \
	   echo ""; \
	   echo "  \033[0;32m✅ Security: gVisor sandbox mode enabled. Agents are isolated.\033[0m"; \
	   echo ""; \
	 fi
	docker-compose up -d
	@echo ""
	@echo "  ✅  TaskForge is starting (10 services)"
	@echo ""
	@echo "  Frontend:        http://localhost:3000"
	@echo "  API:             http://localhost:8000"
	@echo "  API Docs:        http://localhost:8000/docs"
	@echo "  Temporal UI:     http://localhost:8088"
	@echo ""
	@echo "  💡  First run: image-builder auto-builds the base agent image (~1.8GB)."
	@echo "      Watch progress with:  make logs-service SERVICE=image-builder"
	@echo ""

down: ## Stop and remove all containers
	docker-compose down

stop: ## Stop all services (keep containers)
	docker-compose stop

restart: ## Restart all services
	docker-compose restart

build: ## Build all service Docker images (control-plane, image-builder, worker, frontend)
	docker-compose build

build-frontend: ## Rebuild only the frontend
	docker-compose build frontend
	docker-compose up -d --no-deps --force-recreate frontend

# ─────────────────────────────────────────────────────────
# Base Agent Image
# ─────────────────────────────────────────────────────────

build-base: ## Force rebuild the base agent image (openclaw-agent:openclaw)
	@echo "Triggering base agent image rebuild via image-builder..."
	@curl -sf http://localhost:8000/health > /dev/null 2>&1 || \
		{ echo "❌ Services must be running first. Run: make up"; exit 1; }
	docker-compose exec image-builder python -c "import httpx; r = httpx.post('http://localhost:8002/build', json={'task_id': '_base_rebuild', 'base_image': 'python:3.11-slim', 'capabilities': {'pip_packages': []}}, timeout=300); print(r.json())"
	@echo ""
	@echo "  Base image rebuild triggered. Watch logs:"
	@echo "    make logs-service SERVICE=image-builder"

build-octaveclaw: ## Build the OctaveClaw agent image (OpenClaw + GNU Octave) and push to registry
	@echo "Building OctaveClaw agent image inside DinD..."
	@docker exec openclaw-docker-dind docker build \
		-t registry:5000/openclaw-agent:octaveclaw \
		-f /agent-images/octaveclaw/Dockerfile \
		/agent-images/octaveclaw/
	@docker exec openclaw-docker-dind docker push registry:5000/openclaw-agent:octaveclaw
	@echo "  ✅  openclaw-agent:octaveclaw built & pushed"

check-base: ## Check if base agent image exists in internal registry
	@docker exec openclaw-docker-dind docker images registry:5000/openclaw-agent:openclaw --format "{{.Repository}}:{{.Tag}}  {{.Size}}  {{.CreatedAt}}" 2>/dev/null \
		|| echo "  ❌ Base image not found. It will be auto-built on next startup."

build-nanobot: ## Build the NanoBot agent image and push to registry
	@echo "Building NanoBot agent image inside DinD..."
	@cp agent-images/base/taskforge-adapter.py agent-images/nanobot/taskforge-adapter.py
	@docker exec openclaw-docker-dind docker build \
		-t registry:5000/openclaw-agent:nanobot \
		-f /agent-images/nanobot/Dockerfile \
		/agent-images/nanobot/
	@docker exec openclaw-docker-dind docker push registry:5000/openclaw-agent:nanobot
	@echo "  ✅  openclaw-agent:nanobot built & pushed"

build-picoclaw: ## Build the PicoClaw agent image and push to registry
	@echo "Building PicoClaw agent image inside DinD..."
	@docker exec openclaw-docker-dind docker build \
		-t registry:5000/openclaw-agent:picoclaw \
		-f /agent-images/picoclaw/Dockerfile \
		/agent-images/picoclaw/
	@docker exec openclaw-docker-dind docker push registry:5000/openclaw-agent:picoclaw
	@echo "  ✅  openclaw-agent:picoclaw built & pushed"

build-zeroclaw: ## Build the ZeroClaw agent image and push to registry
	@echo "Building ZeroClaw agent image inside DinD..."
	@cp agent-images/base/taskforge-adapter.py agent-images/zeroclaw/taskforge-adapter.py
	@docker exec openclaw-docker-dind docker build \
		-t registry:5000/openclaw-agent:zeroclaw \
		-f /agent-images/zeroclaw/Dockerfile \
		/agent-images/zeroclaw/
	@docker exec openclaw-docker-dind docker push registry:5000/openclaw-agent:zeroclaw
	@echo "  ✅  openclaw-agent:zeroclaw built & pushed"

build-browser: ## Build the Browser agent image (Chromium + agent-browser) and push to registry
	@echo "Building Browser agent image inside DinD..."
	@cp agent-images/base/taskforge-adapter.py agent-images/browser/taskforge-adapter.py
	@docker exec openclaw-docker-dind docker build \
		-t registry:5000/openclaw-agent:browser \
		-f /agent-images/browser/Dockerfile \
		/agent-images/browser/
	@docker exec openclaw-docker-dind docker push registry:5000/openclaw-agent:browser
	@rm -f agent-images/browser/taskforge-adapter.py
	@echo "  ✅  openclaw-agent:browser built & pushed"

build-browser-v2: ## Build Browser v2 image (Chromium + agent-browser + obscura) and push to registry
	@echo "Building Browser v2 agent image inside DinD..."
	@cp agent-images/base/taskforge-adapter.py agent-images/browser_v2/taskforge-adapter.py
	@docker exec openclaw-docker-dind docker build \
		-t registry:5000/openclaw-agent:browser_v2 \
		-f /agent-images/browser_v2/Dockerfile \
		/agent-images/browser_v2/
	@docker exec openclaw-docker-dind docker push registry:5000/openclaw-agent:browser_v2
	@rm -f agent-images/browser_v2/taskforge-adapter.py
	@echo "  ✅  openclaw-agent:browser_v2 built & pushed"

build-browser-v3: ## Build Browser v3 image (Chromium + agent-browser + Lightpanda) and push to registry
	@echo "Building Browser v3 agent image inside DinD..."
	@cp agent-images/base/taskforge-adapter.py agent-images/browser_v3/taskforge-adapter.py
	@docker exec openclaw-docker-dind docker build \
		-t registry:5000/openclaw-agent:browser_v3 \
		-f /agent-images/browser_v3/Dockerfile \
		/agent-images/browser_v3/
	@docker exec openclaw-docker-dind docker push registry:5000/openclaw-agent:browser_v3
	@rm -f agent-images/browser_v3/taskforge-adapter.py
	@echo "  ✅  openclaw-agent:browser_v3 built & pushed"

build-browser-v4: ## Build Browser v4 image (Chromium + agent-browser + Lightpanda) and push to registry
	@echo "Building Browser v4 agent image inside DinD..."
	@cp agent-images/base/taskforge-adapter.py agent-images/browser_v4/taskforge-adapter.py
	@docker exec openclaw-docker-dind docker build \
		-t registry:5000/openclaw-agent:browser_v4 \
		-f /agent-images/browser_v4/Dockerfile \
		/agent-images/browser_v4/
	@docker exec openclaw-docker-dind docker push registry:5000/openclaw-agent:browser_v4
	@rm -f agent-images/browser_v4/taskforge-adapter.py
	@echo "  ✅  openclaw-agent:browser_v4 built & pushed"


build-all-images: build-base build-octaveclaw build-nanobot build-picoclaw build-zeroclaw build-browser build-browser-v2 build-browser-v3 build-browser-v4 ## Build all 8 agent base images
	@echo ""
	@echo "  ✅  All agent base images built & pushed to registry:"
	@echo "      openclaw-agent:openclaw  (Full Python)"
	@echo "      openclaw-agent:octaveclaw (OpenClaw + GNU Octave)"
	@echo "      openclaw-agent:nanobot   (Alpine Python)"
	@echo "      openclaw-agent:picoclaw  (Shell)"
	@echo "      openclaw-agent:zeroclaw  (Rust)"
	@echo "      openclaw-agent:browser   (Chromium + agent-browser)"
	@echo "      openclaw-agent:browser_v2 (Chromium + agent-browser + obscura)"
	@echo "      openclaw-agent:browser_v3 (Chromium + agent-browser + lightpanda)"

# ─────────────────────────────────────────────────────────
# Logs & Status
# ─────────────────────────────────────────────────────────

docker-clean-dag: ## Remove old DAG agent images from DinD (default retention 5 days; override with DAG_RETENTION_DAYS)
	@scripts/cleanup-dag-images.sh $(DAG_RETENTION_DAYS)

docker-clean-dag-dry-run: ## Dry-run: list old DAG images that would be removed (deletes nothing)
	@scripts/cleanup-dag-images.sh $(DAG_RETENTION_DAYS) --dry-run

# ─────────────────────────────────────────────────────────
# Logs & Status
# ─────────────────────────────────────────────────────────

logs: ## Follow logs from all services
	docker-compose logs -f

logs-service: ## Follow logs from one service (usage: make logs-service SERVICE=control-plane)
	docker-compose logs -f $(SERVICE)

ps: ## Show running services
	docker-compose ps

health: ## Check health of all services
	@echo ""
	@echo "  TaskForge Health Check"
	@echo "  ─────────────────────"
	@curl -sf http://localhost:8000/health > /dev/null 2>&1 \
		&& echo "  ✅  Control Plane    http://localhost:8000" \
		|| echo "  ❌  Control Plane    http://localhost:8000"
	@curl -sf http://localhost:3000 > /dev/null 2>&1 \
		&& echo "  ✅  Frontend         http://localhost:3000" \
		|| echo "  ❌  Frontend         http://localhost:3000"
	@curl -sf http://localhost:8088 > /dev/null 2>&1 \
		&& echo "  ✅  Temporal UI      http://localhost:8088" \
		|| echo "  ❌  Temporal UI      http://localhost:8088"
	@docker exec openclaw-docker-dind docker info > /dev/null 2>&1 \
		&& echo "  ✅  Docker-in-Docker" \
		|| echo "  ❌  Docker-in-Docker"
	@docker exec openclaw-docker-dind docker images registry:5000/openclaw-agent:openclaw -q 2>/dev/null | grep -q . \
		&& echo "  ✅  Base agent image (in registry)" \
		|| echo "  ⏳  Base agent image (building or missing)"
	@echo ""

# ─────────────────────────────────────────────────────────
# Maintenance
# ─────────────────────────────────────────────────────────

clean: ## Stop everything, remove containers and volumes (DESTRUCTIVE)
	docker-compose down -v
	docker system prune -f
	@echo "  ⚠️   All data deleted. Next 'make up' will rebuild everything from scratch."

backup: ## Backup database and workspaces to ./backups/
	@mkdir -p backups
	@docker-compose exec -T postgres pg_dump -U openclaw openclaw > backups/taskforge-$(shell date +%Y%m%d-%H%M%S).sql
	@tar -czf backups/workspaces-$(shell date +%Y%m%d-%H%M%S).tar.gz workspaces/
	@echo "  ✅  Backup saved to ./backups/"

restore: ## Restore database (usage: make restore BACKUP=backups/taskforge-20250101-120000.sql)
	@echo "Restoring from $(BACKUP)..."
	@docker-compose exec -T postgres psql -U openclaw openclaw < $(BACKUP)
	@echo "  ✅  Restore complete"

# ─────────────────────────────────────────────────────────
# Scaling
# ─────────────────────────────────────────────────────────

scale-workers: ## Scale temporal workers (usage: make scale-workers WORKERS=3)
	docker-compose up -d --scale temporal-worker=$(WORKERS)
	@echo "  ✅  Scaled to $(WORKERS) temporal worker(s)"

