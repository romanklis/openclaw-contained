# Makefile for TaskForge
# Auditable Agent Orchestration for OpenClaw

.PHONY: help up down build restart stop logs logs-service ps health clean \
        backup restore scale-workers build-base

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
	docker-compose exec image-builder python -c "\
		import httpx; \
		r = httpx.post('http://localhost:8002/api/build', json={ \
			'task_id': '_base_rebuild', \
			'base_image': 'python:3.11-slim', \
			'capabilities': {'pip_packages': []} \
		}, timeout=300); \
		print(r.json())"
	@echo ""
	@echo "  Base image rebuild triggered. Watch logs:"
	@echo "    make logs-service SERVICE=image-builder"

check-base: ## Check if base agent image exists in internal registry
	@docker exec openclaw-docker-dind docker images registry:5000/openclaw-agent:openclaw --format "{{.Repository}}:{{.Tag}}  {{.Size}}  {{.CreatedAt}}" 2>/dev/null \
		|| echo "  ❌ Base image not found. It will be auto-built on next startup."

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

