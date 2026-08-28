# OpenClaw Browser v2 Agent Image
# Version: 1.0.0
#
# Extends browser image with Obscura CLI for stealth browser automation.
# Obscura binaries are downloaded from official GitHub releases.

FROM registry:5000/openclaw-agent:browser

# Image metadata
LABEL org.openclaw.image.type="browser_v2"
LABEL org.openclaw.image.version="1.0.0"
LABEL org.openclaw.image.description="OpenClaw browser image with Obscura CLI"
LABEL image_type="browser_v2"

USER root

# Install Obscura binary pair (obscura + obscura-worker)
RUN set -eux; \
    arch="$(uname -m)"; \
    case "$arch" in \
      x86_64) obscura_arch="x86_64-linux" ;; \
      aarch64|arm64) obscura_arch="aarch64-linux" ;; \
      *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/obscura.tar.gz "https://github.com/h4ckf0r0day/obscura/releases/latest/download/obscura-${obscura_arch}.tar.gz"; \
    tar -xzf /tmp/obscura.tar.gz -C /tmp; \
    install -m 0755 /tmp/obscura /usr/local/bin/obscura; \
    install -m 0755 /tmp/obscura-worker /usr/local/bin/obscura-worker; \
    rm -f /tmp/obscura.tar.gz /tmp/obscura /tmp/obscura-worker

# Keep adapter in sync with base build context
COPY taskforge-adapter.py /opt/openclaw/taskforge-adapter.py

WORKDIR /workspace
USER agent

ENV OPENCLAW_IMAGE_TYPE=browser_v2
ENV OPENCLAW_RUNTIME_DESCRIPTION="- Python 3 (Debian, standard library)\n- \`httpx\` (HTTP client)\n- \`curl\`, \`git\`\n- \`obscura\` and \`obscura-worker\` for stealth browser automation\n- Browser-ready runtime inherited from the browser image\n- Chromium at /usr/bin/chromium with all required shared libraries"

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import sys; sys.exit(0)"

ENTRYPOINT ["python", "/opt/openclaw/taskforge-adapter.py"]
CMD []
