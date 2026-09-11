# OpenClaw Banking Agent Image (MongoDB + PostgreSQL drivers)
# Version: 1.0.0
#
# Deterministic, database-capable agent runtime for the banking governance demo.
# Bakes in the MongoDB and PostgreSQL Python drivers so analysis steps never need
# to discover/install DB clients at run time. Connectivity is endpoint-injected
# per node (worker extra_service_env); this image only provides the drivers.

FROM registry:5000/openclaw-agent:openclaw

LABEL org.openclaw.image.type="banking"
LABEL org.openclaw.image.version="1.0.0"
LABEL org.openclaw.image.description="OpenClaw image with MongoDB (pymongo) and PostgreSQL (psycopg2) drivers for banking demo data access"
LABEL image_type="banking"

USER root

# Ensure agent user exists (base image may not have it in registry)
RUN if ! id -u agent >/dev/null 2>&1; then \
        groupadd -g 1001 agent && \
        useradd -u 1001 -g agent -m -s /bin/bash agent; \
    fi

# Install database drivers baked into the image
RUN python3 -m pip install --no-cache-dir \
        "pymongo[srv]==4.7.3" \
        "psycopg2-binary==2.9.9"

# Prepare agent home
RUN mkdir -p /home/agent/.cache && \
    chown -R agent:agent /home/agent

ENV HOME=/home/agent
ENV XDG_CACHE_HOME=/home/agent/.cache
ENV OPENCLAW_IMAGE_TYPE=banking
ENV OPENCLAW_RUNTIME_DESCRIPTION="- Python 3 (Debian, standard library)\n- \`httpx\` (HTTP client)\n- \`curl\`, \`git\`\n- \`pymongo\` (MongoDB driver, incl. srv URIs)\n- \`psycopg2\` / psycopg2-binary (PostgreSQL driver)\n- DB connectivity is endpoint-injected per node; connect ONLY to the provided host(s); never scan the network."

WORKDIR /workspace

USER agent

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python3 -c "import pymongo, psycopg2; import sys; sys.exit(0)"
