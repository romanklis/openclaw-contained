# OpenClaw Browser v3 Agent Image
# Version: 1.0.0
#
# Extends browser image with Lightpanda browser binary.
# Lightpanda binaries are downloaded from official GitHub releases.

FROM registry:5000/openclaw-agent:browser

# Image metadata
LABEL org.openclaw.image.type="browser_v3"
LABEL org.openclaw.image.version="1.0.0"
LABEL org.openclaw.image.description="OpenClaw browser image with Lightpanda"
LABEL image_type="browser_v3"

USER root

# Install Lightpanda browser binary
RUN set -eux; \
    arch="$(uname -m)"; \
    case "$arch" in \
      x86_64) lp_arch="x86_64-linux" ;; \
      aarch64|arm64) lp_arch="aarch64-linux" ;; \
      *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /usr/local/bin/lightpanda "https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-${lp_arch}"; \
    chmod 0755 /usr/local/bin/lightpanda

# Keep adapter in sync with base build context
COPY taskforge-adapter.py /opt/openclaw/taskforge-adapter.py

WORKDIR /workspace
USER agent

ENV OPENCLAW_IMAGE_TYPE=browser_v3

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import sys; sys.exit(0)"

ENTRYPOINT ["python", "/opt/openclaw/taskforge-adapter.py"]
CMD []
