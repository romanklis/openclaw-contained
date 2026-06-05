# OpenClaw OctaveClaw Agent Image
# Version: 1.1.0
#
# Extends OpenClaw with GNU Octave for numerical simulations.

FROM registry:5000/openclaw-agent:openclaw

LABEL org.openclaw.image.type="octaveclaw"
LABEL org.openclaw.image.version="1.1.0"
LABEL org.openclaw.image.description="OpenClaw image with GNU Octave for financial time-series and simulation workloads"
LABEL image_type="octaveclaw"

USER root

RUN apt-get update && apt-get install -y \
    ca-certificates \
    curl \
    gnuplot \
    libblas-dev \
    liblapack-dev \
    octave \
    octave-cgi \
    octave-control \
    octave-dataframe \
    octave-econometrics \
    octave-financial \
    octave-io \
    octave-jsonlab \
    octave-optim \
    octave-parallel \
    octave-signal \
    octave-sockets \
    octave-statistics \
    octave-tsa \
    && rm -rf /var/lib/apt/lists/*

# Build-time smoke test for critical packages used in quant workflows and
# lightweight Octave web/API integrations.
RUN octave --quiet --eval "pkg load financial; pkg load econometrics; pkg load statistics; pkg load tsa; pkg load io; pkg load sockets; pkg load cgi; pkg load jsonlab; disp('octaveclaw package check: OK')"

WORKDIR /workspace
USER agent

ENV OPENCLAW_IMAGE_TYPE=octaveclaw
ENV QT_QPA_PLATFORM=offscreen
ENV GNUTERM=dumb
ENV OCTAVE_DEFAULT_PLOT_TOOLKIT=gnuplot

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import sys; sys.exit(0)"
