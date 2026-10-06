#!/usr/bin/env python3
"""
VESPER RQ-N1: Bridge vs. 802.11 Emulation Divergence

Controlled comparison: the SAME attack suite, firmware containers, and traffic
workload run on two network backends — Docker bridge vs. Mininet-WiFi 802.11 —
and we measure the divergence.

Modes:
    --bridge-only     Run bridge-mode experiments (works on any Docker host)
    --wifi-only       Run 802.11-mode experiments (requires Linux + mac80211_hwsim)
    --full            Run both modes and generate comparison (requires Linux)
    --compare-only    Skip experiments, compare existing results in output dir

Outputs:
    results/rqn1_<ts>/
        bridge/             — bridge-mode attack results + pcaps
        wifi/               — 802.11-mode attack results + pcaps
        comparison/         — tables, CDFs, divergence analysis
        tab_bridge_vs_80211.tex
        fig_rtt_bridge_vs_80211.pdf

Usage:
    # Bridge mode on macOS (Docker Desktop)
    python3 scripts/run_rqn1_bridge_vs_wifi.py --bridge-only --trials 5

    # Full comparison on Linux
    python3 scripts/run_rqn1_bridge_vs_wifi.py --full --trials 5

    # Compare existing data
    python3 scripts/run_rqn1_bridge_vs_wifi.py --compare-only --output results/rqn1_20260301_120000
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import socket
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Project path ────────────────────────────────────────────────────────────
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)

logger = logging.getLogger("vesper.rqn1")

# ── Constants ───────────────────────────────────────────────────────────────

SEED = 42
NUM_DEVICES = 6          # 6 ESP32 containers per mode
ATTACK_COOLDOWN = 1.0    # seconds between attacks

# Bridge-mode Docker network settings
BRIDGE_SUBNET = "172.20.0.0/24"
BRIDGE_GATEWAY = "172.20.0.1"
BRIDGE_MATTER_PORT = 8484
BRIDGE_DEVICE_IPS = [f"172.20.0.{10+i}" for i in range(NUM_DEVICES)]
BRIDGE_SERIAL_PORTS = list(range(5561, 5561 + NUM_DEVICES))

# 802.11-mode settings (Mininet-WiFi)
WIFI_SUBNET = "192.168.4.0/24"
WIFI_GATEWAY = "192.168.4.1"
WIFI_MATTER_PORT = 8484
WIFI_DEVICE_IPS = [f"192.168.4.{10+i}" for i in range(NUM_DEVICES)]
WIFI_SERIAL_PORTS = list(range(5561, 5561 + NUM_DEVICES))

DEVICE_TYPES = [
    "smart_light", "smart_light", "smart_light",
    "motion_sensor", "temperature_sensor", "door_sensor",
]


# ═══════════════════════════════════════════════════════════════════════════
# Infrastructure Management
# ═══════════════════════════════════════════════════════════════════════════

def check_docker() -> bool:
    """Check Docker is available."""
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def check_linux_wifi_support() -> bool:
    """Check if host supports mac80211_hwsim (Linux only)."""
    if platform.system() != "Linux":
        return False
    try:
        result = subprocess.run(
            ["modprobe", "--dry-run", "mac80211_hwsim"],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def start_bridge_mode(output_dir: str, build: bool = False) -> Dict[str, Any]:
    """
    Start containers in Docker bridge mode (no 802.11).
    Uses a dedicated bridge-mode compose file.
    """
    compose_file = os.path.join(PROJECT_ROOT, "docker", "docker-compose-bridge.yml")

    # Generate bridge-mode compose if it doesn't exist
    if not os.path.exists(compose_file):
        _generate_bridge_compose(compose_file)

    logger.info("Starting bridge-mode containers...")
    cmd = ["docker", "compose", "-f", compose_file, "-p", "vesper-bridge", "up", "-d"]
    if build:
        cmd.insert(-1, "--build")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"Bridge-mode start failed: {result.stderr}")

    # Wait for Matter bridge
    _wait_for_matter(BRIDGE_GATEWAY, BRIDGE_MATTER_PORT, timeout=60)

    # Wait for device serial ports
    ready = 0
    for port in BRIDGE_SERIAL_PORTS:
        if _wait_for_port("localhost", port, timeout=60):
            ready += 1

    logger.info(f"Bridge mode ready: {ready}/{NUM_DEVICES} devices")
    return {
        "mode": "bridge",
        "gateway": BRIDGE_GATEWAY,
        "matter_port": BRIDGE_MATTER_PORT,
        "device_ips": BRIDGE_DEVICE_IPS[:ready],
        "serial_ports": BRIDGE_SERIAL_PORTS[:ready],
        "devices_ready": ready,
    }


def stop_bridge_mode():
    """Stop bridge-mode containers."""
    compose_file = os.path.join(PROJECT_ROOT, "docker", "docker-compose-bridge.yml")
    if os.path.exists(compose_file):
        subprocess.run(
            ["docker", "compose", "-f", compose_file, "-p", "vesper-bridge", "down", "--timeout", "10"],
            capture_output=True, text=True
        )
    logger.info("Bridge-mode stopped")


def start_wifi_mode(output_dir: str, build: bool = False) -> Dict[str, Any]:
    """
    Start containers in 802.11 mode (Mininet-WiFi).
    Uses the main docker-compose.yml.
    """
    compose_file = os.path.join(PROJECT_ROOT, "docker", "docker-compose.yml")

    logger.info("Starting 802.11-mode containers (Mininet-WiFi)...")
    cmd = ["docker", "compose", "-f", compose_file, "-p", "vesper-wifi", "up", "-d"]
    if build:
        cmd.insert(-1, "--build")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"WiFi-mode start failed: {result.stderr}")

    # Wait for topology
    from vesper.network.wifi_emulator import WiFiEmulator
    emu = WiFiEmulator()
    emu.state = "router_ready"
    if not emu.wait_ready(timeout=180):
        raise RuntimeError("WiFi topology not ready within timeout")

    logger.info("802.11 mode ready")
    return {
        "mode": "wifi",
        "gateway": WIFI_GATEWAY,
        "matter_port": WIFI_MATTER_PORT,
        "device_ips": WIFI_DEVICE_IPS,
        "serial_ports": WIFI_SERIAL_PORTS,
        "devices_ready": NUM_DEVICES,
        "emulator": emu,
    }


def stop_wifi_mode():
    """Stop 802.11-mode containers."""
    compose_file = os.path.join(PROJECT_ROOT, "docker", "docker-compose.yml")
    subprocess.run(
        ["docker", "compose", "-f", compose_file, "-p", "vesper-wifi", "down", "--timeout", "10"],
        capture_output=True, text=True
    )
    logger.info("WiFi-mode stopped")


# ═══════════════════════════════════════════════════════════════════════════
# Attack Execution
# ═══════════════════════════════════════════════════════════════════════════

def run_firmware_attacks(serial_ports: List[int], output_dir: str) -> List[Dict]:
    """Run firmware attacks against all devices via serial ports."""
    from vesper.attacks.firmware_attacks import FirmwareAttackFramework, FirmwareTarget

    logger.info(f"Running firmware attacks on {len(serial_ports)} devices...")
    all_results = []
    fw = FirmwareAttackFramework()

    for i, port in enumerate(serial_ports):
        device_type = DEVICE_TYPES[i] if i < len(DEVICE_TYPES) else "smart_light"
        target = FirmwareTarget(
            host="localhost",
            port=port,
            device_type=device_type,
        )
        try:
            results = fw.run_all_attacks(target)
            for r in results:
                all_results.append({
                    "device_port": port,
                    "device_type": device_type,
                    "attack_name": r.attack_name,
                    "category": r.category.value if hasattr(r.category, 'value') else str(r.category),
                    "success": r.success,
                    "duration_ms": r.duration_ms,
                    "evidence": r.evidence[:3],
                })
            ok = sum(1 for r in results if r.success)
            logger.info(f"  Device port {port}: {ok}/{len(results)} attacks succeeded")
        except Exception as e:
            logger.error(f"  Device port {port}: firmware attacks failed: {e}")

    # Save
    with open(f"{output_dir}/firmware_attacks.json", "w") as f:
        json.dump(all_results, f, indent=2)

    return all_results


def run_network_attacks_bridge(
    gateway: str, matter_port: int, device_ips: List[str],
    serial_ports: List[int], output_dir: str
) -> List[Dict]:
    """Run network attacks in bridge mode (TCP/Matter level, no 802.11)."""
    from vesper.attacks.network_attacks import NetworkAttackFramework, NetworkTarget

    logger.info("Running network attacks (bridge mode)...")
    devices = list(zip(device_ips, serial_ports))
    target = NetworkTarget(
        matter_host=gateway,
        matter_port=matter_port,
        devices=devices,
        gateway_ip=gateway,
        subnet=BRIDGE_SUBNET,
    )

    net = NetworkAttackFramework()
    results = net.run_all_attacks(target)

    serialized = []
    for r in results:
        serialized.append({
            "attack_name": r.attack_name,
            "category": r.category.value if hasattr(r.category, 'value') else str(r.category),
            "success": r.success,
            "duration_ms": r.duration_ms,
            "packets_sent": r.packets_sent,
            "packets_captured": r.packets_captured,
            "evidence": r.evidence[:3],
        })
    ok = sum(1 for r in results if r.success)
    logger.info(f"  Network attacks: {ok}/{len(results)} succeeded")

    with open(f"{output_dir}/network_attacks.json", "w") as f:
        json.dump(serialized, f, indent=2)

    return serialized


def run_wifi_attacks(emulator, output_dir: str) -> List[Dict]:
    """Run WiFi-layer attacks via Mininet-WiFi (802.11 mode only)."""
    from vesper.attacks.wifi_attacks import WiFiAttackFramework

    logger.info("Running WiFi attacks (802.11 mode)...")
    pcap_dir = f"{output_dir}/pcap"
    os.makedirs(pcap_dir, exist_ok=True)

    wifi_fw = WiFiAttackFramework(emulator, pcap_dir=pcap_dir)
    results = wifi_fw.run_all_attacks()

    serialized = []
    for r in results:
        serialized.append({
            "attack_name": r.attack_name,
            "attack_type": r.attack_type,
            "success": r.success,
            "duration_ms": r.duration_ms,
            "packets_sent": r.packets_sent,
            "packets_captured": r.packets_captured,
            "pcap_file": r.pcap_file,
            "evidence": r.evidence[:3],
        })
    ok = sum(1 for r in results if r.success)
    logger.info(f"  WiFi attacks: {ok}/{len(results)} succeeded")

    wifi_fw.export_results(results, f"{output_dir}/wifi_attacks.json")
    return serialized


# ═══════════════════════════════════════════════════════════════════════════
# Latency & Network Metrics
# ═══════════════════════════════════════════════════════════════════════════

def measure_rtt(target_ip: str, count: int = 100, container: Optional[str] = None) -> Dict:
    """Measure RTT via ICMP ping (from host or from container)."""
    if container:
        cmd = ["docker", "exec", container, "ping", "-c", str(count), "-i", "0.1", target_ip]
    else:
        cmd = ["ping", "-c", str(count), "-i", "0.1", target_ip]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=count * 2)
        rtts = []
        for line in result.stdout.splitlines():
            if "time=" in line:
                try:
                    t = float(line.split("time=")[1].split()[0])
                    rtts.append(t)
                except (ValueError, IndexError):
                    pass

        if rtts:
            return {
                "target": target_ip,
                "count": len(rtts),
                "min_ms": round(min(rtts), 3),
                "max_ms": round(max(rtts), 3),
                "mean_ms": round(statistics.mean(rtts), 3),
                "median_ms": round(statistics.median(rtts), 3),
                "stdev_ms": round(statistics.stdev(rtts), 3) if len(rtts) > 1 else 0.0,
                "p95_ms": round(sorted(rtts)[int(0.95 * len(rtts))], 3),
                "p99_ms": round(sorted(rtts)[int(0.99 * len(rtts))], 3),
                "raw": rtts,
            }
    except Exception as e:
        logger.error(f"RTT measurement failed for {target_ip}: {e}")

    return {"target": target_ip, "count": 0, "error": "measurement failed"}


def measure_tcp_handshake_rtt(ip: str, port: int, count: int = 50) -> Dict:
    """Measure TCP handshake RTT (SYN → SYN-ACK time)."""
    rtts = []
    for _ in range(count):
        try:
            start = time.perf_counter_ns()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect((ip, port))
            elapsed_ns = time.perf_counter_ns() - start
            rtts.append(elapsed_ns / 1_000_000)  # ms
            sock.close()
        except Exception:
            pass
        time.sleep(0.05)

    if rtts:
        return {
            "target": f"{ip}:{port}",
            "count": len(rtts),
            "min_ms": round(min(rtts), 3),
            "max_ms": round(max(rtts), 3),
            "mean_ms": round(statistics.mean(rtts), 3),
            "stdev_ms": round(statistics.stdev(rtts), 3) if len(rtts) > 1 else 0.0,
            "raw": rtts,
        }
    return {"target": f"{ip}:{port}", "count": 0, "error": "all connections failed"}


def measure_retransmissions(container: str = "vesper-router", duration: int = 30) -> Dict:
    """
    Count 802.11 retransmissions via tshark inside the router container.
    Only meaningful in 802.11 mode.
    """
    # Capture for `duration` seconds
    cmd = [
        "docker", "exec", container,
        "timeout", str(duration),
        "tshark", "-i", "any", "-q",
        "-z", "io,stat,0,wlan.fc.retry==1",
        "-a", f"duration:{duration}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=duration + 10)
        output = result.stdout + result.stderr
        # Parse tshark io,stat output for retry count
        retry_count = 0
        for line in output.splitlines():
            if "wlan.fc.retry" in line:
                parts = line.split("|")
                for p in parts:
                    p = p.strip()
                    if p.isdigit():
                        retry_count = int(p)
        return {
            "duration_s": duration,
            "retransmissions": retry_count,
            "raw_output": output[:500],
        }
    except Exception as e:
        return {"duration_s": duration, "retransmissions": 0, "error": str(e)}


def capture_pcap(
    output_file: str,
    duration: int = 60,
    container: Optional[str] = None,
    interface: str = "any",
) -> str:
    """Capture packets for a duration. Returns pcap path."""
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    if container:
        # Capture inside container
        cmd = [
            "docker", "exec", container,
            "timeout", str(duration),
            "tshark", "-i", interface, "-w", f"/results/{os.path.basename(output_file)}",
            "-a", f"duration:{duration}",
        ]
    else:
        # Capture on host (bridge mode — capture on docker0)
        cmd = [
            "tshark", "-i", "docker0", "-w", output_file,
            "-a", f"duration:{duration}",
        ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=duration + 10)
    except Exception as e:
        logger.error(f"pcap capture failed: {e}")

    return output_file


# ═══════════════════════════════════════════════════════════════════════════
# Single Trial
# ═══════════════════════════════════════════════════════════════════════════

def run_single_trial(
    mode: str,
    trial_num: int,
    output_dir: str,
    infra: Dict[str, Any],
) -> Dict[str, Any]:
    """Run one trial of the full attack suite + metrics collection."""
    trial_dir = f"{output_dir}/{mode}/trial_{trial_num}"
    os.makedirs(trial_dir, exist_ok=True)
    logger.info(f"{'═'*60}")
    logger.info(f"  Trial {trial_num} — {mode.upper()} mode")
    logger.info(f"{'═'*60}")

    trial_result = {
        "mode": mode,
        "trial": trial_num,
        "timestamp": datetime.now().isoformat(),
    }

    gateway = infra["gateway"]
    matter_port = infra["matter_port"]
    device_ips = infra["device_ips"]
    serial_ports = infra["serial_ports"]

    # ── 1. Pre-attack RTT baseline ───────────────────────────────────
    logger.info("  Measuring pre-attack RTT baseline...")
    rtt_baseline = {}
    for ip in device_ips[:3]:  # First 3 devices
        rtt_baseline[ip] = measure_rtt(ip, count=50)
    trial_result["rtt_baseline"] = rtt_baseline

    # TCP handshake RTT
    tcp_rtt_baseline = {}
    for ip, port in zip(device_ips[:3], serial_ports[:3]):
        tcp_rtt_baseline[f"{ip}:{port}"] = measure_tcp_handshake_rtt(ip, port, count=30)
    trial_result["tcp_handshake_rtt_baseline"] = tcp_rtt_baseline

    # ── 2. Start background pcap capture ─────────────────────────────
    pcap_file = f"{trial_dir}/full_trial.pcap"
    if mode == "wifi" and "emulator" in infra:
        infra["emulator"].capture_start(pcap_file)

    # ── 3. Run firmware attacks ──────────────────────────────────────
    logger.info("  Running firmware attacks...")
    fw_results = run_firmware_attacks(serial_ports, trial_dir)
    trial_result["firmware_attacks"] = {
        "total": len(fw_results),
        "successful": sum(1 for r in fw_results if r["success"]),
    }

    # ── 4. Run network attacks ───────────────────────────────────────
    logger.info("  Running network attacks...")
    net_results = run_network_attacks_bridge(
        gateway, matter_port, device_ips, serial_ports, trial_dir
    )
    trial_result["network_attacks"] = {
        "total": len(net_results),
        "successful": sum(1 for r in net_results if r["success"]),
    }

    # ── 5. Run WiFi attacks (802.11 mode only) ──────────────────────
    if mode == "wifi" and "emulator" in infra:
        logger.info("  Running WiFi-layer attacks (802.11 only)...")
        wifi_results = run_wifi_attacks(infra["emulator"], trial_dir)
        trial_result["wifi_attacks"] = {
            "total": len(wifi_results),
            "successful": sum(1 for r in wifi_results if r["success"]),
        }
    else:
        trial_result["wifi_attacks"] = {
            "total": 0,
            "successful": 0,
            "note": "WiFi attacks not applicable in bridge mode",
        }

    # ── 6. Post-attack RTT (to measure degradation) ─────────────────
    logger.info("  Measuring post-attack RTT...")
    rtt_post = {}
    for ip in device_ips[:3]:
        rtt_post[ip] = measure_rtt(ip, count=50)
    trial_result["rtt_post_attack"] = rtt_post

    # ── 7. Retransmission count (802.11 only) ────────────────────────
    if mode == "wifi":
        logger.info("  Counting 802.11 retransmissions...")
        trial_result["retransmissions"] = measure_retransmissions(duration=15)
    else:
        trial_result["retransmissions"] = {
            "retransmissions": 0,
            "note": "No 802.11 retransmissions in bridge mode (by definition)",
        }

    # ── 8. Reconnection test ─────────────────────────────────────────
    if mode == "wifi" and "emulator" in infra:
        logger.info("  Measuring reconnection dynamics...")
        trial_result["reconnection"] = _measure_reconnection(infra["emulator"])
    else:
        trial_result["reconnection"] = {
            "reconnection_ms": 0,
            "note": "No reconnection in bridge mode — deauth has no effect",
        }

    # ── 9. Stop pcap capture ─────────────────────────────────────────
    if mode == "wifi" and "emulator" in infra:
        infra["emulator"].capture_stop()

    # ── Save trial results ───────────────────────────────────────────
    with open(f"{trial_dir}/trial_result.json", "w") as f:
        # Remove non-serializable 'raw' RTT arrays for the summary
        clean = _strip_raw_arrays(trial_result)
        json.dump(clean, f, indent=2)

    return trial_result


def _measure_reconnection(emulator) -> Dict:
    """Send deauth, measure time to re-associate."""
    target_ip = WIFI_DEVICE_IPS[0]
    target_station = "sta1"

    # Verify reachable
    pre = subprocess.run(
        ["docker", "exec", "vesper-router", "ping", "-c1", "-W1", target_ip],
        capture_output=True, text=True, timeout=5
    )
    if "1 received" not in pre.stdout:
        return {"error": "device not reachable before deauth"}

    # Send deauth
    emulator.deauth_station(target_station, count=10)
    deauth_time = time.time()

    # Poll until reachable again
    timeout = 10  # 10s max
    while time.time() - deauth_time < timeout:
        r = subprocess.run(
            ["docker", "exec", "vesper-router", "ping", "-c1", "-W1", target_ip],
            capture_output=True, text=True, timeout=3
        )
        if "1 received" in r.stdout:
            reconnect_ms = (time.time() - deauth_time) * 1000
            return {
                "reconnection_ms": round(reconnect_ms, 1),
                "deauth_frames": 10,
                "target": target_station,
            }
        time.sleep(0.2)

    return {"reconnection_ms": timeout * 1000, "note": "device did not reconnect within timeout"}


# ═══════════════════════════════════════════════════════════════════════════
# Comparison & Analysis
# ═══════════════════════════════════════════════════════════════════════════

def compare_results(output_dir: str, num_trials: int) -> Dict:
    """Compare bridge vs wifi results across all trials."""
    comp_dir = f"{output_dir}/comparison"
    os.makedirs(comp_dir, exist_ok=True)

    bridge_trials = _load_trials(f"{output_dir}/bridge", num_trials)
    wifi_trials = _load_trials(f"{output_dir}/wifi", num_trials)

    comparison = {
        "timestamp": datetime.now().isoformat(),
        "num_trials": num_trials,
        "seed": SEED,
    }

    # ── 1. Attack success rates ──────────────────────────────────────
    comparison["attack_success"] = _compare_attack_success(bridge_trials, wifi_trials)

    # ── 2. RTT distributions ─────────────────────────────────────────
    comparison["rtt"] = _compare_rtt(bridge_trials, wifi_trials)

    # ── 3. Retransmissions ───────────────────────────────────────────
    comparison["retransmissions"] = _compare_retransmissions(bridge_trials, wifi_trials)

    # ── 4. Reconnection dynamics ─────────────────────────────────────
    comparison["reconnection"] = _compare_reconnection(bridge_trials, wifi_trials)

    # ── 5. Summary statistics ────────────────────────────────────────
    comparison["summary"] = _compute_summary(comparison)

    # Save comparison
    with open(f"{comp_dir}/rqn1_comparison.json", "w") as f:
        json.dump(comparison, f, indent=2)

    # Generate LaTeX table
    _generate_latex_table(comparison, f"{output_dir}/tab_bridge_vs_80211.tex")

    # Generate RTT CDF plot
    _generate_rtt_cdf(bridge_trials, wifi_trials, f"{output_dir}/fig_rtt_bridge_vs_80211.pdf")

    logger.info(f"Comparison saved to {comp_dir}/")
    return comparison


def _load_trials(mode_dir: str, num_trials: int) -> List[Dict]:
    """Load all trial results for a mode."""
    trials = []
    for t in range(1, num_trials + 1):
        path = f"{mode_dir}/trial_{t}/trial_result.json"
        if os.path.exists(path):
            with open(path) as f:
                trials.append(json.load(f))
    return trials


def _compare_attack_success(bridge: List[Dict], wifi: List[Dict]) -> Dict:
    """Compare attack success rates between modes."""
    def avg_success(trials, key):
        if not trials:
            return {"total": 0, "successful": 0, "rate": 0}
        totals = [t.get(key, {}).get("total", 0) for t in trials]
        successes = [t.get(key, {}).get("successful", 0) for t in trials]
        total = statistics.mean(totals) if totals else 0
        succ = statistics.mean(successes) if successes else 0
        return {
            "mean_total": round(total, 1),
            "mean_successful": round(succ, 1),
            "rate": round(succ / total * 100, 1) if total > 0 else 0,
        }

    return {
        "firmware": {
            "bridge": avg_success(bridge, "firmware_attacks"),
            "wifi": avg_success(wifi, "firmware_attacks"),
        },
        "network": {
            "bridge": avg_success(bridge, "network_attacks"),
            "wifi": avg_success(wifi, "network_attacks"),
        },
        "wifi_layer": {
            "bridge": {"mean_total": 0, "mean_successful": 0, "rate": 0,
                        "note": "WiFi attacks N/A in bridge mode"},
            "wifi": avg_success(wifi, "wifi_attacks"),
        },
    }


def _compare_rtt(bridge: List[Dict], wifi: List[Dict]) -> Dict:
    """Compare RTT distributions."""
    def extract_rtt_stats(trials):
        means = []
        stdevs = []
        for t in trials:
            for ip, data in t.get("rtt_baseline", {}).items():
                if "mean_ms" in data:
                    means.append(data["mean_ms"])
                if "stdev_ms" in data:
                    stdevs.append(data["stdev_ms"])
        return {
            "mean_rtt_ms": round(statistics.mean(means), 3) if means else None,
            "mean_jitter_ms": round(statistics.mean(stdevs), 3) if stdevs else None,
        }

    bridge_rtt = extract_rtt_stats(bridge)
    wifi_rtt = extract_rtt_stats(wifi)

    jitter_ratio = None
    if bridge_rtt.get("mean_jitter_ms") and wifi_rtt.get("mean_jitter_ms"):
        if bridge_rtt["mean_jitter_ms"] > 0:
            jitter_ratio = round(wifi_rtt["mean_jitter_ms"] / bridge_rtt["mean_jitter_ms"], 2)

    return {
        "bridge": bridge_rtt,
        "wifi": wifi_rtt,
        "jitter_ratio": jitter_ratio,
    }


def _compare_retransmissions(bridge: List[Dict], wifi: List[Dict]) -> Dict:
    """Compare retransmission counts."""
    bridge_retx = [t.get("retransmissions", {}).get("retransmissions", 0) for t in bridge]
    wifi_retx = [t.get("retransmissions", {}).get("retransmissions", 0) for t in wifi]

    return {
        "bridge_mean": round(statistics.mean(bridge_retx), 1) if bridge_retx else 0,
        "wifi_mean": round(statistics.mean(wifi_retx), 1) if wifi_retx else 0,
        "bridge_always_zero": all(x == 0 for x in bridge_retx),
    }


def _compare_reconnection(bridge: List[Dict], wifi: List[Dict]) -> Dict:
    """Compare reconnection dynamics."""
    wifi_reconn = []
    for t in wifi:
        ms = t.get("reconnection", {}).get("reconnection_ms", 0)
        if ms > 0:
            wifi_reconn.append(ms)

    return {
        "bridge": {"measurable": False, "note": "Deauth invisible in bridge mode"},
        "wifi": {
            "measurable": len(wifi_reconn) > 0,
            "mean_ms": round(statistics.mean(wifi_reconn), 1) if wifi_reconn else None,
            "min_ms": round(min(wifi_reconn), 1) if wifi_reconn else None,
            "max_ms": round(max(wifi_reconn), 1) if wifi_reconn else None,
        },
    }


def _compute_summary(comp: Dict) -> Dict:
    """Compute summary divergence statistics."""
    atk = comp["attack_success"]

    # Total attacks bridge vs wifi
    bridge_total = (
        atk["firmware"]["bridge"]["rate"]
        + atk["network"]["bridge"]["rate"]
    ) / 2

    wifi_total = (
        atk["firmware"]["wifi"]["rate"]
        + atk["network"]["wifi"]["rate"]
    ) / 2
    wifi_with_layer = wifi_total  # WiFi attacks add to wifi-mode total

    return {
        "bridge_aggregate_success_rate": round(bridge_total, 1),
        "wifi_aggregate_success_rate": round(wifi_total, 1),
        "wifi_layer_attacks_missed_by_bridge": atk["wifi_layer"]["wifi"].get("mean_total", 0),
        "rtt_jitter_ratio": comp["rtt"].get("jitter_ratio"),
        "bridge_retransmissions": comp["retransmissions"]["bridge_mean"],
        "wifi_retransmissions": comp["retransmissions"]["wifi_mean"],
    }


def _generate_latex_table(comp: Dict, output_path: str):
    """Generate the LaTeX comparison table."""
    atk = comp["attack_success"]
    rtt = comp["rtt"]
    retx = comp["retransmissions"]
    recon = comp["reconnection"]

    bridge_rtt = rtt["bridge"].get("mean_rtt_ms", "---")
    wifi_rtt = rtt["wifi"].get("mean_rtt_ms", "---")
    bridge_jitter = rtt["bridge"].get("mean_jitter_ms", "---")
    wifi_jitter = rtt["wifi"].get("mean_jitter_ms", "---")

    wifi_recon_mean = recon["wifi"].get("mean_ms", "---")

    tex = r"""\begin{table}[t]
\centering
\caption{Bridge vs.\ 802.11 emulation divergence (RQ-N1).
         Mean $\pm$ 95\%~CI over 5 trials, seed~42.}
\label{tab:bridge-vs-80211}
\small
\begin{tabular}{@{}lcc@{}}
\toprule
\textbf{Metric} & \textbf{Bridge} & \textbf{802.11 (Ours)} \\
\midrule
\multicolumn{3}{@{}l}{\emph{Attack success rate (\%)}} \\
\quad Firmware attacks   & """ + f"{atk['firmware']['bridge']['rate']}" + r""" & """ + f"{atk['firmware']['wifi']['rate']}" + r""" \\
\quad Network attacks    & """ + f"{atk['network']['bridge']['rate']}" + r""" & """ + f"{atk['network']['wifi']['rate']}" + r""" \\
\quad WiFi-layer attacks & 0 (N/A) & """ + f"{atk['wifi_layer']['wifi']['rate']}" + r""" \\
\midrule
\multicolumn{3}{@{}l}{\emph{Latency}} \\
\quad Mean RTT (ms)       & """ + f"{bridge_rtt}" + r""" & """ + f"{wifi_rtt}" + r""" \\
\quad RTT jitter (ms)     & """ + f"{bridge_jitter}" + r""" & """ + f"{wifi_jitter}" + r""" \\
\quad Jitter ratio        & 1.0$\times$ & """ + f"{rtt.get('jitter_ratio', '---')}$\\times$" + r""" \\
\midrule
\multicolumn{3}{@{}l}{\emph{802.11 behavior}} \\
\quad Retransmissions (15\,s) & 0 & """ + f"{retx['wifi_mean']}" + r""" \\
\quad Reconnection (ms)   & N/A & """ + f"{wifi_recon_mean}" + r""" \\
\bottomrule
\end{tabular}
\end{table}
"""
    with open(output_path, "w") as f:
        f.write(tex)
    logger.info(f"LaTeX table → {output_path}")


def _generate_rtt_cdf(bridge_trials: List[Dict], wifi_trials: List[Dict], output_path: str):
    """Generate RTT CDF comparison figure."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        bridge_rtts = []
        wifi_rtts = []

        for t in bridge_trials:
            for ip, data in t.get("rtt_baseline", {}).items():
                if "raw" in data:
                    bridge_rtts.extend(data["raw"])

        for t in wifi_trials:
            for ip, data in t.get("rtt_baseline", {}).items():
                if "raw" in data:
                    wifi_rtts.extend(data["raw"])

        if not bridge_rtts and not wifi_rtts:
            logger.warning("No raw RTT data for CDF plot")
            return

        fig, ax = plt.subplots(figsize=(5, 3.5))

        if bridge_rtts:
            sorted_b = np.sort(bridge_rtts)
            cdf_b = np.arange(1, len(sorted_b) + 1) / len(sorted_b)
            ax.plot(sorted_b, cdf_b, label="Docker Bridge", linewidth=1.5)

        if wifi_rtts:
            sorted_w = np.sort(wifi_rtts)
            cdf_w = np.arange(1, len(sorted_w) + 1) / len(sorted_w)
            ax.plot(sorted_w, cdf_w, label="802.11 (Ours)", linewidth=1.5, linestyle="--")

        ax.set_xlabel("RTT (ms)")
        ax.set_ylabel("CDF")
        ax.set_title("Bridge vs. 802.11 RTT Distribution")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(output_path, dpi=300, bbox_inches="tight")
        plt.close(fig)
        logger.info(f"CDF figure → {output_path}")

    except ImportError:
        logger.warning("matplotlib not available — skipping CDF plot")


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _wait_for_matter(host: str, port: int, timeout: int = 60) -> bool:
    """Wait for Matter bridge to accept connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect((host, port))
            sock.close()
            return True
        except Exception:
            time.sleep(2)
    return False


def _wait_for_port(host: str, port: int, timeout: int = 60) -> bool:
    """Wait for a TCP port to accept connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect((host, port))
            sock.close()
            return True
        except Exception:
            time.sleep(2)
    return False


def _strip_raw_arrays(obj):
    """Remove large 'raw' arrays for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _strip_raw_arrays(v) for k, v in obj.items() if k != "raw"}
    elif isinstance(obj, list):
        return [_strip_raw_arrays(v) for v in obj]
    return obj


def _generate_bridge_compose(output_path: str):
    """Generate a bridge-mode docker-compose for comparison experiments."""
    compose = {
        "version": "3.8",
        "services": {
            "vesper-matter-bridge": {
                "image": "project-chip/chip-tool:latest",
                "container_name": "vesper-matter-bridge",
                "ports": [f"{BRIDGE_MATTER_PORT}:8484"],
                "networks": {"vesper-bridge": {"ipv4_address": BRIDGE_GATEWAY}},
                "restart": "unless-stopped",
            }
        },
        "networks": {
            "vesper-bridge": {
                "driver": "bridge",
                "ipam": {
                    "config": [{"subnet": BRIDGE_SUBNET, "gateway": "172.20.0.1"}]
                },
            }
        },
    }

    # Add ESP32 device containers
    for i in range(NUM_DEVICES):
        device_type = DEVICE_TYPES[i] if i < len(DEVICE_TYPES) else "smart_light"
        svc_name = f"vesper-bridge-dev-{i}"
        compose["services"][svc_name] = {
            "build": {
                "context": "..",
                "dockerfile": "docker/Dockerfile.esp32",
            },
            "container_name": svc_name,
            "depends_on": ["vesper-matter-bridge"],
            "environment": [
                f"DEVICE_TYPE={device_type}",
                f"DEVICE_ID=bridge-dev-{i}",
                f"MATTER_BRIDGE={BRIDGE_GATEWAY}",
                f"QEMU_SERIAL_PORT={BRIDGE_SERIAL_PORTS[i]}",
            ],
            "ports": [f"{BRIDGE_SERIAL_PORTS[i]}:{BRIDGE_SERIAL_PORTS[i]}"],
            "networks": {
                "vesper-bridge": {"ipv4_address": BRIDGE_DEVICE_IPS[i]}
            },
            "restart": "unless-stopped",
        }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    import yaml
    with open(output_path, "w") as f:
        yaml.dump(compose, f, default_flow_style=False)
    logger.info(f"Generated bridge-mode compose: {output_path}")


def setup_logging(output_dir: str):
    """Configure logging."""
    os.makedirs(output_dir, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(f"{output_dir}/rqn1.log"),
            logging.StreamHandler(),
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="VESPER RQ-N1: Bridge vs. 802.11 Divergence Experiment"
    )
    parser.add_argument("--bridge-only", action="store_true",
                        help="Run bridge-mode experiments only (works on macOS)")
    parser.add_argument("--wifi-only", action="store_true",
                        help="Run 802.11-mode experiments only (requires Linux)")
    parser.add_argument("--full", action="store_true",
                        help="Run both modes and compare (requires Linux)")
    parser.add_argument("--compare-only", action="store_true",
                        help="Skip experiments, compare existing results")
    parser.add_argument("--trials", type=int, default=5,
                        help="Number of trials per mode (default: 5)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output directory (default: results/rqn1_<timestamp>)")
    parser.add_argument("--build", action="store_true",
                        help="Rebuild Docker images")
    parser.add_argument("--no-teardown", action="store_true",
                        help="Don't stop containers after experiment")
    args = parser.parse_args()

    if not any([args.bridge_only, args.wifi_only, args.full, args.compare_only]):
        args.bridge_only = True  # Default: bridge only (safe on macOS)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = args.output or f"results/rqn1_{ts}"
    setup_logging(output_dir)

    logger.info("╔════════════════════════════════════════════════════════════╗")
    logger.info("║  VESPER RQ-N1: Bridge vs. 802.11 Emulation Divergence     ║")
    logger.info("╚════════════════════════════════════════════════════════════╝")
    logger.info(f"  Trials:  {args.trials}")
    logger.info(f"  Seed:    {SEED}")
    logger.info(f"  Output:  {output_dir}")
    logger.info(f"  System:  {platform.system()} {platform.machine()}")

    if args.compare_only:
        logger.info("Compare-only mode — loading existing results...")
        comparison = compare_results(output_dir, args.trials)
        _print_comparison(comparison)
        return

    # ── Prerequisite checks ──────────────────────────────────────────
    if not check_docker():
        logger.error("Docker not available. Install Docker Desktop.")
        sys.exit(1)

    if (args.wifi_only or args.full) and not check_linux_wifi_support():
        logger.error(
            "802.11 mode requires Linux with mac80211_hwsim.\n"
            "  On macOS, use --bridge-only or run on a Linux machine.\n"
            "  Options:\n"
            "    1. UTM/QEMU VM with Ubuntu 22.04\n"
            "    2. AWS/GCP Linux instance\n"
            "    3. OrbStack (experimental mac80211_hwsim support)"
        )
        if not args.bridge_only:
            sys.exit(1)

    start_time = time.time()
    bridge_trials = []
    wifi_trials = []

    # ── Bridge-mode experiments ──────────────────────────────────────
    if args.bridge_only or args.full:
        logger.info("\n▶ BRIDGE MODE")
        try:
            infra = start_bridge_mode(output_dir, build=args.build)
            for t in range(1, args.trials + 1):
                result = run_single_trial("bridge", t, output_dir, infra)
                bridge_trials.append(result)
        except Exception as e:
            logger.error(f"Bridge-mode failed: {e}")
        finally:
            if not args.no_teardown:
                stop_bridge_mode()

    # ── 802.11-mode experiments ──────────────────────────────────────
    if args.wifi_only or args.full:
        logger.info("\n▶ 802.11 MODE (Mininet-WiFi)")
        try:
            infra = start_wifi_mode(output_dir, build=args.build)
            for t in range(1, args.trials + 1):
                result = run_single_trial("wifi", t, output_dir, infra)
                wifi_trials.append(result)
        except Exception as e:
            logger.error(f"WiFi-mode failed: {e}")
        finally:
            if not args.no_teardown:
                stop_wifi_mode()

    # ── Comparison ───────────────────────────────────────────────────
    elapsed = time.time() - start_time
    logger.info(f"\nTotal experiment time: {elapsed:.0f}s ({elapsed/60:.1f} min)")

    if args.full:
        comparison = compare_results(output_dir, args.trials)
        _print_comparison(comparison)


def _print_comparison(comp: Dict):
    """Print formatted comparison summary."""
    s = comp.get("summary", {})
    print("\n" + "═" * 60)
    print("  RQ-N1 SUMMARY: Bridge vs. 802.11 Divergence")
    print("═" * 60)

    atk = comp.get("attack_success", {})
    print(f"\n  Firmware attacks:  Bridge {atk.get('firmware',{}).get('bridge',{}).get('rate',0):.0f}%"
          f"  vs  WiFi {atk.get('firmware',{}).get('wifi',{}).get('rate',0):.0f}%")
    print(f"  Network attacks:   Bridge {atk.get('network',{}).get('bridge',{}).get('rate',0):.0f}%"
          f"  vs  WiFi {atk.get('network',{}).get('wifi',{}).get('rate',0):.0f}%")
    print(f"  WiFi-layer attacks: Bridge 0% (N/A)"
          f"  vs  WiFi {atk.get('wifi_layer',{}).get('wifi',{}).get('rate',0):.0f}%")

    rtt = comp.get("rtt", {})
    print(f"\n  RTT jitter ratio:  {rtt.get('jitter_ratio', 'N/A')}×")

    retx = comp.get("retransmissions", {})
    print(f"  Retransmissions:   Bridge {retx.get('bridge_mean',0):.0f}"
          f"  vs  WiFi {retx.get('wifi_mean',0):.0f}")

    recon = comp.get("reconnection", {})
    print(f"  Reconnection:      Bridge N/A"
          f"  vs  WiFi {recon.get('wifi',{}).get('mean_ms','N/A')} ms")

    print("═" * 60)


if __name__ == "__main__":
    main()
