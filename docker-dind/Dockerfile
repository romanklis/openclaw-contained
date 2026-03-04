FROM docker:24-dind

# iproute2 gives us the `ip` command needed by the watchdog entrypoint
# to snapshot and restore eth0's IPv4 address.
RUN apk add --no-cache iproute2

# Install gVisor (runsc) into the DinD container so agent containers
# can be launched with runtime=runsc while staying on the Compose network.
# The static binary works on Alpine without additional dependencies.
RUN set -eux; \
    ARCH="$(uname -m)"; \
    wget -qO /usr/local/bin/runsc \
      "https://storage.googleapis.com/gvisor/releases/release/latest/${ARCH}/runsc" && \
    chmod +x /usr/local/bin/runsc && \
    runsc --version

COPY entrypoint-wrapper.sh /usr/local/bin/entrypoint-wrapper.sh
RUN chmod +x /usr/local/bin/entrypoint-wrapper.sh

ENTRYPOINT ["entrypoint-wrapper.sh"]
