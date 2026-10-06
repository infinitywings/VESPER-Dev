#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# VESPER — wmediumd Channel Emulation Experiments
# ═══════════════════════════════════════════════════════════════════════════════
#
# One-shot script: installs wmediumd, runs RQ-N1 + RQ-N2 with channel
# emulation, then runs again without it for comparison.
#
# Produces 4 result directories:
#   results/wmediumd_TIMESTAMP/rqn1_wmediumd/     (bridge vs 802.11+wmediumd)
#   results/wmediumd_TIMESTAMP/rqn1_baseline/      (bridge vs 802.11 baseline)
#   results/wmediumd_TIMESTAMP/rqn2_wmediumd/      (hardening sweep +wmediumd)
#   results/wmediumd_TIMESTAMP/rqn2_baseline/       (hardening sweep baseline)
#
# Usage:
#   sudo bash scripts/run_wmediumd_experiments.sh              # Full run
#   sudo bash scripts/run_wmediumd_experiments.sh --quick      # 1 trial, configs 0,7 only
#   sudo bash scripts/run_wmediumd_experiments.sh --rqn1-only  # RQ-N1 only
#   sudo bash scripts/run_wmediumd_experiments.sh --rqn2-only  # RQ-N2 only
#
# Prerequisites: Ubuntu 22.04, sudo, internet access
# Time: ~2-4 hours (full), ~20 min (quick)
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VESPER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_DIR="$VESPER_DIR/results/wmediumd_${TIMESTAMP}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log()    { echo -e "${GREEN}[$(date +%H:%M:%S)]${NC} $*"; }
warn()   { echo -e "${YELLOW}[$(date +%H:%M:%S)] WARNING:${NC} $*"; }
err()    { echo -e "${RED}[$(date +%H:%M:%S)] ERROR:${NC} $*" >&2; }
header() { echo -e "\n${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"; echo -e "${CYAN}║${NC}  ${BOLD}$*${NC}"; echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}\n"; }

# ── Parse arguments ──────────────────────────────────────────────────────────
TRIALS=3
RUN_RQN1=true
RUN_RQN2=true
QUICK=false
WMEDIUMD_SCENARIO="typical_home"
SKIP_BASELINE=false

for arg in "$@"; do
    case $arg in
        --quick)         QUICK=true; TRIALS=1 ;;
        --rqn1-only)     RUN_RQN2=false ;;
        --rqn2-only)     RUN_RQN1=false ;;
        --trials=*)      TRIALS="${arg#*=}" ;;
        --scenario=*)    WMEDIUMD_SCENARIO="${arg#*=}" ;;
        --skip-baseline) SKIP_BASELINE=true ;;
        --help|-h)
            echo "Usage: sudo $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --quick           1 trial, RQ-N2 configs 0,7 only (~20 min)"
            echo "  --rqn1-only       Run only RQ-N1"
            echo "  --rqn2-only       Run only RQ-N2"
            echo "  --trials=N        Number of trials (default: 3)"
            echo "  --scenario=NAME   wmediumd scenario: ideal, typical_home, challenging"
            echo "  --skip-baseline   Skip non-wmediumd baseline runs"
            echo ""
            exit 0
            ;;
    esac
done

# Quick mode: fewer configs for RQ-N2
RQN2_CONFIGS_FLAG="--full"
if [[ "$QUICK" == "true" ]]; then
    RQN2_CONFIGS_FLAG="--configs 0,7"
fi

# ── Preflight ────────────────────────────────────────────────────────────────
header "VESPER wmediumd Experiment Runner"

echo -e "  ${BOLD}Output:${NC}    $OUTPUT_DIR"
echo -e "  ${BOLD}Trials:${NC}    $TRIALS"
echo -e "  ${BOLD}Scenario:${NC}  $WMEDIUMD_SCENARIO"
echo -e "  ${BOLD}RQ-N1:${NC}     $RUN_RQN1"
echo -e "  ${BOLD}RQ-N2:${NC}     $RUN_RQN2"
echo -e "  ${BOLD}Baseline:${NC}  $([ "$SKIP_BASELINE" == "true" ] && echo "SKIP" || echo "yes")"
echo ""

if [[ "$(uname -s)" != "Linux" ]]; then
    err "FATAL: Must run on Linux (current: $(uname -s))"
    err "mac80211_hwsim is a Linux kernel module."
    err ""
    err "Quick cloud setup (cheapest):"
    err "  1. Create Ubuntu 22.04 VM (UTM, EC2 t3.xlarge, or Hetzner CX31)"
    err "  2. rsync -avz --exclude='.venv' --exclude='data/' ~/Desktop/vesper/ user@VM_IP:~/vesper/"
    err "  3. ssh user@VM_IP"
    err "  4. cd ~/vesper && sudo bash scripts/run_wmediumd_experiments.sh"
    exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
    err "FATAL: Must run as root (sudo)"
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

# ── Step 1: Install system dependencies ──────────────────────────────────────
header "Step 1/5: Installing dependencies"

# Core networking tools
PACKAGES=(
    hostapd wpa-supplicant iw wireless-tools
    tshark iperf3
    libnl-3-dev libnl-genl-3-dev libconfig-dev
    git gcc make pkg-config
    python3 python3-pip python3-venv
)

MISSING=()
for pkg in "${PACKAGES[@]}"; do
    if ! dpkg -s "$pkg" &>/dev/null; then
        MISSING+=("$pkg")
    fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    log "Installing ${#MISSING[@]} missing packages: ${MISSING[*]}"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING[@]}" 2>&1 | tail -3
else
    log "✓ All system packages already installed"
fi

# Kernel module
if ! modinfo mac80211_hwsim &>/dev/null; then
    log "Installing kernel modules..."
    apt-get install -y -qq "linux-modules-extra-$(uname -r)" 2>&1 | tail -3
fi
log "✓ mac80211_hwsim module available"

# ── Step 2: Build and install wmediumd ───────────────────────────────────────
header "Step 2/5: Building wmediumd"

if command -v wmediumd &>/dev/null; then
    log "✓ wmediumd already installed: $(which wmediumd)"
else
    WMEDIUMD_BUILD="/tmp/wmediumd-build"
    if [[ ! -d "$WMEDIUMD_BUILD/wmediumd" ]]; then
        log "Cloning wmediumd..."
        mkdir -p "$WMEDIUMD_BUILD"
        git clone --depth 1 https://github.com/bcopeland/wmediumd "$WMEDIUMD_BUILD/wmediumd" 2>&1 | tail -3
    fi

    log "Building wmediumd..."
    make -C "$WMEDIUMD_BUILD/wmediumd" -j"$(nproc)" 2>&1 | tail -5

    log "Installing wmediumd..."
    make -C "$WMEDIUMD_BUILD/wmediumd" install 2>&1 | tail -3

    if command -v wmediumd &>/dev/null; then
        log "✓ wmediumd installed successfully: $(which wmediumd)"
    else
        err "wmediumd build failed"
        exit 1
    fi
fi

# ── Step 3: Set up Python environment ────────────────────────────────────────
header "Step 3/5: Python environment"

cd "$VESPER_DIR"

if [[ ! -d ".venv" ]]; then
    log "Creating Python venv..."
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q -e . 2>/dev/null || pip install -q -r requirements.txt 2>/dev/null || true
log "✓ Python environment ready ($(python3 --version))"

# Verify the scripts exist
for script in scripts/run_rqn1_native.py scripts/run_rqn2_native.py scripts/wmediumd_helper.py; do
    if [[ ! -f "$script" ]]; then
        err "Missing: $script"
        exit 1
    fi
done
log "✓ All experiment scripts present"

# ── Step 4: Clean up any stale state ─────────────────────────────────────────
header "Step 4/5: Cleaning stale state"

killall hostapd wpa_supplicant wmediumd 2>/dev/null || true
for i in 0 1 2 3; do
    ip netns del "ns-sta$i" 2>/dev/null || true
    ip netns del "ns-bridge$i" 2>/dev/null || true
done
ip link del br-vesper 2>/dev/null || true
modprobe -r mac80211_hwsim 2>/dev/null || true
sleep 1
log "✓ Clean state"

# ── Step 5: Run experiments ──────────────────────────────────────────────────
header "Step 5/5: Running experiments"

TOTAL_START=$(date +%s)
RESULTS_SUMMARY="$OUTPUT_DIR/experiment_summary.json"

run_rqn1() {
    local label="$1"
    local extra_flags="$2"
    local out_dir="$OUTPUT_DIR/rqn1_${label}"

    log "━━━ RQ-N1 ($label): Bridge vs 802.11, $TRIALS trials ━━━"
    python3 scripts/run_rqn1_native.py \
        --full \
        --trials "$TRIALS" \
        --output "$out_dir" \
        $extra_flags \
        2>&1 | tee "$out_dir.log"

    if [[ -f "$out_dir/rqn1_full_results.json" ]]; then
        log "✓ RQ-N1 ($label) results: $out_dir/rqn1_full_results.json"
    else
        warn "RQ-N1 ($label) may have failed — no results JSON"
    fi
}

run_rqn2() {
    local label="$1"
    local extra_flags="$2"
    local out_dir="$OUTPUT_DIR/rqn2_${label}"

    log "━━━ RQ-N2 ($label): Hardening sweep, $TRIALS trials ━━━"
    python3 scripts/run_rqn2_native.py \
        $RQN2_CONFIGS_FLAG \
        --trials "$TRIALS" \
        --output "$out_dir" \
        $extra_flags \
        2>&1 | tee "$out_dir.log"

    if [[ -f "$out_dir/rqn2_summary.json" ]] || [[ -f "$out_dir/rqn2_full_summary.json" ]]; then
        log "✓ RQ-N2 ($label) results saved"
    else
        warn "RQ-N2 ($label) may have failed — no summary JSON"
    fi
}

# ── RQ-N1 ────────────────────────────────────────────────────────────────────
if [[ "$RUN_RQN1" == "true" ]]; then
    # Run WITH wmediumd
    RQN1_WM_START=$(date +%s)
    run_rqn1 "wmediumd" "--wmediumd --wmediumd-scenario $WMEDIUMD_SCENARIO"
    RQN1_WM_ELAPSED=$(( $(date +%s) - RQN1_WM_START ))
    log "RQ-N1 (wmediumd) completed in $((RQN1_WM_ELAPSED / 60))m $((RQN1_WM_ELAPSED % 60))s"

    # Cleanup between runs
    killall hostapd wpa_supplicant wmediumd 2>/dev/null || true
    modprobe -r mac80211_hwsim 2>/dev/null || true
    sleep 3

    # Run WITHOUT wmediumd (baseline comparison)
    if [[ "$SKIP_BASELINE" != "true" ]]; then
        RQN1_BL_START=$(date +%s)
        run_rqn1 "baseline" ""
        RQN1_BL_ELAPSED=$(( $(date +%s) - RQN1_BL_START ))
        log "RQ-N1 (baseline) completed in $((RQN1_BL_ELAPSED / 60))m $((RQN1_BL_ELAPSED % 60))s"

        killall hostapd wpa_supplicant 2>/dev/null || true
        modprobe -r mac80211_hwsim 2>/dev/null || true
        sleep 3
    fi
fi

# ── RQ-N2 ────────────────────────────────────────────────────────────────────
if [[ "$RUN_RQN2" == "true" ]]; then
    # Run WITH wmediumd
    RQN2_WM_START=$(date +%s)
    run_rqn2 "wmediumd" "--wmediumd --wmediumd-scenario $WMEDIUMD_SCENARIO"
    RQN2_WM_ELAPSED=$(( $(date +%s) - RQN2_WM_START ))
    log "RQ-N2 (wmediumd) completed in $((RQN2_WM_ELAPSED / 60))m $((RQN2_WM_ELAPSED % 60))s"

    killall hostapd wpa_supplicant wmediumd 2>/dev/null || true
    modprobe -r mac80211_hwsim 2>/dev/null || true
    sleep 3

    # Run WITHOUT wmediumd (baseline)
    if [[ "$SKIP_BASELINE" != "true" ]]; then
        RQN2_BL_START=$(date +%s)
        run_rqn2 "baseline" ""
        RQN2_BL_ELAPSED=$(( $(date +%s) - RQN2_BL_START ))
        log "RQ-N2 (baseline) completed in $((RQN2_BL_ELAPSED / 60))m $((RQN2_BL_ELAPSED % 60))s"
    fi
fi

# ── Final cleanup ────────────────────────────────────────────────────────────
killall hostapd wpa_supplicant wmediumd 2>/dev/null || true
for i in 0 1 2 3; do
    ip netns del "ns-sta$i" 2>/dev/null || true
    ip netns del "ns-bridge$i" 2>/dev/null || true
done
ip link del br-vesper 2>/dev/null || true
modprobe -r mac80211_hwsim 2>/dev/null || true

TOTAL_ELAPSED=$(( $(date +%s) - TOTAL_START ))

# ── Generate comparison summary ──────────────────────────────────────────────
header "Generating comparison summary"

python3 - "$OUTPUT_DIR" <<'PYEOF'
import json, sys, os, glob
from pathlib import Path

output_dir = Path(sys.argv[1])
summary = {"output_dir": str(output_dir), "experiments": {}}

for label in ["rqn1_wmediumd", "rqn1_baseline", "rqn2_wmediumd", "rqn2_baseline"]:
    d = output_dir / label
    if not d.exists():
        continue

    # Find any JSON results
    jsons = list(d.glob("*.json"))
    if jsons:
        with open(jsons[0]) as f:
            data = json.load(f)
        summary["experiments"][label] = {
            "results_file": str(jsons[0]),
            "status": "completed",
        }

        # Extract key metrics for RQ-N1
        if "wifi_trials" in data:
            wifi_trials = data["wifi_trials"]
            if wifi_trials:
                rtts = []
                for trial in wifi_trials:
                    icmp = trial.get("icmp_rtt", {})
                    for ip_data in icmp.values():
                        if isinstance(ip_data, dict) and "mean_ms" in ip_data:
                            rtts.append(ip_data["mean_ms"])
                if rtts:
                    import statistics
                    summary["experiments"][label]["mean_rtt_ms"] = round(statistics.mean(rtts), 3)
                    summary["experiments"][label]["stdev_rtt_ms"] = round(statistics.stdev(rtts), 3) if len(rtts) > 1 else 0

                # Retransmissions
                retx_vals = [t.get("retransmissions_10s", 0) for t in wifi_trials]
                if retx_vals:
                    summary["experiments"][label]["mean_retransmissions"] = round(statistics.mean(retx_vals), 1)

                # Attack rates
                fw_rates = [t.get("firmware_attacks", {}).get("rate", 0) for t in wifi_trials]
                wifi_rates = [t.get("wifi_attacks", {}).get("rate", 0) for t in wifi_trials]
                if fw_rates:
                    summary["experiments"][label]["mean_fw_attack_rate"] = round(statistics.mean(fw_rates), 1)
                if wifi_rates:
                    summary["experiments"][label]["mean_wifi_attack_rate"] = round(statistics.mean(wifi_rates), 1)

                summary["experiments"][label]["wmediumd_enabled"] = data.get("wmediumd_enabled", False)
    else:
        summary["experiments"][label] = {"status": "no_results"}

# Print comparison
print("\n" + "═" * 65)
print("  WMEDIUMD EXPERIMENT COMPARISON")
print("═" * 65)

for pair in [("rqn1_baseline", "rqn1_wmediumd"), ("rqn2_baseline", "rqn2_wmediumd")]:
    base_label, wm_label = pair
    base = summary["experiments"].get(base_label, {})
    wm = summary["experiments"].get(wm_label, {})

    if not base or not wm:
        continue

    exp_name = "RQ-N1" if "rqn1" in base_label else "RQ-N2"
    print(f"\n  {exp_name}:")

    for metric in ["mean_rtt_ms", "stdev_rtt_ms", "mean_retransmissions",
                   "mean_fw_attack_rate", "mean_wifi_attack_rate"]:
        b = base.get(metric, "---")
        w = wm.get(metric, "---")
        if b != "---" and w != "---":
            name = metric.replace("mean_", "").replace("_", " ").title()
            delta = ""
            try:
                pct = ((float(w) - float(b)) / max(float(b), 0.001)) * 100
                delta = f" ({pct:+.1f}%)"
            except (ValueError, ZeroDivisionError):
                pass
            print(f"    {name:<25} Baseline: {b:<10}  wmediumd: {w:<10}{delta}")

print("\n" + "═" * 65)

# Save JSON
with open(output_dir / "experiment_summary.json", "w") as f:
    json.dump(summary, f, indent=2)
print(f"\n  Summary saved to: {output_dir}/experiment_summary.json")
PYEOF

# ── Final banner ─────────────────────────────────────────────────────────────
header "ALL EXPERIMENTS COMPLETE"

echo -e "  ${BOLD}Total time:${NC}  $((TOTAL_ELAPSED / 3600))h $((TOTAL_ELAPSED % 3600 / 60))m $((TOTAL_ELAPSED % 60))s"
echo -e "  ${BOLD}Results:${NC}     $OUTPUT_DIR/"
echo ""
echo -e "  ${BOLD}Contents:${NC}"
ls -1d "$OUTPUT_DIR"/*/ 2>/dev/null | while read d; do
    name=$(basename "$d")
    n_json=$(find "$d" -name "*.json" | wc -l)
    echo -e "    📁 $name/ ($n_json JSON files)"
done
echo ""
echo -e "  ${BOLD}Copy results back to macOS:${NC}"
echo -e "    rsync -avz user@THIS_VM:$OUTPUT_DIR/ ~/Desktop/vesper/results/wmediumd_${TIMESTAMP}/"
echo ""
