"""
VESPER-Soft: Software-Only Device Emulator

A pure-Python emulator that mimics the firmware's TCP command protocol
without QEMU or real ARM firmware.  Used as a comparison baseline to
demonstrate that firmware-in-the-loop (FIL) produces higher-fidelity
network traffic than software emulation alone.

Key differences from real QEMU FIL:
    - Deterministic response times (no ARM instruction pipeline effects)
    - No memory corruption artifacts in overflow attacks
    - Perfect string handling (no C string vulnerabilities)
    - No UART buffering effects on packet boundaries

This module provides a drop-in replacement for the QEMU firmware
containers, exposing the same TCP serial interface on a configurable port.

Usage:
    emulator = SoftwareEmulator(port=15050, device_type="smart_light")
    emulator.start()
    # ... run attacks against 127.0.0.1:15050 ...
    emulator.stop()

    # Or as a context manager:
    with SoftwareEmulator(port=15050) as emu:
        target = FirmwareTarget(host="127.0.0.1", port=15050)
        results = fw_attack.run_all_attacks(target)
"""

from __future__ import annotations

import json
import logging
import random
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class SoftDeviceState:
    """Internal state of the software-emulated device."""
    device_id: str = "vesper-soft-001"
    device_type: str = "smart_light"
    firmware_version: str = "SOFT-1.0.0"
    light_state: str = "OFF"
    brightness: int = 0
    temperature: float = 22.5
    humidity: float = 45.0
    motion_detected: bool = False
    door_state: str = "CLOSED"
    authenticated: bool = False
    auth_token: str = ""
    uptime_s: float = 0.0
    calibration_offset: float = 0.0
    schedule: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    command_count: int = 0


class SoftwareEmulator:
    """
    Software-only IoT device emulator.

    Mimics the VESPER firmware's TCP command protocol:
        - IDENTIFY → device info
        - STATUS → current state
        - LIGHT ON/OFF → control
        - GET_VALUE / GET_TEMP → sensor readings
        - AUTH:token → authentication
        - CALIBRATE:value → sensor calibration
        - SET_SCHEDULE:idx:val → schedule management
        - etc.

    Unlike real QEMU firmware:
        - No buffer overflow vulnerabilities
        - No format string vulnerabilities
        - Proper input validation
        - Deterministic timing
    """

    def __init__(
        self,
        port: int = 15050,
        device_type: str = "smart_light",
        device_id: str = "vesper-soft-001",
        response_delay_ms: float = 1.0,
    ):
        self.port = port
        self.response_delay = response_delay_ms / 1000.0
        self.state = SoftDeviceState(
            device_id=device_id,
            device_type=device_type,
        )
        self._server_sock: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._start_time = 0.0
        self._stats = {
            "connections": 0,
            "commands_processed": 0,
            "invalid_commands": 0,
        }

    def start(self):
        """Start the emulator TCP server."""
        self._running = True
        self._start_time = time.time()
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.settimeout(1.0)
        self._server_sock.bind(("0.0.0.0", self.port))
        self._server_sock.listen(10)
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        logger.info(f"VESPER-Soft started: {self.state.device_type} on port {self.port}")

    def stop(self):
        """Stop the emulator."""
        self._running = False
        if self._server_sock:
            self._server_sock.close()
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("VESPER-Soft stopped")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()

    def _accept_loop(self):
        while self._running:
            try:
                conn, addr = self._server_sock.accept()
                self._stats["connections"] += 1
                threading.Thread(
                    target=self._handle_client, args=(conn, addr), daemon=True
                ).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_client(self, conn: socket.socket, addr):
        """Handle a single TCP client connection."""
        conn.settimeout(5.0)
        try:
            data = conn.recv(4096)
            if not data:
                conn.close()
                return

            # Parse command (same protocol as real firmware)
            cmd_text = data.decode("utf-8", errors="replace").strip()

            # Simulate processing delay
            time.sleep(self.response_delay)

            response = self._process_command(cmd_text)
            conn.sendall((response + "\n").encode())

        except (socket.timeout, ConnectionError, OSError):
            pass
        finally:
            conn.close()

    def _process_command(self, cmd: str) -> str:
        """
        Process a firmware command and return response.

        Unlike real firmware, this has proper input validation and
        no memory safety vulnerabilities.
        """
        self.state.command_count += 1
        self.state.uptime_s = time.time() - self._start_time
        self._stats["commands_processed"] += 1

        # Strip null bytes (software handles them safely, unlike C firmware)
        cmd = cmd.replace("\x00", "").strip()

        if not cmd:
            return "ERROR:EMPTY_COMMAND"

        parts = cmd.split(":", 1)
        verb = parts[0].upper().strip()
        arg = parts[1].strip() if len(parts) > 1 else ""

        # Route to command handler
        handlers = {
            "IDENTIFY": self._cmd_identify,
            "STATUS": self._cmd_status,
            "LIGHT": self._cmd_light,
            "LIGHT ON": self._cmd_light_on,
            "LIGHT OFF": self._cmd_light_off,
            "GET_VALUE": self._cmd_get_value,
            "GET_TEMP": self._cmd_get_temp,
            "GET_ALL": self._cmd_get_all,
            "GET_SWITCH": self._cmd_get_switch,
            "AUTH": self._cmd_auth,
            "DEBUG_DUMP": self._cmd_debug_dump,
            "SETID": self._cmd_setid,
            "FWUPDATE": self._cmd_fwupdate,
            "CALIBRATE": self._cmd_calibrate,
            "SET_SCHEDULE": self._cmd_set_schedule,
            "HELP": self._cmd_help,
            "PING": lambda a: "PONG",
            "READ_MEM": self._cmd_read_mem,
        }

        # Handle "LIGHT ON" / "LIGHT OFF" as single command
        full_cmd = cmd.upper().strip()
        if full_cmd in handlers:
            return handlers[full_cmd](arg)

        handler = handlers.get(verb)
        if handler:
            return handler(arg)

        self._stats["invalid_commands"] += 1
        return f"ERROR:UNKNOWN:{verb}"

    # ── Command handlers ──────────────────────────────────────────────

    def _cmd_identify(self, arg: str) -> str:
        return (
            f"VESPER-SOFT|DEVICE:{self.state.device_type}|"
            f"ID:{self.state.device_id}|FW:{self.state.firmware_version}"
        )

    def _cmd_status(self, arg: str) -> str:
        return (
            f"STATUS:OK|TYPE:{self.state.device_type}|"
            f"UPTIME:{self.state.uptime_s:.0f}|"
            f"CMDS:{self.state.command_count}"
        )

    def _cmd_light(self, arg: str) -> str:
        arg_upper = arg.upper().strip()
        if arg_upper == "ON":
            return self._cmd_light_on("")
        elif arg_upper == "OFF":
            return self._cmd_light_off("")
        return f"LIGHT:{self.state.light_state}|BRIGHTNESS:{self.state.brightness}"

    def _cmd_light_on(self, arg: str) -> str:
        self.state.light_state = "ON"
        self.state.brightness = 100
        return "LIGHT:ON|BRIGHTNESS:100"

    def _cmd_light_off(self, arg: str) -> str:
        self.state.light_state = "OFF"
        self.state.brightness = 0
        return "LIGHT:OFF|BRIGHTNESS:0"

    def _cmd_get_value(self, arg: str) -> str:
        value = self.state.temperature + self.state.calibration_offset
        # Add small noise
        value += random.gauss(0, 0.1)
        return f"VALUE:{value:.2f}|UNIT:celsius"

    def _cmd_get_temp(self, arg: str) -> str:
        temp = self.state.temperature + self.state.calibration_offset
        temp += random.gauss(0, 0.1)
        return f"TEMP:{temp:.2f}"

    def _cmd_get_all(self, arg: str) -> str:
        return (
            f"LIGHT:{self.state.light_state}|"
            f"TEMP:{self.state.temperature:.1f}|"
            f"HUMIDITY:{self.state.humidity:.1f}|"
            f"DOOR:{self.state.door_state}"
        )

    def _cmd_get_switch(self, arg: str) -> str:
        return f"SWITCH:{self.state.light_state}"

    def _cmd_auth(self, arg: str) -> str:
        # Software emulator properly validates auth
        if not arg:
            return "AUTH:FAIL:NO_TOKEN"
        # Accept any non-empty token (research testbed)
        self.state.authenticated = True
        self.state.auth_token = arg
        return "AUTH:OK"

    def _cmd_debug_dump(self, arg: str) -> str:
        # Software emulator does NOT leak sensitive data
        return (
            f"DEBUG|TYPE:{self.state.device_type}|"
            f"CMDS:{self.state.command_count}|"
            f"UPTIME:{self.state.uptime_s:.0f}"
        )

    def _cmd_setid(self, arg: str) -> str:
        if not arg:
            return "ERROR:SETID:NO_VALUE"
        # Proper bounds checking (unlike C firmware)
        if len(arg) > 32:
            return "ERROR:SETID:TOO_LONG"
        self.state.device_id = arg[:32]
        return f"ID:{self.state.device_id}"

    def _cmd_fwupdate(self, arg: str) -> str:
        if not arg:
            return "ERROR:FWUPDATE:NO_DATA"
        # Proper length validation (unlike C firmware)
        if len(arg) > 1024:
            return "ERROR:FWUPDATE:TOO_LARGE"
        return "FWUPDATE:REJECTED:SIGNATURE_REQUIRED"

    def _cmd_calibrate(self, arg: str) -> str:
        if not arg:
            return "ERROR:CALIBRATE:NO_VALUE"
        try:
            val = float(arg)
        except ValueError:
            return "ERROR:CALIBRATE:INVALID_VALUE"
        # Proper range validation (unlike C firmware)
        if not (-10.0 <= val <= 10.0):
            return "ERROR:CALIBRATE:OUT_OF_RANGE"
        self.state.calibration_offset = val
        return f"CALIBRATED:{val:.1f}"

    def _cmd_set_schedule(self, arg: str) -> str:
        if not arg:
            return "ERROR:SCHEDULE:NO_VALUE"
        parts = arg.split(":", 1)
        if len(parts) != 2:
            return "ERROR:SCHEDULE:INVALID_FORMAT"
        try:
            idx = int(parts[0])
            val = int(parts[1])
        except ValueError:
            return "ERROR:SCHEDULE:INVALID_VALUE"
        # Proper bounds checking (unlike C firmware)
        if idx < 0 or idx >= len(self.state.schedule):
            return f"ERROR:SCHEDULE:INDEX_OUT_OF_BOUNDS:{idx}"
        if val < 0 or val > 86400:
            return "ERROR:SCHEDULE:VALUE_OUT_OF_RANGE"
        self.state.schedule[idx] = val
        return f"SCHEDULE:{idx}:{val}:ACK"

    def _cmd_help(self, arg: str) -> str:
        return (
            "COMMANDS:IDENTIFY,STATUS,LIGHT,GET_VALUE,GET_TEMP,"
            "GET_ALL,AUTH,CALIBRATE,SET_SCHEDULE,HELP"
        )

    def _cmd_read_mem(self, arg: str) -> str:
        # Software emulator blocks memory reads
        return "ERROR:READ_MEM:ACCESS_DENIED"

    @property
    def stats(self) -> dict:
        return self._stats.copy()
