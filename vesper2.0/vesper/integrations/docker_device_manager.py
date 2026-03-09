"""
Docker Virtual Device Manager for VESPER.

Manages virtual IoT devices as Docker containers, each running
QEMU-emulated firmware. Provides bi-directional communication
between the simulation and SmartThings.

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │                    Docker Host                               │
    │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐   │
    │  │  Container 1  │  │  Container 2  │  │  Container N  │   │
    │  │  QEMU + FW    │  │  QEMU + FW    │  │  QEMU + FW    │   │
    │  │  (Switch)     │  │  (Sensor)     │  │  (Lock)       │   │
    │  └───────┬───────┘  └───────┬───────┘  └───────┬───────┘   │
    │          │                  │                  │            │
    │          └──────────────────┼──────────────────┘            │
    │                             │                               │
    │                    ┌────────┴────────┐                      │
    │                    │  Device Bridge  │                      │
    │                    │  (TCP/Serial)   │                      │
    │                    └────────┬────────┘                      │
    └─────────────────────────────┼───────────────────────────────┘
                                  │
                         ┌────────┴────────┐
                         │  VESPER Core    │
                         │  + SmartThings  │
                         └─────────────────┘
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

try:
    import docker
    from docker.models.containers import Container
    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False
    docker = None
    Container = None

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

VESPER_NETWORK_NAME = "vesper-devices"
VESPER_IMAGE_PREFIX = "vesper-device"
DEFAULT_QEMU_IMAGE = "vesper-qemu-arm:latest"

# Base ports for device communication
BASE_SERIAL_PORT = 10000
BASE_TCP_PORT = 11000
BASE_HTTP_PORT = 12000


class DeviceType(str, Enum):
    """Virtual device types with their firmware templates."""
    SWITCH = "switch"
    DIMMER = "dimmer"
    MOTION_SENSOR = "motion_sensor"
    CONTACT_SENSOR = "contact_sensor"
    TEMPERATURE_SENSOR = "temperature_sensor"
    HUMIDITY_SENSOR = "humidity_sensor"
    LOCK = "lock"
    THERMOSTAT = "thermostat"
    BUTTON = "button"
    RGB_LIGHT = "rgb_light"


class ContainerStatus(str, Enum):
    """Container status states."""
    CREATING = "creating"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"
    REMOVED = "removed"


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class VirtualDeviceConfig:
    """Configuration for a virtual device container."""
    
    # Device identification
    device_id: str
    device_type: DeviceType
    friendly_name: str
    
    # Location
    room: Optional[str] = None
    
    # Firmware
    firmware_path: Optional[str] = None  # Custom firmware binary
    firmware_template: Optional[str] = None  # Or use a template
    
    # QEMU settings
    qemu_machine: str = "lm3s6965evb"  # Default ARM Cortex-M3 board
    qemu_cpu: str = "cortex-m3"
    memory_mb: int = 16
    
    # Communication
    serial_enabled: bool = True
    tcp_enabled: bool = True
    http_enabled: bool = False  # REST API on device
    
    # Container settings
    docker_image: str = DEFAULT_QEMU_IMAGE
    container_name: Optional[str] = None
    
    # Initial state
    initial_state: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.container_name:
            self.container_name = f"vesper-{self.device_type.value}-{self.device_id[:8]}"


@dataclass
class VirtualDevice:
    """Represents a running virtual device container."""
    
    config: VirtualDeviceConfig
    container_id: Optional[str] = None
    
    # Communication ports (assigned at runtime)
    serial_port: int = 0
    tcp_port: int = 0
    http_port: int = 0
    
    # Status
    status: ContainerStatus = ContainerStatus.STOPPED
    
    # Current state (synced with firmware)
    state: Dict[str, Any] = field(default_factory=dict)
    
    # Timestamps
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    
    # Communication handles
    _serial_reader: Optional[asyncio.StreamReader] = None
    _serial_writer: Optional[asyncio.StreamWriter] = None
    _tcp_reader: Optional[asyncio.StreamReader] = None
    _tcp_writer: Optional[asyncio.StreamWriter] = None


@dataclass
class DeviceManagerConfig:
    """Configuration for the Docker Device Manager."""
    
    # Docker settings
    docker_socket: str = "unix:///var/run/docker.sock"
    network_name: str = VESPER_NETWORK_NAME
    
    # Image settings
    base_image: str = DEFAULT_QEMU_IMAGE
    build_context: Optional[str] = None  # Path to Dockerfile directory
    
    # Port ranges
    serial_port_start: int = BASE_SERIAL_PORT
    tcp_port_start: int = BASE_TCP_PORT
    http_port_start: int = BASE_HTTP_PORT
    
    # Cleanup settings
    auto_remove_containers: bool = True
    cleanup_on_exit: bool = True
    
    # State persistence
    state_file: Optional[str] = None  # JSON file to persist device states
    
    @classmethod
    def from_env(cls) -> "DeviceManagerConfig":
        """Create config from environment variables."""
        return cls(
            docker_socket=os.getenv("DOCKER_HOST", "unix:///var/run/docker.sock"),
            network_name=os.getenv("VESPER_NETWORK", VESPER_NETWORK_NAME),
            state_file=os.getenv("VESPER_STATE_FILE"),
        )


# =============================================================================
# Docker Virtual Device Manager
# =============================================================================

class DockerDeviceManager:
    """
    Manages virtual IoT devices as Docker containers.
    
    Each device runs in its own container with:
    - QEMU emulating the target MCU (e.g., ARM Cortex-M3)
    - Custom or template firmware
    - Serial/TCP communication for state sync
    
    Usage:
        manager = DockerDeviceManager()
        await manager.start()
        
        # Create a virtual switch
        device = await manager.create_device(VirtualDeviceConfig(
            device_id="switch-001",
            device_type=DeviceType.SWITCH,
            friendly_name="Kitchen Light",
            room="Kitchen",
        ))
        
        # Control the device
        await manager.send_command(device.config.device_id, "turn_on")
        
        # Get state
        state = await manager.get_device_state(device.config.device_id)
        
        # Clean up
        await manager.stop()
    """
    
    def __init__(self, config: Optional[DeviceManagerConfig] = None):
        """Initialize the device manager."""
        self.config = config or DeviceManagerConfig.from_env()
        
        if not DOCKER_AVAILABLE:
            logger.warning("Docker SDK not available. Install with: pip install docker")
            self._client = None
        else:
            try:
                self._client = docker.DockerClient(base_url=self.config.docker_socket)
                logger.info("Connected to Docker daemon")
            except Exception as e:
                logger.error(f"Failed to connect to Docker: {e}")
                self._client = None
        
        # Device registry
        self._devices: Dict[str, VirtualDevice] = {}
        
        # Port allocation
        self._next_serial_port = self.config.serial_port_start
        self._next_tcp_port = self.config.tcp_port_start
        self._next_http_port = self.config.http_port_start
        
        # State change callbacks
        self._state_callbacks: List[Callable[[str, Dict[str, Any]], None]] = []
        
        # Background tasks
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False
    
    @property
    def is_available(self) -> bool:
        """Check if Docker is available."""
        return self._client is not None
    
    # =========================================================================
    # Lifecycle
    # =========================================================================
    
    async def start(self) -> bool:
        """Start the device manager."""
        if not self.is_available:
            logger.error("Cannot start: Docker not available")
            return False
        
        # Ensure network exists
        await self._ensure_network()
        
        # Load persisted state
        if self.config.state_file:
            await self._load_state()
        
        # Start monitoring
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_containers())
        
        logger.info("Docker Device Manager started")
        return True
    
    async def stop(self) -> None:
        """Stop the device manager and optionally clean up containers."""
        self._running = False
        
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        
        # Save state
        if self.config.state_file:
            await self._save_state()
        
        # Cleanup containers if configured
        if self.config.cleanup_on_exit:
            await self.remove_all_devices()
        
        logger.info("Docker Device Manager stopped")
    
    async def _ensure_network(self) -> None:
        """Ensure the Docker network exists."""
        if not self._client:
            return
        
        try:
            self._client.networks.get(self.config.network_name)
            logger.debug(f"Network {self.config.network_name} exists")
        except docker.errors.NotFound:
            self._client.networks.create(
                self.config.network_name,
                driver="bridge",
            )
            logger.info(f"Created network: {self.config.network_name}")
    
    # =========================================================================
    # Device Creation
    # =========================================================================
    
    async def create_device(self, config: VirtualDeviceConfig) -> Optional[VirtualDevice]:
        """
        Create and start a virtual device container.
        
        Args:
            config: Device configuration
            
        Returns:
            VirtualDevice if successful, None otherwise
        """
        if not self.is_available:
            logger.error("Cannot create device: Docker not available")
            return None
        
        if config.device_id in self._devices:
            logger.error(f"Device already exists: {config.device_id}")
            return None
        
        # Allocate ports
        serial_port = self._allocate_port("serial")
        tcp_port = self._allocate_port("tcp")
        http_port = self._allocate_port("http") if config.http_enabled else 0
        
        # Create device object
        device = VirtualDevice(
            config=config,
            serial_port=serial_port,
            tcp_port=tcp_port,
            http_port=http_port,
            status=ContainerStatus.CREATING,
            state=config.initial_state.copy(),
            created_at=datetime.utcnow(),
        )
        
        try:
            # Prepare firmware
            firmware_path = await self._prepare_firmware(config)
            
            # Build container configuration
            container_config = self._build_container_config(device, firmware_path)
            
            # Create container
            container = self._client.containers.create(**container_config)
            device.container_id = container.id
            device.status = ContainerStatus.STARTING
            
            # Start container
            container.start()
            device.status = ContainerStatus.RUNNING
            device.started_at = datetime.utcnow()
            
            # Register device
            self._devices[config.device_id] = device
            
            # Wait for device to be ready
            await self._wait_for_device_ready(device)
            
            logger.info(f"Created virtual device: {config.friendly_name} ({config.device_id})")
            return device
            
        except Exception as e:
            logger.error(f"Failed to create device {config.device_id}: {e}")
            device.status = ContainerStatus.ERROR
            return None
    
    def _allocate_port(self, port_type: str) -> int:
        """Allocate a port for device communication."""
        if port_type == "serial":
            port = self._next_serial_port
            self._next_serial_port += 1
        elif port_type == "tcp":
            port = self._next_tcp_port
            self._next_tcp_port += 1
        elif port_type == "http":
            port = self._next_http_port
            self._next_http_port += 1
        else:
            raise ValueError(f"Unknown port type: {port_type}")
        return port
    
    async def _prepare_firmware(self, config: VirtualDeviceConfig) -> str:
        """Prepare firmware binary for the device."""
        if config.firmware_path and os.path.exists(config.firmware_path):
            return config.firmware_path
        
        # Generate firmware from template
        firmware_dir = Path(tempfile.mkdtemp(prefix="vesper_fw_"))
        firmware_path = firmware_dir / "firmware.elf"
        
        # Get template based on device type
        template = self._get_firmware_template(config.device_type)
        
        # For now, just create a placeholder
        # In production, this would compile the template
        firmware_path.write_text(template)
        
        return str(firmware_path)
    
    def _get_firmware_template(self, device_type: DeviceType) -> str:
        """Get firmware template for device type."""
        # Templates define the device behavior
        templates = {
            DeviceType.SWITCH: "switch_firmware_template",
            DeviceType.DIMMER: "dimmer_firmware_template",
            DeviceType.MOTION_SENSOR: "motion_sensor_firmware_template",
            DeviceType.CONTACT_SENSOR: "contact_sensor_firmware_template",
            DeviceType.LOCK: "lock_firmware_template",
        }
        return templates.get(device_type, "generic_firmware_template")
    
    def _build_container_config(
        self,
        device: VirtualDevice,
        firmware_path: str,
    ) -> Dict[str, Any]:
        """Build Docker container configuration."""
        config = device.config
        
        # Environment variables for the container
        env = {
            "DEVICE_ID": config.device_id,
            "DEVICE_TYPE": config.device_type.value,
            "DEVICE_NAME": config.friendly_name,
            "QEMU_MACHINE": config.qemu_machine,
            "QEMU_CPU": config.qemu_cpu,
            "MEMORY_MB": str(config.memory_mb),
            "SERIAL_PORT": str(device.serial_port),
            "TCP_PORT": str(device.tcp_port),
        }
        
        # Port bindings
        ports = {}
        if config.serial_enabled:
            ports[f"{device.serial_port}/tcp"] = device.serial_port
        if config.tcp_enabled:
            ports[f"{device.tcp_port}/tcp"] = device.tcp_port
        if config.http_enabled:
            ports[f"{device.http_port}/tcp"] = device.http_port
        
        # Volume mounts
        volumes = {}
        if firmware_path:
            volumes[firmware_path] = {
                "bind": "/firmware/firmware.elf",
                "mode": "ro",
            }
        
        return {
            "image": config.docker_image,
            "name": config.container_name,
            "environment": env,
            "ports": ports,
            "volumes": volumes,
            "network": self.config.network_name,
            "detach": True,
            "auto_remove": self.config.auto_remove_containers,
            "labels": {
                "vesper.device_id": config.device_id,
                "vesper.device_type": config.device_type.value,
                "vesper.managed": "true",
            },
        }
    
    async def _wait_for_device_ready(
        self,
        device: VirtualDevice,
        timeout: float = 30.0,
    ) -> bool:
        """Wait for device to be ready to communicate."""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                # Try to connect via TCP
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("localhost", device.tcp_port),
                    timeout=2.0,
                )
                device._tcp_reader = reader
                device._tcp_writer = writer
                
                # Send ping command
                writer.write(b'{"cmd":"ping"}\n')
                await writer.drain()
                
                # Wait for response
                response = await asyncio.wait_for(reader.readline(), timeout=2.0)
                if b"pong" in response:
                    logger.debug(f"Device {device.config.device_id} is ready")
                    return True
                    
            except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
                await asyncio.sleep(0.5)
        
        logger.warning(f"Device {device.config.device_id} did not become ready")
        return False
    
    # =========================================================================
    # Device Management
    # =========================================================================
    
    async def remove_device(self, device_id: str) -> bool:
        """Remove a virtual device and its container."""
        device = self._devices.get(device_id)
        if not device:
            logger.error(f"Device not found: {device_id}")
            return False
        
        try:
            # Close connections
            if device._tcp_writer:
                device._tcp_writer.close()
                await device._tcp_writer.wait_closed()
            
            # Stop and remove container
            if device.container_id and self._client:
                try:
                    container = self._client.containers.get(device.container_id)
                    container.stop(timeout=5)
                    container.remove(force=True)
                except docker.errors.NotFound:
                    pass
            
            # Remove from registry
            del self._devices[device_id]
            device.status = ContainerStatus.REMOVED
            
            logger.info(f"Removed device: {device_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to remove device {device_id}: {e}")
            return False
    
    async def remove_all_devices(self) -> None:
        """Remove all virtual devices."""
        device_ids = list(self._devices.keys())
        for device_id in device_ids:
            await self.remove_device(device_id)
    
    def get_device(self, device_id: str) -> Optional[VirtualDevice]:
        """Get a device by ID."""
        return self._devices.get(device_id)
    
    def list_devices(self) -> List[VirtualDevice]:
        """List all devices."""
        return list(self._devices.values())
    
    # =========================================================================
    # Device Communication
    # =========================================================================
    
    async def send_command(
        self,
        device_id: str,
        command: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Send a command to a virtual device.
        
        Args:
            device_id: Device ID
            command: Command name (e.g., "turn_on", "set_level")
            params: Command parameters
            
        Returns:
            Response from device or None on error
        """
        device = self._devices.get(device_id)
        if not device:
            logger.error(f"Device not found: {device_id}")
            return None
        
        if device.status != ContainerStatus.RUNNING:
            logger.error(f"Device not running: {device_id}")
            return None
        
        # Build command message
        message = {
            "cmd": command,
            "params": params or {},
            "timestamp": int(time.time() * 1000),
        }
        
        try:
            # Send via TCP
            if device._tcp_writer:
                device._tcp_writer.write(json.dumps(message).encode() + b"\n")
                await device._tcp_writer.drain()
                
                # Wait for response
                if device._tcp_reader:
                    response_line = await asyncio.wait_for(
                        device._tcp_reader.readline(),
                        timeout=5.0,
                    )
                    response = json.loads(response_line.decode())
                    
                    # Update local state
                    if "state" in response:
                        device.state.update(response["state"])
                        await self._notify_state_change(device_id, device.state)
                    
                    return response
                    
        except asyncio.TimeoutError:
            logger.error(f"Command timeout for device {device_id}")
        except Exception as e:
            logger.error(f"Command error for device {device_id}: {e}")
        
        return None
    
    async def get_device_state(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Get current state of a device."""
        response = await self.send_command(device_id, "get_state")
        if response and "state" in response:
            return response["state"]
        return self._devices.get(device_id, VirtualDevice).state
    
    async def set_device_state(
        self,
        device_id: str,
        state: Dict[str, Any],
    ) -> bool:
        """Set device state."""
        response = await self.send_command(device_id, "set_state", {"state": state})
        return response is not None and response.get("success", False)
    
    # =========================================================================
    # State Callbacks
    # =========================================================================
    
    def on_state_change(
        self,
        callback: Callable[[str, Dict[str, Any]], None],
    ) -> None:
        """Register a callback for state changes."""
        self._state_callbacks.append(callback)
    
    async def _notify_state_change(
        self,
        device_id: str,
        state: Dict[str, Any],
    ) -> None:
        """Notify callbacks of state change."""
        for callback in self._state_callbacks:
            try:
                result = callback(device_id, state)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"State callback error: {e}")
    
    # =========================================================================
    # Monitoring
    # =========================================================================
    
    async def _monitor_containers(self) -> None:
        """Background task to monitor container status."""
        while self._running:
            try:
                for device_id, device in list(self._devices.items()):
                    if not device.container_id or not self._client:
                        continue
                    
                    try:
                        container = self._client.containers.get(device.container_id)
                        status = container.status
                        
                        if status == "running":
                            device.status = ContainerStatus.RUNNING
                        elif status == "exited":
                            device.status = ContainerStatus.STOPPED
                        elif status == "paused":
                            device.status = ContainerStatus.STOPPED
                        else:
                            device.status = ContainerStatus.ERROR
                            
                    except docker.errors.NotFound:
                        device.status = ContainerStatus.REMOVED
                        
            except Exception as e:
                logger.error(f"Monitor error: {e}")
            
            await asyncio.sleep(5.0)
    
    # =========================================================================
    # State Persistence
    # =========================================================================
    
    async def _save_state(self) -> None:
        """Save device states to file."""
        if not self.config.state_file:
            return
        
        state_data = {
            device_id: {
                "config": {
                    "device_id": device.config.device_id,
                    "device_type": device.config.device_type.value,
                    "friendly_name": device.config.friendly_name,
                    "room": device.config.room,
                },
                "state": device.state,
            }
            for device_id, device in self._devices.items()
        }
        
        try:
            with open(self.config.state_file, "w") as f:
                json.dump(state_data, f, indent=2)
            logger.debug(f"Saved state to {self.config.state_file}")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
    
    async def _load_state(self) -> None:
        """Load device states from file."""
        if not self.config.state_file or not os.path.exists(self.config.state_file):
            return
        
        try:
            with open(self.config.state_file, "r") as f:
                state_data = json.load(f)
            
            # Recreate devices from saved state
            for device_id, data in state_data.items():
                config_data = data.get("config", {})
                config = VirtualDeviceConfig(
                    device_id=config_data.get("device_id", device_id),
                    device_type=DeviceType(config_data.get("device_type", "switch")),
                    friendly_name=config_data.get("friendly_name", "Unknown"),
                    room=config_data.get("room"),
                    initial_state=data.get("state", {}),
                )
                await self.create_device(config)
                
            logger.info(f"Loaded {len(state_data)} devices from {self.config.state_file}")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")


# =============================================================================
# Dockerfile Template
# =============================================================================

DOCKERFILE_TEMPLATE = """
# Dockerfile for VESPER Virtual Device
# Runs QEMU with firmware for simulated IoT devices

FROM ubuntu:22.04

# Install QEMU and dependencies
RUN apt-get update && apt-get install -y \\
    qemu-system-arm \\
    qemu-system-misc \\
    socat \\
    netcat-openbsd \\
    python3 \\
    python3-pip \\
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies for device bridge
RUN pip3 install aiohttp

# Create firmware directory
RUN mkdir -p /firmware

# Copy device bridge script
COPY device_bridge.py /app/device_bridge.py

# Copy startup script
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

WORKDIR /app

# Environment variables (set at runtime)
ENV DEVICE_ID=""
ENV DEVICE_TYPE=""
ENV QEMU_MACHINE="lm3s6965evb"
ENV QEMU_CPU="cortex-m3"
ENV MEMORY_MB="16"
ENV SERIAL_PORT="10000"
ENV TCP_PORT="11000"

EXPOSE ${SERIAL_PORT} ${TCP_PORT}

ENTRYPOINT ["/app/entrypoint.sh"]
"""


ENTRYPOINT_SCRIPT = """#!/bin/bash

# Start QEMU in background with serial port exposed
qemu-system-arm \\
    -M ${QEMU_MACHINE} \\
    -cpu ${QEMU_CPU} \\
    -m ${MEMORY_MB}M \\
    -nographic \\
    -kernel /firmware/firmware.elf \\
    -serial tcp::${SERIAL_PORT},server,nowait \\
    &

# Wait for QEMU to start
sleep 2

# Start the device bridge
python3 /app/device_bridge.py \\
    --device-id "${DEVICE_ID}" \\
    --device-type "${DEVICE_TYPE}" \\
    --serial-port "${SERIAL_PORT}" \\
    --tcp-port "${TCP_PORT}"
"""


# =============================================================================
# Helper Functions
# =============================================================================

def create_device_dockerfile(output_dir: str) -> None:
    """Create Dockerfile and support files for building device images."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Write Dockerfile
    (output_path / "Dockerfile").write_text(DOCKERFILE_TEMPLATE)
    
    # Write entrypoint script
    (output_path / "entrypoint.sh").write_text(ENTRYPOINT_SCRIPT)
    
    # Write device bridge script (placeholder)
    device_bridge = '''#!/usr/bin/env python3
"""Device bridge for QEMU-based virtual device."""
import asyncio
import json
import argparse

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--device-type", required=True)
    parser.add_argument("--serial-port", type=int, required=True)
    parser.add_argument("--tcp-port", type=int, required=True)
    args = parser.parse_args()
    
    print(f"Device bridge started: {args.device_id}")
    
    # TODO: Implement actual bridge logic
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
'''
    (output_path / "device_bridge.py").write_text(device_bridge)
    
    logger.info(f"Created Dockerfile at {output_path}")
