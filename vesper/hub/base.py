"""
Abstract Hub interface for VESPER.

All hub implementations (virtual, physical) must implement this interface.
The hub is the central routing point for all device traffic, enabling
monitoring, packet inspection, and attack injection.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class HubState(Enum):
    """Hub lifecycle states."""
    INITIALIZING = auto()
    READY = auto()
    RUNNING = auto()
    PAUSED = auto()
    ERROR = auto()
    STOPPED = auto()


class HubCapability(Enum):
    """Capabilities a hub can support."""
    MATTER = "matter"
    MATTER_BRIDGE = "matter_bridge"
    ZIGBEE = "zigbee"
    ZWAVE = "zwave"
    WIFI = "wifi"
    BLUETOOTH = "bluetooth"
    SMARTTHINGS = "smartthings"
    HOMEASSISTANT = "homeassistant"


@dataclass
class DeviceRecord:
    """A device registered with the hub."""
    device_id: str
    device_type: str
    protocol: str  # matter, smartthings, etc.
    name: str = ""
    room: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)
    ip_address: Optional[str] = None
    mac_address: Optional[str] = None
    manufacturer: str = ""
    model: str = ""
    firmware_version: str = ""
    last_seen: float = 0.0
    online: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HubTrafficRecord:
    """A record of a message passing through the hub."""
    timestamp: float
    source_id: str
    target_id: str
    protocol: str
    direction: str  # "inbound" | "outbound" | "internal"
    topic: str = ""
    payload_size: int = 0
    payload_summary: str = ""
    latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseHub(ABC):
    """
    Abstract base class for all VESPER hubs.

    The hub acts as a central router for all device communication,
    providing a unified interface for device management regardless
    of the underlying protocol (Matter, SmartThings, etc.).
    """

    def __init__(self, hub_id: str = "vesper-hub", name: str = "VESPER Hub"):
        self.hub_id = hub_id
        self.name = name
        self.state = HubState.INITIALIZING
        self._devices: dict[str, DeviceRecord] = {}
        self._traffic_log: list[HubTrafficRecord] = []
        self._traffic_callbacks: list[Callable[[HubTrafficRecord], None]] = []
        self._device_callbacks: list[Callable[[str, DeviceRecord], None]] = []
        self._max_traffic_log = 10000
        self._capabilities: set[HubCapability] = set()
        self._start_time: float = 0.0

    # ── Lifecycle ──────────────────────────────────────────────────────

    @abstractmethod
    async def start(self) -> None:
        """Start the hub and all its services."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the hub and clean up resources."""
        ...

    @abstractmethod
    async def health_check(self) -> dict[str, Any]:
        """Return health status of the hub and its services."""
        ...

    # ── Device Management ──────────────────────────────────────────────

    def register_device(self, device: DeviceRecord) -> None:
        """Register a device with the hub."""
        device.last_seen = time.time()
        self._devices[device.device_id] = device
        logger.info(f"[{self.hub_id}] Registered device: {device.device_id} ({device.protocol})")
        for cb in self._device_callbacks:
            try:
                cb("registered", device)
            except Exception as e:
                logger.error(f"Device callback error: {e}")

    def unregister_device(self, device_id: str) -> Optional[DeviceRecord]:
        """Remove a device from the hub."""
        device = self._devices.pop(device_id, None)
        if device:
            logger.info(f"[{self.hub_id}] Unregistered device: {device_id}")
            for cb in self._device_callbacks:
                try:
                    cb("unregistered", device)
                except Exception:
                    pass
        return device

    def get_device(self, device_id: str) -> Optional[DeviceRecord]:
        """Get a device by ID."""
        return self._devices.get(device_id)

    def get_all_devices(self) -> dict[str, DeviceRecord]:
        """Get all registered devices."""
        return dict(self._devices)

    def get_devices_by_protocol(self, protocol: str) -> list[DeviceRecord]:
        """Get all devices using a specific protocol."""
        return [d for d in self._devices.values() if d.protocol == protocol]

    def get_devices_by_room(self, room: str) -> list[DeviceRecord]:
        """Get all devices in a specific room."""
        return [d for d in self._devices.values() if d.room == room]

    # ── Device State ───────────────────────────────────────────────────

    @abstractmethod
    async def send_command(
        self,
        device_id: str,
        command: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Send a command to a device through the hub.

        Args:
            device_id: Target device ID.
            command: Command name (e.g., "on", "off", "set_brightness").
            params: Optional command parameters.

        Returns:
            Response from the device.
        """
        ...

    @abstractmethod
    async def get_device_state(self, device_id: str) -> dict[str, Any]:
        """Get current state of a device."""
        ...

    @abstractmethod
    async def set_device_state(
        self, device_id: str, state: dict[str, Any]
    ) -> bool:
        """Set state on a device. Returns True if successful."""
        ...

    # ── Traffic Monitoring ─────────────────────────────────────────────

    def record_traffic(self, record: HubTrafficRecord) -> None:
        """Record a traffic event passing through the hub."""
        self._traffic_log.append(record)
        if len(self._traffic_log) > self._max_traffic_log:
            self._traffic_log = self._traffic_log[-self._max_traffic_log:]
        for cb in self._traffic_callbacks:
            try:
                cb(record)
            except Exception as e:
                logger.error(f"Traffic callback error: {e}")

    def get_traffic_log(
        self,
        limit: int = 100,
        protocol: Optional[str] = None,
        device_id: Optional[str] = None,
        since: Optional[float] = None,
    ) -> list[HubTrafficRecord]:
        """Query the traffic log with optional filters."""
        records = self._traffic_log
        if protocol:
            records = [r for r in records if r.protocol == protocol]
        if device_id:
            records = [
                r
                for r in records
                if r.source_id == device_id or r.target_id == device_id
            ]
        if since:
            records = [r for r in records if r.timestamp >= since]
        return records[-limit:]

    def on_traffic(self, callback: Callable[[HubTrafficRecord], None]) -> None:
        """Register a callback for traffic events."""
        self._traffic_callbacks.append(callback)

    def on_device_change(
        self, callback: Callable[[str, DeviceRecord], None]
    ) -> None:
        """Register a callback for device registration/state changes."""
        self._device_callbacks.append(callback)

    # ── Capabilities ───────────────────────────────────────────────────

    @property
    def capabilities(self) -> set[HubCapability]:
        """Get hub capabilities."""
        return self._capabilities

    def supports(self, capability: HubCapability) -> bool:
        """Check if the hub supports a capability."""
        return capability in self._capabilities

    # ── Stats ──────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Get hub statistics."""
        now = time.time()
        online_devices = sum(1 for d in self._devices.values() if d.online)
        protocols = {}
        for d in self._devices.values():
            protocols[d.protocol] = protocols.get(d.protocol, 0) + 1

        return {
            "hub_id": self.hub_id,
            "name": self.name,
            "state": self.state.name,
            "uptime_seconds": now - self._start_time if self._start_time else 0,
            "total_devices": len(self._devices),
            "online_devices": online_devices,
            "offline_devices": len(self._devices) - online_devices,
            "protocols": protocols,
            "capabilities": [c.value for c in self._capabilities],
            "traffic_records": len(self._traffic_log),
        }
