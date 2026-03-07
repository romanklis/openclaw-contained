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
import json
import subprocess
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="OpenClaw Image Builder", version="0.1.0")

# Docker client (initialized on startup)
docker_client = None

REGISTRY_URL = os.getenv("REGISTRY_URL", "localhost:5000")
BASE_IMAGE = f"{REGISTRY_URL}/openclaw-agent:openclaw"
AGENT_IMAGES_DIR = Path(os.getenv("AGENT_IMAGES_DIR", "/app/agent-images"))
CONTROL_PLANE_URL = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")


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

# Per-image-type install strategies
# ─────────────────────────────────
#  openclaw  : Debian + /opt/venv/bin/pip + npm + apt-get
#  nanobot   : Alpine + /usr/local/bin/pip + apk (no npm)
#  picoclaw  : Alpine + apk only (no Python, no npm)
#  zeroclaw  : Debian + /usr/bin/pip3 --break-system-packages + apt-get (no npm)

DOCKERFILE_TEMPLATE = """
FROM {{ base_image }}

# Build metadata
LABEL task_id="{{ task_id }}"
LABEL build_id="{{ build_id }}"
LABEL capabilities="{{ capabilities }}"
LABEL image_type="{{ image_type }}"

{% if system_packages %}
# Install system packages ({{ pkg_manager }})
USER root
{% if pkg_manager == 'apk' %}
RUN apk add --no-cache \\
{% for pkg in system_packages %}
    {{ pkg }}{{ ' \\\\' if not loop.last else '' }}
{% endfor %}
{% else %}
RUN apt-get update && apt-get install -y \\
{% for pkg in system_packages %}
    {{ pkg }} \\
{% endfor %}
    && rm -rf /var/lib/apt/lists/*
{% endif %}
{% endif %}

{% if pip_packages %}
{% if image_type == 'picoclaw' %}
# ⚠ PicoClaw has no Python — cannot install pip packages
# Requested: {{ pip_packages | join(', ') }}
RUN echo "ERROR: pip packages requested but PicoClaw has no Python runtime" >&2 && exit 1
{% elif image_type == 'openclaw' %}
# Install Python packages into venv (OpenClaw)
USER root
RUN /opt/venv/bin/pip install --no-cache-dir {{ pip_packages | join(' ') }}
{% elif image_type == 'nanobot' %}
# Install Python packages (NanoBot — Alpine Python)
USER root
RUN pip install --no-cache-dir {{ pip_packages | join(' ') }}
{% elif image_type == 'zeroclaw' %}
# Install Python packages (ZeroClaw — Debian system Python)
USER root
RUN pip3 install --no-cache-dir --break-system-packages {{ pip_packages | join(' ') }}
{% else %}
# Install Python packages (generic — auto-detect pip)
USER root
RUN set -e; \\
    PIP=""; \\
    for p in /opt/venv/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip /usr/bin/pip3; do \\
        if [ -x "$p" ]; then PIP="$p"; break; fi; \\
    done; \\
    if [ -z "$PIP" ]; then echo "ERROR: no pip found" >&2; exit 1; fi; \\
    echo "Using pip: $PIP"; \\
    $PIP install --no-cache-dir {{ pip_packages | join(' ') }} || \\
    $PIP install --no-cache-dir --break-system-packages {{ pip_packages | join(' ') }}
{% endif %}
{% endif %}

{% if npm_packages %}
{% if image_type == 'openclaw' %}
# Install NPM packages globally (OpenClaw only)
USER root
RUN npm install -g \\
{% for pkg in npm_packages %}
    {{ pkg }} \\
{% endfor %}
    && npm list -g --depth=0
{% else %}
# ⚠ npm packages requested but {{ image_type }} has no Node.js
RUN echo "WARNING: npm packages not available on {{ image_type }}" >&2
{% endif %}
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
RUN echo "Image built successfully for task {{ task_id }} ({{ image_type }})"
"""


def _detect_image_type(base_image: str) -> str:
    """Detect the image type from a base image tag.

    Strategy (in order):
      1. Direct tag match: openclaw-agent:nanobot → nanobot
      2. Docker image labels: org.openclaw.image.type
      3. Docker image env vars: OPENCLAW_IMAGE_TYPE=...
      4. Fallback: 'openclaw'
    """
    KNOWN_TYPES = {"nanobot", "openclaw", "picoclaw", "zeroclaw"}
    tag = base_image.rsplit(":", 1)[-1] if ":" in base_image else ""

    # 1. Direct base image tags
    if tag in KNOWN_TYPES:
        return tag

    # 2 & 3. Inspect the image via Docker SDK
    try:
        image = docker_client.images.get(base_image)
        # Check labels
        labels = image.labels or {}
        label_type = labels.get("image_type") or labels.get("org.openclaw.image.type", "")
        if label_type in KNOWN_TYPES:
            return label_type
        # Map base-agent label to openclaw
        if label_type == "base-agent":
            return "openclaw"

        # Check env vars
        env_list = image.attrs.get("Config", {}).get("Env", []) or []
        for entry in env_list:
            if entry.startswith("OPENCLAW_IMAGE_TYPE="):
                val = entry.split("=", 1)[1].strip()
                if val in KNOWN_TYPES:
                    return val
    except Exception as e:
        logger.warning(f"   └─ Could not inspect image {base_image}: {e}")

    # 4. Fallback
    logger.warning(f"   └─ Falling back to 'openclaw' for image {base_image}")
    return "openclaw"


def generate_dockerfile(
    task_id: str,
    build_id: str,
    base_image: str,
    capabilities: List[BuildCapability]
) -> str:
    """Generate Dockerfile from capabilities, tailored to the base image type."""

    image_type = _detect_image_type(base_image)
    logger.info(f"   └─ Detected image type: {image_type}")

    # Choose the right system package manager
    pkg_manager = "apk" if image_type in ("nanobot", "picoclaw") else "apt"

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
        image_type=image_type,
        pkg_manager=pkg_manager,
        capabilities=",".join([f"{c.type}:{c.name}" for c in capabilities]),
        system_packages=apt_packages,
        pip_packages=pip_packages,
        npm_packages=npm_packages,
        tools=tools
    )
    
    return dockerfile


# =============================================================================
# Import scanning — auto-detect third-party packages from app source files
# =============================================================================

# Standard library modules (Python 3.11) — imports of these do NOT need pip
_STDLIB_MODULES = {
    "abc", "aifc", "argparse", "array", "ast", "asynchat", "asyncio",
    "asyncore", "atexit", "audioop", "base64", "bdb", "binascii",
    "binhex", "bisect", "builtins", "bz2", "calendar", "cgi", "cgitb",
    "chunk", "cmath", "cmd", "code", "codecs", "codeop", "collections",
    "colorsys", "compileall", "concurrent", "configparser", "contextlib",
    "contextvars", "copy", "copyreg", "cProfile", "crypt", "csv",
    "ctypes", "curses", "dataclasses", "datetime", "dbm", "decimal",
    "difflib", "dis", "distutils", "doctest", "email", "encodings",
    "enum", "errno", "faulthandler", "fcntl", "filecmp", "fileinput",
    "fnmatch", "fractions", "ftplib", "functools", "gc", "getopt",
    "getpass", "gettext", "glob", "graphlib", "grp", "gzip", "hashlib",
    "heapq", "hmac", "html", "http", "idlelib", "imaplib", "imghdr",
    "imp", "importlib", "inspect", "io", "ipaddress", "itertools",
    "json", "keyword", "lib2to3", "linecache", "locale", "logging",
    "lzma", "mailbox", "mailcap", "marshal", "math", "mimetypes",
    "mmap", "modulefinder", "multiprocessing", "netrc", "nis", "nntplib",
    "numbers", "operator", "optparse", "os", "ossaudiodev", "pathlib",
    "pdb", "pickle", "pickletools", "pipes", "pkgutil", "platform",
    "plistlib", "poplib", "posix", "posixpath", "pprint", "profile",
    "pstats", "pty", "pwd", "py_compile", "pyclbr", "pydoc",
    "queue", "quopri", "random", "re", "readline", "reprlib",
    "resource", "rlcompleter", "runpy", "sched", "secrets", "select",
    "selectors", "shelve", "shlex", "shutil", "signal", "site",
    "smtpd", "smtplib", "sndhdr", "socket", "socketserver", "spwd",
    "sqlite3", "sre_compile", "sre_constants", "sre_parse", "ssl",
    "stat", "statistics", "string", "stringprep", "struct", "subprocess",
    "sunau", "symtable", "sys", "sysconfig", "syslog", "tabnanny",
    "tarfile", "telnetlib", "tempfile", "termios", "test", "textwrap",
    "threading", "time", "timeit", "tkinter", "token", "tokenize",
    "tomllib", "trace", "traceback", "tracemalloc", "tty", "turtle",
    "turtledemo", "types", "typing", "unicodedata", "unittest", "urllib",
    "uu", "uuid", "venv", "warnings", "wave", "weakref", "webbrowser",
    "winreg", "winsound", "wsgiref", "xdrlib", "xml", "xmlrpc",
    "zipapp", "zipfile", "zipimport", "zlib",
    # Also underscore-prefixed internal modules
    "_thread", "__future__", "_abc", "_collections_abc",
}

# Map of import names that differ from pip package names
_IMPORT_TO_PIP = {
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "gi": "PyGObject",
    "google": "google-api-python-client",
    "jose": "python-jose",
    "lxml": "lxml",
    "magic": "python-magic",
    "PIL": "Pillow",
    "serial": "pyserial",
    "skimage": "scikit-image",
    "sklearn": "scikit-learn",
    "usb": "pyusb",
    "yaml": "pyyaml",
    "attr": "attrs",
    "wx": "wxPython",
}


def _scan_imports_for_pip_packages(app_dir: Path) -> List[str]:
    """Scan Python files in *app_dir* for import statements and return a list
    of pip package names for any third-party modules detected.

    Uses simple regex-based parsing (no AST) so it works even on files with
    syntax errors.  Only top-level module names are considered.
    """
    import_re = re.compile(
        r'^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)'
    )

    top_level_imports: set[str] = set()

    for py_file in app_dir.rglob("*.py"):
        try:
            text = py_file.read_text(errors="replace")
        except Exception:
            continue
        for line in text.splitlines():
            m = import_re.match(line)
            if m:
                top_level_imports.add(m.group(1))

    # Determine which are relative (local) — any module name that corresponds
    # to a file/dir inside app_dir is local, not third-party.
    local_modules: set[str] = set()
    for item in app_dir.iterdir():
        if item.is_dir() and (item / "__init__.py").exists():
            local_modules.add(item.name)
        elif item.suffix == ".py":
            local_modules.add(item.stem)

    third_party = top_level_imports - _STDLIB_MODULES - local_modules

    # Map import names → pip package names
    pip_packages: list[str] = []
    for mod in sorted(third_party):
        pip_name = _IMPORT_TO_PIP.get(mod, mod)
        pip_packages.append(pip_name)

    return pip_packages


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


# =============================================================================
# SBOM Generation (Trivy)
# =============================================================================

def _trivy_available() -> bool:
    """Check if the trivy binary is on PATH."""
    try:
        subprocess.run(["trivy", "--version"], capture_output=True, timeout=10)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _generate_sbom_trivy(image_ref: str, fmt: str = "spdx-json") -> Optional[Dict[str, Any]]:
    """Run `trivy image --format <fmt>` and return the parsed JSON document.

    Supports 'spdx-json' and 'cyclonedx' (CycloneDX JSON).
    Returns None on failure (non-fatal — the build still succeeds).
    """
    trivy_fmt = fmt  # trivy accepts 'spdx-json' and 'cyclonedx'
    try:
        result = subprocess.run(
            [
                "trivy", "image",
                "--format", trivy_fmt,
                "--quiet",
                "--skip-db-update",      # use cached DB; updated on startup
                "--skip-java-db-update",
                image_ref,
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            # Retry once *with* DB update in case cache is stale
            result = subprocess.run(
                [
                    "trivy", "image",
                    "--format", trivy_fmt,
                    "--quiet",
                    image_ref,
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        logger.warning("Trivy SBOM generation returned non-zero or empty: rc=%s stderr=%s",
                        result.returncode, result.stderr[:500])
    except subprocess.TimeoutExpired:
        logger.warning("Trivy SBOM generation timed out for %s", image_ref)
    except Exception as e:
        logger.warning("Trivy SBOM generation failed for %s: %s", image_ref, e)
    return None


def _extract_packages_spdx(doc: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extract a flat package list from an SPDX JSON document."""
    packages = []
    for pkg in doc.get("packages", []):
        name = pkg.get("name", "")
        if not name or name == doc.get("name"):
            continue  # skip the root document element
        # Determine type from externalRefs or purl
        pkg_type = ""
        version = pkg.get("versionInfo", "")
        for ref in pkg.get("externalRefs", []):
            purl = ref.get("referenceLocator", "")
            if purl.startswith("pkg:pypi/"):
                pkg_type = "pip"
            elif purl.startswith("pkg:deb/"):
                pkg_type = "apt"
            elif purl.startswith("pkg:npm/"):
                pkg_type = "npm"
            elif purl.startswith("pkg:golang/"):
                pkg_type = "go"
            elif purl.startswith("pkg:"):
                pkg_type = purl.split(":")[1].split("/")[0]
        license_info = pkg.get("licenseConcluded", pkg.get("licenseDeclared", ""))
        if license_info == "NOASSERTION":
            license_info = ""
        packages.append({
            "name": name,
            "version": version,
            "type": pkg_type,
            "license": license_info,
        })
    return packages


def _extract_packages_cyclonedx(doc: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extract a flat package list from a CycloneDX JSON document."""
    packages = []
    for comp in doc.get("components", []):
        name = comp.get("name", "")
        version = comp.get("version", "")
        pkg_type = ""
        purl = comp.get("purl", "")
        if purl.startswith("pkg:pypi/"):
            pkg_type = "pip"
        elif purl.startswith("pkg:deb/"):
            pkg_type = "apt"
        elif purl.startswith("pkg:npm/"):
            pkg_type = "npm"
        elif purl.startswith("pkg:golang/"):
            pkg_type = "go"
        elif purl.startswith("pkg:"):
            pkg_type = purl.split(":")[1].split("/")[0]

        license_info = ""
        for lic in comp.get("licenses", []):
            lid = lic.get("license", {})
            license_info = lid.get("id", lid.get("name", ""))
            if license_info:
                break
        packages.append({
            "name": name,
            "version": version,
            "type": pkg_type,
            "license": license_info,
        })
    return packages


async def _generate_and_store_sbom(
    image_ref: str,
    task_id: str,
    image_tag: str,
    image_version: int,
):
    """Generate SPDX + CycloneDX SBOMs via Trivy and POST them to the control plane."""
    if not _trivy_available():
        logger.info("Trivy not installed — skipping SBOM generation")
        return

    import httpx

    for fmt, extractor in [
        ("spdx-json", _extract_packages_spdx),
        ("cyclonedx", _extract_packages_cyclonedx),
    ]:
        doc = _generate_sbom_trivy(image_ref, fmt)
        if doc is None:
            continue

        packages = extractor(doc)
        payload = {
            "task_id": task_id,
            "image_tag": image_tag,
            "image_version": image_version,
            "format": "spdx-json" if fmt == "spdx-json" else "cyclonedx-json",
            "document": doc,
            "packages": packages,
            "generator": "trivy",
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(f"{CONTROL_PLANE_URL}/api/sbom", json=payload)
                if resp.status_code == 201:
                    logger.info("Stored %s SBOM for %s v%s (%d packages)",
                                fmt, task_id, image_version, len(packages))
                else:
                    logger.warning("Failed to store SBOM: %s %s", resp.status_code, resp.text[:300])
        except Exception as e:
            logger.warning("Failed to POST SBOM to control plane: %s", e)


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

        # Generate SBOM (non-blocking — build is already marked success)
        version_num = int(version) if version.isdigit() else 1
        try:
            await _generate_and_store_sbom(
                image_ref=image_tag,
                task_id=task_id,
                image_tag=registry_tag,
                image_version=version_num,
            )
        except Exception as sbom_err:
            logger.warning(f"SBOM generation failed (non-fatal): {sbom_err}")
        
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
        
        # ---- Parse LABEL capabilities (covers both pip and apt) ----
        # Format: LABEL capabilities="pip_package:flask,pip_package:redis,apt_package:redis-server"
        for m in re.finditer(r'capabilities="([^"]+)"', content):
            for cap in m.group(1).split(","):
                cap = cap.strip()
                if cap.startswith("pip_package:"):
                    pkg = cap[len("pip_package:"):]
                    if pkg and pkg not in pip_packages:
                        pip_packages.append(pkg)
                elif cap.startswith("apt_package:"):
                    pkg = cap[len("apt_package:"):]
                    if pkg and pkg not in apt_packages:
                        apt_packages.append(pkg)
        
        # ---- APT packages from RUN commands ----
        # Handle multi-line: apt-get install -y \<newline>  pkg1 \<newline>  && rm ...
        for m in re.finditer(r"apt-get install\s+-y\s+(.*?)(?:&&|$)", content, re.DOTALL):
            block = m.group(1)
            for token in block.split():
                token = token.strip().rstrip("\\")
                if token and not token.startswith("-") and token not in apt_packages:
                    apt_packages.append(token)
        
        # ---- PIP packages from RUN commands ----
        # Look for packages after --no-cache-dir
        for m in re.finditer(r"--no-cache-dir\s+(.+?)(?:\s*[;|]|$)", content):
            for pkg in m.group(1).split():
                if not pkg.startswith("-") and pkg not in pip_packages:
                    pip_packages.append(pkg)
    
    logger.info(f"Deployment packages from capabilities — pip: {pip_packages}, apt: {apt_packages}")

    # ---- Also scan app source files for third-party imports ----
    # This catches packages that were pre-installed in the agent base image
    # (e.g. `requests` in ZeroClaw) but never explicitly requested as a capability.
    workspace_path = Path("/workspaces")
    try:
        control_plane_url_env = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8000")
        import httpx as _httpx
        import asyncio as _aio
        loop = _aio.get_event_loop()
        # We're already in an async context from FastAPI, but we can do sync fetch
        # Actually, we're in the request handler — use sync httpx
        import httpx as _httpx_sync
        with _httpx_sync.Client(timeout=10.0) as _client:
            _resp = _client.get(f"{control_plane_url_env}/api/tasks/{request.task_id}")
            if _resp.status_code == 200:
                _task_data = _resp.json()
                _ws_id = _task_data.get("workspace_id", "")
                _ws_path = workspace_path / _ws_id
                if _ws_path.exists():
                    scanned = _scan_imports_for_pip_packages(_ws_path)
                    for pkg in scanned:
                        if pkg.lower() not in {p.lower() for p in pip_packages}:
                            pip_packages.append(pkg)
                    logger.info(f"Import scan found additional packages: {scanned}")
    except Exception as _scan_err:
        logger.warning(f"Import scanning failed (non-fatal): {_scan_err}")

    logger.info(f"Final deployment packages — pip: {pip_packages}, apt: {apt_packages}")

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


# =============================================================================
# SBOM & Vulnerability Scan Endpoints
# =============================================================================

class ScanRequest(BaseModel):
    """Request to scan an image for vulnerabilities."""
    image_ref: str  # e.g. "registry:5000/openclaw-agent:task-xxx-v1"
    task_id: str


@app.post("/scan/vulnerabilities")
async def scan_vulnerabilities(request: ScanRequest):
    """Run Trivy vulnerability scan against an image.

    Returns a JSON report of CVEs found, cross-referenced with the
    SBOM packages if available.
    """
    if not _trivy_available():
        raise HTTPException(503, "Trivy is not installed — vulnerability scanning unavailable")

    try:
        result = subprocess.run(
            [
                "trivy", "image",
                "--format", "json",
                "--quiet",
                "--skip-db-update",
                request.image_ref,
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            # Retry with DB update
            result = subprocess.run(
                [
                    "trivy", "image",
                    "--format", "json",
                    "--quiet",
                    request.image_ref,
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )

        if result.returncode != 0:
            return {
                "status": "error",
                "error": result.stderr[:1000],
                "task_id": request.task_id,
            }

        report = json.loads(result.stdout) if result.stdout.strip() else {}

        # Flatten vulnerabilities for easier consumption
        vulns = []
        for target in report.get("Results", []):
            for vuln in target.get("Vulnerabilities", []):
                vulns.append({
                    "id": vuln.get("VulnerabilityID"),
                    "package": vuln.get("PkgName"),
                    "installed_version": vuln.get("InstalledVersion"),
                    "fixed_version": vuln.get("FixedVersion"),
                    "severity": vuln.get("Severity"),
                    "title": vuln.get("Title", ""),
                    "description": vuln.get("Description", "")[:300],
                    "target": target.get("Target"),
                })

        return {
            "status": "ok",
            "task_id": request.task_id,
            "image_ref": request.image_ref,
            "vulnerability_count": len(vulns),
            "vulnerabilities": vulns,
            "raw_report": report,
        }

    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Vulnerability scan timed out")
    except Exception as e:
        raise HTTPException(500, f"Scan failed: {e}")


@app.post("/scan/sbom")
async def generate_sbom_on_demand(request: ScanRequest):
    """Generate SBOM for an image on demand (outside the build pipeline).

    Useful for scanning existing images that were built before SBOM
    generation was enabled.
    """
    if not _trivy_available():
        raise HTTPException(503, "Trivy is not installed — SBOM generation unavailable")

    # Extract version from image tag
    tag = request.image_ref.split(":")[-1] if ":" in request.image_ref else "v1"
    version_match = re.search(r"v(\d+)", tag)
    version_num = int(version_match.group(1)) if version_match else 1

    await _generate_and_store_sbom(
        image_ref=request.image_ref,
        task_id=request.task_id,
        image_tag=request.image_ref,
        image_version=version_num,
    )

    return {
        "status": "ok",
        "task_id": request.task_id,
        "image_ref": request.image_ref,
        "image_version": version_num,
        "message": "SBOM generation triggered",
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
