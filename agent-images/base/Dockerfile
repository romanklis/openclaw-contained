# OpenClaw Base Agent Image
# Version: 1.0.0
# 
# This is the foundational image for all OpenClaw agents.
# Capabilities are layered on top of this image.

FROM python:3.11-slim

# Image metadata
LABEL org.openclaw.image.type="base-agent"
LABEL org.openclaw.image.version="1.0.0"
LABEL org.openclaw.image.description="Base image for OpenClaw policy-driven agents"

# Install system dependencies
RUN apt-get update && apt-get install -y \
    # Core utilities
    curl \
    wget \
    git \
    ca-certificates \
    # Build tools (for Python packages)
    gcc \
    g++ \
    make \
    # Network tools
    netcat-openbsd \
    # Process management
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for agent execution
RUN useradd -m -u 1001 -s /bin/bash agent && \
    mkdir -p /workspace /workspace/output && \
    chown -R agent:agent /workspace

# Install base Python packages
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt

# Copy agent runtime
COPY agent_runtime.py /opt/openclaw/
COPY policy_client.py /opt/openclaw/
COPY config.py /opt/openclaw/

# Set up workspace
WORKDIR /workspace
USER agent

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    OPENCLAW_WORKSPACE=/workspace \
    OPENCLAW_OUTPUT=/workspace/output \
    OPENCLAW_POLICY_ENFORCER=http://policy-engine:8001

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import sys; sys.exit(0)"

# Default command
CMD ["python", "/opt/openclaw/agent_runtime.py"]
