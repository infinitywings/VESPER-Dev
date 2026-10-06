"""
Matter–Firmware Bridge: Connects Habitat 3D simulation to the
matter.js bridge, exposing simulated devices as real Matter endpoints
discoverable by python-matter-server and Home Assistant.

This is the **critical integration layer** that closes the loop:

    3D Humanoid moves
      → Habitat sensor detects motion (EventBus)
      → MatterFirmwareBridge translates to REST call → matter.js bridge
      → matter.js exposes device as Matter endpoint
      → python-matter-server discovers endpoint
      → Home Assistant shows device with live state
      → Security attacks target Matter fabric

Architecture:
    ┌──────────────────────────────────────────────────────┐
    │  Habitat 3D Simulation                                │
    │    EventBus  ─────────────────────────────────┐       │
    │      ↑↓                                       ↓       │
    │  MotionSensor / DoorSensor / LightSensor      │       │
    └───────────────────────────────────────────────┼───────┘
                                                    │
    ┌───────────────────────────────────────────────┼───────┐
    │  MatterFirmwareBridge  (this module)          │       │
    │    EventBus subscriber  ←─────────────────────┘       │
    │      ↓                                                │
    │    WiFiEmulator.route_to_bridge()                      │
    │      │                                                │
    │      ├─ Docker: station namespace → 802.11 → AP       │
    │      │   (tshark captures on ap1-wlan1)               │
    │      └─ Sim: latency/jitter/loss → direct HTTP       │
    │      ↓                                                │
    │    matter.js bridge → python-matter-server → HA       │
    └───────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from vesper.core.event_bus import EventBus, Event, EventPriority

logger = logging.getLogger(__name__)

# Matter bridge client
try:
    from vesper.matter.bridge_client import MatterBridgeClient
    MATTER_BRIDGE_AVAILABLE = True
except ImportError:
    MATTER_BRIDGE_AVAILABLE = False
    logger.warning("Matter bridge client not available.")


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class BridgeConfig:
    """Configuration for the Matter-Firmware bridge."""
    # Matter bridge REST API (runs as Docker container or locally)
    matter_bridge_url: str = "http://localhost:8484"

    # Serial fallback (direct QEMU serial TCP)
    serial_enabled: bool = False
    serial_host: str = "localhost"

    # Event mapping
    motion_cooldown: float = 3.0     # Min seconds between motion events
    door_debounce: float = 1.0       # Min seconds between door events
    temperature_interval: float = 30.0  # How often to push temp readings

    # Behaviour
    forward_to_smartthings: bool = False  # Also push to SmartThings cloud


@dataclass
class DeviceMapping:
    """Maps a 3D simulation device to its Matter bridge counterpart."""
    sim_device_id: str          # ID in Habitat / EventBus (e.g., "motion_living_room")
    device_type: str            # "motion_sensor", "smart_light", etc.
    room: str                   # Room name
    matter_device_id: str       # ID registered on the matter.js bridge
    serial_port: Optional[int] = None  # QEMU serial TCP port (optional)
    last_event_time: float = 0.0


# ── Default device fleet (matches docker-compose.yml) ────────────────────────

DEFAULT_DEVICE_MAP: List[DeviceMapping] = [
    DeviceMapping(
        sim_device_id="sim_kitchen_light",
        device_type="smart_light",
        room="kitchen",
        matter_device_id="kitchen-light-01",
        serial_port=5561,
    ),
    DeviceMapping(
        sim_device_id="sim_living_room_light",
        device_type="smart_light",
        room="living room",
        matter_device_id="living-room-light-01",
        serial_port=5562,
    ),
    DeviceMapping(
        sim_device_id="sim_bedroom_light",
        device_type="smart_light",
        room="bedroom",
        matter_device_id="bedroom-light-01",
        serial_port=5563,
    ),
    DeviceMapping(
        sim_device_id="sim_motion_sensor",
        device_type="motion_sensor",
        room="hallway",
        matter_device_id="motion-sensor-01",
        serial_port=5564,
    ),
    DeviceMapping(
        sim_device_id="sim_temp_sensor",
        device_type="temperature_sensor",
        room="living room",
        matter_device_id="temp-sensor-01",
        serial_port=5565,
    ),
    DeviceMapping(
        sim_device_id="sim_door_sensor",
        device_type="door_sensor",
        room="entrance",
        matter_device_id="door-sensor-01",
        serial_port=5566,
    ),
    DeviceMapping(
        sim_device_id="sim_smart_plug",
        device_type="smart_plug",
        room="kitchen",
        matter_device_id="smart-plug-01",
        serial_port=5567,
    ),
    DeviceMapping(
        sim_device_id="sim_humidity_sensor",
        device_type="humidity_sensor",
        room="bathroom",
        matter_device_id="humidity-sensor-01",
        serial_port=5568,
    ),
]


# ── Bridge implementation ─────────────────────────────────────────────────────

class MatterFirmwareBridge:
    """
    Bridges the Habitat 3D EventBus to the matter.js bridge.

    Subscribes to EventBus events (motion_detected, door_opened, etc.)
    and forwards them through the WiFiEmulator → Matter bridge pipeline.

    When a WiFiEmulator is provided, ALL traffic flows through it:
      - Docker mode: real 802.11 frames (capturable by tshark)
      - Sim mode: simulated latency/jitter/loss with full traffic logging

    Falls back to direct MatterBridgeClient REST calls if no WiFiEmulator
    is available (e.g. unit tests).

    State updates from Matter controllers (e.g., Home Assistant toggling
    a light) are polled periodically and published back to the EventBus.
    """

    def __init__(
        self,
        event_bus: EventBus,
        config: Optional[BridgeConfig] = None,
        device_map: Optional[List[DeviceMapping]] = None,
        wifi_emulator: Optional[Any] = None,
        hub: Optional[Any] = None,         # injected VirtualHub
        registry: Optional[Any] = None,    # injected DeviceRegistry
    ):
        self.event_bus = event_bus
        self.config = config or BridgeConfig()
        self.device_map = {d.sim_device_id: d for d in (device_map or DEFAULT_DEVICE_MAP)}

        # Reverse lookup: room+type → DeviceMapping
        self._room_type_map: Dict[Tuple[str, str], DeviceMapping] = {}
        for d in self.device_map.values():
            self._room_type_map[(d.room.lower(), d.device_type)] = d

        # WiFi emulator — routes traffic through the network
        self._wifi: Optional[Any] = wifi_emulator  # WiFiEmulator instance
        self._hub: Optional[Any] = hub              # VirtualHub instance
        self._registry: Optional[Any] = registry    # DeviceRegistry instance

        # Matter bridge client (fallback when no WiFi emulator)
        self._bridge: Optional[MatterBridgeClient] = None
        self._bridge_connected = False

        # Statistics
        self.stats = {
            "events_received": 0,
            "matter_published": 0,
            "matter_received": 0,
            "errors": 0,
        }

        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the bridge: connect to Matter bridge, subscribe to EventBus."""
        logger.info("Starting Matter-Firmware bridge...")
        self._running = True

        # 1. Subscribe to EventBus events from 3D simulation
        self._subscribe_eventbus()

        # 2. Connect to matter.js bridge REST API
        if MATTER_BRIDGE_AVAILABLE:
            self._connect_matter_bridge()

        logger.info(
            f"Matter-Firmware bridge started "
            f"(matter={'connected' if self._bridge_connected else 'disconnected'}, "
            f"wifi_routed={self._wifi is not None}, "
            f"devices={len(self.device_map)})"
        )

    def stop(self) -> None:
        """Stop the bridge."""
        logger.info("Stopping Matter-Firmware bridge...")
        self._running = False
        self._bridge = None
        self._bridge_connected = False
        logger.info(f"Bridge stopped. Stats: {self.stats}")

    # ── EventBus → Matter bridge (outbound) ───────────────────────────────

    def _subscribe_eventbus(self) -> None:
        """Subscribe to relevant EventBus events from 3D simulation."""
        self.event_bus.subscribe("motion_detected", self._on_motion_detected)
        self.event_bus.subscribe("motion_cleared", self._on_motion_cleared)
        self.event_bus.subscribe("door_opened", self._on_door_event)
        self.event_bus.subscribe("door_closed", self._on_door_event)
        self.event_bus.subscribe("device_state_changed", self._on_device_state_changed)
        self.event_bus.subscribe("light_on", self._on_light_event)
        self.event_bus.subscribe("light_off", self._on_light_event)
        self.event_bus.subscribe("temperature_reading", self._on_sensor_reading)
        self.event_bus.subscribe("humidity_reading", self._on_sensor_reading)
        self.event_bus.subscribe("agent_entered_room", self._on_agent_room_change)
        self.event_bus.subscribe("agent_left_room", self._on_agent_room_change)
        logger.debug("Subscribed to EventBus events")

    def _on_motion_detected(self, event: Event) -> None:
        """Handle motion detected → update Matter device."""
        self.stats["events_received"] += 1
        room = event.payload.get("room", "").lower()
        device_id = event.payload.get("device_id", "")

        mapping = self._find_device(room, "motion_sensor", device_id)
        if not mapping:
            return

        now = time.time()
        if now - mapping.last_event_time < self.config.motion_cooldown:
            return
        mapping.last_event_time = now

        self._send_state_update(mapping, {"motion": True, "occupancy": True})

    def _on_motion_cleared(self, event: Event) -> None:
        """Handle motion cleared → update Matter device."""
        room = event.payload.get("room", "").lower()
        mapping = self._find_device(room, "motion_sensor")
        if mapping:
            self._send_state_update(mapping, {"motion": False, "occupancy": False})

    def _on_door_event(self, event: Event) -> None:
        """Handle door open/close → update Matter contact sensor."""
        self.stats["events_received"] += 1
        room = event.payload.get("room", "").lower()
        is_open = event.event_type == "door_opened"

        mapping = self._find_device(room, "door_sensor")
        if not mapping:
            return

        now = time.time()
        if now - mapping.last_event_time < self.config.door_debounce:
            return
        mapping.last_event_time = now

        self._send_state_update(mapping, {"open": is_open, "contact": not is_open})

    def _on_device_state_changed(self, event: Event) -> None:
        """Handle generic device state change."""
        self.stats["events_received"] += 1
        device_id = event.payload.get("device_id", "")
        new_state = event.payload.get("new_state", "")

        mapping = self.device_map.get(device_id)
        if mapping:
            power = new_state == "on" if isinstance(new_state, str) else bool(new_state)
            self._send_state_update(mapping, {"power": "on" if power else "off"})

    def _on_light_event(self, event: Event) -> None:
        """Handle light on/off events."""
        self.stats["events_received"] += 1
        room = event.payload.get("room", "").lower()
        is_on = event.event_type == "light_on"

        mapping = self._find_device(room, "smart_light")
        if mapping:
            self._send_state_update(mapping, {"power": "on" if is_on else "off"})

    def _on_sensor_reading(self, event: Event) -> None:
        """Handle environmental sensor readings."""
        self.stats["events_received"] += 1
        room = event.payload.get("room", "").lower()

        if event.event_type == "temperature_reading":
            mapping = self._find_device(room, "temperature_sensor")
            if mapping:
                temp = event.payload.get("temperature", 22.0)
                self._send_state_update(mapping, {"temperature": temp})

        elif event.event_type == "humidity_reading":
            mapping = self._find_device(room, "humidity_sensor")
            if mapping:
                humidity = event.payload.get("humidity", 50.0)
                self._send_state_update(mapping, {"humidity": humidity})

    def _on_agent_room_change(self, event: Event) -> None:
        """Handle agent room transitions → update presence sensors."""
        room = event.payload.get("room", "").lower()
        entering = event.event_type == "agent_entered_room"

        mapping = self._find_device(room, "motion_sensor")
        if mapping:
            self._send_state_update(mapping, {
                "motion": entering,
                "occupancy": entering,
            })

    # ── Matter bridge communication ───────────────────────────────────────

    def _send_state_update(self, mapping: DeviceMapping, state: Dict[str, Any]) -> None:
        """Send a state update to the matter.js bridge.

        Routes through WiFiEmulator when available (so traffic is visible
        on the emulated WiFi and captured by tshark).  Falls back to direct
        MatterBridgeClient REST call otherwise.
        """
        # Prefer WiFi-routed path (tracked + capturable)
        if self._wifi is not None:
            try:
                self._wifi.route_to_bridge(
                    mapping.matter_device_id,
                    "PUT",
                    f"/devices/{mapping.matter_device_id}/state",
                    state,
                )
                self.stats["matter_published"] += 1
                logger.debug(f"Matter update (via WiFi): {mapping.matter_device_id} → {state}")
                return
            except Exception as e:
                self.stats["errors"] += 1
                logger.warning(f"WiFi route failed for {mapping.matter_device_id}: {e}")
                # Fall through to direct bridge call

        # Fallback: direct bridge client (no WiFi tracking)
        if not self._bridge or not self._bridge_connected:
            # Last resort: update registry directly so state is not lost
            if self._registry:
                self._registry.update_state(mapping.matter_device_id, {"state": state})
            return

        try:
            self._bridge.update_state_sync(mapping.matter_device_id, state)
            self.stats["matter_published"] += 1
            logger.debug(f"Matter update (direct): {mapping.matter_device_id} → {state}")
        except Exception as e:
            self.stats["errors"] += 1
            logger.warning(f"Matter bridge error for {mapping.matter_device_id}: {e}")

        # Also update registry so it stays in sync
        if self._registry:
            self._registry.update_state(mapping.matter_device_id, {"state": state})

    def _find_device(
        self,
        room: str,
        device_type: str,
        device_id: str = "",
    ) -> Optional[DeviceMapping]:
        """Find a DeviceMapping by room+type or sim device ID."""
        if device_id and device_id in self.device_map:
            return self.device_map[device_id]
        return self._room_type_map.get((room, device_type))

    def _connect_matter_bridge(self) -> None:
        """Connect to the matter.js bridge and register all devices.

        When WiFiEmulator is available, device registration also flows through
        the WiFi network so the initial POST /devices/bulk is captured.
        """
        try:
            self._bridge = MatterBridgeClient(
                base_url=self.config.matter_bridge_url,
            )

            if not self._bridge.wait_ready_sync(max_wait=30):
                logger.warning("Matter bridge not reachable")
                self._bridge = None
                return

            self._bridge_connected = True
            logger.info(f"Connected to Matter bridge at {self.config.matter_bridge_url}")

            # Register all devices on the bridge
            devices_to_add = []
            for mapping in self.device_map.values():
                devices_to_add.append({
                    "id": mapping.matter_device_id,
                    "type": mapping.device_type,
                    "name": f"{mapping.room.title()} {mapping.device_type.replace('_', ' ').title()}",
                    "room": mapping.room,
                    "state": {},
                })

            if not devices_to_add:
                return

            # Route bulk registration through WiFi if available
            if self._wifi is not None:
                result = self._wifi.route_to_bridge(
                    devices_to_add[0]["id"],  # Use first device for station routing
                    "POST", "/devices/bulk", devices_to_add,
                )
                created = len(result) if isinstance(result, list) else 0
                logger.info(f"Registered {created}/{len(devices_to_add)} devices (via WiFi)")
            else:
                results = self._bridge.add_devices_bulk_sync(devices_to_add)
                created = sum(1 for r in results if r.get("status") == "created")
                logger.info(f"Registered {created}/{len(devices_to_add)} devices (direct)")

        except Exception as e:
            logger.error(f"Failed to connect to Matter bridge: {e}")
            self._bridge = None

    # ── Public API ────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Get bridge statistics (includes WiFi traffic summary if available)."""
        stats = {
            **self.stats,
            "bridge_connected": self._bridge_connected,
            "wifi_routed": self._wifi is not None,
            "num_devices": len(self.device_map),
        }
        if self._wifi is not None:
            stats["wifi_traffic"] = self._wifi.traffic.summary()
        return stats

    def get_device_status(self) -> List[Dict[str, Any]]:
        """Get status of all mapped devices."""
        devices = []
        for mapping in self.device_map.values():
            devices.append({
                "sim_id": mapping.sim_device_id,
                "matter_id": mapping.matter_device_id,
                "type": mapping.device_type,
                "room": mapping.room,
            })
        return devices

    def add_device_mapping(self, mapping: DeviceMapping) -> None:
        """Add a new device mapping at runtime."""
        self.device_map[mapping.sim_device_id] = mapping
        self._room_type_map[(mapping.room.lower(), mapping.device_type)] = mapping

        # Register on bridge if connected
        if self._bridge and self._bridge_connected:
            try:
                self._bridge.add_device_sync(
                    device_id=mapping.matter_device_id,
                    device_type=mapping.device_type,
                    name=f"{mapping.room.title()} {mapping.device_type.replace('_', ' ').title()}",
                    room=mapping.room,
                )
            except Exception as e:
                logger.warning(f"Failed to register device on bridge: {e}")

    def health_check(self) -> Dict[str, Any]:
        """Check bridge health."""
        bridge_ok = False
        if self._bridge:
            try:
                data = self._bridge._get_sync("/health")
                bridge_ok = data.get("status") == "ok"
            except Exception:
                pass

        return {
            "bridge_connected": bridge_ok,
            "running": self._running,
            "devices": len(self.device_map),
            "stats": self.stats,
        }


# Keep backward compatibility alias
WiFiFirmwareBridge = MatterFirmwareBridge
