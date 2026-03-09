#!/usr/bin/env python3
"""
VESPER-Soft Baseline Runner

Runs the same attack suite against the VESPER-Soft (software-only) emulator
and captures pcap files, producing a parallel dataset for fidelity comparison.

This demonstrates that FIL (QEMU) produces more realistic network traffic
than pure software emulation.

Usage:
    python scripts/run_soft_baseline.py
    python scripts/run_soft_baseline.py --output-dir results/pcap_soft
"""

import argparse
import json
import logging
import platform
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent))

from vesper.firmware.software_emulator import SoftwareEmulator
from vesper.attacks.firmware_attacks import (
    FirmwareAttackFramework,
    FirmwareTarget,
    AttackResult,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("soft_baseline")


def default_loopback() -> str:
    return "lo0" if platform.system() == "Darwin" else "lo"


def start_capture(interface: str, pcap_path: str, bpf: str):
    tshark = shutil.which("tshark") or shutil.which("dumpcap")
    if not tshark:
        return None
    Path(pcap_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [tshark, "-i", interface, "-w", pcap_path]
    if bpf:
        cmd += ["-f", bpf]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def stop_capture(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def count_packets(pcap_path: str) -> int:
    tshark = shutil.which("tshark")
    if not tshark or not Path(pcap_path).exists():
        return 0
    try:
        result = subprocess.run(
            [tshark, "-r", pcap_path, "-T", "fields", "-e", "frame.number"],
            capture_output=True, text=True, timeout=10,
        )
        return len([l for l in result.stdout.strip().split("\n") if l.strip()])
    except Exception:
        return 0


def main():
    parser = argparse.ArgumentParser(description="VESPER-Soft Baseline")
    parser.add_argument("--output-dir", type=str, default="results/pcap_soft")
    parser.add_argument("--interface", type=str, default="")
    parser.add_argument("--base-port", type=int, default=15060)
    parser.add_argument("--benign-commands", type=int, default=20)
    args = parser.parse_args()

    interface = args.interface or default_loopback()
    output_dir = args.output_dir

    device_types = [
        "smart_light", "motion_sensor", "temperature_sensor",
        "humidity_sensor", "door_sensor", "smart_plug",
    ]

    print()
    print("╔" + "═" * 68 + "╗")
    print("║" + " VESPER-Soft Baseline Capture ".center(68) + "║")
    print("╚" + "═" * 68 + "╝")

    fw_framework = FirmwareAttackFramework()
    all_records = []

    for i, dtype in enumerate(device_types):
        port = args.base_port + i
        device_dir = Path(output_dir) / "firmware" / dtype
        device_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n{'━' * 70}")
        print(f"  VESPER-Soft: {dtype} on port {port}")
        print(f"{'━' * 70}")

        emu = SoftwareEmulator(port=port, device_type=dtype)
        emu.start()
        time.sleep(0.5)

        bpf = f"tcp port {port}"
        target = FirmwareTarget(host="127.0.0.1", port=port, device_type=dtype)

        try:
            # Benign baseline
            print(f"  Capturing benign baseline...")
            benign_pcap = str(device_dir / "baseline_benign.pcap")
            cap = start_capture(interface, benign_pcap, bpf)
            time.sleep(0.3)

            for cmd in ["IDENTIFY", "STATUS", "GET_VALUE", "LIGHT ON", "STATUS",
                        "LIGHT OFF", "GET_TEMP", "IDENTIFY", "STATUS", "GET_VALUE"] * 2:
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(3.0)
                    sock.connect(("127.0.0.1", port))
                    sock.sendall((cmd + "\n").encode())
                    sock.recv(4096)
                    sock.close()
                except Exception:
                    pass
                time.sleep(0.2)

            time.sleep(0.3)
            stop_capture(cap)

            pkts = count_packets(benign_pcap)
            print(f"  [OK] Benign: {pkts} pkts")
            all_records.append({
                "pcap_path": benign_pcap,
                "attack_name": "baseline_benign",
                "attack_category": "benign",
                "attack_suite": "benign",
                "device_type": dtype,
                "label": 0,
                "packet_count": pkts,
            })

            # Per-attack captures
            for attack_idx, attack_fn in enumerate(fw_framework.attacks):
                attack_name = attack_fn.__name__
                pcap_file = str(device_dir / f"{attack_name}.pcap")

                print(f"  [{attack_idx + 1:02d}/{len(fw_framework.attacks)}] "
                      f"{attack_name}...", end="", flush=True)

                cap = start_capture(interface, pcap_file, bpf)
                time.sleep(0.3)

                try:
                    result = fw_framework._timed_attack(attack_fn, target)
                except Exception:
                    result = AttackResult(
                        attack_name=attack_name, category="fuzzing",
                        severity="info", success=False, description="Exception",
                    )

                time.sleep(0.3)
                stop_capture(cap)

                pkts = count_packets(pcap_file)
                status = "OK" if result.success else "--"
                print(f" [{status}] {pkts} pkts / {result.duration_ms:.0f}ms")

                all_records.append({
                    "pcap_path": pcap_file,
                    "attack_name": attack_name,
                    "attack_category": result.category.value if hasattr(result.category, 'value') else str(result.category),
                    "attack_suite": "firmware",
                    "device_type": dtype,
                    "label": 1,
                    "success": result.success,
                    "packet_count": pkts,
                    "timestamp": datetime.now().isoformat(),
                })

                # Save meta
                meta_file = str(device_dir / f"{attack_name}.json")
                with open(meta_file, "w") as f:
                    json.dump({
                        "attack_name": result.attack_name,
                        "success": result.success,
                        "duration_ms": result.duration_ms,
                        "device_type": dtype,
                        "emulator": "vesper-soft",
                    }, f, indent=2)

                time.sleep(0.2)

        finally:
            emu.stop()

    # Write manifest
    manifest = {
        "generated": datetime.now().isoformat(),
        "emulator": "vesper-soft",
        "total_captures": len(all_records),
        "captures": all_records,
    }
    manifest_path = os.path.join(output_dir, "manifest.json")
    os.makedirs(output_dir, exist_ok=True)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n  Total captures: {len(all_records)}")
    print(f"  Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
