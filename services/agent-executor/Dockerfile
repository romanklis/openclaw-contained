# Use OpenClaw official image as base
FROM ghcr.io/openclaw/openclaw:latest

# Install Python for the wrapper script
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment for Python dependencies
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies for the wrapper
RUN pip install --no-cache-dir httpx

# Copy the wrapper script
COPY openclaw-wrapper.py /app/wrapper.py

# Set working directory
WORKDIR /workspace

# The wrapper will invoke OpenClaw CLI
ENTRYPOINT ["python3", "/app/wrapper.py"]

