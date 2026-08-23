# OpenClaw Browser Agent Image (Camoufox v4 + curl_cffi)
# Version: 1.0.3

FROM registry:5000/openclaw-agent:openclaw

LABEL org.openclaw.image.type="browser_v4"
LABEL org.openclaw.image.version="1.0.3"
LABEL org.openclaw.image.description="OpenClaw browser image with Camoufox and curl_cffi for agentic browsing"
LABEL image_type="browser_v4"

USER root

# Ensure agent user exists (base image may not have it in registry)
RUN if ! id -u agent >/dev/null 2>&1; then \
        groupadd -g 1001 agent && \
        useradd -u 1001 -g agent -m -s /bin/bash agent; \
    fi

# Install libraries required by Firefox/Camoufox
RUN apt-get update && apt-get install -y \
    libgtk-3-0 \
    libasound2 \
    libx11-xcb1 \
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
    libxshmfence1 \
    libdbus-1-3 \
    fonts-liberation \
    fonts-noto-color-emoji \
    xxd \
    file \
    && rm -rf /var/lib/apt/lists/*

# Install Camoufox and curl_cffi (with CLI support)
RUN python3 -m pip install --no-cache-dir \
    "camoufox[geoip]==0.4.11" \
    "curl_cffi[cli]"

# Install web processing libraries
RUN python3 -m pip install --no-cache-dir \
    beautifulsoup4 \
    lxml \
    html5lib \
    cssselect \
    pyquery \
    readability-lxml \
    trafilatura \
    newspaper3k \
    goose3 \
    extruct \
    w3lib \
    scrapy \
    feedparser \
    lxml-html-clean \
    html-sanitizer \
    fake-useragent \
    pypdf \
    pymupdf

# Copy adapter
COPY taskforge-adapter.py /opt/openclaw/taskforge-adapter.py
COPY test /opt/openclaw

# Prepare agent home
RUN mkdir -p /home/agent/.cache && \
    chown -R agent:agent /home/agent

ENV HOME=/home/agent
ENV XDG_CACHE_HOME=/home/agent/.cache
ENV OPENCLAW_IMAGE_TYPE=browser_v4
ENV DBUS_SESSION_BUS_ADDRESS=/dev/null
ENV OPENCLAW_RUNTIME_DESCRIPTION="- Python 3 (Debian, standard library)\n- \`httpx\` (HTTP client)\n- \`curl\`, \`git\`\n- Camoufox (Firefox-based stealth browser) with GeoIP\n- \`curl_cffi\` for TLS fingerprinting\n- Web processing: beautifulsoup4, lxml, html5lib, cssselect, pyquery\n- Content extraction: readability-lxml, trafilatura, newspaper3k, goose3, extruct\n- \`scrapy\`, \`feedparser\`, \`w3lib\`, \`fake-useragent`"

WORKDIR /workspace

# Download browser + GeoIP while running as root
RUN python3 -m camoufox fetch

# Give the agent ownership of the downloaded browser cache
RUN chown -R agent:agent /home/agent/.cache

# Give the agent ownership of the Camoufox package (contains GeoLite DB)
RUN CAMOUFOX_DIR=$(python3 -c "import camoufox, pathlib; print(pathlib.Path(camoufox.__file__).parent)") && \
    chown -R agent:agent "$CAMOUFOX_DIR"

USER agent

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python3 -c "import sys, curl_cffi; sys.exit(0)"

ENTRYPOINT ["python3", "/opt/openclaw/taskforge-adapter.py"]
CMD []