"""
VESPER ESP32 QEMU Runner

Manages ESP32 QEMU instances for the VESPER attack framework.
Each runner controls one qemu-system-xtensa process running
SmartThings-compatible firmware.

This replaces the old LM3S6965-based qemu_runner.py with:
  - ESP32 (Xtensa LX6) target
  - WiFi networking via open_eth virtual NIC
  - Matter communication via bridge REST API
  - SmartThings Device SDK integration

Usage:
    from vesper.firmware.esp32_runner import ESP32Runner, ESP32Config

    config = ESP32Config(
        device_type="smart_light",
        device_id="kitchen-light-01",
        serial_port=5561,
    )
    runner = ESP32Runner(config)
    runner.start()
    response = runner.send_command("STATUS")
    runner.stop()
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ESP32State(str, Enum):
    """State of an ESP32 QEMU instance."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"


@dataclass
class ESP32Config:
    """Configuration for an ESP32 QEMU instance."""
    device_type: str = "smart_light"
    device_id: str = "vesper-device-01"
    serial_port: int = 5561
    matter_bridge_url: str = "http://localhost:8484"
    firmware_path: Optional[str] = None  # Auto-detected if None
    qemu_path: Optional[str] = None      # Auto-detected if None


class ESP32Runner:
    """
    Manages a single ESP32 QEMU instance.

    Can operate in two modes:
    1. Docker mode (default): Device runs inside a Docker container
       managed by docker-compose.
    2. Local mode: Runs qemu-system-xtensa directly on the host
       (requires Espressif QEMU fork installed).
    """

    def __init__(self, config: ESP32Config, docker_mode: bool = True):
        self.config = config
        self.docker_mode = docker_mode
        self.state = ESP32State.STOPPED
        self._process: Optional[subprocess.Popen] = None
        self._container_name: Optional[str] = None

    def start(self) -> None:
        """Start the ESP32 QEMU instance."""
        if self.docker_mode:
            self._start_docker()
        else:
            self._start_local()

    def stop(self) -> None:
        """Stop the ESP32 QEMU instance."""
        if self.docker_mode:
            self._stop_docker()
        else:
            self._stop_local()
        self.state = ESP32State.STOPPED

    def send_command(self, command: str, timeout: float = 5.0) -> str:
        """
        Send a command to the device via its serial port.

        The ESP32 firmware accepts the same text command protocol
        as the old LM3S6965 firmware (backward compatible):
          STATUS, ON, OFF, SET_BRIGHTNESS, AUTH, FW_UPDATE, etc.
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            host = "localhost"
            sock.connect((host, self.config.serial_port))
            sock.sendall((command + "\n").encode())
            time.sleep(0.3)
            response = sock.recv(4096).decode(errors="replace")
            sock.close()
            return response.strip()
        except socket.timeout:
            logger.warning(f"Serial timeout for {self.config.device_id}")
            return ""
        except Exception as e:
            logger.error(f"Serial error for {self.config.device_id}: {e}")
            return ""

    def send_raw(self, data: bytes, timeout: float = 5.0) -> bytes:
        """Send raw bytes to the serial port (for fuzzing/overflow attacks)."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(("localhost", self.config.serial_port))
            sock.sendall(data)
            time.sleep(0.3)
            response = sock.recv(8192)
            sock.close()
            return response
        except Exception as e:
            logger.error(f"Raw send error for {self.config.device_id}: {e}")
            return b""

    def is_alive(self) -> bool:
        """Check if the QEMU instance is running and responsive."""
        try:
            resp = self.send_command("STATUS", timeout=3.0)
            return "STATE:" in resp or "OK" in resp
        except Exception:
            return False

    def get_info(self) -> Dict[str, Any]:
        """Get device information."""
        return {
            "device_id": self.config.device_id,
            "device_type": self.config.device_type,
            "serial_port": self.config.serial_port,
            "matter_bridge": self.config.matter_bridge_url,
            "state": self.state.value,
            "alive": self.is_alive() if self.state == ESP32State.RUNNING else False,
        }

    # ── Docker mode ───────────────────────────────────────────────────────

    def _start_docker(self) -> None:
        """The device container is managed by docker-compose; just verify it."""
        self.state = ESP32State.STARTING
        # The container should already be running via docker-compose
        # Just wait for serial port
        for _ in range(30):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                sock.connect(("localhost", self.config.serial_port))
                sock.close()
                self.state = ESP32State.RUNNING
                logger.info(f"ESP32 {self.config.device_id} ready on port {self.config.serial_port}")
                return
            except Exception:
                time.sleep(1)

        self.state = ESP32State.ERROR
        logger.error(f"ESP32 {self.config.device_id} serial port not available")

    def _stop_docker(self) -> None:
        """Docker lifecycle is managed by compose; nothing to do here."""
        pass

    # ── Local mode ────────────────────────────────────────────────────────

    def _start_local(self) -> None:
        """Start QEMU directly on the host (for development/testing)."""
        qemu = self.config.qemu_path or shutil.which("qemu-system-xtensa")
        if not qemu:
            raise FileNotFoundError(
                "qemu-system-xtensa not found. Install Espressif QEMU fork: "
                "https://github.com/espressif/qemu"
            )

        firmware = self.config.firmware_path
        if not firmware:
            # Try default build output
            fw_dir = Path(__file__).resolve().parent / "esp32" / "build"
            firmware = str(fw_dir / "vesper-esp32.bin")

        if not os.path.exists(firmware):
            raise FileNotFoundError(f"Firmware not found: {firmware}")

        self.state = ESP32State.STARTING
        self._process = subprocess.Popen(
            [
                qemu,
                "-nographic",
                "-machine", "esp32",
                "-drive", f"file={firmware},if=mtd,format=raw",
                "-serial", f"tcp::{self.config.serial_port},server,nowait",
                "-nic", "user,model=open_eth",
                "-no-reboot",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(3)

        if self._process.poll() is not None:
            stderr = self._process.stderr.read().decode() if self._process.stderr else ""
            self.state = ESP32State.ERROR
            raise RuntimeError(f"QEMU exited immediately: {stderr}")

        self.state = ESP32State.RUNNING
        logger.info(f"ESP32 QEMU started (PID={self._process.pid})")

    def _stop_local(self) -> None:
        """Stop the local QEMU process."""
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None


def create_device_runners(devices: Optional[List[Dict]] = None) -> Dict[str, ESP32Runner]:
    """
    Create ESP32Runner instances for all devices.

    Returns a dict of {device_id: ESP32Runner}.
    """
    from vesper.network.wifi_emulator import DEFAULT_DEVICES

    runners = {}
    device_configs = devices or DEFAULT_DEVICES

    for dev in device_configs:
        if isinstance(dev, dict):
            config = ESP32Config(
                device_type=dev.get("device_type", "smart_light"),
                device_id=dev.get("device_id", "unknown"),
                serial_port=dev.get("serial_port", 5561),
            )
        else:
            # DeviceConfig from wifi_emulator
            config = ESP32Config(
                device_type=dev.device_type.value if hasattr(dev.device_type, 'value') else dev.device_type,
                device_id=dev.device_id,
                serial_port=dev.serial_port,
            )
        runners[config.device_id] = ESP32Runner(config)

    return runners
