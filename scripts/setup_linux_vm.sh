#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# VESPER — Linux VM / Cloud Instance Setup Script
# ═══════════════════════════════════════════════════════════════════════════════
#
# One-shot setup for running VESPER's 802.11 WiFi experiments on Linux.
# Tested on: Ubuntu 22.04 LTS (x86_64 and aarch64)
#
# What this script installs:
#   1. mac80211_hwsim kernel module (802.11 radio simulation)
#   2. Docker Engine + Docker Compose v2
#   3. Mininet-WiFi (network emulation with hostapd + wpa_supplicant)
#   4. tshark / Wireshark CLI (packet capture)
#   5. Python 3.10+ with VESPER dependencies
#   6. Builds Docker images (vesper-router + vesper-esp32)
#
# Usage:
#   # On a fresh Ubuntu 22.04 VM:
#   chmod +x scripts/setup_linux_vm.sh
#   sudo scripts/setup_linux_vm.sh          # System packages
#   scripts/setup_linux_vm.sh --user        # User-level Python + Docker images
#
# Time estimate: ~15-20 minutes (depending on network speed)
#
# VM Options:
#   A) UTM on macOS:   Ubuntu 22.04 ARM64 Server, 4 vCPU, 8GB RAM, 40GB disk
#   B) AWS EC2:        t3.xlarge (4 vCPU, 16GB), Ubuntu 22.04 AMI, 40GB gp3
#   C) GCP:            e2-standard-4, Ubuntu 22.04, 40GB balanced PD
#   D) Azure:          Standard_D4s_v3, Ubuntu 22.04, 40GB
#   E) Hetzner:        CX31 (4 vCPU, 8GB), Ubuntu 22.04 — cheapest (~€7/mo)
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

VESPER_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[VESPER]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── Check OS ─────────────────────────────────────────────────────────────────
check_os() {
    if [[ "$(uname -s)" != "Linux" ]]; then
        err "This script must run on Linux. Current OS: $(uname -s)"
        err "See LINUX_EXPERIMENTS.md for VM setup instructions."
        exit 1
    fi

    if ! grep -qi "ubuntu\|debian" /etc/os-release 2>/dev/null; then
        warn "This script is tested on Ubuntu 22.04. Your distro may need adjustments."
    fi

    log "OS: $(cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"')"
    log "Kernel: $(uname -r)"
    log "Arch: $(uname -m)"
}

# ── Phase 1: System packages (requires sudo) ────────────────────────────────
install_system_packages() {
    log "═══ Phase 1: System packages ═══"

    log "Updating package index..."
    apt-get update -qq

    log "Installing core dependencies..."
    apt-get install -y --no-install-recommends \
        build-essential git wget curl sudo ca-certificates gnupg lsb-release \
        python3 python3-pip python3-venv python3-dev \
        iproute2 iptables iputils-ping net-tools bridge-utils ethtool kmod \
        hostapd wpasupplicant wireless-tools iw rfkill \
        dnsmasq \
        mosquitto mosquitto-clients \
        tshark tcpdump \
        cgroup-tools openvswitch-switch \
        iperf3 \
        jq bc \
        linux-modules-extra-$(uname -r) || warn "linux-modules-extra not found (may already be included)"

    log "System packages installed ✓"
}

# ── Phase 2: mac80211_hwsim ─────────────────────────────────────────────────
setup_hwsim() {
    log "═══ Phase 2: mac80211_hwsim ═══"

    # Check if module is available
    if modprobe --dry-run mac80211_hwsim 2>/dev/null; then
        log "mac80211_hwsim module available ✓"
    else
        err "mac80211_hwsim module NOT available!"
        err "This typically means:"
        err "  1. You're on a minimal/cloud kernel — install linux-modules-extra-\$(uname -r)"
        err "  2. You're running inside a container (won't work — need a full VM)"
        err "  3. Your kernel was built without CONFIG_MAC80211_HWSIM"
        exit 1
    fi

    # Load the module with enough radios for experiments
    modprobe mac80211_hwsim radios=12
    log "Loaded mac80211_hwsim with 12 radios"

    # Verify
    if iw dev | grep -q "phy#"; then
        log "Virtual WiFi radios active: $(iw dev | grep -c Interface) interfaces"
    else
        warn "No virtual radios detected — module loaded but no interfaces"
    fi

    # Make it persistent across reboots
    echo "mac80211_hwsim" > /etc/modules-load.d/vesper-hwsim.conf
    echo "options mac80211_hwsim radios=12" > /etc/modprobe.d/vesper-hwsim.conf
    log "mac80211_hwsim configured for boot persistence ✓"
}

# ── Phase 3: Docker ─────────────────────────────────────────────────────────
install_docker() {
    log "═══ Phase 3: Docker Engine ═══"

    if command -v docker &>/dev/null; then
        log "Docker already installed: $(docker --version)"
    else
        log "Installing Docker..."
        curl -fsSL https://get.docker.com | sh
        log "Docker installed: $(docker --version)"
    fi

    # Add current user to docker group
    if [[ -n "${SUDO_USER:-}" ]]; then
        usermod -aG docker "$SUDO_USER"
        log "Added $SUDO_USER to docker group (re-login to take effect)"
    fi

    # Start Docker
    systemctl enable docker
    systemctl start docker

    # Verify
    docker info > /dev/null 2>&1 && log "Docker Engine running ✓" || warn "Docker may need a re-login"
}

# ── Phase 4: Mininet-WiFi ──────────────────────────────────────────────────
install_mininet_wifi() {
    log "═══ Phase 4: Mininet-WiFi ═══"

    if python3 -c "import mn_wifi" 2>/dev/null; then
        log "Mininet-WiFi already installed ✓"
        return
    fi

    log "Cloning Mininet-WiFi..."
    MNWIFI_DIR="/opt/mininet-wifi"
    if [[ -d "$MNWIFI_DIR" ]]; then
        cd "$MNWIFI_DIR" && git pull
    else
        git clone --depth 1 https://github.com/intrig-unicamp/mininet-wifi.git "$MNWIFI_DIR"
    fi

    cd "$MNWIFI_DIR"
    log "Installing Mininet-WiFi (this takes ~5 minutes)..."
    # Install with wmediumd for realistic propagation
    python3 -m pip install . 2>/dev/null || pip3 install .
    # Build and install wmediumd
    util/install.sh -W  # -W = install wmediumd only

    # Verify
    if python3 -c "from mn_wifi.net import Mininet_wifi; print('OK')" 2>/dev/null; then
        log "Mininet-WiFi installed ✓"
    else
        warn "Mininet-WiFi import failed — may need PATH adjustment"
    fi
}

# ── Phase 5: User-level setup (no sudo needed) ─────────────────────────────
user_setup() {
    log "═══ Phase 5: User-level setup ═══"
    cd "$VESPER_DIR"

    # Python venv
    if [[ ! -d ".venv" ]]; then
        log "Creating Python venv..."
        python3 -m venv .venv
    fi
    source .venv/bin/activate

    log "Installing Python dependencies..."
    pip install --upgrade pip
    pip install -e ".[dev]" 2>/dev/null || pip install -r requirements.txt

    # Additional experiment dependencies
    pip install matplotlib numpy scipy pyyaml

    log "Python venv ready: $(python3 --version)"
    log "Packages: $(pip list 2>/dev/null | wc -l) installed"
}

# ── Phase 6: Build Docker images ────────────────────────────────────────────
build_docker_images() {
    log "═══ Phase 6: Building Docker images ═══"
    cd "$VESPER_DIR"

    log "Building vesper-router image (Mininet-WiFi + mosquitto + tshark)..."
    docker build -f docker/Dockerfile.router -t vesper-router:latest . 2>&1 | tail -5

    log "Building vesper-esp32 image (QEMU ESP32 firmware)..."
    docker build -f docker/Dockerfile.esp32 -t vesper-esp32:latest . 2>&1 | tail -5

    log "Docker images built ✓"
    docker images | grep vesper
}

# ── Phase 7: Smoke test ────────────────────────────────────────────────────
smoke_test() {
    log "═══ Phase 7: Smoke test ═══"

    # Test 1: mac80211_hwsim
    log "Test 1: mac80211_hwsim..."
    if lsmod | grep -q mac80211_hwsim; then
        log "  ✓ mac80211_hwsim loaded"
    else
        modprobe mac80211_hwsim radios=4
        log "  ✓ mac80211_hwsim loaded (just now)"
    fi

    # Test 2: Docker
    log "Test 2: Docker..."
    if docker run --rm hello-world >/dev/null 2>&1; then
        log "  ✓ Docker working"
    else
        warn "  ✗ Docker test failed (may need re-login for group permissions)"
    fi

    # Test 3: tshark
    log "Test 3: tshark..."
    if command -v tshark &>/dev/null; then
        log "  ✓ tshark $(tshark --version 2>&1 | head -1)"
    else
        warn "  ✗ tshark not found"
    fi

    # Test 4: Python imports
    log "Test 4: Python imports..."
    source "$VESPER_DIR/.venv/bin/activate" 2>/dev/null || true
    python3 -c "
import vesper
from vesper.network.wifi_emulator import WiFiEmulator, WiFiConfig
from vesper.attacks.firmware_attacks import FirmwareAttackFramework
from vesper.attacks.wifi_attacks import WiFiAttackFramework
print('  ✓ All VESPER imports OK')
" 2>/dev/null || warn "  ✗ Some imports failed (run 'pip install -e .' in venv)"

    # Test 5: Quick WiFi topology (10 seconds)
    log "Test 5: Quick Mininet-WiFi topology (10s)..."
    timeout 15 python3 -c "
import sys, os
sys.path.insert(0, '$VESPER_DIR')
try:
    from mn_wifi.net import Mininet_wifi
    from mn_wifi.node import OVSKernelAP
    net = Mininet_wifi()
    ap1 = net.addAccessPoint('ap1', ssid='test', mode='g', channel='6', position='50,50,0')
    sta1 = net.addStation('sta1', position='45,50,0')
    net.configureWifiNodes()
    net.build()
    net.start()
    import time; time.sleep(3)
    result = sta1.cmd('ping -c 2 -W 1 10.0.0.1')
    net.stop()
    if '2 received' in result or '1 received' in result:
        print('  ✓ Mininet-WiFi topology works')
    else:
        print('  ~ Mininet-WiFi started but ping failed (may be normal for quick test)')
except Exception as e:
    print(f'  ✗ Mininet-WiFi test failed: {e}')
" 2>/dev/null || warn "  ✗ Mininet-WiFi test failed"

    log ""
    log "═══════════════════════════════════════════════════════════"
    log "  VESPER Linux environment ready!"
    log "  Next: run the flagship experiments:"
    log ""
    log "    cd $VESPER_DIR"
    log "    source .venv/bin/activate"
    log "    bash scripts/run_all_flagship_experiments.sh"
    log "═══════════════════════════════════════════════════════════"
}

# ── Main ────────────────────────────────────────────────────────────────────
main() {
    check_os

    if [[ "${1:-}" == "--user" ]]; then
        # User-level only (no sudo needed)
        user_setup
        build_docker_images
        smoke_test
    elif [[ "${1:-}" == "--smoke" ]]; then
        smoke_test
    elif [[ $EUID -ne 0 ]]; then
        err "System setup requires sudo. Run: sudo $0"
        err "Or for user-level only: $0 --user"
        exit 1
    else
        # Full setup (requires sudo)
        install_system_packages
        setup_hwsim
        install_docker
        install_mininet_wifi

        # Drop to user for Python/Docker setup
        if [[ -n "${SUDO_USER:-}" ]]; then
            log "Switching to user $SUDO_USER for Python/Docker setup..."
            su - "$SUDO_USER" -c "cd $VESPER_DIR && bash scripts/setup_linux_vm.sh --user"
        else
            user_setup
            build_docker_images
            smoke_test
        fi
    fi
}

main "$@"
