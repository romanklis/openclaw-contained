# OpenClaw Browser Agent Image
# Version: 1.0.0
#
# Extends the openclaw base image with a pre-installed Chromium browser,
# all required shared libraries, and the agent-browser npm package.
#
# Motivation: agent-browser (and puppeteer/playwright) need system Chromium
# binaries and dozens of shared libraries to be present BEFORE npm install
# runs their post-install scripts.  Installing them via the dynamic capability
# system (which layers packages onto openclaw:base) fails because the post-
# install steps cannot complete without these prerequisites.  This image bakes
# everything in so agents can use browser automation out of the box.

FROM registry:5000/openclaw-agent:openclaw

# Image metadata
LABEL org.openclaw.image.type="browser"
LABEL org.openclaw.image.version="1.0.0"
LABEL org.openclaw.image.description="OpenClaw agent with pre-installed Chromium and agent-browser"
LABEL image_type="browser"

USER root

# Ensure agent user exists (base image may not have it in registry)
RUN if ! id -u agent >/dev/null 2>&1; then \
        groupadd -g 1001 agent && \
        useradd -u 1001 -g agent -m -s /bin/bash agent; \
    fi

# Install Chromium and all shared libraries required by headless Chrome
RUN apt-get update && apt-get install -y \
    chromium \
    # Core shared libraries
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libxshmfence1 \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Tell all browser tools to skip downloading a bundled Chromium and
# point to the system-installed binary instead
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=true \
    PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium \
    AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium \
    AGENT_BROWSER_ARGS="--no-sandbox,--disable-gpu"

# Install agent-browser (and companion npm tools) using the system Chromium
RUN npm install -g \
    agent-browser \
    puppeteer \
    && npm list -g --depth=0

# Override adapter with latest runtime logic from build context
COPY taskforge-adapter.py /opt/openclaw/taskforge-adapter.py

# Switch back to non-root agent user
WORKDIR /workspace
USER agent

ENV OPENCLAW_IMAGE_TYPE=browser
ENV OPENCLAW_RUNTIME_DESCRIPTION="- Python 3 (Debian, standard library)\n- \`httpx\` (HTTP client)\n- \`curl\`, \`git\`\n- \`agent-browser\` for browser automation\n- Chromium at /usr/bin/chromium with all required shared libraries\n- \`puppeteer\` with system Chromium\n- Browser-ready runtime with Chromium and shared libraries"

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import sys; sys.exit(0)"

ENTRYPOINT ["python", "/opt/openclaw/taskforge-adapter.py"]
CMD []
