# Use OpenClaw official image as base
# Pin to specific version for reproducibility
FROM ghcr.io/openclaw/openclaw:0.5.43

# Install Python for the wrapper script
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies system-wide (no venv)
ENV PIP_BREAK_SYSTEM_PACKAGES=1
RUN pip install --no-cache-dir httpx

# Copy the wrapper script
COPY openclaw-wrapper.py /app/wrapper.py

# Set working directory
WORKDIR /workspace

# The wrapper will invoke OpenClaw CLI
ENTRYPOINT ["python3", "/app/wrapper.py"]

