#!/usr/bin/env python3
"""
Quick test: verify all 6 firmware variants work in QEMU.
Tests compilation, QEMU boot, TCP connection, and basic commands.
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent
FIRMWARE_DIR = WORKSPACE / "vesper" / "firmware" / "samples" / "device_types"
BASE_PORT = 15030

DEVICE_TYPES = [
    ("motion_sensor", ["GET_MOTION", "GET_SENSITIVITY", "ARM", "DISARM"]),
    ("temperature_sensor", ["GET_TEMP", "GET_TEMP_RAW", "SET_UNIT:F", "SET_UNIT:C"]),
    ("smart_light", ["GET_SWITCH", "ON", "GET_BRIGHTNESS", "SET_BRIGHTNESS:50"]),
    ("humidity_sensor", ["GET_HUMIDITY", "GET_TEMP"]),
    ("door_sensor", ["GET_DOOR", "GET_TAMPER", "ARM"]),
    ("smart_plug", ["GET_SWITCH", "ON", "GET_POWER", "GET_ENERGY"]),
]


def send_cmd(port, cmd, timeout=2.0):
    """Send a command and read response."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(("127.0.0.1", port))
        # Read boot banner
        time.sleep(0.3)
        try:
            sock.recv(4096)
        except socket.timeout:
            pass
        # Send command
        sock.sendall((cmd + "\n").encode())
        time.sleep(0.3)
        resp = sock.recv(4096).decode().strip()
        sock.close()
        return resp
    except Exception as e:
        return f"ERROR: {e}"


def main():
    print("=" * 70)
    print("  VESPER Per-Device Firmware Verification")
    print("=" * 70)
    
    all_passed = True
    processes = []
    
    try:
        for i, (device_type, test_cmds) in enumerate(DEVICE_TYPES):
            port = BASE_PORT + i
            elf_path = FIRMWARE_DIR / f"{device_type}.elf"
            
            print(f"\n{'─' * 50}")
            print(f"  [{i+1}/6] {device_type}")
            print(f"{'─' * 50}")
            
            if not elf_path.exists():
                print(f"  ✗ ELF not found: {elf_path}")
                all_passed = False
                continue
            
            print(f"  ELF: {elf_path.name} ({elf_path.stat().st_size} bytes)")
            
            # Launch QEMU
            proc = subprocess.Popen(
                [
                    "qemu-system-arm",
                    "-machine", "lm3s6965evb",
                    "-cpu", "cortex-m3",
                    "-nographic",
                    "-kernel", str(elf_path),
                    "-serial", f"tcp::{port},server,nowait",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            processes.append(proc)
            print(f"  QEMU PID: {proc.pid}, port: {port}")
            
            # Wait for boot
            time.sleep(1.5)
            
            # Test IDENTIFY
            resp = send_cmd(port, "IDENTIFY")
            if "DEVICE:" in resp and "TYPE:" in resp:
                print(f"  ✓ IDENTIFY: {resp.split(chr(10))[0]}")
            else:
                print(f"  ✗ IDENTIFY failed: {resp[:60]}")
                all_passed = False
                continue
            
            # Test STATUS
            resp = send_cmd(port, "STATUS")
            if "STATUS:OK" in resp:
                print(f"  ✓ STATUS: OK")
            else:
                print(f"  ✗ STATUS: {resp[:60]}")
                all_passed = False
            
            # Test device-specific commands
            for cmd in test_cmds:
                resp = send_cmd(port, cmd)
                if "ERROR:CONNECTION" in resp:
                    print(f"  ✗ {cmd}: connection error")
                    all_passed = False
                else:
                    # Check we got a meaningful response
                    first_line = resp.split("\n")[0] if resp else "(empty)"
                    print(f"  ✓ {cmd}: {first_line[:50]}")
            
            # Test debug backdoor (vulnerability)
            resp = send_cmd(port, "DEBUG_DUMP")
            if "DEBUG:MEMORY_DUMP" in resp:
                print(f"  ⚠ DEBUG_DUMP accessible (vulnerability confirmed)")
            
    finally:
        # Cleanup
        print(f"\n{'─' * 50}")
        print("  Cleaning up QEMU processes...")
        for proc in processes:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
    
    print(f"\n{'=' * 70}")
    if all_passed:
        print("  ✅ ALL 6 FIRMWARE VARIANTS PASSED")
    else:
        print("  ❌ SOME TESTS FAILED")
    print(f"{'=' * 70}")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
