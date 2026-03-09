#!/usr/bin/env python3
"""
VESPER Pcap Capture Verification

Verifies that WiresharkLiveCapture works end-to-end:
1. Checks tshark/dumpcap availability
2. Starts a capture on the loopback interface
3. Sends TCP traffic to a local echo server (or firmware if running)
4. Stops capture and validates the .pcap file
5. Optionally reads back packets with pyshark/tshark

Usage:
    python scripts/verify_pcap_capture.py
    python scripts/verify_pcap_capture.py --firmware-port 15001
    python scripts/verify_pcap_capture.py --interface lo0
"""

import argparse
import json
import logging
import os
import platform
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from vesper.network.home_network import (
    NetworkConfig,
    NetworkMode,
    WiresharkConfig,
    WiresharkLiveCapture,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("verify_pcap")


# ─── Minimal TCP echo server ─────────────────────────────────────────────────

class MiniEchoServer:
    """Tiny TCP server that echoes back received data, for pcap verification."""

    def __init__(self, port: int = 0):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", port))
        self._sock.listen(5)
        self.port = self._sock.getsockname()[1]
        self._running = False
        self._thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        self._sock.settimeout(1.0)
        while self._running:
            try:
                conn, addr = self._sock.accept()
                threading.Thread(
                    target=self._handle, args=(conn,), daemon=True
                ).start()
            except socket.timeout:
                continue

    def _handle(self, conn: socket.socket):
        conn.settimeout(2.0)
        try:
            while self._running:
                data = conn.recv(4096)
                if not data:
                    break
                conn.sendall(data)
        except (socket.timeout, OSError):
            pass
        finally:
            conn.close()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        self._sock.close()


# ─── Verification steps ──────────────────────────────────────────────────────

def check_capture_tool() -> str:
    """Check tshark or dumpcap is installed. Returns tool path or empty string."""
    for name in ("tshark", "dumpcap"):
        path = shutil.which(name)
        if path:
            version = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=5
            )
            ver_line = version.stdout.split("\n")[0] if version.stdout else "unknown"
            print(f"  PASS  Found {name}: {path}")
            print(f"         Version: {ver_line}")
            return path
    print("  FAIL  Neither tshark nor dumpcap found on PATH")
    print("         Install Wireshark CLI tools:")
    if platform.system() == "Darwin":
        print("           brew install --cask wireshark")
    else:
        print("           sudo apt install tshark")
    return ""


def resolve_loopback_interface() -> str:
    """Return the correct loopback interface name for this OS."""
    if platform.system() == "Darwin":
        return "lo0"
    return "lo"


def verify_capture_round_trip(
    interface: str,
    port: int,
    pcap_dir: str,
    num_messages: int = 10,
    use_firmware: bool = False,
) -> dict:
    """
    Start capture, send traffic, stop capture, validate pcap.

    Returns dict with verification results.
    """
    results = {
        "capture_started": False,
        "messages_sent": 0,
        "messages_echoed": 0,
        "pcap_exists": False,
        "pcap_size_bytes": 0,
        "pcap_packet_count": 0,
        "pcap_path": "",
        "errors": [],
    }

    # Build a WiresharkLiveCapture with the right config
    ws_config = WiresharkConfig(
        enabled=True,
        capture_interface=interface,
        capture_filter=f"tcp port {port}",
        pcap_dir=pcap_dir,
    )
    net_config = NetworkConfig(
        mode=NetworkMode.HOST,  # forces loopback fallback in _resolve_interface
        wireshark=ws_config,
    )
    capture = WiresharkLiveCapture(net_config)

    if not capture._tool:
        results["errors"].append("No capture tool found")
        return results

    # Start capture
    capture.start()
    time.sleep(1.0)  # Let tshark/dumpcap initialize

    if capture._process and capture._process.poll() is None:
        results["capture_started"] = True
        print(f"  PASS  Capture started (PID {capture._process.pid})")
    else:
        stderr = ""
        if capture._process:
            stderr = capture._process.stderr.read().decode(errors="replace")
        results["errors"].append(f"Capture process failed to start: {stderr}")
        print(f"  FAIL  Capture did not start: {stderr}")
        return results

    # Send traffic
    time.sleep(0.5)
    for i in range(num_messages):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3.0)
            sock.connect(("127.0.0.1", port))
            msg = f"VESPER_VERIFY_{i:04d}\n".encode()
            sock.sendall(msg)
            results["messages_sent"] += 1

            if not use_firmware:
                # Echo server: expect exact echo
                buf = b""
                deadline = time.time() + 2.0
                while time.time() < deadline:
                    try:
                        chunk = sock.recv(4096)
                        if chunk:
                            buf += chunk
                            if b"\n" in buf:
                                break
                        else:
                            break
                    except socket.timeout:
                        break
                if b"VESPER_VERIFY" in buf:
                    results["messages_echoed"] += 1
            else:
                # Firmware: just read whatever it sends back
                try:
                    resp = sock.recv(4096)
                    if resp:
                        results["messages_echoed"] += 1
                except socket.timeout:
                    pass

            sock.close()
        except Exception as e:
            results["errors"].append(f"Message {i}: {e}")

        time.sleep(0.1)

    # Give tshark time to flush
    time.sleep(1.0)

    # Stop capture
    pcap_path = capture.stop()
    results["pcap_path"] = pcap_path or ""

    # Validate pcap file
    if pcap_path and Path(pcap_path).exists():
        results["pcap_exists"] = True
        results["pcap_size_bytes"] = Path(pcap_path).stat().st_size
        print(f"  PASS  Pcap file exists: {pcap_path}")
        print(f"         Size: {results['pcap_size_bytes']:,} bytes")

        # Count packets via tshark -r (if available)
        tshark = shutil.which("tshark")
        if tshark:
            try:
                count_result = subprocess.run(
                    [tshark, "-r", pcap_path, "-T", "fields", "-e", "frame.number"],
                    capture_output=True, text=True, timeout=10,
                )
                lines = [
                    l for l in count_result.stdout.strip().split("\n") if l.strip()
                ]
                results["pcap_packet_count"] = len(lines)
                print(f"  PASS  Packet count: {results['pcap_packet_count']}")
            except Exception as e:
                results["errors"].append(f"tshark read failed: {e}")
    else:
        results["errors"].append(f"Pcap file not found at {pcap_path}")
        print(f"  FAIL  Pcap file not found at {pcap_path}")

    return results


def verify_firmware_port(host: str, port: int, timeout: float = 5.0) -> bool:
    """Check if a firmware instance is reachable."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.sendall(b"IDENTIFY\n")
        buf = b""
        deadline = time.time() + 3.0
        while time.time() < deadline:
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
        sock.close()
        return len(buf) > 0  # Got some response even if not VESPER banner
    except (socket.error, OSError):
        return False


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Verify VESPER pcap capture infrastructure"
    )
    parser.add_argument(
        "--interface", type=str, default="",
        help="Network interface to capture on (default: auto-detect loopback)"
    )
    parser.add_argument(
        "--firmware-port", type=int, default=0,
        help="If set, verify against a running firmware instance instead of echo server"
    )
    parser.add_argument(
        "--pcap-dir", type=str, default="",
        help="Directory for pcap output (default: temp dir)"
    )
    parser.add_argument(
        "--num-messages", type=int, default=10,
        help="Number of test messages to send (default: 10)"
    )
    args = parser.parse_args()

    print()
    print("=" * 70)
    print("  VESPER Pcap Capture Verification")
    print("=" * 70)

    # Step 1: Check tools
    print("\n[1/4] Checking capture tools...")
    tool = check_capture_tool()
    if not tool:
        print("\nABORTED: No capture tool found.")
        sys.exit(1)

    # Step 2: Determine interface
    interface = args.interface or resolve_loopback_interface()
    print(f"\n[2/4] Capture interface: {interface}")

    # Step 3: Determine target (echo server or firmware)
    echo_server = None
    use_firmware = False
    port = args.firmware_port

    if port:
        print(f"\n[3/4] Checking firmware at 127.0.0.1:{port}...")
        if verify_firmware_port("127.0.0.1", port):
            print(f"  PASS  Firmware responsive on port {port}")
            use_firmware = True
        else:
            print(f"  WARN  Firmware not responding on port {port}, using echo server")
            port = 0

    if not port:
        print("\n[3/4] Starting local echo server...")
        echo_server = MiniEchoServer()
        echo_server.start()
        port = echo_server.port
        print(f"  PASS  Echo server listening on 127.0.0.1:{port}")

    # Step 4: Run capture verification
    pcap_dir = args.pcap_dir or tempfile.mkdtemp(prefix="vesper_pcap_verify_")
    print(f"\n[4/4] Running capture round-trip test ({args.num_messages} messages)...")
    print(f"       Pcap dir: {pcap_dir}")

    try:
        results = verify_capture_round_trip(
            interface=interface,
            port=port,
            pcap_dir=pcap_dir,
            num_messages=args.num_messages,
            use_firmware=use_firmware,
        )
    finally:
        if echo_server:
            echo_server.stop()

    # Summary
    print("\n" + "─" * 70)
    print("  VERIFICATION SUMMARY")
    print("─" * 70)
    all_pass = True

    checks = [
        ("Capture tool found", bool(tool)),
        ("Capture started", results["capture_started"]),
        (f"Messages sent ({results['messages_sent']}/{args.num_messages})",
         results["messages_sent"] == args.num_messages),
        (f"Messages echoed ({results['messages_echoed']}/{results['messages_sent']})",
         results["messages_echoed"] > 0),
        ("Pcap file created", results["pcap_exists"]),
        (f"Pcap has content ({results['pcap_size_bytes']:,} bytes)",
         results["pcap_size_bytes"] > 0),
        (f"Pcap has packets ({results['pcap_packet_count']})",
         results["pcap_packet_count"] > 0),
    ]

    for label, passed in checks:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"  [{status}] {label}")

    if results["errors"]:
        print(f"\n  Errors:")
        for err in results["errors"]:
            print(f"    - {err}")

    if all_pass:
        print(f"\n  ALL CHECKS PASSED — pcap capture infrastructure is working")
        print(f"  Pcap: {results['pcap_path']}")
    else:
        print(f"\n  SOME CHECKS FAILED — review errors above")

    print("=" * 70)

    # Write results to JSON for CI/automation
    report_path = Path(pcap_dir) / "verify_results.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Results: {report_path}\n")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
