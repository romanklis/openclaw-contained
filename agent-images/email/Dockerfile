# OpenClaw Email Intelligence Agent Image
# Version: 1.0.0
#
# Deterministic runtime for the email-intelligence showcase. The base image
# already ships pypdf/pymupdf and httpx; this layer adds the PostgreSQL driver
# and demo connection defaults so agent nodes can persist structured
# extractions without discovering/installing clients at run time.

FROM registry:5000/openclaw-agent:openclaw

LABEL org.openclaw.image.type="email"
LABEL org.openclaw.image.version="1.0.0"
LABEL org.openclaw.image.description="OpenClaw image with PostgreSQL driver, PDF parsing, and the read-only IMAP/credential-gateway path for the email-intelligence demo"
LABEL image_type="email"

USER root

# Ensure agent user exists (base image may not have it in registry)
RUN if ! id -u agent >/dev/null 2>&1; then \
        groupadd -g 1001 agent && \
        useradd -u 1001 -g agent -m -s /bin/bash agent; \
    fi

# PostgreSQL driver for the phase-1 structured storage.
RUN python3 -m pip install --no-cache-dir "psycopg2-binary==2.9.9"

# Prepare agent home
RUN mkdir -p /home/agent/.cache && \
    chown -R agent:agent /home/agent

# Demo-only connection defaults. The example DB role is least-privilege
# (no DELETE, no DDL, no scheduler tables). Production deployments must inject
# the password from a secret store instead of baking it into an image.
ENV HOME=/home/agent
ENV XDG_CACHE_HOME=/home/agent/.cache
ENV OPENCLAW_IMAGE_TYPE=email
ENV OPENCLAW_RUNTIME_DESCRIPTION="- Python 3 (Debian, standard library)\n- \`httpx\` (HTTP client)\n- \`curl\`, \`git\`\n- \`pypdf\` / \`pymupdf\` (PDF text extraction)\n- \`psycopg2\` / psycopg2-binary (PostgreSQL driver)\n- Read-only IMAP through the credential gateway (email_imap skill driver); never connect to IMAP directly.\n- DB connectivity is restricted to the provided example database/role; never scan the network."
ENV EMAIL_DB_HOST=email-postgres
ENV EMAIL_DB_PORT=5432
ENV EMAIL_DB_NAME=email_intelligence
ENV EMAIL_DB_USER=taskforge_writer
ENV EMAIL_DB_PASSWORD=taskforge_writer_pwd_demo
ENV EMAIL_CREDENTIAL=taskforge-mail

WORKDIR /workspace

USER agent

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python3 -c "import psycopg2, pypdf; import sys; sys.exit(0)"
