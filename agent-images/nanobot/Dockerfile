# NanoBot — Lightweight Alpine Python Agent Image
# Version: 1.0.0
#
# Optimized for fast startup and low memory footprint.
# Ideal for quick scripts, data transformations, and lightweight tasks.

FROM python:3.11-alpine

# Image metadata
LABEL org.openclaw.image.type="nanobot"
LABEL org.openclaw.image.version="1.0.0"
LABEL org.openclaw.image.description="Lightweight Alpine Python agent for fast scripts and data tasks"
LABEL org.openclaw.image.runtime="python-alpine"

# Install minimal system dependencies
RUN apk add --no-cache \
    curl \
    wget \
    git \
    ca-certificates \
    bash \
    jq \
    && rm -rf /var/cache/apk/*

# Create non-root user for agent execution
RUN adduser -D -u 1001 -s /bin/bash agent && \
    mkdir -p /workspace /workspace/output && \
    chown -R agent:agent /workspace

# Install base Python packages (minimal set)
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt

# Copy shared TaskForge adapter + NanoBot wrapper
COPY taskforge-adapter.py /opt/openclaw/
COPY nanobot-wrapper.py /opt/openclaw/

# Set up workspace
WORKDIR /workspace
USER agent

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    OPENCLAW_WORKSPACE=/workspace \
    OPENCLAW_OUTPUT=/workspace/output \
    OPENCLAW_POLICY_ENFORCER=http://policy-engine:8001 \
    OPENCLAW_IMAGE_TYPE=nanobot

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import sys; sys.exit(0)"

# Default: run the NanoBot adapter (TaskForge-native agentic loop)
ENTRYPOINT ["python", "/opt/openclaw/nanobot-wrapper.py"]
CMD []
