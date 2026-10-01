#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# VESPER — UTM VM Bootstrap: From Fresh Ubuntu to Experiment Results
# ═══════════════════════════════════════════════════════════════════════════════
#
# Run this ONE script on a fresh Ubuntu 22.04 VM inside UTM.
# It does EVERYTHING: installs deps, builds wmediumd, runs experiments.
#
# Usage (inside the VM):
#   cd ~/vesper
#   sudo bash scripts/bootstrap_utm.sh             # Full (~3-4 hours)
#   sudo bash scripts/bootstrap_utm.sh --quick      # Quick test (~30 min)
#   sudo bash scripts/bootstrap_utm.sh --setup-only  # Install deps only
#
# After completion, copy results back to your Mac:
#   # On macOS:
#   scp -r user@VM_IP:~/vesper/results/wmediumd_* ~/Desktop/vesper/results/
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# If bootstrap is at ~/bootstrap_utm.sh, VESPER_DIR is ~ (scripts are in ~/scripts/)
# If bootstrap is at ~/vesper/scripts/bootstrap_utm.sh, VESPER_DIR is ~/vesper
if [[ -d "$SCRIPT_DIR/scripts" ]]; then
    VESPER_DIR="$SCRIPT_DIR"
elif [[ -d "$SCRIPT_DIR/../scripts" ]]; then
    VESPER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
else
    VESPER_DIR="$SCRIPT_DIR"
fi
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log()    { echo -e "${GREEN}[$(date +%H:%M:%S)]${NC} $*"; }
warn()   { echo -e "${YELLOW}[$(date +%H:%M:%S)] WARN:${NC} $*"; }
err()    { echo -e "${RED}[$(date +%H:%M:%S)] ERROR:${NC} $*" >&2; }
header() { echo -e "\n${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"; echo -e "${CYAN}║${NC}  ${BOLD}$*${NC}"; echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}\n"; }

# Parse args
TRIALS=3
QUICK=false
SETUP_ONLY=false
WMEDIUMD_SCENARIO="typical_home"

for arg in "$@"; do
    case $arg in
        --quick)      QUICK=true; TRIALS=1 ;;
        --setup-only) SETUP_ONLY=true ;;
        --trials=*)   TRIALS="${arg#*=}" ;;
        --help|-h)
            echo "Usage: sudo $0 [--quick|--setup-only|--trials=N]"
            exit 0 ;;
    esac
done

# ═════════════════════════════════════════════════════════════════════════════
# PHASE 1: System Setup
# ═════════════════════════════════════════════════════════════════════════════
header "Phase 1: System Dependencies"

if [[ "$(uname -s)" != "Linux" ]]; then
    err "Must run on Linux. You're on $(uname -s)."
    exit 1
fi
if [[ "$(id -u)" -ne 0 ]]; then
    err "Must run as root: sudo $0"
    exit 1
fi

log "OS: $(grep PRETTY_NAME /etc/os-release | cut -d= -f2 | tr -d '\"')"
log "Kernel: $(uname -r)"

# Install packages
log "Installing system packages..."
apt-get update -qq

apt-get install -y -qq --no-install-recommends \
    build-essential git wget curl sudo ca-certificates \
    python3 python3-pip python3-venv python3-dev \
    iproute2 iptables iputils-ping net-tools bridge-utils ethtool kmod \
    hostapd wpasupplicant wireless-tools iw rfkill \
    tshark tcpdump \
    iperf3 arping \
    libnl-3-dev libnl-genl-3-dev libconfig-dev pkg-config \
    jq bc \
    2>&1 | tail -5

# Kernel module
log "Loading mac80211_hwsim..."
apt-get install -y -qq "linux-modules-extra-$(uname -r)" 2>/dev/null || true
modprobe mac80211_hwsim radios=4 2>/dev/null || true

if lsmod | grep -q mac80211_hwsim; then
    log "✓ mac80211_hwsim loaded"
    modprobe -r mac80211_hwsim  # unload for now, scripts will reload
else
    err "mac80211_hwsim failed to load. Check: apt install linux-modules-extra-$(uname -r)"
    exit 1
fi

# ═════════════════════════════════════════════════════════════════════════════
# PHASE 2: Build wmediumd
# ═════════════════════════════════════════════════════════════════════════════
header "Phase 2: Building wmediumd"

if command -v wmediumd &>/dev/null; then
    log "✓ wmediumd already installed"
else
    WMED_DIR="/tmp/wmediumd-build"
    if [[ ! -d "$WMED_DIR/wmediumd/.git" ]]; then
        log "Cloning wmediumd..."
        rm -rf "$WMED_DIR"
        mkdir -p "$WMED_DIR"
        git clone --depth 1 https://github.com/bcopeland/wmediumd "$WMED_DIR/wmediumd" 2>&1 | tail -3
    fi

    log "Building..."
    make -C "$WMED_DIR/wmediumd" -j"$(nproc)" 2>&1 | tail -5
    make -C "$WMED_DIR/wmediumd" install 2>&1 | tail -3

    if command -v wmediumd &>/dev/null; then
        log "✓ wmediumd installed: $(which wmediumd)"
    else
        err "wmediumd build failed!"
        exit 1
    fi
fi

# ═════════════════════════════════════════════════════════════════════════════
# PHASE 3: Python Environment
# ═════════════════════════════════════════════════════════════════════════════
header "Phase 3: Python Environment"

cd "$VESPER_DIR"

if [[ ! -d ".venv" ]]; then
    log "Creating venv..."
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q --upgrade pip 2>/dev/null
pip install -q -e . 2>/dev/null || pip install -q -r requirements.txt 2>/dev/null || true
log "✓ Python $(python3 --version | cut -d' ' -f2) venv ready"

# Verify scripts
for f in scripts/run_rqn1_native.py scripts/run_rqn2_native.py scripts/wmediumd_helper.py; do
    python3 -c "import ast; ast.parse(open('$f').read())" || { err "$f has syntax errors"; exit 1; }
done
log "✓ All experiment scripts valid"

if [[ "$SETUP_ONLY" == "true" ]]; then
    header "Setup Complete!"
    echo -e "  Run experiments with:"
    echo -e "    ${BOLD}sudo bash scripts/run_wmediumd_experiments.sh${NC}"
    echo -e "    ${BOLD}sudo bash scripts/run_wmediumd_experiments.sh --quick${NC}"
    exit 0
fi

# ═════════════════════════════════════════════════════════════════════════════
# PHASE 4: Run Experiments
# ═════════════════════════════════════════════════════════════════════════════
header "Phase 4: Running Experiments"

OUTPUT_DIR="$VESPER_DIR/results/wmediumd_${TIMESTAMP}"
mkdir -p "$OUTPUT_DIR"

TOTAL_START=$(date +%s)

# Clean stale state
killall hostapd wpa_supplicant wmediumd 2>/dev/null || true
for i in 0 1 2 3; do
    ip netns del "ns-sta$i" 2>/dev/null || true
    ip netns del "ns-bridge$i" 2>/dev/null || true
done
ip link del br-vesper 2>/dev/null || true
modprobe -r mac80211_hwsim 2>/dev/null || true
sleep 1

RQN2_FLAG="--full"
if [[ "$QUICK" == "true" ]]; then
    RQN2_FLAG="--configs 0,7"
fi

# ── RQ-N1 with wmediumd ─────────────────────────────────────────────────────
header "RQ-N1: Bridge vs 802.11+wmediumd ($TRIALS trials)"
RQN1_WM="$OUTPUT_DIR/rqn1_wmediumd"

python3 scripts/run_rqn1_native.py \
    --full --trials "$TRIALS" \
    --wmediumd --wmediumd-scenario "$WMEDIUMD_SCENARIO" \
    --output "$RQN1_WM" \
    2>&1 | tee "$OUTPUT_DIR/rqn1_wmediumd.log"

log "✓ RQ-N1 (wmediumd) done"

# Cleanup between runs
killall hostapd wpa_supplicant wmediumd 2>/dev/null || true
modprobe -r mac80211_hwsim 2>/dev/null || true
sleep 3

# ── RQ-N1 baseline (no wmediumd) ────────────────────────────────────────────
header "RQ-N1: Bridge vs 802.11 baseline ($TRIALS trials)"
RQN1_BL="$OUTPUT_DIR/rqn1_baseline"

python3 scripts/run_rqn1_native.py \
    --full --trials "$TRIALS" \
    --output "$RQN1_BL" \
    2>&1 | tee "$OUTPUT_DIR/rqn1_baseline.log"

log "✓ RQ-N1 (baseline) done"

killall hostapd wpa_supplicant 2>/dev/null || true
modprobe -r mac80211_hwsim 2>/dev/null || true
sleep 3

# ── RQ-N2 with wmediumd ─────────────────────────────────────────────────────
header "RQ-N2: Hardening sweep + wmediumd ($TRIALS trials)"
RQN2_WM="$OUTPUT_DIR/rqn2_wmediumd"

python3 scripts/run_rqn2_native.py \
    $RQN2_FLAG --trials "$TRIALS" \
    --wmediumd --wmediumd-scenario "$WMEDIUMD_SCENARIO" \
    --output "$RQN2_WM" \
    2>&1 | tee "$OUTPUT_DIR/rqn2_wmediumd.log"

log "✓ RQ-N2 (wmediumd) done"

killall hostapd wpa_supplicant wmediumd 2>/dev/null || true
modprobe -r mac80211_hwsim 2>/dev/null || true
sleep 3

# ── RQ-N2 baseline ──────────────────────────────────────────────────────────
header "RQ-N2: Hardening sweep baseline ($TRIALS trials)"
RQN2_BL="$OUTPUT_DIR/rqn2_baseline"

python3 scripts/run_rqn2_native.py \
    $RQN2_FLAG --trials "$TRIALS" \
    --output "$RQN2_BL" \
    2>&1 | tee "$OUTPUT_DIR/rqn2_baseline.log"

log "✓ RQ-N2 (baseline) done"

# Final cleanup
killall hostapd wpa_supplicant wmediumd 2>/dev/null || true
for i in 0 1 2 3; do
    ip netns del "ns-sta$i" 2>/dev/null || true
    ip netns del "ns-bridge$i" 2>/dev/null || true
done
ip link del br-vesper 2>/dev/null || true
modprobe -r mac80211_hwsim 2>/dev/null || true

TOTAL_ELAPSED=$(( $(date +%s) - TOTAL_START ))

# ═════════════════════════════════════════════════════════════════════════════
# PHASE 5: Summary
# ═════════════════════════════════════════════════════════════════════════════
header "ALL EXPERIMENTS COMPLETE"

echo -e "  ${BOLD}Time:${NC}    $((TOTAL_ELAPSED / 3600))h $((TOTAL_ELAPSED % 3600 / 60))m $((TOTAL_ELAPSED % 60))s"
echo -e "  ${BOLD}Results:${NC} $OUTPUT_DIR/"
echo ""

for subdir in rqn1_wmediumd rqn1_baseline rqn2_wmediumd rqn2_baseline; do
    d="$OUTPUT_DIR/$subdir"
    if [[ -d "$d" ]]; then
        n_json=$(find "$d" -name "*.json" | wc -l)
        echo -e "  📁 ${BOLD}$subdir/${NC} ($n_json JSON files)"
    fi
done

echo ""
echo -e "  ${BOLD}Next: copy results back to your Mac:${NC}"
echo -e "    scp -r $(whoami)@\$(hostname -I | awk '{print \$1}'):$OUTPUT_DIR/ ~/Desktop/vesper/results/"
echo ""
