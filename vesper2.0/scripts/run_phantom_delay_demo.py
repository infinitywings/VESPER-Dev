#!/usr/bin/env python3
"""
VESPER Phantom-Delay Attack Demo

Demonstrates the IoT Phantom-Delay Attack (Fu et al., DSN 2022) within
VESPER's simulated 3D smart home environment.

This script:
  1. Launches a QEMU firmware device (or uses a running one)
  2. Runs all 8 phantom-delay attack variants
  3. Prints a detailed report with CVSS scores
  4. Exports results to JSON

Usage:
    # Against a running firmware device
    python scripts/run_phantom_delay_demo.py --host 127.0.0.1 --port 15011

    # With Docker (starts a firmware container)
    python scripts/run_phantom_delay_demo.py --use-docker

    # Custom delay duration
    python scripts/run_phantom_delay_demo.py --delay 60

    # With MQTT broker
    python scripts/run_phantom_delay_demo.py --mqtt-host 127.0.0.1 --mqtt-port 1883
"""

import argparse
import json
import logging
import os
import sys
import time
import subprocess
import signal
import socket

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vesper.attacks.phantom_delay_attack import (
    PhantomDelayAttackSuite,
    PhantomDelayConfig,
    DelayAttackResult,
    to_network_attack_results,
)
from vesper.attacks.network_attacks import NetworkTarget


def check_port(host: str, port: int, timeout: float = 1.0) -> bool:
    """Check if a TCP port is reachable."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def start_qemu_device(port: int = 15011, device_type: str = "smart_light") -> subprocess.Popen:
    """Start a QEMU firmware device for testing."""
    firmware_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "vesper", "firmware", "samples"
    )
    elf_path = os.path.join(firmware_dir, f"{device_type}.elf")

    if not os.path.exists(elf_path):
        print(f"  ⚠ Firmware not found: {elf_path}")
        print(f"  → Trying sensor_firmware.elf as fallback...")
        elf_path = os.path.join(firmware_dir, "sensor_firmware.elf")
        if not os.path.exists(elf_path):
            print(f"  ✗ No firmware found. Run: cd vesper/firmware/samples && make")
            return None

    cmd = [
        "qemu-system-arm",
        "-M", "lm3s6965evb",
        "-nographic",
        "-serial", f"tcp::{port},server,nowait",
        "-kernel", elf_path,
    ]

    print(f"  Starting QEMU: {device_type} on port {port}...")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)  # Wait for QEMU to start

    if check_port("127.0.0.1", port):
        print(f"  ✓ QEMU device ready on port {port}")
        return proc
    else:
        print(f"  ✗ QEMU device failed to start")
        proc.kill()
        return None


def main():
    parser = argparse.ArgumentParser(
        description="VESPER Phantom-Delay Attack Demo (Fu et al., DSN 2022)"
    )
    parser.add_argument(
        "--host", type=str, default="127.0.0.1",
        help="Firmware device host (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=15011,
        help="Firmware device UART TCP port (default: 15011)"
    )
    parser.add_argument(
        "--mqtt-host", type=str, default="127.0.0.1",
        help="MQTT broker host (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--mqtt-port", type=int, default=1883,
        help="MQTT broker port (default: 1883)"
    )
    parser.add_argument(
        "--delay", type=float, default=30.0,
        help="Delay duration in seconds (default: 30.0)"
    )
    parser.add_argument(
        "--use-docker", action="store_true",
        help="Start firmware in Docker container"
    )
    parser.add_argument(
        "--use-qemu", action="store_true",
        help="Start a local QEMU instance"
    )
    parser.add_argument(
        "--device-type", type=str, default="smart_light",
        choices=["smart_light", "motion_sensor", "temperature_sensor",
                 "humidity_sensor", "door_sensor", "smart_plug"],
        help="Device type to test (default: smart_light)"
    )
    parser.add_argument(
        "--output-dir", type=str, default="results/phantom_delay",
        help="Output directory (default: results/phantom_delay)"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose logging"
    )
    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    print()
    print("=" * 70)
    print("  VESPER — Phantom-Delay Attack Demo")
    print("  Reproducing: Fu et al., IEEE/IFIP DSN 2022")
    print("  'IoT Phantom-Delay Attacks: Demystifying and Exploiting")
    print("   IoT Timeout Behaviors'")
    print("=" * 70)
    print()

    qemu_proc = None

    # Start QEMU if requested
    if args.use_qemu:
        qemu_proc = start_qemu_device(args.port, args.device_type)
        if qemu_proc is None:
            print("Failed to start QEMU. Exiting.")
            sys.exit(1)

    # Check device connectivity
    print(f"  Checking device at {args.host}:{args.port}...")
    if check_port(args.host, args.port):
        print(f"  ✓ Device reachable\n")
    else:
        if not args.use_qemu and not args.use_docker:
            print(f"  ⚠ Device not reachable at {args.host}:{args.port}")
            print(f"  → Starting local QEMU instance...")
            qemu_proc = start_qemu_device(args.port, args.device_type)
            if qemu_proc is None:
                print(
                    "\n  No firmware device available. "
                    "Running in simulation-only mode...\n"
                )

    # Configure target
    target = NetworkTarget(
        mqtt_host=args.mqtt_host,
        mqtt_port=args.mqtt_port,
        devices=[(args.host, args.port)],
        gateway_ip="172.20.0.1",
        subnet="172.20.0.0/24",
    )

    # Run attacks
    print(f"  Delay duration: {args.delay}s")
    print(f"  Device type:    {args.device_type}")
    print(f"  Target:         {args.host}:{args.port}")
    print()

    suite = PhantomDelayAttackSuite()

    try:
        start_time = time.time()
        results = suite.run_all_attacks(target, delay_seconds=args.delay)
        elapsed = time.time() - start_time

        # Print report
        suite.print_report(results)

        # Summary
        total = len(results)
        successful = sum(1 for r in results if r.success)
        cvss_scores = [r.cvss_score for r in results if r.cvss_score > 0]
        mean_cvss = sum(cvss_scores) / len(cvss_scores) if cvss_scores else 0

        print(f"\n  Execution time: {elapsed:.1f}s")
        print(f"  Success rate:   {successful}/{total} ({successful/total*100:.0f}%)")
        print(f"  Mean CVSS:      {mean_cvss:.1f}")

        # Export results
        os.makedirs(args.output_dir, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_file = os.path.join(
            args.output_dir,
            f"phantom_delay_{args.device_type}_{timestamp}.json"
        )
        suite.export_results(results, output_file)
        print(f"\n  Results exported to: {output_file}")

        # Export summary
        summary = {
            "attack_paper": "Fu et al., IoT Phantom-Delay Attacks, DSN 2022",
            "device_type": args.device_type,
            "delay_seconds": args.delay,
            "total_attacks": total,
            "successful": successful,
            "success_rate": successful / total,
            "mean_cvss": mean_cvss,
            "execution_time_seconds": elapsed,
            "timestamp": timestamp,
            "attack_variants": {
                "state_update_delay": sum(
                    1 for r in results
                    if r.variant.value == "state_update_delay" and r.success
                ),
                "erroneous_execution": sum(
                    1 for r in results
                    if r.variant.value == "erroneous_execution" and r.success
                ),
                "routine_invalidation": sum(
                    1 for r in results
                    if r.variant.value == "routine_invalidation" and r.success
                ),
                "action_reorder": sum(
                    1 for r in results
                    if r.variant.value == "action_reorder" and r.success
                ),
            },
        }
        summary_file = os.path.join(
            args.output_dir,
            f"phantom_delay_summary_{timestamp}.json"
        )
        with open(summary_file, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"  Summary exported to: {summary_file}")

    finally:
        # Cleanup QEMU
        if qemu_proc:
            print("\n  Cleaning up QEMU process...")
            qemu_proc.kill()
            qemu_proc.wait()

    print("\n  Done.\n")


if __name__ == "__main__":
    main()
