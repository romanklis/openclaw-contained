# OpenClaw ANN2SNN Agent Image — PyTorch (CPU) simulation & control benchmark stack
# Version: 1.0.0
#
# Capability image derived from DAG dag-309c6cac: refactor the ball-and-plate prototype
# (torch/numpy/matplotlib) into importable modules, run pytest, serve an interactive
# control dashboard (Flask + Gunicorn) and push the result to GitHub.
#
# Baked in: torch (CPU build), numpy, matplotlib (Agg), pytest, flask(+cors), gunicorn.
# Not baked: CUDA/GPU libs (CPU only), browsers, secrets.

FROM registry:5000/openclaw-agent:openclaw

LABEL org.openclaw.image.type="ann2snn"
LABEL org.openclaw.image.version="1.0.0"
LABEL org.openclaw.image.description="OpenClaw image with CPU PyTorch, numpy, matplotlib, pytest, Flask and Gunicorn for ANN/SNN simulation and control benchmarks"
LABEL image_type="ann2snn"

USER root

# libgomp is required by the CPU PyTorch wheels; ffmpeg records/encodes demo videos.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgomp1 ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# CPU-only PyTorch from the official CPU wheel index, then the rest from PyPI.
RUN python3 -m pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url https://pypi.org/simple \
        "torch==2.4.1"

RUN python3 -m pip install --no-cache-dir \
        "numpy==1.26.4" \
        "matplotlib==3.9.2" \
        "pytest==8.3.3" \
        "flask==3.0.3" \
        "flask-cors==5.0.0" \
        "gunicorn==23.0.0"

# Headless plotting + writable caches for the agent user.
RUN mkdir -p /home/agent/.cache/torch /home/agent/.cache/matplotlib && \
    chown -R agent:agent /home/agent
ENV HOME=/home/agent
ENV XDG_CACHE_HOME=/home/agent/.cache
ENV MPLBACKEND=Agg
ENV TORCH_HOME=/home/agent/.cache/torch

ENV OPENCLAW_IMAGE_TYPE=ann2snn
ENV OPENCLAW_RUNTIME_DESCRIPTION="- Python 3 (Debian, standard library)\n- \`torch\` (CPU-only build) + \`numpy\`\n- \`matplotlib\` (Agg backend; save PNGs, no display)\n- \`pytest\` (test runner)\n- \`flask\` + \`flask-cors\` (backend API)\n- \`gunicorn\` (WSGI server for deployment)\n- \`ffmpeg\` (record/encode demo videos, e.g. X11/Xvfb capture, MP4/H.264)\n- Node.js + npm, gcc/g++/make, \`git\`, \`curl\`\n- CPU only (no CUDA/GPU); no browser binaries. Credentials (e.g. GitHub tokens) come from env/credential gateway — NEVER bake or commit secrets. Do NOT start long-running servers yourself: write the app, test it, then emit DEPLOYMENT_REQUEST:<app>:<port>:<entrypoint>."

WORKDIR /workspace

USER agent

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s \
    CMD python3 -c "import torch, numpy, matplotlib, flask, gunicorn; import sys; sys.exit(0)"
