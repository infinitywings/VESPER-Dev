#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# VESPER Router Container — Entrypoint
#
# Loads mac80211_hwsim, starts OVS, then launches the Mininet-WiFi topology.
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  VESPER Emulated WiFi Router (Mininet-WiFi)                 ║"
echo "╚══════════════════════════════════════════════════════════════╝"

NUM_RADIOS="${VESPER_NUM_RADIOS:-10}"

# ── 1. Load mac80211_hwsim ────────────────────────────────────────────────────
echo "[1/3] Loading mac80211_hwsim (${NUM_RADIOS} radios)..."
if ! lsmod | grep -q mac80211_hwsim; then
    modprobe mac80211_hwsim radios="${NUM_RADIOS}" 2>/dev/null || {
        echo "WARNING: Cannot load mac80211_hwsim. Ensure --privileged flag."
        echo "         Continuing anyway — Mininet-WiFi will handle it."
    }
else
    echo "  ✓ mac80211_hwsim already loaded"
fi

# ── 2. Start Open vSwitch (needed by Mininet-WiFi) ───────────────────────────
echo "[2/3] Starting Open vSwitch..."
service openvswitch-switch start 2>/dev/null || {
    ovsdb-server --remote=punix:/var/run/openvswitch/db.sock \
                 --remote=db:Open_vSwitch,Open_vSwitch,manager_options \
                 --pidfile --detach 2>/dev/null || true
    ovs-vswitchd --pidfile --detach 2>/dev/null || true
}
echo "  ✓ OVS running"

# ── 3. Launch VESPER topology ─────────────────────────────────────────────────
echo "[3/3] Launching VESPER WiFi topology..."
exec python3 /opt/vesper/vesper_topology.py "$@"
