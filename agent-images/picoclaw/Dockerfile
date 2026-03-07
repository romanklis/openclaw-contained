# PicoClaw — Minimal Shell Agent Image
# Version: 1.0.0
#
# Ultra-minimal environment for simple shell-based tasks.
# No Python runtime — only POSIX shell, coreutils, and essential CLI tools.
# Ideal for file manipulation, system commands, and lightweight automation.

FROM alpine:3.19

# Image metadata
LABEL org.openclaw.image.type="picoclaw"
LABEL org.openclaw.image.version="1.0.0"
LABEL org.openclaw.image.description="Minimal shell agent for simple CLI tasks"
LABEL org.openclaw.image.runtime="shell-alpine"

# Install shell utilities
RUN apk add --no-cache \
    bash \
    coreutils \
    curl \
    wget \
    git \
    jq \
    yq \
    grep \
    sed \
    gawk \
    findutils \
    ca-certificates \
    openssh-client \
    && rm -rf /var/cache/apk/*

# Create non-root user for agent execution
RUN adduser -D -u 1001 -s /bin/bash agent && \
    mkdir -p /workspace /workspace/output && \
    chown -R agent:agent /workspace

# Copy the TaskForge-native shell adapter
COPY picoclaw-adapter.sh /opt/openclaw/picoclaw-adapter.sh
COPY policy_check.sh /opt/openclaw/policy_check.sh
RUN chmod +x /opt/openclaw/picoclaw-adapter.sh /opt/openclaw/policy_check.sh

# Set up workspace
WORKDIR /workspace
USER agent

# Environment variables
ENV OPENCLAW_WORKSPACE=/workspace \
    OPENCLAW_OUTPUT=/workspace/output \
    OPENCLAW_POLICY_ENFORCER=http://policy-engine:8001 \
    OPENCLAW_IMAGE_TYPE=picoclaw

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=3s \
    CMD [ -f /opt/openclaw/picoclaw-adapter.sh ] && exit 0 || exit 1

# Default: run the PicoClaw shell adapter (TaskForge-native)
ENTRYPOINT ["/bin/bash", "/opt/openclaw/picoclaw-adapter.sh"]
CMD []
