#!/usr/bin/env python3
"""
VESPER ESP32 Attack Demo — 3 Attack Suites
==========================================
Runs all three VESPER attack suites against a real ESP32 (M5Stack).
Each suite can also be run individually:

    python scripts/attacks/firmware.py --target 192.168.1.112:15011
    python scripts/attacks/network.py  --target 192.168.1.112:15011
    python scripts/attacks/relay.py    --target 192.168.1.112:15011 --delay 5

Run all three in sequence:
    python scripts/esp32_attack_demo.py --target 192.168.1.112:15011
    python scripts/esp32_attack_demo.py --target 192.168.1.112:15011 --delay 8
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.attacks.common   import GREEN, RED, RESET, reset_clock, verify_connection, print_result
from scripts.attacks.firmware import attack_firmware_info_disclosure
from scripts.attacks.network  import attack_network_replay
from scripts.attacks.relay    import attack_relay_phantom_delay


# ── banner / summary ──────────────────────────────────────────────────────────

def print_banner():
    print("\n" + "=" * 70)
    print("  🔓 VESPER ESP32 Attack Demo — 3 Attack Suites")
    print("  Mirrors the Firmware / Network / Relay suites from the main eval")
    print("=" * 70 + "\n")


def print_summary(results: list):
    total = len(results)
    ok    = sum(1 for r in results if r["success"])
    print("\n" + "=" * 70)
    print("  📊 ATTACK SUMMARY")
    print("=" * 70)
    print(f"  Total Attacks:   {total}")
    print(f"  Successful:      {ok}")
    print(f"  Failed:          {total - ok}")
    print(f"  Success Rate:    {ok/total*100:.0f}%")
    print()
    print("  By Suite  (mirrors the 3 suites in the 30-scene evaluation):")
    for i, r in enumerate(results, 1):
        mark = f"{GREEN}✓{RESET}" if r["success"] else f"{RED}✗{RESET}"
        cvss = r.get("cvss", 0)
        print(f"    {mark}  Suite {i} — {r['suite']:30s} CVSS {cvss:.1f}")
    print("=" * 70 + "\n")


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="VESPER ESP32 Attack Demo — 3 Suites"
    )
    parser.add_argument("--target", required=True,
                        help="ESP32 address, e.g. 192.168.1.112:15011")
    parser.add_argument("--delay", type=float, default=5.0,
                        help="Phantom-delay seconds for Suite 3 (default: 5)")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip connection verification")
    args = parser.parse_args()

    parts = args.target.split(":")
    host  = parts[0]
    port  = int(parts[1]) if len(parts) > 1 else 15011

    print_banner()
    print(f"Target: {host}:{port}\n")

    if not args.no_verify:
        if not verify_connection(host, port):
            print("Cannot reach ESP32.  Check IP address and WiFi.\n")
            return 1

    print("Running 3 demo attacks — one per suite ...\n")
    results = []

    # ── Suite 1: Firmware ─────────────────────────────────────────────────────
    reset_clock()
    r1 = attack_firmware_info_disclosure(host, port)
    results.append(r1)
    print_result(r1, 1, 3)
    time.sleep(1)

    # ── Suite 2: Network ──────────────────────────────────────────────────────
    reset_clock()
    r2 = attack_network_replay(host, port)
    results.append(r2)
    print_result(r2, 2, 3)
    time.sleep(1)

    # ── Suite 3: Relay/Phantom-Delay ─────────────────────────────────────────
    reset_clock()
    r3 = attack_relay_phantom_delay(host, port, delay_s=args.delay)
    results.append(r3)
    print_result(r3, 3, 3)

    print_summary(results)

    print("💡 TIP: Watch the M5Stack LCD during attacks!")
    print("   - Yellow text: received commands")
    print("   - Green/Red panels: ARM / DISARM state")
    print("   - Purple panel: buffer overflow detected")
    print("   - Orange flash: motion detected (shake the device!)\n")

    return 0 if all(r["success"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
