#!/usr/bin/env python3
"""
VESPER Attack Demo — Full Security Assessment

Demonstrates the complete attack framework:
1. Compiles per-device firmware
2. Launches QEMU firmware in Docker containers
3. Starts simulated home network with MQTT broker
4. Runs firmware attacks against each device type
5. Runs network attacks against the home network
6. Generates comprehensive security report

Usage:
    python scripts/run_attack_demo.py
    python scripts/run_attack_demo.py --firmware-only
    python scripts/run_attack_demo.py --network-only
    python scripts/run_attack_demo.py --device-type smart_light
"""

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from vesper.firmware.device_firmware_manager import (
    DeviceFirmwareManager,
    DeviceType,
    DeviceFirmwareConfig,
)
from vesper.attacks.firmware_attacks import (
    FirmwareAttackFramework,
    FirmwareTarget,
    AttackResult,
)
from vesper.attacks.network_attacks import (
    NetworkAttackFramework,
    NetworkTarget,
    NetworkAttackResult,
)
from vesper.network.home_network import (
    SimulatedHomeNetwork,
    NetworkConfig,
    Protocol,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("attack_demo")


# ─── Helper Functions ────────────────────────────────────────────────────

def wait_for_port(host: str, port: int, timeout: float = 15.0) -> bool:
    """Wait until a TCP port is accepting connections and firmware is responsive."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect((host, port))
            # Port is open — now verify firmware is responsive
            time.sleep(0.5)  # Let firmware boot and settle
            sock.sendall(b"IDENTIFY\n")
            # Read response in a loop (firmware sends byte-by-byte over UART)
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
                time.sleep(0.1)  # Small delay to let bytes accumulate
            sock.close()
            # Connected but no VESPER banner yet, retry
            time.sleep(0.5)
        except (socket.error, OSError):
            time.sleep(0.5)
    return False


def launch_qemu_container(
    firmware_path: str,
    tcp_port: int,
    container_name: str,
    docker_image: str = "vesper-qemu-arm:latest",
) -> str:
    """Launch a QEMU firmware in a Docker container."""
    # Stop existing container if any
    subprocess.run(
        ["docker", "rm", "-f", container_name],
        capture_output=True, timeout=10
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


def launch_qemu_native(firmware_path: str, tcp_port: int) -> subprocess.Popen:
    """Launch QEMU natively (no Docker) — for quick testing."""
    cmd = [
        "qemu-system-arm",
        "-machine", "lm3s6965evb",
        "-cpu", "cortex-m3",
        "-nographic",
        "-monitor", "none",
        "-kernel", firmware_path,
        "-serial", f"tcp::{tcp_port},server,nowait",
    ]
    
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return proc


def stop_container(container_name: str):
    """Stop and remove a Docker container."""
    subprocess.run(
        ["docker", "rm", "-f", container_name],
        capture_output=True, timeout=10
    )


# ─── Main Demo ──────────────────────────────────────────────────────────

def run_firmware_attacks_demo(
    device_types: list = None,
    use_docker: bool = False,
    base_port: int = 15020,
):
    """
    Run firmware attacks against each device type.
    """
    print("\n" + "=" * 80)
    print("  VESPER FIRMWARE ATTACK DEMO")
    print("=" * 80)
    
    workspace = str(Path(__file__).parent.parent)
    fw_manager = DeviceFirmwareManager(workspace)
    fw_attack = FirmwareAttackFramework()
    
    if device_types is None:
        device_types = [
            DeviceType.MOTION_SENSOR,
            DeviceType.TEMPERATURE_SENSOR,
            DeviceType.SMART_LIGHT,
            DeviceType.HUMIDITY_SENSOR,
            DeviceType.DOOR_SENSOR,
            DeviceType.SMART_PLUG,
        ]
    
    all_results = {}
    processes = []
    containers = []
    
    try:
        for i, dtype in enumerate(device_types):
            port = base_port + i
            type_name = dtype.value
            
            print(f"\n{'─' * 60}")
            print(f"  Device: {type_name} (port {port})")
            print(f"{'─' * 60}")
            
            # Compile firmware
            try:
                fw_path = fw_manager.compile_firmware(dtype)
                print(f"  ✓ Compiled: {fw_path}")
            except Exception as e:
                print(f"  ✗ Compile failed: {e}")
                # Use generic firmware as fallback
                fw_path = fw_manager.compile_firmware(DeviceType.GENERIC)
                print(f"  → Fallback to generic: {fw_path}")
            
            # Launch firmware
            if use_docker:
                container_name = f"vesper-attack-{type_name}"
                cid = launch_qemu_container(str(fw_path), port, container_name)
                if cid:
                    containers.append(container_name)
                    print(f"  ✓ Docker container: {cid}")
                else:
                    print(f"  ✗ Docker launch failed, trying native QEMU")
                    proc = launch_qemu_native(str(fw_path), port)
                    processes.append(proc)
            else:
                proc = launch_qemu_native(str(fw_path), port)
                processes.append(proc)
                print(f"  ✓ QEMU started (PID {proc.pid})")
            
            # Wait for firmware to boot
            print(f"  Waiting for firmware to boot...", end="", flush=True)
            if wait_for_port("127.0.0.1", port, timeout=10.0):
                print(" READY")
            else:
                print(" TIMEOUT — skipping")
                continue
            
            # Run attacks
            target = FirmwareTarget(
                host="127.0.0.1",
                port=port,
                device_type=type_name,
            )
            
            print(f"  Running {len(fw_attack.attacks)} firmware attacks...")
            results = fw_attack.run_all_attacks(target)
            all_results[type_name] = results
            
            # Quick summary
            successful = sum(1 for r in results if r.success)
            print(f"  Result: {successful}/{len(results)} attacks successful")
    
    finally:
        # Cleanup
        print("\n  Cleaning up...")
        for proc in processes:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        for name in containers:
            stop_container(name)
    
    return all_results


def run_network_attacks_demo(
    device_ports: list = None,
    mqtt_port: int = 1883,
):
    """
    Run network attacks against the simulated home network.
    """
    print("\n" + "=" * 80)
    print("  VESPER NETWORK ATTACK DEMO")
    print("=" * 80)
    
    # Start simulated home network
    net_config = NetworkConfig(mqtt_port=mqtt_port)
    home_network = SimulatedHomeNetwork(net_config)
    
    try:
        home_network.start()
        print(f"  ✓ Home network started (MQTT on port {mqtt_port})")
        
        # Add simulated devices
        if device_ports:
            for i, port in enumerate(device_ports):
                dev = home_network.add_device(
                    f"device-{i}",
                    protocol=Protocol.TCP,
                    tcp_port=port,
                )
                home_network.connect_device(dev.device_id)
                print(f"  ✓ Device {dev.device_id} connected at {dev.ip_address}")
        
        time.sleep(1)  # Let network settle
        
        # Configure attack target
        target = NetworkTarget(
            mqtt_host="127.0.0.1",
            mqtt_port=mqtt_port,
            devices=[("127.0.0.1", p) for p in (device_ports or [])],
        )
        
        # Run attacks
        net_attack = NetworkAttackFramework()
        print(f"\n  Running network attack suite...")
        results = net_attack.run_all_attacks(target)
        
        # Get network stats
        topology = home_network.get_network_topology()
        capture_stats = home_network.packet_capture.get_stats()
        
        successful = sum(1 for r in results if r.success)
        print(f"\n  Result: {successful}/{len(results)} attacks successful")
        print(f"  Packets captured: {capture_stats.get('total_packets', 0)}")
        
        return results, topology
    
    finally:
        home_network.stop()
        print("  ✓ Home network stopped")


def generate_report(
    fw_results: dict,
    net_results: list,
    output_dir: str,
):
    """Generate comprehensive security assessment report."""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Firmware results
    if fw_results:
        for device_type, results in fw_results.items():
            filepath = os.path.join(output_dir, f"firmware_attacks_{device_type}_{timestamp}.json")
            FirmwareAttackFramework.export_results(results, filepath)
            print(f"  Exported: {filepath}")
        
        # Print combined firmware report
        print("\n")
        for device_type, results in fw_results.items():
            print(f"\n  === {device_type.upper()} ===")
            FirmwareAttackFramework.print_report(results)
    
    # Network results
    if net_results:
        filepath = os.path.join(output_dir, f"network_attacks_{timestamp}.json")
        NetworkAttackFramework.export_results(net_results, filepath)
        print(f"  Exported: {filepath}")
        NetworkAttackFramework.print_report(net_results)
    
    # Combined summary
    summary = {
        "timestamp": timestamp,
        "firmware_attacks": {},
        "network_attacks": {},
    }
    
    if fw_results:
        for device_type, results in fw_results.items():
            total = len(results)
            successful = sum(1 for r in results if r.success)
            summary["firmware_attacks"][device_type] = {
                "total": total,
                "successful": successful,
                "rate": f"{successful/total*100:.1f}%" if total else "N/A",
            }
    
    if net_results:
        total = len(net_results)
        successful = sum(1 for r in net_results if r.success)
        summary["network_attacks"] = {
            "total": total,
            "successful": successful,
            "rate": f"{successful/total*100:.1f}%" if total else "N/A",
        }
    
    summary_path = os.path.join(output_dir, f"security_summary_{timestamp}.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\n  Summary: {summary_path}")
    
    return summary


# ─── Entry Point ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="VESPER IoT Security Assessment Demo"
    )
    parser.add_argument(
        "--firmware-only", action="store_true",
        help="Run only firmware attacks"
    )
    parser.add_argument(
        "--network-only", action="store_true",
        help="Run only network attacks"
    )
    parser.add_argument(
        "--device-type", type=str, default=None,
        help="Target specific device type (e.g., smart_light, motion_sensor)"
    )
    parser.add_argument(
        "--use-docker", action="store_true",
        help="Use Docker containers instead of native QEMU"
    )
    parser.add_argument(
        "--base-port", type=int, default=15020,
        help="Base TCP port for QEMU instances (default: 15020)"
    )
    parser.add_argument(
        "--mqtt-port", type=int, default=11883,
        help="MQTT broker port for network attacks (default: 11883)"
    )
    parser.add_argument(
        "--output-dir", type=str, default="results/security",
        help="Output directory for reports (default: results/security)"
    )
    
    args = parser.parse_args()
    
    print("\n" + "╔" + "═" * 78 + "╗")
    print("║" + " VESPER IoT Security Assessment Framework ".center(78) + "║")
    print("║" + " Firmware + Network Attack Suite ".center(78) + "║")
    print("╚" + "═" * 78 + "╝")
    
    fw_results = {}
    net_results = []
    
    # Determine device types
    if args.device_type:
        try:
            device_types = [DeviceType(args.device_type)]
        except ValueError:
            print(f"Unknown device type: {args.device_type}")
            print(f"Available: {[d.value for d in DeviceType]}")
            sys.exit(1)
    else:
        device_types = None  # All types
    
    # Run firmware attacks
    if not args.network_only:
        fw_results = run_firmware_attacks_demo(
            device_types=device_types,
            use_docker=args.use_docker,
            base_port=args.base_port,
        )
    
    # Collect ports of running firmware for network attacks
    device_ports = []
    if fw_results and not args.firmware_only:
        device_ports = list(range(
            args.base_port,
            args.base_port + len(fw_results)
        ))
    
    # Run network attacks
    if not args.firmware_only:
        net_results_data = run_network_attacks_demo(
            device_ports=device_ports,
            mqtt_port=args.mqtt_port,
        )
        if isinstance(net_results_data, tuple):
            net_results = net_results_data[0]
        else:
            net_results = net_results_data
    
    # Generate report
    print("\n\n" + "=" * 80)
    print("  GENERATING SECURITY ASSESSMENT REPORT")
    print("=" * 80)
    
    summary = generate_report(fw_results, net_results, args.output_dir)
    
    # Final summary
    print("\n" + "=" * 80)
    print("  ASSESSMENT COMPLETE")
    print("=" * 80)
    
    if fw_results:
        total_fw = sum(len(r) for r in fw_results.values())
        success_fw = sum(sum(1 for a in r if a.success) for r in fw_results.values())
        print(f"  Firmware: {success_fw}/{total_fw} attacks successful across {len(fw_results)} device types")
    
    if net_results:
        success_net = sum(1 for r in net_results if r.success)
        print(f"  Network:  {success_net}/{len(net_results)} attacks successful")
    
    print(f"  Reports:  {args.output_dir}/")
    print("=" * 80)


if __name__ == "__main__":
    main()
