"""
Canonical Device Registry for VESPER.

Single source of truth for all device state. Replaces the 4 separate
registries (Environment._devices, VirtualHub._devices,
integrations/device_registry, IoTDeviceManager) with one thread-safe
shared store.

Every layer (Engine, Hub, WiFi, Evaluator, Dashboard) reads from and
writes to this registry. State-change callbacks allow reactive updates.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Type alias for state-change callbacks
StateChangeCallback = Callable[[str, Dict[str, Any], Dict[str, Any]], None]

# ── Singleton guard (set by VesperEngine in strict mode) ──────────────────
_INSTANCE_COUNT = 0
_STRICT_MODE = False  # Set to True by VesperEngine when strict=True


@dataclass
class DeviceEntry:
    """A device registered in the canonical registry."""
    device_id: str
    device_type: str            # motion_sensor, contact_sensor, light, ...
    protocol: str = "matter"    # matter | smartthings | homeassistant
    name: str = ""
    room: str = ""
    zone_id: str = ""
    state: Dict[str, Any] = field(default_factory=dict)
    online: bool = True
    last_seen: float = 0.0
    ip_address: Optional[str] = None
    mac_address: Optional[str] = None
    wifi_station: Optional[str] = None   # e.g. "sta1"
    manufacturer: str = ""
    model: str = ""
    firmware_version: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON/dashboard."""
        return {
            "device_id": self.device_id,
            "device_type": self.device_type,
            "protocol": self.protocol,
            "name": self.name,
            "room": self.room,
            "zone_id": self.zone_id,
            "state": dict(self.state),
            "online": self.online,
            "last_seen": self.last_seen,
            "ip_address": self.ip_address,
            "mac_address": self.mac_address,
            "wifi_station": self.wifi_station,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "firmware_version": self.firmware_version,
            "metadata": dict(self.metadata),
        }


class DeviceRegistry:
    """
    Thread-safe canonical device registry.

    Created once in VesperEngine, injected into every component.
    Optionally publishes state-change events on the EventBus.
    """

    def __init__(self, event_bus=None):
        global _INSTANCE_COUNT
        _INSTANCE_COUNT += 1
        if _STRICT_MODE and _INSTANCE_COUNT > 1:
            raise RuntimeError(
                f"STRICT MODE: {self.__class__.__name__} instantiated "
                f"{_INSTANCE_COUNT} times. Only VesperEngine may create this object."
            )
        elif _INSTANCE_COUNT > 1:
            logger.warning("%s instantiated %d times", self.__class__.__name__, _INSTANCE_COUNT)

        self._devices: Dict[str, DeviceEntry] = {}
        self._lock = threading.Lock()
        self._event_bus = event_bus
        self._listeners: List[StateChangeCallback] = []

    # —— Registration ——————————————————————————————————————

    def register(self, device_id: str, entry: DeviceEntry) -> None:
        """Register a new device. Raises ValueError if already exists."""
        with self._lock:
            if device_id in self._devices:
                raise ValueError(f"Device {device_id} already registered")
            entry.device_id = device_id
            entry.last_seen = time.time()
            self._devices[device_id] = entry
        logger.debug("Registry: registered %s (%s)", device_id, entry.device_type)
        self._publish_event("device.registered", device_id, {}, entry.state)

    def register_or_update(self, device_id: str, entry: DeviceEntry) -> None:
        """Register a device, or update if it already exists."""
        with self._lock:
            entry.device_id = device_id
            entry.last_seen = time.time()
            self._devices[device_id] = entry
        logger.debug("Registry: register_or_update %s", device_id)

    def unregister(self, device_id: str) -> bool:
        """Remove a device from the registry."""
        with self._lock:
            entry = self._devices.pop(device_id, None)
        if entry:
            logger.debug("Registry: unregistered %s", device_id)
            self._publish_event("device.unregistered", device_id, entry.state, {})
            return True
        return False

    # —— Lookup ————————————————————————————————————————————

    def get(self, device_id: str) -> Optional[DeviceEntry]:
        """Get a device by ID (returns None if not found)."""
        with self._lock:
            return self._devices.get(device_id)

    def all_devices(self) -> List[DeviceEntry]:
        """Return a list of all registered devices."""
        with self._lock:
            return list(self._devices.values())

    def by_type(self, device_type: str) -> List[DeviceEntry]:
        """Get all devices of a given type."""
        with self._lock:
            return [d for d in self._devices.values() if d.device_type == device_type]

    def by_room(self, room: str) -> List[DeviceEntry]:
        """Get all devices in a room."""
        with self._lock:
            return [d for d in self._devices.values() if d.room == room]

    def by_protocol(self, protocol: str) -> List[DeviceEntry]:
        """Get all devices using a protocol."""
        with self._lock:
            return [d for d in self._devices.values() if d.protocol == protocol]

    @property
    def count(self) -> int:
        """Number of registered devices."""
        return len(self._devices)

    # —— State updates —————————————————————————————————————

    def update_state(self, device_id: str, new_state: Dict[str, Any]) -> bool:
        """
        Merge ``new_state`` into the device's current state.

        Fires state-change callbacks and EventBus event.
        Returns False if device not found.
        """
        with self._lock:
            entry = self._devices.get(device_id)
            if not entry:
                return False
            old_state = dict(entry.state)
            entry.state.update(new_state)
            entry.last_seen = time.time()

        # Notify listeners (outside lock)
        for cb in self._listeners:
            try:
                cb(device_id, old_state, entry.state)
            except Exception as exc:
                logger.error("Registry listener error: %s", exc)

        self._publish_event("device.state_changed", device_id, old_state, entry.state)
        return True

    def set_online(self, device_id: str, online: bool) -> None:
        """Update a device's online status."""
        with self._lock:
            entry = self._devices.get(device_id)
            if entry:
                entry.online = online
                entry.last_seen = time.time()

    # —— Listeners / callbacks —————————————————————————————

    def add_listener(self, callback: StateChangeCallback) -> None:
        """Register a callback invoked on every state change."""
        self._listeners.append(callback)

    # —— Snapshot ——————————————————————————————————————————

    def snapshot(self) -> Dict[str, Any]:
        """Return a serializable snapshot of the full registry."""
        with self._lock:
            return {
                "device_count": len(self._devices),
                "devices": {
                    did: entry.to_dict()
                    for did, entry in self._devices.items()
                },
            }

    # —— Private helpers ———————————————————————————————————

    def _publish_event(
        self,
        event_type: str,
        device_id: str,
        old_state: Dict[str, Any],
        new_state: Dict[str, Any],
    ) -> None:
        """Publish a device event on the shared EventBus (if available)."""
        if self._event_bus is None:
            return
        try:
            from vesper.core.event_bus import Event
            self._event_bus.publish(Event.create(
                event_type=event_type,
                source_id=device_id,
                payload={
                    "device_id": device_id,
                    "old_state": old_state,
                    "new_state": new_state,
                },
            ))
        except Exception as exc:
            logger.debug("Registry EventBus publish error: %s", exc)

    def __len__(self) -> int:
        return len(self._devices)

    def __contains__(self, device_id: str) -> bool:
        return device_id in self._devices

    def __repr__(self) -> str:
        return f"DeviceRegistry({len(self._devices)} devices)"
