"""
Image Builder Service - Dynamically builds agent images with approved capabilities
"""
from fastapi import FastAPI, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import docker
from pathlib import Path
from jinja2 import Template
import logging
import os
import re
import uuid
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="OpenClaw Image Builder", version="0.1.0")

# Docker client (initialized on startup)
docker_client = None

REGISTRY_URL = os.getenv("REGISTRY_URL", "localhost:5000")
BASE_IMAGE = f"{REGISTRY_URL}/openclaw-agent:openclaw"
AGENT_IMAGES_DIR = Path(os.getenv("AGENT_IMAGES_DIR", "/app/agent-images"))


# =============================================================================
# Schemas
# =============================================================================

class BuildCapability(BaseModel):
    """Capability to add to image"""
    type: str  # pip_package, apt_package, npm_package, tool
    name: str
    version: Optional[str] = None


class BuildRequest(BaseModel):
    """Image build request"""
    task_id: str
    base_image: str = BASE_IMAGE
    capabilities: List[BuildCapability]


class BuildResponse(BaseModel):
    """Build response"""
    build_id: str
    task_id: str
    image_tag: str
    status: str
    log_url: Optional[str] = None


class BuildStatus(BaseModel):
    """Build status"""
    build_id: str
    status: str  # pending, building, success, failed
    image_tag: Optional[str] = None
    digest: Optional[str] = None
    error: Optional[str] = None
    logs: Optional[str] = None


class DeploymentBuildRequest(BaseModel):
    """Build request for a deployment image (minimal, no OpenClaw)"""
    deployment_id: str
    task_id: str
    entrypoint: str = "python app.py"
    port: int = 5000
    pip_packages: Optional[List[str]] = None  # extra pip packages


# =============================================================================
# Dockerfile Generation
# =============================================================================

DOCKERFILE_TEMPLATE = """
FROM {{ base_image }}

# Build metadata
LABEL task_id="{{ task_id }}"
LABEL build_id="{{ build_id }}"
LABEL capabilities="{{ capabilities }}"

{% if apt_packages %}
# Install APT packages
USER root
RUN apt-get update && apt-get install -y \\
{% for pkg in apt_packages %}
    {{ pkg }} \\
{% endfor %}
    && rm -rf /var/lib/apt/lists/*
{% endif %}

{% if pip_packages %}
# Install Python packages into both venv and system python
# Use absolute paths to ensure we install into BOTH interpreters
USER root
RUN /opt/venv/bin/pip install --no-cache-dir {{ pip_packages | join(' ') }} ; /usr/bin/pip3 install --no-cache-dir --break-system-packages {{ pip_packages | join(' ') }} || /usr/bin/pip3 install --no-cache-dir {{ pip_packages | join(' ') }} || true
{% endif %}

{% if npm_packages %}
# Install NPM packages globally
USER root
RUN npm install -g \\
{% for pkg in npm_packages %}
    {{ pkg }} \\
{% endfor %}
    && npm list -g --depth=0
{% endif %}

{% if tools %}
# Copy additional tools
{% for tool in tools %}
COPY tools/{{ tool }} /usr/local/bin/{{ tool }}
RUN chmod +x /usr/local/bin/{{ tool }}
{% endfor %}
{% endif %}

WORKDIR /workspace

# Verify installation
RUN echo "Image built successfully for task {{ task_id }}"
"""


def generate_dockerfile(
    task_id: str,
    build_id: str,
    base_image: str,
    capabilities: List[BuildCapability]
) -> str:
    """Generate Dockerfile from capabilities"""
    
    apt_packages = []
    pip_packages = []
    npm_packages = []
    tools = []
    
    for cap in capabilities:
        if cap.type == "apt_package":
            pkg = f"{cap.name}={cap.version}" if cap.version else cap.name
            apt_packages.append(pkg)
        elif cap.type == "pip_package":
            pkg = f"{cap.name}=={cap.version}" if cap.version else cap.name
            pip_packages.append(pkg)
        elif cap.type == "npm_package":
            pkg = f"{cap.name}@{cap.version}" if cap.version else cap.name
            npm_packages.append(pkg)
        elif cap.type == "tool":
            tools.append(cap.name)
    
    template = Template(DOCKERFILE_TEMPLATE)
    
    dockerfile = template.render(
        base_image=base_image,
        task_id=task_id,
        build_id=build_id,
        capabilities=",".join([f"{c.type}:{c.name}" for c in capabilities]),
        apt_packages=apt_packages,
        pip_packages=pip_packages,
        npm_packages=npm_packages,
        tools=tools
    )
    
    return dockerfile


# Deployment image Dockerfile template (minimal — no OpenClaw)
DEPLOYMENT_DOCKERFILE_TEMPLATE = """
FROM python:3.11-slim

LABEL deployment_id="{{ deployment_id }}"
LABEL task_id="{{ task_id }}"

WORKDIR /app

{% if apt_packages %}
# Install system packages
RUN apt-get update && apt-get install -y \\
{% for pkg in apt_packages %}
    {{ pkg }} \\
{% endfor %}
    && rm -rf /var/lib/apt/lists/*
{% endif %}

{% if pip_packages %}
# Install Python dependencies
RUN pip install --no-cache-dir {{ pip_packages | join(' ') }}
{% endif %}

# Copy application files
COPY app/ /app/

# Rewrite any /workspace/ references to /app/ inside copied files
RUN find /app -type f \\( -name '*.py' -o -name '*.sh' -o -name '*.yaml' -o -name '*.yml' -o -name '*.json' -o -name '*.toml' -o -name '*.cfg' -o -name '*.conf' -o -name '*.ini' -o -name '*.txt' -o -name '*.html' -o -name '*.js' \\) -exec sed -i 's|/workspace/|/app/|g; s|/workspace|/app|g' {} + 2>/dev/null || true

# Make shell scripts executable
RUN find /app -name '*.sh' -exec chmod +x {} + 2>/dev/null || true

EXPOSE {{ port }}

CMD {{ entrypoint_cmd }}
"""


def generate_deployment_dockerfile(
    deployment_id: str,
    task_id: str,
    entrypoint: str,
    port: int,
    pip_packages: Optional[List[str]] = None,
    apt_packages: Optional[List[str]] = None,
) -> str:
    """Generate a minimal Dockerfile for a deployment (no OpenClaw)."""
    # Rewrite /workspace/ paths to /app/ since deployment copies files to /app/
    entrypoint = entrypoint.replace("/workspace/", "/app/")
    entrypoint = entrypoint.replace("/workspace", "/app")
    
    # Strip wrapping quotes from sh -c "..." style entrypoints
    import json as json_mod
    if 'sh -c' in entrypoint:
        # Extract the command after sh -c, stripping surrounding quotes
        sh_match = re.search(r'sh\s+-c\s+["\']?(.+?)["\']?\s*$', entrypoint)
        if sh_match:
            inner_cmd = sh_match.group(1)
            entrypoint_cmd = json_mod.dumps(["sh", "-c", inner_cmd])
        else:
            entrypoint_cmd = json_mod.dumps(["sh", "-c", entrypoint.split('sh -c', 1)[1].strip().strip('"\"')])
    elif '&&' in entrypoint or '|' in entrypoint or ';' in entrypoint:
        # Complex shell command — use shell form
        entrypoint_cmd = json_mod.dumps(["sh", "-c", entrypoint])
    else:
        # Simple command — split into exec form
        parts = entrypoint.split()
        entrypoint_cmd = json_mod.dumps(parts)

    template = Template(DEPLOYMENT_DOCKERFILE_TEMPLATE)
    return template.render(
        deployment_id=deployment_id,
        task_id=task_id,
        port=port,
        entrypoint_cmd=entrypoint_cmd,
        pip_packages=pip_packages or [],
        apt_packages=apt_packages or [],
    )


# =============================================================================
# Build Management
# =============================================================================

# In-memory build tracking (should be database in production)
builds: Dict[str, BuildStatus] = {}


async def build_image_task(
    build_id: str,
    task_id: str,
    dockerfile: str,
    image_tag: str
):
    """Background task to build image"""
    
    logger.info(f"Starting build {build_id} for task {task_id}")
    
    builds[build_id].status = "building"
    
    try:
        # Save Dockerfile to agent-images directory for version control
        task_image_dir = AGENT_IMAGES_DIR / task_id
        task_image_dir.mkdir(parents=True, exist_ok=True)
        
        # Extract version from image_tag (e.g., "openclaw-agent:task-xxx-v2" -> "v2")
        version = image_tag.split("-v")[-1] if "-v" in image_tag else "v1"
        dockerfile_path = task_image_dir / f"Dockerfile.{version}"
        dockerfile_path.write_text(dockerfile)
        
        # Also save as latest
        latest_path = task_image_dir / "Dockerfile"
        latest_path.write_text(dockerfile)
        
        logger.info(f"Saved Dockerfile to {dockerfile_path}")
        
        # Use task directory as build context
        build_dir = task_image_dir
        
        # Build image
        logger.info(f"Building image {image_tag} from {build_dir}")
        
        image, build_logs = docker_client.images.build(
            path=str(build_dir),
            dockerfile=f"Dockerfile.{version}",
            tag=image_tag,
            rm=True,
            pull=False
        )
        
        # Collect logs
        log_output = []
        for chunk in build_logs:
            if 'stream' in chunk:
                log_output.append(chunk['stream'])
        
        builds[build_id].logs = ''.join(log_output)
        
        # Tag for registry
        registry_tag = f"{REGISTRY_URL}/{image_tag}"
        image.tag(registry_tag)
        
        # Push to registry
        logger.info(f"Pushing image to {registry_tag}")
        push_logs = docker_client.images.push(registry_tag)
        
        # Update build status
        builds[build_id].status = "success"
        builds[build_id].image_tag = registry_tag
        builds[build_id].digest = image.id
        
        logger.info(f"Build {build_id} completed successfully")
        
    except Exception as e:
        logger.error(f"Build {build_id} failed: {e}")
        builds[build_id].status = "failed"
        builds[build_id].error = str(e)


async def build_deployment_image_task(
    build_id: str,
    deployment_id: str,
    task_id: str,
    dockerfile: str,
    image_tag: str,
):
    """Background task to build a deployment image."""
    logger.info(f"Starting deployment build {build_id} for {deployment_id}")
    builds[build_id].status = "building"

    try:
        # Prepare build context directory
        deploy_dir = AGENT_IMAGES_DIR / "deployments" / deployment_id
        deploy_dir.mkdir(parents=True, exist_ok=True)

        # Write Dockerfile
        df_path = deploy_dir / "Dockerfile"
        df_path.write_text(dockerfile)

        # Copy workspace files from the task's workspace
        # The workspace files are stored in /workspaces/<workspace_id> but
        # we also get them from the task output deliverables via control plane.
        app_dir = deploy_dir / "app"
        app_dir.mkdir(parents=True, exist_ok=True)

        # Try to copy files from the task workspace mount
        workspace_path = Path("/workspaces")
        # Find workspace directory for this task (pattern: workspace-*)
        import httpx
        try:
            control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
            import asyncio
            # Fetch task to get workspace_id and latest output with deliverables
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{control_plane_url}/api/tasks/{task_id}")
                if resp.status_code == 200:
                    task_data = resp.json()
                    ws_id = task_data.get("workspace_id", "")
                    ws_path = workspace_path / ws_id
                    if ws_path.exists():
                        import shutil
                        # Copy all files from workspace to app dir
                        for item in ws_path.iterdir():
                            if item.name in ("AGENTS.md", "SOUL.md", "result.json", ".openclaw"):
                                continue
                            dest = app_dir / item.name
                            if item.is_dir():
                                shutil.copytree(str(item), str(dest), dirs_exist_ok=True)
                            else:
                                shutil.copy2(str(item), str(dest))
                        logger.info(f"Copied workspace files from {ws_path} to {app_dir}")

                # Also try latest outputs for deliverables
                resp = await client.get(f"{control_plane_url}/api/tasks/{task_id}/outputs")
                if resp.status_code == 200:
                    outputs = resp.json()
                    # Get deliverables from latest output
                    for output in reversed(outputs):
                        deliverables = output.get("deliverables") or {}
                        if deliverables:
                            for fname, content in deliverables.items():
                                fpath = app_dir / fname
                                fpath.parent.mkdir(parents=True, exist_ok=True)
                                fpath.write_text(content)
                            logger.info(f"Wrote {len(deliverables)} deliverable files to {app_dir}")
                            break
        except Exception as e:
            logger.warning(f"Could not fetch workspace files: {e}")

        # Verify we have at least one file
        app_files = list(app_dir.iterdir())
        if not app_files:
            raise Exception("No application files found for deployment")

        logger.info(f"Building deployment image {image_tag} from {deploy_dir}")

        image, build_logs = docker_client.images.build(
            path=str(deploy_dir),
            dockerfile="Dockerfile",
            tag=image_tag,
            rm=True,
            pull=True,
        )

        log_output = []
        for chunk in build_logs:
            if "stream" in chunk:
                log_output.append(chunk["stream"])
        builds[build_id].logs = "".join(log_output)

        # Tag and push to registry
        registry_tag = f"{REGISTRY_URL}/{image_tag}"
        image.tag(registry_tag)
        docker_client.images.push(registry_tag)

        builds[build_id].status = "success"
        builds[build_id].image_tag = registry_tag
        builds[build_id].digest = image.id

        logger.info(f"Deployment build {build_id} completed: {registry_tag}")

    except Exception as e:
        logger.error(f"Deployment build {build_id} failed: {e}")
        builds[build_id].status = "failed"
        builds[build_id].error = str(e)


# =============================================================================
# API Endpoints
# =============================================================================

@app.post("/build", response_model=BuildResponse)
async def build_image(
    request: BuildRequest,
    background_tasks: BackgroundTasks
):
    """Build new agent image with capabilities"""
    
    build_id = str(uuid.uuid4())[:8]
    
    # Get latest version for this task by counting previous successful builds
    existing_versions = [
        b for b in builds.values()
        if request.task_id in (b.image_tag or "") and b.status in ("success", "building", "pending")
    ]
    version = len(existing_versions) + 1
    
    image_tag = f"openclaw-agent:{request.task_id}-v{version}"
    logger.info(f"Version calculation: {len(existing_versions)} existing builds → v{version}")
    
    logger.info(f"Creating build {build_id} for task {request.task_id}")
    
    # Expand comma-separated capability names into individual entries
    # and auto-detect system packages vs pip packages
    KNOWN_APT_PACKAGES = {
        "redis-server", "redis-tools", "postgresql", "postgresql-client",
        "sqlite3", "libsqlite3-dev", "nginx", "apache2", "ffmpeg",
        "imagemagick", "graphviz", "tesseract-ocr", "poppler-utils",
        "wkhtmltopdf", "chromium", "chromium-browser", "libreoffice",
        "gcc", "g++", "make", "cmake", "libffi-dev", "libssl-dev",
        "libxml2-dev", "libxslt1-dev", "libjpeg-dev", "libpng-dev",
        "zlib1g-dev", "libpq-dev", "default-libmysqlclient-dev",
    }
    expanded_capabilities = []
    for cap in request.capabilities:
        # Split comma-separated names
        names = [n.strip() for n in cap.name.split(",") if n.strip()]
        for name in names:
            cap_type = cap.type
            # Auto-detect: if it's a known system package, switch to apt_package
            if name in KNOWN_APT_PACKAGES:
                cap_type = "apt_package"
                logger.info(f"Auto-detected {name} as APT system package")
            elif cap_type == "pip_package" and name.startswith("lib"):
                cap_type = "apt_package"  # lib* packages are typically system
                logger.info(f"Auto-detected {name} as APT system package (lib* prefix)")
            expanded_capabilities.append(
                BuildCapability(type=cap_type, name=name, version=cap.version)
            )
    
    logger.info(f"Expanded {len(request.capabilities)} capability entries → {len(expanded_capabilities)} individual packages")
    
    # Generate Dockerfile
    dockerfile = generate_dockerfile(
        request.task_id,
        build_id,
        request.base_image,
        expanded_capabilities
    )
    
    # Create build record
    builds[build_id] = BuildStatus(
        build_id=build_id,
        status="pending",
        image_tag=image_tag
    )
    
    # Start build in background
    background_tasks.add_task(
        build_image_task,
        build_id,
        request.task_id,
        dockerfile,
        image_tag
    )
    
    return BuildResponse(
        build_id=build_id,
        task_id=request.task_id,
        image_tag=image_tag,
        status="pending",
        log_url=f"/builds/{build_id}/logs"
    )


@app.get("/builds/{build_id}", response_model=BuildStatus)
async def get_build_status(build_id: str):
    """Get build status"""
    
    if build_id not in builds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Build {build_id} not found"
        )
    
    return builds[build_id]


@app.post("/build-deployment", response_model=BuildResponse)
async def build_deployment_image(
    request: DeploymentBuildRequest,
    background_tasks: BackgroundTasks,
):
    """Build a minimal deployment image from workspace files."""
    build_id = str(uuid.uuid4())[:8]
    image_tag = f"openclaw-deploy:{request.deployment_id}"

    logger.info(f"Creating deployment build {build_id} for {request.deployment_id}")

    # Determine pip packages from task capabilities (approved ones)
    pip_packages = list(request.pip_packages or [])
    apt_packages = []
    
    # Also check if the task's agent image Dockerfile has pip/apt installs
    task_dir = AGENT_IMAGES_DIR / request.task_id
    # Check ALL versioned Dockerfiles (Dockerfile.1, Dockerfile.2, etc.)
    import re
    for df_path in sorted(task_dir.glob("Dockerfile*")):
        content = df_path.read_text()
        
        # Extract apt packages from "apt-get install -y pkg1 pkg2"
        for m in re.finditer(r"apt-get install\s+-y\s+(.+?)(?:\s*&&|$)", content, re.MULTILINE):
            for pkg in m.group(1).split():
                pkg = pkg.strip().rstrip("\\")
                if pkg and not pkg.startswith("-") and pkg not in apt_packages:
                    apt_packages.append(pkg)
        
        # Extract pip packages from "pip install ... pkg1 pkg2"
        for m in re.finditer(r"pip\d?\s+install\s+[^\\]*?([a-zA-Z0-9_-]+(?:\s+[a-zA-Z0-9_-]+)*)\s*[;\\]", content):
            for pkg in m.group(1).split():
                if pkg not in ("--no-cache-dir", "--break-system-packages") and not pkg.startswith("-"):
                    if pkg not in pip_packages:
                        pip_packages.append(pkg)
        # Simpler: look for known packages after --no-cache-dir
        for m in re.finditer(r"--no-cache-dir\s+(.+?)(?:\s*[;|]|$)", content):
            for pkg in m.group(1).split():
                if not pkg.startswith("-") and pkg not in pip_packages:
                    pip_packages.append(pkg)
    
    logger.info(f"Deployment packages — pip: {pip_packages}, apt: {apt_packages}")

    dockerfile = generate_deployment_dockerfile(
        deployment_id=request.deployment_id,
        task_id=request.task_id,
        entrypoint=request.entrypoint,
        port=request.port,
        pip_packages=pip_packages if pip_packages else None,
        apt_packages=apt_packages if apt_packages else None,
    )

    builds[build_id] = BuildStatus(
        build_id=build_id,
        status="pending",
        image_tag=image_tag,
    )

    background_tasks.add_task(
        build_deployment_image_task,
        build_id,
        request.deployment_id,
        request.task_id,
        dockerfile,
        image_tag,
    )

    return BuildResponse(
        build_id=build_id,
        task_id=request.task_id,
        image_tag=image_tag,
        status="pending",
        log_url=f"/builds/{build_id}/logs",
    )


@app.get("/builds/{build_id}/logs")
async def get_build_logs(build_id: str):
    """Get build logs"""
    
    if build_id not in builds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Build {build_id} not found"
        )
    
    return {
        "build_id": build_id,
        "logs": builds[build_id].logs or ""
    }


@app.on_event("startup")
async def startup_event():
    """Initialize Docker client and ensure the base agent image exists."""
    global docker_client
    
    max_retries = 30
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Attempting to connect to Docker daemon (attempt {attempt + 1}/{max_retries})...")
            docker_client = docker.from_env()
            docker_client.ping()
            logger.info("Successfully connected to Docker daemon")
            break
        except Exception as e:
            logger.warning(f"Failed to connect to Docker: {e}")
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                logger.error("Failed to connect to Docker daemon after all retries")
                raise

    # --- Bootstrap: build & push base agent image if missing ----------------
    await _ensure_base_image()


async def _ensure_base_image():
    """Build and push the base openclaw-agent image to the registry if it
    doesn't already exist.  This makes `docker-compose up` fully self-
    contained — no manual setup step required."""
    import io, tarfile

    try:
        docker_client.images.get(BASE_IMAGE)
        logger.info(f"Base image {BASE_IMAGE} already present")
        return
    except docker.errors.ImageNotFound:
        pass
    except Exception:
        pass

    # Also try pulling from registry in case DinD was restarted but
    # the registry still has the image.
    try:
        docker_client.images.pull(BASE_IMAGE)
        logger.info(f"Pulled base image {BASE_IMAGE} from registry")
        return
    except Exception:
        pass

    logger.info(f"Base image {BASE_IMAGE} not found — building...")

    build_ctx = Path("/agent-executor")
    dockerfile_path = build_ctx / "Dockerfile.openclaw"
    if not dockerfile_path.exists():
        logger.error(f"Cannot bootstrap base image: {dockerfile_path} not found")
        return

    try:
        local_tag = "openclaw-agent:openclaw"
        image, build_logs = docker_client.images.build(
            path=str(build_ctx),
            dockerfile="Dockerfile.openclaw",
            tag=local_tag,
            rm=True,
        )
        for chunk in build_logs:
            if "stream" in chunk:
                logger.info(chunk["stream"].rstrip())

        # Tag for the internal registry
        image.tag(BASE_IMAGE)
        logger.info(f"Tagged {local_tag} → {BASE_IMAGE}")

        logger.info(f"Pushing {BASE_IMAGE} to registry...")
        for line in docker_client.images.push(BASE_IMAGE, stream=True, decode=True):
            if "status" in line:
                logger.info(f"  {line['status']} {line.get('progress', '')}")
            if "error" in line:
                logger.error(f"  Push error: {line['error']}")

        logger.info(f"Base image {BASE_IMAGE} built and pushed successfully")
    except Exception as e:
        logger.error(f"Failed to build base image: {e}")


@app.get("/health")
async def health():
    """Health check"""
    docker_connected = False
    try:
        if docker_client:
            docker_client.ping()
            docker_connected = True
    except Exception:
        pass
    
    return {
        "status": "healthy" if docker_connected else "unhealthy",
        "service": "image-builder",
        "docker_connected": docker_connected
    }
