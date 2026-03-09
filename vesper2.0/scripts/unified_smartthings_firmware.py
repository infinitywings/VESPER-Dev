#!/usr/bin/env python3
"""
VESPER Unified SmartThings + Docker Firmware Server.

This script runs:
1. SmartThings Schema Connector (webhook server for cloud integration)
2. Docker Containers each running QEMU ARM firmware emulation

Each virtual IoT device = 1 Docker container running real ARM Cortex-M3
firmware in QEMU, communicating via TCP serial.

Usage:
    1. Start ngrok: ngrok http 8443
    2. Run this script: python scripts/unified_smartthings_firmware.py
    3. Link VESPER in SmartThings app
    4. Control devices from the app!
"""

import asyncio
import logging
import os
import sys
import signal
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Callable, Any, List

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vesper.integrations import (
    SmartThingsSchemaConnector,
    SchemaConnectorConfig,
    VirtualDeviceDefinition,
    DeviceHandlerType,
)

# Configure logging
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/unified_server_{datetime.now():%Y%m%d_%H%M%S}.log"),
    ],
)
logger = logging.getLogger(__name__)

# SmartThings credentials
SMARTTHINGS_CLIENT_ID = os.getenv(
    "SMARTTHINGS_CLIENT_ID", 
    "vesper-smart-home-2025"
)
SMARTTHINGS_CLIENT_SECRET = os.getenv(
    "SMARTTHINGS_CLIENT_SECRET",
    "VESPER_SmartHome_Secret_2025_SecureKey_AbC123XyZ789",
)

# Docker image name (built from docker/Dockerfile.device)
DOCKER_IMAGE = "vesper-qemu-arm:latest"

# Base host port for TCP serial (15001, 15002, 15003, …)
BASE_HOST_PORT = 15001


@dataclass
class FirmwareDeviceConfig:
    """Configuration for a Docker + QEMU firmware device."""
    device_id: str
    name: str
    room: str = "Living Room"
    device_type: DeviceHandlerType = DeviceHandlerType.SWITCH
    host_port: int = 15001          # TCP port on host for serial
    container_port: int = 5555      # TCP port inside container


class DockerFirmwareDevice:
    """
    Manages a single Docker container running QEMU ARM firmware.

    Communication is over TCP: the container exposes a TCP port that maps
    to QEMU's serial output.  The host connects to localhost:<host_port>
    to send commands and receive responses.
    """

    def __init__(self, config: FirmwareDeviceConfig):
        self.config = config
        self.container_name = f"vesper-{config.device_id}"
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._running = False
        self._read_task: Optional[asyncio.Task] = None

        # Cached SmartThings state
        self.state: Dict[str, Any] = {
            "st.switch.switch": "off",
            "st.switchLevel.level": 100,
            "st.healthCheck.healthStatus": "online",
            "temperature": 22.5,
            "humidity": 45.0,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> bool:
        """Start the Docker container and connect via TCP serial."""
        # Remove stale container with the same name (if any)
        proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", self.container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

        # Start container
        docker_cmd = [
            "docker", "run", "-d",
            "--name", self.container_name,
            "-e", f"DEVICE_ID={self.config.device_id}",
            "-e", f"DEVICE_NAME={self.config.name}",
            "-e", f"SERIAL_PORT={self.config.container_port}",
            "-p", f"{self.config.host_port}:{self.config.container_port}",
            DOCKER_IMAGE,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                logger.error(
                    f"Docker run failed for {self.config.device_id}: "
                    f"{stderr.decode().strip()}"
                )
                return False

            container_id = stdout.decode().strip()[:12]
            logger.info(
                f"🐳 Container started: {self.container_name} ({container_id})"
            )

        except FileNotFoundError:
            logger.error("Docker is not installed or not in PATH")
            return False

        # Wait for the TCP serial port to become ready
        connected = await self._connect_tcp(retries=15, delay=0.5)
        if not connected:
            logger.error(
                f"Could not connect to {self.container_name} TCP serial "
                f"on port {self.config.host_port}"
            )
            return False

        self._running = True
        self._read_task = asyncio.create_task(self._read_loop())

        # Identify device
        await asyncio.sleep(0.3)
        await self._send("IDENTIFY")

        logger.info(f"✅ Firmware device started: {self.config.name}")
        return True

    async def stop(self):
        """Stop container and clean up."""
        self._running = False

        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass

        # Close TCP connection
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

        # Stop & remove Docker container
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", self.container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception as e:
            logger.warning(f"Error removing container {self.container_name}: {e}")

        logger.info(f"🛑 Stopped: {self.config.name}")

    # ------------------------------------------------------------------
    # TCP Serial Communication
    # ------------------------------------------------------------------

    async def _connect_tcp(self, retries: int = 15, delay: float = 0.5) -> bool:
        """Connect to the container's TCP serial port with retries."""
        for attempt in range(retries):
            try:
                self._reader, self._writer = await asyncio.open_connection(
                    "127.0.0.1", self.config.host_port,
                )
                logger.info(
                    f"🔗 TCP connected: {self.config.name} "
                    f"-> localhost:{self.config.host_port}"
                )
                return True
            except (ConnectionRefusedError, OSError):
                if attempt < retries - 1:
                    await asyncio.sleep(delay)
        return False

    async def _send(self, command: str):
        """Send a command to the firmware over TCP serial."""
        if self._writer and not self._writer.is_closing():
            try:
                self._writer.write((command + "\n").encode())
                await self._writer.drain()
                logger.debug(f"[{self.config.device_id}] TX: {command}")
            except Exception as e:
                logger.error(f"TCP send error ({self.config.device_id}): {e}")

    async def _read_loop(self):
        """Continuously read responses from firmware over TCP."""
        while self._running and self._reader:
            try:
                line = await asyncio.wait_for(
                    self._reader.readline(), timeout=1.0,
                )
                if line:
                    text = line.decode("utf-8", errors="replace").strip()
                    self._process_line(text)
                elif line == b"":
                    # Connection closed by container
                    logger.warning(
                        f"TCP connection closed for {self.config.device_id}"
                    )
                    break
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.error(f"TCP read error ({self.config.device_id}): {e}")
                break

    def _process_line(self, line: str):
        """Process a line received from the firmware."""
        if not line:
            return
        logger.info(f"[{self.config.device_id}] RX: {line}")

        if line.startswith("SWITCH:"):
            value = line.split(":")[1].strip().lower()
            self.state["st.switch.switch"] = value
        elif line.startswith("TEMP:"):
            try:
                self.state["temperature"] = float(line.split(":")[1])
            except ValueError:
                pass
        elif line.startswith("HUMIDITY:"):
            try:
                self.state["humidity"] = float(line.split(":")[1])
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # SmartThings command handlers
    # ------------------------------------------------------------------

    async def handle_command(
        self, capability: str, command: str, args: list,
    ) -> bool:
        """Handle a SmartThings command by forwarding to firmware."""
        logger.info(f"🎮 [{self.config.name}] {capability}.{command}({args})")

        if capability == "st.switch":
            if command == "on":
                await self._send("ON")
                self.state["st.switch.switch"] = "on"
            elif command == "off":
                await self._send("OFF")
                self.state["st.switch.switch"] = "off"
        elif capability == "st.switchLevel" and command == "setLevel":
            level = args[0] if args else 100
            self.state["st.switchLevel.level"] = level
            if level > 0:
                await self._send("ON")
                self.state["st.switch.switch"] = "on"
            else:
                await self._send("OFF")
                self.state["st.switch.switch"] = "off"

        return True

    def get_smartthings_state(self) -> Dict[str, Any]:
        """Return state dict formatted for SmartThings."""
        return self.state.copy()


class UnifiedServer:
    """
    Combines SmartThings Schema Connector + Docker QEMU firmware devices.
    """

    def __init__(self, port: int = 8443):
        self.port = port
        self.connector: Optional[SmartThingsSchemaConnector] = None
        self.firmware_devices: Dict[str, DockerFirmwareDevice] = {}
        self._running = False

    async def setup(self):
        """Initialise all components."""
        print("\n" + "=" * 60)
        print("  VESPER Unified SmartThings + Docker Firmware Server")
        print("=" * 60)

        # ---- SmartThings Schema Connector ----------------------------
        config = SchemaConnectorConfig(
            host="0.0.0.0",
            port=self.port,
            webhook_path="/schema",
            smartthings_client_id=SMARTTHINGS_CLIENT_ID,
            smartthings_client_secret=SMARTTHINGS_CLIENT_SECRET,
        )
        self.connector = SmartThingsSchemaConnector(config)
        self.connector.on_command(self._handle_command)

        # ---- Device definitions --------------------------------------
        device_configs = [
            FirmwareDeviceConfig(
                device_id="vesper-fw-kitchen",
                name="Kitchen Light (Firmware)",
                room="Kitchen",
                device_type=DeviceHandlerType.DIMMER,
                host_port=BASE_HOST_PORT,
            ),
            FirmwareDeviceConfig(
                device_id="vesper-fw-living",
                name="Living Room (Firmware)",
                room="Living Room",
                device_type=DeviceHandlerType.DIMMER,
                host_port=BASE_HOST_PORT + 1,
            ),
            FirmwareDeviceConfig(
                device_id="vesper-fw-bedroom",
                name="Bedroom Light (Firmware)",
                room="Bedroom",
                device_type=DeviceHandlerType.DIMMER,
                host_port=BASE_HOST_PORT + 2,
            ),
        ]

        # ---- Start Docker containers --------------------------------
        print("\n[1/2] Starting Docker containers …")
        for cfg in device_configs:
            device = DockerFirmwareDevice(cfg)
            if await device.start():
                self.firmware_devices[cfg.device_id] = device
                print(f"  ✅ {cfg.name}  (port {cfg.host_port})")
            else:
                print(f"  ❌ {cfg.name}  (container failed)")

            # Register with SmartThings Schema connector
            st_device = VirtualDeviceDefinition(
                external_device_id=cfg.device_id,
                friendly_name=cfg.name,
                device_handler_type=cfg.device_type,
                manufacturer_name="VESPER",
                model_name="QEMU Firmware Device",
                sw_version="1.0.0",
                room_name=cfg.room,
            )

            if cfg.device_id in self.firmware_devices:
                st_device.state = self.firmware_devices[cfg.device_id].get_smartthings_state()
            else:
                st_device.state = {
                    "st.switch.switch": "off",
                    "st.healthCheck.healthStatus": "online",
                }

            self.connector.register_device(st_device)

        print("[2/2] Setup complete!")

    async def _handle_command(
        self,
        device_id: str,
        capability: str,
        command: str,
        arguments: list,
    ) -> bool:
        """Handle commands from SmartThings app → firmware."""
        logger.info(f"📱 SmartThings command: {device_id} -> {capability}.{command}")

        fw_device = self.firmware_devices.get(device_id)
        if fw_device:
            result = await fw_device.handle_command(capability, command, arguments)

            # Sync state back to schema connector
            st_device = self.connector.get_device(device_id)
            if st_device:
                st_device.state = fw_device.get_smartthings_state()
            return result

        # Software-only fallback
        st_device = self.connector.get_device(device_id)
        if st_device:
            if capability == "st.switch":
                st_device.state["st.switch.switch"] = (
                    "on" if command == "on" else "off"
                )
            elif capability == "st.switchLevel":
                st_device.state["st.switchLevel.level"] = (
                    arguments[0] if arguments else 100
                )
            return True

        return False

    async def start(self):
        """Start the SmartThings webhook server."""
        await self.connector.start()
        self._running = True
        self._print_info()

        while self._running:
            await asyncio.sleep(1)

    async def stop(self):
        """Stop everything cleanly."""
        self._running = False

        # Stop Docker containers
        for device in self.firmware_devices.values():
            await device.stop()

        # Stop Schema connector
        if self.connector:
            await self.connector.stop()

        logger.info("Server stopped.")

    def _print_info(self):
        """Print status banner."""
        print("\n" + "=" * 60)
        print("  SERVER RUNNING")
        print("=" * 60)
        print(f"""
Webhook URL:  http://localhost:{self.port}/schema
Health Check: http://localhost:{self.port}/health

DOCKER FIRMWARE DEVICES ({len(self.firmware_devices)} containers):""")

        for dev_id, device in self.firmware_devices.items():
            print(f"  🐳 {device.config.name}")
            print(f"     Container: {device.container_name}")
            print(f"     TCP Port:  localhost:{device.config.host_port}")
            print(f"     State:     {device.state.get('st.switch.switch', 'unknown')}")
            print()

        print("""SMARTTHINGS SETUP:
  1. Start ngrok:  ngrok http 8443
  2. Update Target URL in SmartThings Developer Portal
  3. Link "VESPER Smart Home" in the SmartThings app
  4. Control devices – commands flow to real firmware!

Press Ctrl+C to stop.
""")
        print("=" * 60)


async def main():
    """Main entry point."""
    server = UnifiedServer(port=8443)
    
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        asyncio.create_task(server.stop())
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)
    
    try:
        await server.setup()
        await server.start()
    except Exception as e:
        logger.error(f"Server error: {e}")
        raise
    finally:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
