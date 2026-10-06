#!/usr/bin/env python3
"""
VESPER RQ-N2: Measured WiFi Hardening Tradeoffs

Iterates over 8 WiFi configurations (4 binary factors) and runs the
full attack suite under each, measuring security gain vs availability cost.

Configuration matrix (8 configs):
    WPA2-PSK + PMF-off  + AP-iso-off + Matter-anon    (baseline)
    WPA2-PSK + PMF-off  + AP-iso-off + Matter-TLS
    WPA2-PSK + PMF-off  + AP-iso-on  + Matter-anon
    WPA2-PSK + PMF-off  + AP-iso-on  + Matter-TLS
    WPA2-PSK + PMF-req  + AP-iso-off + Matter-anon
    WPA2-PSK + PMF-req  + AP-iso-off + Matter-TLS
    WPA3-SAE + PMF-req  + AP-iso-off + Matter-anon    (WPA3 mandates PMF)
    WPA3-SAE + PMF-req  + AP-iso-on  + Matter-TLS     (fully hardened)

Requirements:
    Linux host with Docker, mac80211_hwsim kernel module (Mininet-WiFi).

Usage:
    python3 scripts/run_rqn2_hardening_sweep.py --full --trials 5
    python3 scripts/run_rqn2_hardening_sweep.py --config 0,7 --trials 3
    python3 scripts/run_rqn2_hardening_sweep.py --analyze-only --output results/rqn2_...

Outputs:
    results/rqn2_<ts>/
        config_N/trial_T/       — per-config, per-trial results
        tab_hardening_measured.tex
        fig_hardening_pareto.pdf
        rqn2_summary.json
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

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)

from vesper.network.wifi_emulator import WiFiEmulator, WiFiConfig, DEFAULT_DEVICES

logger = logging.getLogger("vesper.rqn2")

SEED = 42

# ═══════════════════════════════════════════════════════════════════════════
# Configuration Matrix
# ═══════════════════════════════════════════════════════════════════════════

HARDENING_CONFIGS = [
    # Config 0: Baseline (worst-case consumer deployment)
    {
        "name": "Baseline",
        "short": "WPA2/no-PMF/no-iso/anon",
        "wifi": WiFiConfig(
            encryption="WPA2-PSK", pmf="disabled",
            ap_isolation=False, matter_tls=False,
        ),
    },
    # Config 1: +Matter-TLS only
    {
        "name": "+Matter-TLS",
        "short": "WPA2/no-PMF/no-iso/TLS",
        "wifi": WiFiConfig(
            encryption="WPA2-PSK", pmf="disabled",
            ap_isolation=False,
            matter_tls=True,
        ),
    },
    # Config 2: +AP isolation only
    {
        "name": "+AP-isolation",
        "short": "WPA2/no-PMF/iso/anon",
        "wifi": WiFiConfig(
            encryption="WPA2-PSK", pmf="disabled",
            ap_isolation=True, matter_tls=False,
        ),
    },
    # Config 3: +AP isolation + Matter-TLS
    {
        "name": "+AP-iso+Matter-TLS",
        "short": "WPA2/no-PMF/iso/TLS",
        "wifi": WiFiConfig(
            encryption="WPA2-PSK", pmf="disabled",
            ap_isolation=True,
            matter_tls=True,
        ),
    },
    # Config 4: +PMF only (WPA2 with PMF required)
    {
        "name": "+PMF",
        "short": "WPA2/PMF/no-iso/anon",
        "wifi": WiFiConfig(
            encryption="WPA2-PSK", pmf="required",
            ap_isolation=False, matter_tls=False,
        ),
    },
    # Config 5: +PMF + Matter-TLS
    {
        "name": "+PMF+Matter-TLS",
        "short": "WPA2/PMF/no-iso/TLS",
        "wifi": WiFiConfig(
            encryption="WPA2-PSK", pmf="required",
            ap_isolation=False,
            matter_tls=True,
        ),
    },
    # Config 6: WPA3-SAE (mandates PMF)
    {
        "name": "WPA3-SAE",
        "short": "WPA3/PMF/no-iso/anon",
        "wifi": WiFiConfig(
            encryption="WPA3-SAE", pmf="required",
            ap_isolation=False, matter_tls=False,
        ),
    },
    # Config 7: Fully hardened
    {
        "name": "Fully hardened",
        "short": "WPA3/PMF/iso/TLS",
        "wifi": WiFiConfig(
            encryption="WPA3-SAE", pmf="required",
            ap_isolation=True,
            matter_tls=True,
            syn_rate_limit=5, syn_burst=10,  # Tightened firewall
            icmp_rate_limit=2,
            port_whitelist=[53, 67, 123, 8484, 5540],
        ),
    },
]


# ═══════════════════════════════════════════════════════════════════════════
# Experiment Execution
# ═══════════════════════════════════════════════════════════════════════════

def run_config(
    config_idx: int,
    trial: int,
    output_dir: str,
    build: bool = False,
) -> Dict[str, Any]:
    """Run one trial of the attack suite under a specific WiFi configuration."""
    cfg = HARDENING_CONFIGS[config_idx]
    trial_dir = f"{output_dir}/config_{config_idx}/trial_{trial}"
    os.makedirs(trial_dir, exist_ok=True)

    logger.info(f"{'═'*60}")
    logger.info(f"  Config {config_idx}: {cfg['name']} — Trial {trial}")
    logger.info(f"  {cfg['short']}")
    logger.info(f"{'═'*60}")

    result = {
        "config_idx": config_idx,
        "config_name": cfg["name"],
        "config_short": cfg["short"],
        "trial": trial,
        "timestamp": datetime.now().isoformat(),
        "wifi_params": {
            "encryption": cfg["wifi"].encryption,
            "pmf": cfg["wifi"].pmf,
            "ap_isolation": cfg["wifi"].ap_isolation,
            "matter_auth": cfg["wifi"].matter_auth,
            "matter_tls": cfg["wifi"].matter_tls,
        },
    }

    # ── 1. Start WiFi topology with this config ─────────────────────
    emu = WiFiEmulator(wifi_config=cfg["wifi"])
    try:
        emu.start(build=build, detach=True)
        if not emu.wait_ready(timeout=180):
            result["error"] = "Topology not ready"
            return result
    except Exception as e:
        result["error"] = str(e)
        logger.error(f"  Failed to start topology: {e}")
        return result

    start_time = time.time()

    try:
        # ── 2. Pre-attack availability baseline ─────────────────────
        logger.info("  Measuring pre-attack availability...")
        result["availability_baseline"] = _measure_availability(emu)

        # ── 3. Start pcap capture ────────────────────────────────────
        pcap_file = f"{trial_dir}/attacks.pcap"
        emu.capture_start(pcap_file)
        time.sleep(1)

        # ── 4. Run firmware attacks ──────────────────────────────────
        logger.info("  Running firmware attacks...")
        fw_results = _run_firmware_attacks(emu, trial_dir)
        result["firmware_attacks"] = {
            "total": len(fw_results),
            "successful": sum(1 for r in fw_results if r.get("success")),
            "by_category": _group_by_category(fw_results),
        }

        # ── 5. Run WiFi-layer attacks ────────────────────────────────
        logger.info("  Running WiFi-layer attacks...")
        wifi_results = _run_wifi_attacks(emu, trial_dir)
        result["wifi_attacks"] = {
            "total": len(wifi_results),
            "successful": sum(1 for r in wifi_results if r.get("success")),
            "by_type": _group_by_type(wifi_results),
        }

        # ── 6. Run network attacks ───────────────────────────────────
        logger.info("  Running network attacks...")
        net_results = _run_network_attacks(emu, trial_dir)
        result["network_attacks"] = {
            "total": len(net_results),
            "successful": sum(1 for r in net_results if r.get("success")),
            "by_category": _group_by_category(net_results),
        }

        # ── 7. Stop capture ──────────────────────────────────────────
        emu.capture_stop()

        # ── 8. Post-attack availability ──────────────────────────────
        logger.info("  Measuring post-attack availability...")
        result["availability_post"] = _measure_availability(emu)

        # ── 9. Throughput test (iperf3) ──────────────────────────────
        logger.info("  Running throughput test...")
        result["throughput"] = _measure_throughput(emu)

        # ── 10. Reconnection latency ─────────────────────────────────
        logger.info("  Measuring reconnection latency...")
        result["reconnection_ms"] = _measure_reconnection_latency(emu)

    except Exception as e:
        logger.error(f"  Trial failed: {e}")
        result["error"] = str(e)
    finally:
        elapsed = time.time() - start_time
        result["duration_s"] = round(elapsed, 1)
        emu.stop()

    # Compute totals
    total = (
        result.get("firmware_attacks", {}).get("total", 0)
        + result.get("wifi_attacks", {}).get("total", 0)
        + result.get("network_attacks", {}).get("total", 0)
    )
    successful = (
        result.get("firmware_attacks", {}).get("successful", 0)
        + result.get("wifi_attacks", {}).get("successful", 0)
        + result.get("network_attacks", {}).get("successful", 0)
    )
    result["total_attacks"] = total
    result["total_successful"] = successful
    result["success_rate"] = round(successful / total * 100, 1) if total > 0 else 0

    # Save
    with open(f"{trial_dir}/result.json", "w") as f:
        json.dump(result, f, indent=2)

    logger.info(f"  Result: {successful}/{total} attacks ({result['success_rate']}%)")
    return result


def _run_firmware_attacks(emu: WiFiEmulator, output_dir: str) -> List[Dict]:
    """Run firmware attacks via QEMU serial ports."""
    from vesper.attacks.firmware_attacks import FirmwareAttackFramework, FirmwareTarget

    all_results = []
    fw = FirmwareAttackFramework()

    for dev in emu.devices[:2]:  # 2 devices per config (to keep sweep tractable)
        target = FirmwareTarget(
            host="localhost",
            port=dev.serial_port,
            device_type=dev.device_type.value,
        )
        try:
            results = fw.run_all_attacks(target)
            for r in results:
                all_results.append({
                    "device": dev.device_id,
                    "attack_name": r.attack_name,
                    "category": r.category.value if hasattr(r.category, 'value') else str(r.category),
                    "success": r.success,
                    "duration_ms": r.duration_ms,
                })
        except Exception as e:
            logger.error(f"  Firmware attacks on {dev.device_id} failed: {e}")

    with open(f"{output_dir}/firmware_attacks.json", "w") as f:
        json.dump(all_results, f, indent=2)
    return all_results


def _run_wifi_attacks(emu: WiFiEmulator, output_dir: str) -> List[Dict]:
    """Run WiFi-layer attacks."""
    from vesper.attacks.wifi_attacks import WiFiAttackFramework

    pcap_dir = f"{output_dir}/pcap"
    os.makedirs(pcap_dir, exist_ok=True)

    wifi = WiFiAttackFramework(emu, pcap_dir=pcap_dir)
    results = wifi.run_all_attacks()

    serialized = []
    for r in results:
        serialized.append({
            "attack_name": r.attack_name,
            "attack_type": r.attack_type,
            "success": r.success,
            "duration_ms": r.duration_ms,
            "packets_sent": r.packets_sent,
        })

    with open(f"{output_dir}/wifi_attacks.json", "w") as f:
        json.dump(serialized, f, indent=2)
    return serialized


def _run_network_attacks(emu: WiFiEmulator, output_dir: str) -> List[Dict]:
    """Run TCP/IP network attacks."""
    from vesper.attacks.network_attacks import NetworkAttackFramework, NetworkTarget

    devices = [(dev.ip, dev.serial_port) for dev in emu.devices[:2]]
    target = NetworkTarget(
        matter_bridge_host=emu.wifi.gateway_ip,
        matter_bridge_port=emu.wifi.matter_bridge_port,
        devices=devices,
        gateway_ip=emu.wifi.gateway_ip,
        subnet=emu.wifi.subnet,
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
        })

    with open(f"{output_dir}/network_attacks.json", "w") as f:
        json.dump(serialized, f, indent=2)
    return serialized


# ═══════════════════════════════════════════════════════════════════════════
# Availability & Throughput Metrics
# ═══════════════════════════════════════════════════════════════════════════

def _measure_availability(emu: WiFiEmulator) -> Dict:
    """Check device reachability and Matter health."""
    reachable = 0
    matter_ok = 0

    for dev in emu.devices[:2]:
        # Ping check
        r = emu._exec_in_router(f"ping -c3 -W1 {dev.ip}")
        if "3 received" in r or "3 packets" in r:
            reachable += 1

        # Matter publish check
        if emu.send_matter(f"vesper/{dev.device_id}/healthcheck", "ping"):
            matter_ok += 1

    return {
        "devices_reachable": reachable,
        "devices_total": min(2, len(emu.devices)),
        "matter_healthy": matter_ok,
    }


def _measure_throughput(emu: WiFiEmulator) -> Dict:
    """Measure TCP throughput via iperf3 (if available)."""
    # Start iperf3 server on router
    emu._exec_in_router("iperf3 -s -D -p 5201 2>/dev/null")
    time.sleep(1)

    # Run from first station
    try:
        output = emu._exec_in_router(
            f"ip netns exec sta1 iperf3 -c {emu.wifi.gateway_ip} -p 5201 -t 5 -J 2>/dev/null"
        )
        if output:
            data = json.loads(output)
            bps = data.get("end", {}).get("sum_sent", {}).get("bits_per_second", 0)
            return {
                "tcp_mbps": round(bps / 1_000_000, 2),
                "raw": output[:200],
            }
    except Exception as e:
        pass
    finally:
        emu._exec_in_router("killall iperf3 2>/dev/null")

    return {"tcp_mbps": None, "error": "iperf3 not available"}


def _measure_reconnection_latency(emu: WiFiEmulator) -> Optional[float]:
    """Deauth sta1, measure time to reassociate."""
    target_ip = emu.devices[0].ip

    # Ensure reachable
    r = emu._exec_in_router(f"ping -c1 -W1 {target_ip}")
    if "1 received" not in r:
        return None

    # Deauth
    emu.deauth_station("sta1", count=10)
    t0 = time.time()

    # Poll
    for _ in range(50):  # 10s max
        time.sleep(0.2)
        r = emu._exec_in_router(f"ping -c1 -W1 {target_ip}")
        if "1 received" in r:
            return round((time.time() - t0) * 1000, 1)

    return 10000.0  # timeout


def _group_by_category(results: List[Dict]) -> Dict:
    groups = {}
    for r in results:
        cat = r.get("category", "unknown")
        if cat not in groups:
            groups[cat] = {"total": 0, "successful": 0}
        groups[cat]["total"] += 1
        if r.get("success"):
            groups[cat]["successful"] += 1
    return groups


def _group_by_type(results: List[Dict]) -> Dict:
    groups = {}
    for r in results:
        t = r.get("attack_type", "unknown")
        if t not in groups:
            groups[t] = {"total": 0, "successful": 0}
        groups[t]["total"] += 1
        if r.get("success"):
            groups[t]["successful"] += 1
    return groups


# ═══════════════════════════════════════════════════════════════════════════
# Analysis & LaTeX Generation
# ═══════════════════════════════════════════════════════════════════════════

def analyze_sweep(output_dir: str, config_indices: List[int], num_trials: int) -> Dict:
    """Aggregate results across configs and trials."""
    summary = {
        "timestamp": datetime.now().isoformat(),
        "num_configs": len(config_indices),
        "num_trials": num_trials,
        "configs": [],
    }

    for ci in config_indices:
        cfg_data = {
            "config_idx": ci,
            "name": HARDENING_CONFIGS[ci]["name"],
            "short": HARDENING_CONFIGS[ci]["short"],
            "trials": [],
            "mean_success_rate": 0,
            "mean_reconnection_ms": None,
            "mean_throughput_mbps": None,
        }

        success_rates = []
        reconnections = []
        throughputs = []

        for t in range(1, num_trials + 1):
            path = f"{output_dir}/config_{ci}/trial_{t}/result.json"
            if os.path.exists(path):
                with open(path) as f:
                    trial = json.load(f)
                cfg_data["trials"].append(trial)
                sr = trial.get("success_rate", 0)
                success_rates.append(sr)
                rm = trial.get("reconnection_ms")
                if rm is not None:
                    reconnections.append(rm)
                tp = trial.get("throughput", {}).get("tcp_mbps")
                if tp is not None:
                    throughputs.append(tp)

        if success_rates:
            cfg_data["mean_success_rate"] = round(statistics.mean(success_rates), 1)
            if len(success_rates) > 1:
                se = statistics.stdev(success_rates) / (len(success_rates) ** 0.5)
                cfg_data["ci95_success_rate"] = round(1.96 * se, 1)
        if reconnections:
            cfg_data["mean_reconnection_ms"] = round(statistics.mean(reconnections), 1)
        if throughputs:
            cfg_data["mean_throughput_mbps"] = round(statistics.mean(throughputs), 2)

        summary["configs"].append(cfg_data)

    # Compute reduction from baseline
    baseline_sr = summary["configs"][0]["mean_success_rate"] if summary["configs"] else 0
    for cfg in summary["configs"]:
        if baseline_sr > 0:
            cfg["attack_reduction_pct"] = round(
                (1 - cfg["mean_success_rate"] / baseline_sr) * 100, 1
            )
        else:
            cfg["attack_reduction_pct"] = 0

    # Save
    with open(f"{output_dir}/rqn2_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    # Generate LaTeX
    _generate_hardening_table(summary, f"{output_dir}/tab_hardening_measured.tex")
    _generate_pareto_plot(summary, f"{output_dir}/fig_hardening_pareto.pdf")

    return summary


def _generate_hardening_table(summary: Dict, output_path: str):
    """Generate the LaTeX hardening results table."""
    tex_lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Measured hardening tradeoffs (RQ-N2). Attack success rate",
        r"         vs.\ availability cost across 8 WiFi configurations.",
        r"         Mean $\pm$ 95\%~CI over 5~trials, seed~42.}",
        r"\label{tab:hardening-measured}",
        r"\small",
        r"\begin{tabular}{@{}lccccc@{}}",
        r"\toprule",
        r"\textbf{Config} & \textbf{Encrypt} & \textbf{Atk~\%} & \textbf{$\Delta$~\%} & \textbf{Reconn (ms)} & \textbf{Mbps} \\",
        r"\midrule",
    ]

    for cfg in summary["configs"]:
        name = cfg["short"]
        sr = cfg["mean_success_rate"]
        ci = cfg.get("ci95_success_rate", "---")
        reduct = cfg.get("attack_reduction_pct", 0)
        recon = cfg.get("mean_reconnection_ms", "---")
        tp = cfg.get("mean_throughput_mbps", "---")

        sr_str = f"{sr}" if ci == "---" else f"{sr}$\\pm${ci}"
        reduct_str = f"$-${reduct}" if reduct > 0 else "0"
        recon_str = str(recon) if recon is not None else "---"
        tp_str = str(tp) if tp is not None else "---"

        tex_lines.append(
            f"  {name} & {cfg['name'][:12]} & {sr_str} & {reduct_str} & {recon_str} & {tp_str} \\\\"
        )

    tex_lines.extend([
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ])

    with open(output_path, "w") as f:
        f.write("\n".join(tex_lines) + "\n")
    logger.info(f"LaTeX table → {output_path}")


def _generate_pareto_plot(summary: Dict, output_path: str):
    """Generate Pareto frontier: security gain vs availability cost."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(5, 3.5))

        xs = []  # attack reduction %
        ys = []  # reconnection latency ms
        labels = []

        for cfg in summary["configs"]:
            x = cfg.get("attack_reduction_pct", 0)
            y = cfg.get("mean_reconnection_ms") or 0
            xs.append(x)
            ys.append(y)
            labels.append(cfg["name"])

        ax.scatter(xs, ys, s=60, zorder=5)
        for i, label in enumerate(labels):
            ax.annotate(label, (xs[i], ys[i]),
                        textcoords="offset points", xytext=(5, 5),
                        fontsize=7)

        ax.set_xlabel("Attack Reduction (%)")
        ax.set_ylabel("Reconnection Latency (ms)")
        ax.set_title("Security vs. Availability Tradeoff (RQ-N2)")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(output_path, dpi=300, bbox_inches="tight")
        plt.close(fig)
        logger.info(f"Pareto plot → {output_path}")

    except ImportError:
        logger.warning("matplotlib not available — skipping Pareto plot")


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def setup_logging(output_dir: str):
    os.makedirs(output_dir, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(f"{output_dir}/rqn2.log"),
            logging.StreamHandler(),
        ],
    )


def main():
    parser = argparse.ArgumentParser(
        description="VESPER RQ-N2: WiFi Hardening Sweep"
    )
    parser.add_argument("--full", action="store_true",
                        help="Run all 8 configurations")
    parser.add_argument("--config", type=str, default=None,
                        help="Comma-separated config indices to run (e.g., 0,7)")
    parser.add_argument("--trials", type=int, default=5,
                        help="Trials per config (default: 5)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output directory")
    parser.add_argument("--analyze-only", action="store_true",
                        help="Only analyze existing results")
    parser.add_argument("--build", action="store_true",
                        help="Rebuild Docker images")
    args = parser.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = args.output or f"results/rqn2_{ts}"
    setup_logging(output_dir)

    # Determine which configs to run
    if args.config:
        config_indices = [int(c.strip()) for c in args.config.split(",")]
    elif args.full:
        config_indices = list(range(len(HARDENING_CONFIGS)))
    else:
        config_indices = [0, 7]  # Default: baseline + fully hardened

    logger.info("╔════════════════════════════════════════════════════════════╗")
    logger.info("║  VESPER RQ-N2: Measured WiFi Hardening Tradeoffs          ║")
    logger.info("╚════════════════════════════════════════════════════════════╝")
    logger.info(f"  Configs: {config_indices}")
    logger.info(f"  Trials:  {args.trials}")
    logger.info(f"  Output:  {output_dir}")

    if args.analyze_only:
        summary = analyze_sweep(output_dir, config_indices, args.trials)
        _print_summary(summary)
        return

    # Check prerequisites
    if platform.system() != "Linux":
        logger.error(
            "RQ-N2 requires Linux with mac80211_hwsim for Mininet-WiFi.\n"
            "  Run this on a Linux host or VM.\n"
            "  Options:\n"
            "    1. UTM/QEMU VM with Ubuntu 22.04\n"
            "    2. AWS/GCP Linux instance (t3.xlarge or better)\n"
            "    3. Use --analyze-only on existing results"
        )
        sys.exit(1)

    start_time = time.time()

    for ci in config_indices:
        logger.info(f"\n{'▓'*60}")
        logger.info(f"  Configuration {ci}/{len(HARDENING_CONFIGS)-1}: {HARDENING_CONFIGS[ci]['name']}")
        logger.info(f"{'▓'*60}")

        for t in range(1, args.trials + 1):
            try:
                run_config(ci, t, output_dir, build=args.build)
            except Exception as e:
                logger.error(f"  Config {ci} trial {t} failed: {e}")

            # Cooldown between trials
            time.sleep(5)

        # Cooldown between configs
        time.sleep(10)

    elapsed = time.time() - start_time
    logger.info(f"\nTotal sweep time: {elapsed:.0f}s ({elapsed/3600:.1f}h)")

    # Analyze
    summary = analyze_sweep(output_dir, config_indices, args.trials)
    _print_summary(summary)


def _print_summary(summary: Dict):
    """Print formatted sweep summary."""
    print("\n" + "═" * 70)
    print("  RQ-N2 HARDENING SWEEP SUMMARY")
    print("═" * 70)
    print(f"\n  {'Config':<25} {'Atk%':>6} {'Δ%':>6} {'Reconn':>8} {'Mbps':>7}")
    print("  " + "─" * 55)

    for cfg in summary["configs"]:
        name = cfg["name"][:24]
        sr = cfg["mean_success_rate"]
        reduct = cfg.get("attack_reduction_pct", 0)
        recon = cfg.get("mean_reconnection_ms", "---")
        tp = cfg.get("mean_throughput_mbps", "---")
        print(f"  {name:<25} {sr:>5.1f}% {reduct:>+5.1f}% {str(recon):>8} {str(tp):>7}")

    print("═" * 70)


if __name__ == "__main__":
    main()
