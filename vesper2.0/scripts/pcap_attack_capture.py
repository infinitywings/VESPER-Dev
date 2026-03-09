#!/usr/bin/env python3
"""
VESPER Per-Attack Pcap Capture

Runs each attack individually with a dedicated Wireshark/tshark capture,
producing one .pcap file per attack per device type.  This gives labelled
network traces for ML feature extraction.

Output structure:
    results/pcap/
      firmware/
        smart_light/
          attack_buffer_overflow_cmd.pcap
          attack_buffer_overflow_cmd.json    # attack result metadata
          ...
      network/
        attack_unauthorized_subscribe.pcap
        attack_unauthorized_subscribe.json
        ...
      benign/
        baseline_smart_light.pcap
        baseline_smart_light.json
      manifest.json   # index of all captures

Usage:
    python scripts/pcap_attack_capture.py
    python scripts/pcap_attack_capture.py --firmware-only
    python scripts/pcap_attack_capture.py --network-only
    python scripts/pcap_attack_capture.py --device-type smart_light
    python scripts/pcap_attack_capture.py --output-dir results/pcap
    python scripts/pcap_attack_capture.py --use-docker
    python scripts/pcap_attack_capture.py --interface lo0
"""

import argparse
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from vesper.firmware.device_firmware_manager import (
    DeviceFirmwareManager,
    DeviceType,
)
from vesper.attacks.firmware_attacks import (
    AttackResult,
    FirmwareAttackFramework,
    FirmwareTarget,
)
from vesper.attacks.network_attacks import (
    NetworkAttackFramework,
    NetworkAttackResult,
    NetworkTarget,
)
from vesper.network.home_network import (
    NetworkConfig,
    NetworkMode,
    SimulatedHomeNetwork,
    WiresharkConfig,
    WiresharkLiveCapture,
    Protocol,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pcap_capture")


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class CaptureRecord:
    """Metadata for a single pcap capture file."""
    pcap_path: str
    attack_name: str
    attack_category: str
    attack_suite: str           # "firmware", "network", or "benign"
    device_type: str
    success: bool
    severity: str = ""
    duration_ms: float = 0.0
    pcap_size_bytes: int = 0
    packet_count: int = 0
    timestamp: str = ""
    cve_reference: str = ""
    label: int = 1              # 1 = attack, 0 = benign


# ─── Helpers ──────────────────────────────────────────────────────────────────

def default_loopback() -> str:
    """Return OS-appropriate loopback interface."""
    return "lo0" if platform.system() == "Darwin" else "lo"


def wait_for_port(host: str, port: int, timeout: float = 15.0) -> bool:
    """Wait until firmware is responsive on the given TCP port."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect((host, port))
            time.sleep(0.5)
            sock.sendall(b"IDENTIFY\n")
            buf = b""
            read_deadline = time.time() + 3.0
            while time.time() < read_deadline:
                try:
                    chunk = sock.recv(4096)
                    if chunk:
                        buf += chunk
                        if b"VESPER" in buf or b"DEVICE:" in buf:
                            sock.close()
                            return True
                    else:
                        break
                except socket.timeout:
                    break
                time.sleep(0.1)
            sock.close()
            time.sleep(0.5)
        except (socket.error, OSError):
            time.sleep(0.5)
    return False


def launch_qemu_native(firmware_path: str, tcp_port: int) -> subprocess.Popen:
    """Launch QEMU natively."""
    cmd = [
        "qemu-system-arm",
        "-machine", "lm3s6965evb",
        "-cpu", "cortex-m3",
        "-nographic",
        "-monitor", "none",
        "-kernel", firmware_path,
        "-serial", f"tcp::{tcp_port},server,nowait",
    ]
    return subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def launch_qemu_container(
    firmware_path: str,
    tcp_port: int,
    container_name: str,
    docker_image: str = "vesper-qemu-arm:latest",
) -> str:
    """Launch QEMU in Docker. Returns container ID or empty string."""
    subprocess.run(
        ["docker", "rm", "-f", container_name],
        capture_output=True, timeout=10,
    )
    cmd = [
        "docker", "run", "--rm", "-d",
        "--name", container_name,
        "-p", f"{tcp_port}:15000",
        "-v", f"{firmware_path}:/firmware/device.elf:ro",
        docker_image,
        "qemu-system-arm",
        "-machine", "lm3s6965evb",
        "-cpu", "cortex-m3",
        "-nographic",
        "-kernel", "/firmware/device.elf",
        "-serial", "tcp::15000,server,nowait",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        logger.error(f"Docker launch failed: {result.stderr}")
        return ""
    return result.stdout.strip()[:12]


def stop_container(name: str):
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10)


def stop_process(proc: subprocess.Popen):
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


def make_capture(
    interface: str,
    port: int,
    pcap_path: str,
    bpf_filter: str = "",
) -> WiresharkLiveCapture:
    """Create a WiresharkLiveCapture configured for a specific pcap path."""
    pcap_dir = str(Path(pcap_path).parent)
    ws_config = WiresharkConfig(
        enabled=True,
        capture_interface=interface,
        capture_filter=bpf_filter or f"tcp port {port}",
        pcap_dir=pcap_dir,
    )
    net_config = NetworkConfig(
        mode=NetworkMode.HOST,
        wireshark=ws_config,
    )
    cap = WiresharkLiveCapture(net_config)
    # Override the auto-generated pcap path to use our exact filename
    cap._pcap_path_override = pcap_path
    return cap


def count_pcap_packets(pcap_path: str) -> int:
    """Count packets in a pcap file using tshark."""
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


def start_capture_to_file(
    interface: str,
    pcap_path: str,
    bpf_filter: str,
) -> Optional[subprocess.Popen]:
    """Start a tshark capture writing directly to the given pcap path."""
    tshark = shutil.which("tshark") or shutil.which("dumpcap")
    if not tshark:
        logger.error("No capture tool (tshark/dumpcap) found")
        return None

    Path(pcap_path).parent.mkdir(parents=True, exist_ok=True)

    cmd = [tshark, "-i", interface, "-w", pcap_path]
    if bpf_filter:
        cmd += ["-f", bpf_filter]

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        return proc
    except Exception as e:
        logger.error(f"Failed to start capture: {e}")
        return None


def stop_capture(proc: subprocess.Popen) -> None:
    """Gracefully stop a tshark/dumpcap process."""
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def generate_benign_traffic(host: str, port: int, num_commands: int = 20) -> List[str]:
    """
    Send benign (normal) commands to firmware and collect responses.
    This generates the baseline "no attack" traffic.
    """
    benign_commands = [
        "IDENTIFY",
        "STATUS",
        "GET_VALUE",
        "LIGHT ON",
        "LIGHT OFF",
        "STATUS",
        "GET_VALUE",
        "LIGHT ON",
        "STATUS",
        "LIGHT OFF",
        "STATUS",
        "GET_VALUE",
        "IDENTIFY",
        "STATUS",
        "LIGHT ON",
        "GET_VALUE",
        "LIGHT OFF",
        "GET_VALUE",
        "STATUS",
        "IDENTIFY",
    ]
    responses = []
    for i in range(min(num_commands, len(benign_commands))):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3.0)
            sock.connect((host, port))
            sock.sendall((benign_commands[i] + "\n").encode())
            buf = b""
            deadline = time.time() + 2.0
            while time.time() < deadline:
                try:
                    chunk = sock.recv(4096)
                    if chunk:
                        buf += chunk
                        if buf.endswith(b"\n") and len(buf) > 2:
                            break
                    else:
                        break
                except socket.timeout:
                    break
            sock.close()
            responses.append(buf.decode("utf-8", errors="replace").strip())
        except Exception as e:
            responses.append(f"ERROR:{e}")
        time.sleep(0.3)
    return responses


# ─── Per-attack capture runners ───────────────────────────────────────────────

def capture_firmware_attacks(
    device_types: List[DeviceType],
    output_dir: str,
    interface: str,
    base_port: int,
    use_docker: bool,
    benign_commands: int,
) -> List[CaptureRecord]:
    """
    For each device type:
      1. Compile + launch firmware
      2. Capture benign baseline traffic
      3. Run each firmware attack with its own pcap capture
    """
    workspace = str(Path(__file__).parent.parent)
    fw_manager = DeviceFirmwareManager(workspace)
    fw_framework = FirmwareAttackFramework()
    records: List[CaptureRecord] = []

    for i, dtype in enumerate(device_types):
        port = base_port + i
        type_name = dtype.value
        device_dir = Path(output_dir) / "firmware" / type_name
        device_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n{'━' * 70}")
        print(f"  Device: {type_name}  |  Port: {port}")
        print(f"{'━' * 70}")

        # Compile firmware
        try:
            fw_path = fw_manager.compile_firmware(dtype)
            print(f"  [OK] Compiled: {fw_path}")
        except Exception as e:
            print(f"  [!!] Compile failed ({e}), falling back to generic")
            try:
                fw_path = fw_manager.compile_firmware(DeviceType.GENERIC)
            except Exception:
                print(f"  [!!] Generic compile also failed — skipping {type_name}")
                continue

        # Launch firmware
        proc = None
        container_name = ""
        if use_docker:
            container_name = f"vesper-pcap-{type_name}"
            cid = launch_qemu_container(str(fw_path), port, container_name)
            if not cid:
                print(f"  [!!] Docker failed, trying native QEMU")
                proc = launch_qemu_native(str(fw_path), port)
        else:
            proc = launch_qemu_native(str(fw_path), port)
            print(f"  [OK] QEMU started (PID {proc.pid})")

        # Wait for firmware
        print(f"  Waiting for firmware to boot...", end="", flush=True)
        if not wait_for_port("127.0.0.1", port, timeout=15.0):
            print(" TIMEOUT — skipping")
            if proc:
                stop_process(proc)
            if container_name:
                stop_container(container_name)
            continue
        print(" READY")

        bpf = f"tcp port {port}"

        try:
            # ── Benign baseline capture ──────────────────────────────────
            print(f"\n  Capturing benign baseline ({benign_commands} commands)...")
            benign_pcap = str(device_dir / "baseline_benign.pcap")
            cap_proc = start_capture_to_file(interface, benign_pcap, bpf)
            time.sleep(0.5)

            responses = generate_benign_traffic("127.0.0.1", port, benign_commands)
            time.sleep(0.5)
            stop_capture(cap_proc)

            benign_size = Path(benign_pcap).stat().st_size if Path(benign_pcap).exists() else 0
            benign_pkts = count_pcap_packets(benign_pcap)
            print(f"  [OK] Benign: {benign_pcap} ({benign_size:,} bytes, {benign_pkts} pkts)")

            records.append(CaptureRecord(
                pcap_path=benign_pcap,
                attack_name="baseline_benign",
                attack_category="benign",
                attack_suite="benign",
                device_type=type_name,
                success=True,
                severity="none",
                duration_ms=0,
                pcap_size_bytes=benign_size,
                packet_count=benign_pkts,
                timestamp=datetime.now().isoformat(),
                label=0,
            ))

            # Save benign metadata
            benign_meta = {
                "commands_sent": benign_commands,
                "responses": responses[:5],  # first 5 for sample
                "pcap_path": benign_pcap,
                "packet_count": benign_pkts,
            }
            with open(str(device_dir / "baseline_benign.json"), "w") as f:
                json.dump(benign_meta, f, indent=2)

            # ── Per-attack captures ──────────────────────────────────────
            target = FirmwareTarget(
                host="127.0.0.1",
                port=port,
                device_type=type_name,
            )

            for attack_idx, attack_fn in enumerate(fw_framework.attacks):
                attack_name = attack_fn.__name__
                pcap_file = str(device_dir / f"{attack_name}.pcap")
                meta_file = str(device_dir / f"{attack_name}.json")

                print(f"  [{attack_idx + 1:02d}/{len(fw_framework.attacks)}] "
                      f"{attack_name}...", end="", flush=True)

                # Start fresh capture for this attack
                cap_proc = start_capture_to_file(interface, pcap_file, bpf)
                time.sleep(0.3)  # Let tshark init

                # Run the single attack
                start_t = time.time()
                try:
                    result: AttackResult = fw_framework._timed_attack(
                        attack_fn, target
                    )
                except Exception as e:
                    result = AttackResult(
                        attack_name=attack_name,
                        category=fw_framework.attacks[0].__name__,
                        severity="info",
                        success=False,
                        description=f"Exception: {e}",
                        duration_ms=(time.time() - start_t) * 1000,
                    )

                time.sleep(0.3)  # Let final packets arrive
                stop_capture(cap_proc)

                # Measure pcap
                pcap_size = Path(pcap_file).stat().st_size if Path(pcap_file).exists() else 0
                pkt_count = count_pcap_packets(pcap_file)

                status = "OK" if result.success else "--"
                print(f" [{status}] {pcap_size:,}B / {pkt_count} pkts / "
                      f"{result.duration_ms:.0f}ms")

                # Record
                records.append(CaptureRecord(
                    pcap_path=pcap_file,
                    attack_name=attack_name,
                    attack_category=result.category.value if hasattr(result.category, 'value') else str(result.category),
                    attack_suite="firmware",
                    device_type=type_name,
                    success=result.success,
                    severity=result.severity.value if hasattr(result.severity, 'value') else str(result.severity),
                    duration_ms=result.duration_ms,
                    pcap_size_bytes=pcap_size,
                    packet_count=pkt_count,
                    timestamp=datetime.now().isoformat(),
                    cve_reference=result.cve_reference,
                    label=1,
                ))

                # Save per-attack metadata
                meta = {
                    "attack_name": result.attack_name,
                    "category": result.category.value if hasattr(result.category, 'value') else str(result.category),
                    "severity": result.severity.value if hasattr(result.severity, 'value') else str(result.severity),
                    "success": result.success,
                    "description": result.description,
                    "evidence": result.evidence[:5],
                    "impact": result.impact,
                    "mitigation": result.mitigation,
                    "cve_reference": result.cve_reference,
                    "duration_ms": result.duration_ms,
                    "pcap_path": pcap_file,
                    "pcap_size_bytes": pcap_size,
                    "packet_count": pkt_count,
                    "device_type": type_name,
                }
                with open(meta_file, "w") as f:
                    json.dump(meta, f, indent=2)

                time.sleep(0.3)  # Brief pause between attacks

        finally:
            # Cleanup firmware instance
            if proc:
                stop_process(proc)
            if container_name:
                stop_container(container_name)

    return records


def capture_network_attacks(
    output_dir: str,
    interface: str,
    mqtt_port: int,
    device_ports: List[int],
) -> List[CaptureRecord]:
    """
    Run each network attack with a dedicated pcap capture.
    """
    net_dir = Path(output_dir) / "network"
    net_dir.mkdir(parents=True, exist_ok=True)
    records: List[CaptureRecord] = []

    print(f"\n{'━' * 70}")
    print(f"  Network Attack Capture  |  MQTT port: {mqtt_port}")
    print(f"{'━' * 70}")

    # Build target
    target = NetworkTarget(
        mqtt_host="127.0.0.1",
        mqtt_port=mqtt_port,
        devices=[("127.0.0.1", p) for p in device_ports],
    )

    # Start simulated home network
    net_config = NetworkConfig(mqtt_port=mqtt_port)
    home_network = SimulatedHomeNetwork(net_config)

    try:
        home_network.start()
        print(f"  [OK] Home network started")

        # Add devices
        for idx, port in enumerate(device_ports):
            dev = home_network.add_device(
                f"net-device-{idx}",
                protocol=Protocol.TCP,
                tcp_port=port,
            )
            home_network.connect_device(dev.device_id)

        time.sleep(1)

        # Collect all attacks from the framework
        net_framework = NetworkAttackFramework()
        all_attacks = []

        # MQTT
        for fn in [
            net_framework.mqtt_suite.attack_unauthorized_subscribe,
            net_framework.mqtt_suite.attack_mqtt_message_injection,
            net_framework.mqtt_suite.attack_mqtt_topic_hijack,
        ]:
            all_attacks.append(("mqtt", fn))

        # TCP
        for fn in [
            net_framework.tcp_suite.attack_tcp_connection_hijack,
            net_framework.tcp_suite.attack_tcp_mitm_proxy,
            net_framework.tcp_suite.attack_tcp_flood,
        ]:
            all_attacks.append(("tcp", fn))

        # Protocol
        for fn in [
            net_framework.protocol_suite.attack_zigbee_replay,
            net_framework.protocol_suite.attack_zigbee_key_extraction,
            net_framework.protocol_suite.attack_protocol_downgrade,
        ]:
            all_attacks.append(("protocol", fn))

        # Infrastructure
        for fn in [
            net_framework.infra_suite.attack_arp_spoof,
            net_framework.infra_suite.attack_dns_poison,
            net_framework.infra_suite.attack_deauth,
            net_framework.infra_suite.attack_evil_twin,
        ]:
            all_attacks.append(("infrastructure", fn))

        # Traffic analysis
        all_attacks.append(("traffic_analysis",
                            net_framework.traffic_suite.attack_traffic_fingerprinting))

        # BPF for network captures — broader filter
        bpf = f"tcp port {mqtt_port}"
        if device_ports:
            port_filters = " or ".join(f"tcp port {p}" for p in device_ports)
            bpf = f"tcp port {mqtt_port} or {port_filters}"

        # ── Benign baseline ──────────────────────────────────────────
        print(f"\n  Capturing network benign baseline...")
        benign_pcap = str(net_dir / "baseline_benign.pcap")
        cap_proc = start_capture_to_file(interface, benign_pcap, bpf)
        time.sleep(0.5)

        # Generate benign MQTT traffic
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3.0)
            sock.connect(("127.0.0.1", mqtt_port))
            for topic in ["home/living/temp", "home/kitchen/light", "home/bedroom/motion"]:
                sock.sendall(f"PUB {topic} normal_value\n".encode())
                time.sleep(0.2)
            sock.close()
        except Exception:
            pass

        time.sleep(0.5)
        stop_capture(cap_proc)

        benign_size = Path(benign_pcap).stat().st_size if Path(benign_pcap).exists() else 0
        benign_pkts = count_pcap_packets(benign_pcap)
        print(f"  [OK] Benign: {benign_size:,}B / {benign_pkts} pkts")

        records.append(CaptureRecord(
            pcap_path=benign_pcap,
            attack_name="baseline_benign",
            attack_category="benign",
            attack_suite="benign",
            device_type="network",
            success=True,
            severity="none",
            pcap_size_bytes=benign_size,
            packet_count=benign_pkts,
            timestamp=datetime.now().isoformat(),
            label=0,
        ))

        # ── Per-attack captures ──────────────────────────────────────
        for attack_idx, (suite_name, attack_fn) in enumerate(all_attacks):
            attack_name = attack_fn.__name__
            pcap_file = str(net_dir / f"{attack_name}.pcap")
            meta_file = str(net_dir / f"{attack_name}.json")

            print(f"  [{attack_idx + 1:02d}/{len(all_attacks)}] "
                  f"{attack_name}...", end="", flush=True)

            cap_proc = start_capture_to_file(interface, pcap_file, bpf)
            time.sleep(0.3)

            start_t = time.time()
            try:
                result: NetworkAttackResult = net_framework._timed(attack_fn, target)
            except Exception as e:
                from vesper.attacks.network_attacks import NetworkAttackCategory
                result = NetworkAttackResult(
                    attack_name=attack_name,
                    category=NetworkAttackCategory.TRAFFIC_ANALYSIS,
                    success=False,
                    description=f"Exception: {e}",
                    duration_ms=(time.time() - start_t) * 1000,
                )

            time.sleep(0.3)
            stop_capture(cap_proc)

            pcap_size = Path(pcap_file).stat().st_size if Path(pcap_file).exists() else 0
            pkt_count = count_pcap_packets(pcap_file)

            status = "OK" if result.success else "--"
            print(f" [{status}] {pcap_size:,}B / {pkt_count} pkts / "
                  f"{result.duration_ms:.0f}ms")

            records.append(CaptureRecord(
                pcap_path=pcap_file,
                attack_name=attack_name,
                attack_category=result.category.value if hasattr(result.category, 'value') else str(result.category),
                attack_suite="network",
                device_type="network",
                success=result.success,
                severity="",
                duration_ms=result.duration_ms,
                pcap_size_bytes=pcap_size,
                packet_count=pkt_count,
                timestamp=datetime.now().isoformat(),
                label=1,
            ))

            meta = {
                "attack_name": result.attack_name,
                "category": result.category.value if hasattr(result.category, 'value') else str(result.category),
                "success": result.success,
                "description": result.description,
                "evidence": result.evidence[:5],
                "packets_sent": result.packets_sent,
                "packets_captured": result.packets_captured,
                "duration_ms": result.duration_ms,
                "pcap_path": pcap_file,
                "pcap_size_bytes": pcap_size,
                "packet_count": pkt_count,
            }
            with open(meta_file, "w") as f:
                json.dump(meta, f, indent=2)

            time.sleep(0.3)

    finally:
        home_network.stop()
        print(f"  [OK] Home network stopped")

    return records


# ─── Manifest generation ─────────────────────────────────────────────────────

def write_manifest(records: List[CaptureRecord], output_dir: str) -> str:
    """Write a manifest.json indexing all captured pcap files."""
    manifest = {
        "generated": datetime.now().isoformat(),
        "total_captures": len(records),
        "firmware_captures": sum(1 for r in records if r.attack_suite == "firmware"),
        "network_captures": sum(1 for r in records if r.attack_suite == "network"),
        "benign_captures": sum(1 for r in records if r.attack_suite == "benign"),
        "total_pcap_bytes": sum(r.pcap_size_bytes for r in records),
        "total_packets": sum(r.packet_count for r in records),
        "captures": [],
    }

    for r in records:
        manifest["captures"].append({
            "pcap_path": r.pcap_path,
            "attack_name": r.attack_name,
            "attack_category": r.attack_category,
            "attack_suite": r.attack_suite,
            "device_type": r.device_type,
            "success": r.success,
            "severity": r.severity,
            "duration_ms": r.duration_ms,
            "pcap_size_bytes": r.pcap_size_bytes,
            "packet_count": r.packet_count,
            "timestamp": r.timestamp,
            "cve_reference": r.cve_reference,
            "label": r.label,
        })

    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    return manifest_path


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="VESPER Per-Attack Pcap Capture"
    )
    parser.add_argument(
        "--firmware-only", action="store_true",
        help="Only run firmware attacks"
    )
    parser.add_argument(
        "--network-only", action="store_true",
        help="Only run network attacks"
    )
    parser.add_argument(
        "--device-type", type=str, default=None,
        help="Target a single device type (e.g., smart_light)"
    )
    parser.add_argument(
        "--output-dir", type=str, default="results/pcap",
        help="Output directory for pcap files (default: results/pcap)"
    )
    parser.add_argument(
        "--interface", type=str, default="",
        help="Capture interface (default: auto-detect loopback)"
    )
    parser.add_argument(
        "--base-port", type=int, default=15030,
        help="Base TCP port for QEMU (default: 15030)"
    )
    parser.add_argument(
        "--mqtt-port", type=int, default=11883,
        help="MQTT broker port (default: 11883)"
    )
    parser.add_argument(
        "--use-docker", action="store_true",
        help="Use Docker instead of native QEMU"
    )
    parser.add_argument(
        "--benign-commands", type=int, default=20,
        help="Number of benign commands per baseline capture (default: 20)"
    )
    args = parser.parse_args()

    # Validate capture tool
    tool = shutil.which("tshark") or shutil.which("dumpcap")
    if not tool:
        print("ERROR: tshark or dumpcap required. Install Wireshark CLI tools.")
        sys.exit(1)

    interface = args.interface or default_loopback()
    output_dir = args.output_dir

    print()
    print("╔" + "═" * 68 + "╗")
    print("║" + " VESPER Per-Attack Pcap Capture ".center(68) + "║")
    print("║" + f" Interface: {interface}  |  Output: {output_dir} ".center(68) + "║")
    print("╚" + "═" * 68 + "╝")

    all_records: List[CaptureRecord] = []

    # Device types
    if args.device_type:
        try:
            device_types = [DeviceType(args.device_type)]
        except ValueError:
            print(f"Unknown device type: {args.device_type}")
            print(f"Available: {[d.value for d in DeviceType]}")
            sys.exit(1)
    else:
        device_types = [
            DeviceType.MOTION_SENSOR,
            DeviceType.TEMPERATURE_SENSOR,
            DeviceType.SMART_LIGHT,
            DeviceType.HUMIDITY_SENSOR,
            DeviceType.DOOR_SENSOR,
            DeviceType.SMART_PLUG,
        ]

    # ── Firmware attacks ─────────────────────────────────────────────
    if not args.network_only:
        fw_records = capture_firmware_attacks(
            device_types=device_types,
            output_dir=output_dir,
            interface=interface,
            base_port=args.base_port,
            use_docker=args.use_docker,
            benign_commands=args.benign_commands,
        )
        all_records.extend(fw_records)

    # ── Network attacks ──────────────────────────────────────────────
    if not args.firmware_only:
        # Use firmware ports if they were launched (they're stopped by now though)
        # For network attacks, the simulated home network runs its own broker
        net_records = capture_network_attacks(
            output_dir=output_dir,
            interface=interface,
            mqtt_port=args.mqtt_port,
            device_ports=[],
        )
        all_records.extend(net_records)

    # ── Manifest ─────────────────────────────────────────────────────
    manifest_path = write_manifest(all_records, output_dir)

    # ── Summary ──────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  CAPTURE SUMMARY")
    print("=" * 70)

    fw_count = sum(1 for r in all_records if r.attack_suite == "firmware")
    net_count = sum(1 for r in all_records if r.attack_suite == "network")
    benign_count = sum(1 for r in all_records if r.attack_suite == "benign")
    total_bytes = sum(r.pcap_size_bytes for r in all_records)
    total_pkts = sum(r.packet_count for r in all_records)

    print(f"  Firmware attack captures:  {fw_count}")
    print(f"  Network attack captures:   {net_count}")
    print(f"  Benign baseline captures:  {benign_count}")
    print(f"  Total pcap files:          {len(all_records)}")
    print(f"  Total pcap size:           {total_bytes:,} bytes ({total_bytes / 1024 / 1024:.1f} MB)")
    print(f"  Total packets:             {total_pkts:,}")
    print(f"  Manifest:                  {manifest_path}")
    print(f"  Output dir:                {output_dir}")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
