#!/bin/sh
# ---------------------------------------------------------------------------
# DinD entrypoint wrapper — network watchdog
#
# Problem
# -------
# The inner dockerd (and gVisor container launches) can flush IPv4
# addresses from both eth0 (Compose bridge) and docker0 (inner bridge).
# When eth0 loses its IP, external services can't reach dockerd:2375.
# When docker0 loses its IP, agent containers on the bridge can't route
# out to the Compose network.
#
# Solution
# --------
# 1. Snapshot eth0's address *before* dockerd starts.
# 2. Wait for dockerd to create docker0 and snapshot that too.
# 3. Poll every second and restore any missing addresses.
# ---------------------------------------------------------------------------

set -eu

ETH0_ADDR=""
ETH0_GW=""
DOCKER0_ADDR=""

snapshot_eth0() {
    ETH0_ADDR="$(ip -4 addr show eth0 2>/dev/null \
                  | awk '/inet / {print $2}' || true)"
    ETH0_GW="$(ip -4 route show default 2>/dev/null \
                | awk '/^default/ {print $3}' || true)"

    if [ -n "$ETH0_ADDR" ]; then
        echo "[ip-watchdog] Snapshotted eth0: addr=${ETH0_ADDR} gw=${ETH0_GW}"
    else
        echo "[ip-watchdog] WARNING: eth0 has no IPv4 at snapshot time"
    fi
}

snapshot_docker0() {
    # docker0 is created by dockerd, so we wait until it appears.
    for _ in $(seq 1 30); do
        DOCKER0_ADDR="$(ip -4 addr show docker0 2>/dev/null \
                         | awk '/inet / {print $2}' || true)"
        if [ -n "$DOCKER0_ADDR" ]; then
            echo "[ip-watchdog] Snapshotted docker0: addr=${DOCKER0_ADDR}"
            return
        fi
        sleep 1
    done
    echo "[ip-watchdog] WARNING: docker0 never got an IPv4 (will use 172.17.0.1/16)"
    DOCKER0_ADDR="172.17.0.1/16"
}

restore_if_missing() {
    IFACE="$1"
    EXPECTED="$2"

    CURRENT="$(ip -4 addr show "$IFACE" 2>/dev/null \
                | awk '/inet / {print $2}' || true)"

    if [ -z "$CURRENT" ] && [ -n "$EXPECTED" ]; then
        echo "[ip-watchdog] ${IFACE} lost its IPv4 — restoring ${EXPECTED}"
        ip addr add "$EXPECTED" dev "$IFACE" 2>/dev/null || true
    fi
}

watchdog() {
    # Wait for dockerd to finish init and snapshot docker0.
    snapshot_docker0

    echo "[ip-watchdog] Watchdog active — polling every 1 s"
    while true; do
        # Restore eth0 (Compose bridge connectivity)
        restore_if_missing eth0 "$ETH0_ADDR"

        # Restore default gateway if missing
        if [ -n "$ETH0_GW" ]; then
            HAS_DEFAULT="$(ip -4 route show default 2>/dev/null | awk '/^default/ {print $3}' || true)"
            if [ -z "$HAS_DEFAULT" ]; then
                ip route add default via "$ETH0_GW" dev eth0 2>/dev/null || true
            fi
        fi

        # Restore docker0 (agent container NAT routing)
        restore_if_missing docker0 "$DOCKER0_ADDR"

        sleep 1
    done
}

# -- main ------------------------------------------------------------------
snapshot_eth0
watchdog &

# Hand off to the real DinD entrypoint with all original arguments.
exec dockerd-entrypoint.sh "$@"
