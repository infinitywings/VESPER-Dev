#!/usr/bin/env python3
"""
VESPER Evaluation Campaign — Mininet-WiFi + ESP32

Runs the full attack campaign against the emulated smart-home WiFi network.

This replaces the old batch evaluation scripts that used Docker bridge
networking + LM3S6965 QEMU. The new setup uses:
  - Mininet-WiFi for real 802.11 emulation (mac80211_hwsim)
  - ESP32 QEMU (Espressif fork) for realistic IoT firmware
  - Matter bridge (matter.js) on the emulated AP
  - tshark packet capture at the WiFi interface

Campaign structure:
  Phase 1: Firmware attacks (serial port — 35 attacks per device)
  Phase 2: WiFi/network attacks (802.11 + TCP/IP — 11 attack types)
  Phase 3: Cross-layer attacks (firmware + network combined)

Usage:
    # Full campaign (requires Linux host with Docker)
    python3 scripts/run_evaluation_wifi.py --full

    # WiFi attacks only (router must be running)
    python3 scripts/run_evaluation_wifi.py --wifi-only

    # Firmware attacks only
    python3 scripts/run_evaluation_wifi.py --firmware-only

    # Quick smoke test (3 devices, 5 attacks)
    python3 scripts/run_evaluation_wifi.py --quick
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to path
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)

from vesper.network.wifi_emulator import WiFiEmulator, WiFiConfig, DEFAULT_DEVICES
from vesper.firmware.esp32_runner import ESP32Runner, ESP32Config, create_device_runners
from vesper.attacks.wifi_attacks import WiFiAttackFramework, WiFiAttackResult
from vesper.attacks.firmware_attacks import FirmwareAttackFramework, FirmwareTarget, AttackResult
from vesper.attacks.network_attacks import NetworkAttackFramework, NetworkTarget

logger = logging.getLogger("vesper.eval")


def setup_logging(output_dir: str) -> None:
    """Configure logging to file and console."""
    os.makedirs(output_dir, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(f"{output_dir}/evaluation.log"),
            logging.StreamHandler(),
        ],
    )


def run_firmware_attacks(
    runners: dict,
    output_dir: str,
) -> list:
    """Phase 1: Run firmware attacks against each ESP32 device."""
    logger.info("═" * 60)
    logger.info("  PHASE 1: Firmware Attacks (ESP32 serial)")
    logger.info("═" * 60)

    all_results = []
    fw_framework = FirmwareAttackFramework()

    for device_id, runner in runners.items():
        logger.info(f"\n  Targeting: {device_id} (port {runner.config.serial_port})")

        target = FirmwareTarget(
            host="localhost",
            port=runner.config.serial_port,
            device_type=runner.config.device_type,
        )

        try:
            results = fw_framework.run_all_attacks(target)
            all_results.extend(results)

            successful = sum(1 for r in results if r.success)
            logger.info(f"  {device_id}: {successful}/{len(results)} attacks succeeded")

            # Save per-device results
            fw_framework.export_results(
                results, f"{output_dir}/firmware_{device_id}.json"
            )
        except Exception as e:
            logger.error(f"  {device_id}: firmware attacks failed: {e}")

    return all_results


def run_wifi_attacks(
    emulator: WiFiEmulator,
    output_dir: str,
) -> list:
    """Phase 2: Run WiFi/network attacks via Mininet-WiFi."""
    logger.info("═" * 60)
    logger.info("  PHASE 2: WiFi & Network Attacks (Mininet-WiFi)")
    logger.info("═" * 60)

    pcap_dir = f"{output_dir}/pcap"
    wifi_framework = WiFiAttackFramework(emulator, pcap_dir=pcap_dir)

    results = wifi_framework.run_all_attacks()

    successful = sum(1 for r in results if r.success)
    logger.info(f"\n  WiFi attacks: {successful}/{len(results)} succeeded")

    # Save results
    wifi_framework.export_results(results, f"{output_dir}/wifi_attacks.json")
    wifi_framework.print_report(results)

    return results


def run_network_attacks(
    emulator: WiFiEmulator,
    runners: dict,
    output_dir: str,
) -> list:
    """Phase 2b: Run TCP/IP network attacks (Matter, TCP, protocol)."""
    logger.info("═" * 60)
    logger.info("  PHASE 2b: TCP/IP Network Attacks")
    logger.info("═" * 60)

    devices = [(dev.ip, dev.serial_port) for dev in emulator.devices]
    target = NetworkTarget(
        matter_bridge_url=f"http://{emulator.wifi.gateway_ip}:8484",
        devices=devices,
        gateway_ip=emulator.wifi.gateway_ip,
        subnet=emulator.wifi.subnet,
    )

    net_framework = NetworkAttackFramework()
    results = net_framework.run_all_attacks(target)

    successful = sum(1 for r in results if r.success)
    logger.info(f"\n  Network attacks: {successful}/{len(results)} succeeded")

    net_framework.export_results(results, f"{output_dir}/network_attacks.json")
    return results


def generate_summary(
    fw_results: list,
    wifi_results: list,
    net_results: list,
    output_dir: str,
    elapsed: float,
) -> dict:
    """Generate consolidated evaluation summary."""
    summary = {
        "timestamp": datetime.now().isoformat(),
        "duration_seconds": round(elapsed, 1),
        "infrastructure": {
            "wifi_emulator": "Mininet-WiFi (Fontes et al., SoftCOM 2015)",
            "firmware_target": "ESP32 (Xtensa LX6) via Espressif QEMU",
            "wifi_auth": "WPA2-PSK (hostapd + mac80211_hwsim)",
            "matter_bridge": "matter.js bridge",
            "packet_capture": "tshark 4.x",
        },
        "firmware_attacks": {
            "total": len(fw_results),
            "successful": sum(1 for r in fw_results if r.success),
            "by_category": {},
        },
        "wifi_attacks": {
            "total": len(wifi_results),
            "successful": sum(1 for r in wifi_results if r.success),
            "total_packets_sent": sum(getattr(r, "packets_sent", 0) for r in wifi_results),
            "pcap_files": sum(1 for r in wifi_results if getattr(r, "pcap_file", None)),
        },
        "network_attacks": {
            "total": len(net_results),
            "successful": sum(1 for r in net_results if r.success),
        },
        "overall": {
            "total_attacks": len(fw_results) + len(wifi_results) + len(net_results),
            "total_successful": (
                sum(1 for r in fw_results if r.success) +
                sum(1 for r in wifi_results if r.success) +
                sum(1 for r in net_results if r.success)
            ),
        },
    }

    # Firmware by category
    for r in fw_results:
        cat = r.category.value if hasattr(r, "category") else "unknown"
        if cat not in summary["firmware_attacks"]["by_category"]:
            summary["firmware_attacks"]["by_category"][cat] = {"total": 0, "success": 0}
        summary["firmware_attacks"]["by_category"][cat]["total"] += 1
        if r.success:
            summary["firmware_attacks"]["by_category"][cat]["success"] += 1

    total = summary["overall"]["total_attacks"]
    succ = summary["overall"]["total_successful"]
    summary["overall"]["success_rate"] = f"{succ/total*100:.1f}%" if total else "N/A"

    # Save
    with open(f"{output_dir}/evaluation_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    return summary


def print_final_report(summary: dict) -> None:
    """Print the final evaluation report."""
    print("\n" + "╔" + "═" * 68 + "╗")
    print("║  VESPER EVALUATION CAMPAIGN — FINAL REPORT" + " " * 25 + "║")
    print("╠" + "═" * 68 + "╣")
    print(f"║  Duration:     {summary['duration_seconds']}s" + " " * (53 - len(str(summary['duration_seconds']))) + "║")
    print(f"║  WiFi:         {summary['infrastructure']['wifi_emulator'][:50]}" + " " * max(0, 3) + "║")
    print(f"║  Firmware:     {summary['infrastructure']['firmware_target'][:50]}" + " " * max(0, 3) + "║")
    print("╠" + "═" * 68 + "╣")

    fw = summary["firmware_attacks"]
    wifi = summary["wifi_attacks"]
    net = summary["network_attacks"]
    overall = summary["overall"]

    print(f"║  Firmware Attacks:  {fw['successful']:3d}/{fw['total']:3d} succeeded" + " " * 35 + "║")
    print(f"║  WiFi Attacks:      {wifi['successful']:3d}/{wifi['total']:3d} succeeded" + " " * 35 + "║")
    print(f"║  Network Attacks:   {net['successful']:3d}/{net['total']:3d} succeeded" + " " * 35 + "║")
    print("╠" + "═" * 68 + "╣")
    print(f"║  TOTAL:             {overall['total_successful']:3d}/{overall['total_attacks']:3d} ({overall['success_rate']})" + " " * 30 + "║")
    print("╚" + "═" * 68 + "╝")


def main():
    parser = argparse.ArgumentParser(description="VESPER Evaluation Campaign (Mininet-WiFi)")
    parser.add_argument("--full", action="store_true", help="Run full campaign (all 3 phases)")
    parser.add_argument("--wifi-only", action="store_true", help="WiFi attacks only")
    parser.add_argument("--firmware-only", action="store_true", help="Firmware attacks only")
    parser.add_argument("--quick", action="store_true", help="Quick smoke test")
    parser.add_argument("--output", default="results/eval_wifi", help="Output directory")
    parser.add_argument("--no-start", action="store_true", help="Skip Docker startup (assume running)")
    parser.add_argument("--build", action="store_true", help="Rebuild Docker images")
    args = parser.parse_args()

    if not any([args.full, args.wifi_only, args.firmware_only, args.quick]):
        args.full = True

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = f"{args.output}_{ts}"
    setup_logging(output_dir)

    logger.info("VESPER Evaluation Campaign Starting")
    logger.info(f"Output: {output_dir}")
    start_time = time.time()

    # ── Start infrastructure ──────────────────────────────────────────
    emulator = WiFiEmulator()

    if not args.no_start:
        logger.info("Starting WiFi topology (Docker compose)...")
        emulator.start(build=args.build, detach=True)
        emulator.wait_ready(timeout=180)
    else:
        emulator.state = "ready"
        logger.info("Skipping Docker startup (--no-start)")

    # ── Create device runners ─────────────────────────────────────────
    runners = create_device_runners()

    fw_results = []
    wifi_results = []
    net_results = []

    try:
        # Phase 1: Firmware
        if args.full or args.firmware_only or args.quick:
            if args.quick:
                # Only first 3 devices
                quick_runners = dict(list(runners.items())[:3])
                fw_results = run_firmware_attacks(quick_runners, output_dir)
            else:
                fw_results = run_firmware_attacks(runners, output_dir)

        # Phase 2: WiFi + Network
        if args.full or args.wifi_only or args.quick:
            wifi_results = run_wifi_attacks(emulator, output_dir)
            if not args.quick:
                net_results = run_network_attacks(emulator, runners, output_dir)

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        elapsed = time.time() - start_time

        # Generate summary
        summary = generate_summary(fw_results, wifi_results, net_results, output_dir, elapsed)
        print_final_report(summary)

        if not args.no_start:
            logger.info("Stopping topology...")
            emulator.stop()

        logger.info(f"Results saved to {output_dir}/")


if __name__ == "__main__":
    main()
