"""
Environment Manager for Vesper.

Manages the simulation environment, IoT device registration, and coordinate system.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional
import uuid

from vesper.config import Config, load_config
from vesper.core.event_bus import EventBus

if TYPE_CHECKING:
    from vesper.devices.base import IoTDevice


logger = logging.getLogger(__name__)


@dataclass
class Zone:
    """
    A zone in the environment where devices can be placed.
    
    Zones represent logical areas like "kitchen", "living_room", etc.
    """
    zone_id: str
    name: str
    bounds_min: tuple[float, float, float]  # (x, y, z) min corner
    bounds_max: tuple[float, float, float]  # (x, y, z) max corner
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def contains(self, point: tuple[float, float, float]) -> bool:
        """Check if a point is within this zone."""
        return all(
            self.bounds_min[i] <= point[i] <= self.bounds_max[i]
            for i in range(3)
        )


class Environment:
    """
    High-level environment manager.
    
    Manages:
    - IoT device registration and placement
    - Zone definitions
    - Device lookup by ID, type, or location
    - Integration with Habitat simulator (when available)
    
    Example:
        env = Environment()
        env.register_device(motion_sensor)
        env.define_zone("kitchen", (0, 0, 0), (5, 3, 5))
    """
    
    def __init__(
        self,
        config: Optional[Config] = None,
        event_bus: Optional[EventBus] = None,
    ):
        """
        Initialize the environment.
        
        Args:
            config: Configuration object. If None, loads default config.
            event_bus: Event bus for device communication. If None, creates one.
        """
        self.config = config or load_config()
        self.event_bus = event_bus or EventBus(
            max_queue_size=self.config.event_bus.max_queue_size,
            enable_logging=self.config.event_bus.logging,
            log_file=self.config.event_bus.log_file,
        )
        
        self._devices: dict[str, "IoTDevice"] = {}
        self._zones: dict[str, Zone] = {}
        self._device_zones: dict[str, str] = {}  # device_id -> zone_id
        
        self._simulation_time: float = 0.0
        self._tick_count: int = 0
        
        logger.info(f"Environment initialized (dataset={self.config.environment.dataset})")
    
    def register_device(self, device: "IoTDevice", zone_id: Optional[str] = None) -> None:
        """
        Register an IoT device in the environment.
        
        Args:
            device: The device to register.
            zone_id: Optional zone to assign the device to.
        """
        if device.device_id in self._devices:
            raise ValueError(f"Device {device.device_id} already registered")
        
        self._devices[device.device_id] = device
        
        # Auto-detect zone if not specified
        if zone_id is None:
            zone_id = self._find_zone_for_location(device.location)
        
        if zone_id:
            self._device_zones[device.device_id] = zone_id
        
        logger.debug(f"Registered device: {device.device_type} (id={device.device_id[:8]})")
    
    def unregister_device(self, device_id: str) -> bool:
        """
        Remove a device from the environment.
        
        Args:
            device_id: ID of the device to remove.
            
        Returns:
            True if device was found and removed.
        """
        if device_id in self._devices:
            del self._devices[device_id]
            self._device_zones.pop(device_id, None)
            logger.debug(f"Unregistered device: {device_id[:8]}")
            return True
        return False
    
    def get_device(self, device_id: str) -> Optional["IoTDevice"]:
        """Get a device by ID."""
        return self._devices.get(device_id)
    
    def get_devices_by_type(self, device_type: str) -> list["IoTDevice"]:
        """Get all devices of a specific type."""
        return [d for d in self._devices.values() if d.device_type == device_type]
    
    def get_devices_in_zone(self, zone_id: str) -> list["IoTDevice"]:
        """Get all devices in a specific zone."""
        device_ids = [
            did for did, zid in self._device_zones.items()
            if zid == zone_id
        ]
        return [self._devices[did] for did in device_ids if did in self._devices]
    
    def get_devices_near(
        self,
        location: tuple[float, float, float],
        radius: float,
    ) -> list["IoTDevice"]:
        """Get all devices within a radius of a location."""
        result = []
        for device in self._devices.values():
            distance = self._distance(device.location, location)
            if distance <= radius:
                result.append(device)
        return result
    
    def define_zone(
        self,
        name: str,
        bounds_min: tuple[float, float, float],
        bounds_max: tuple[float, float, float],
        zone_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Zone:
        """
        Define a zone in the environment.
        
        Args:
            name: Human-readable zone name.
            bounds_min: Minimum corner (x, y, z).
            bounds_max: Maximum corner (x, y, z).
            zone_id: Optional custom ID. If None, auto-generated.
            metadata: Optional metadata for the zone.
            
        Returns:
            The created Zone object.
        """
        zone_id = zone_id or str(uuid.uuid4())
        zone = Zone(
            zone_id=zone_id,
            name=name,
            bounds_min=bounds_min,
            bounds_max=bounds_max,
            metadata=metadata or {},
        )
        self._zones[zone_id] = zone
        logger.debug(f"Defined zone: {name} (id={zone_id[:8]})")
        return zone
    
    def get_zone(self, zone_id: str) -> Optional[Zone]:
        """Get a zone by ID."""
        return self._zones.get(zone_id)
    
    def _find_zone_for_location(
        self,
        location: tuple[float, float, float],
    ) -> Optional[str]:
        """Find the zone containing a location."""
        for zone_id, zone in self._zones.items():
            if zone.contains(location):
                return zone_id
        return None
    
    @staticmethod
    def _distance(
        a: tuple[float, float, float],
        b: tuple[float, float, float],
    ) -> float:
        """Calculate Euclidean distance between two points."""
        return sum((a[i] - b[i]) ** 2 for i in range(3)) ** 0.5
    
    def tick(self, dt: float) -> None:
        """
        Advance the simulation by one tick.
        
        Args:
            dt: Time delta in seconds.
        """
        self._simulation_time += dt
        self._tick_count += 1
        
        # Update all devices
        for device in self._devices.values():
            device.update(dt)
        
        # Process pending events
        self.event_bus.process_events()
    
    @property
    def simulation_time(self) -> float:
        """Current simulation time in seconds."""
        return self._simulation_time
    
    @property
    def tick_count(self) -> int:
        """Total number of ticks executed."""
        return self._tick_count
    
    @property
    def device_count(self) -> int:
        """Number of registered devices."""
        return len(self._devices)
    
    @property
    def zone_count(self) -> int:
        """Number of defined zones."""
        return len(self._zones)
    
    def get_state(self) -> dict[str, Any]:
        """Get the current state of the environment."""
        return {
            "simulation_time": self._simulation_time,
            "tick_count": self._tick_count,
            "device_count": len(self._devices),
            "zone_count": len(self._zones),
            "devices": {
                device_id: device.get_state()
                for device_id, device in self._devices.items()
            },
            "event_bus_stats": self.event_bus.stats,
        }
