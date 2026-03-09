"""
IoT Device Manager for VESPER.

Manages the lifecycle and coordination of IoT devices in the simulation.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type, Callable
from pathlib import Path

from vesper.core.event_bus import EventBus, Event, EventPriority
from vesper.devices.base import IoTDevice
from vesper.devices.motion_sensor import MotionSensor
from vesper.devices.contact_sensor import ContactSensor
from vesper.devices.smart_door import SmartDoor
from vesper.devices.light_sensor import LightSensor


logger = logging.getLogger(__name__)


# Device type registry
DEVICE_TYPES: Dict[str, Type[IoTDevice]] = {
    "motion_sensor": MotionSensor,
    "contact_sensor": ContactSensor,
    "smart_door": SmartDoor,
    "light_sensor": LightSensor,
}


@dataclass
class DeviceGroup:
    """Group of related devices (e.g., all devices in a room)."""
    name: str
    device_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass 
class DeviceManagerConfig:
    """Configuration for the device manager."""
    max_devices: int = 100
    enable_persistence: bool = False
    persistence_path: Optional[str] = None
    default_cooldown: float = 1.0
    default_detection_radius: float = 4.0


class DeviceManager:
    """
    Manages IoT devices in the VESPER simulation.
    
    Features:
    - Device lifecycle management (create, update, destroy)
    - Device grouping (by room, type, etc.)
    - Batch operations
    - State persistence
    - Event aggregation
    
    Example:
        from vesper.devices.manager import DeviceManager
        
        manager = DeviceManager()
        
        # Create devices
        motion = manager.create_device(
            device_type="motion_sensor",
            position=(0, 0, 0),
            name="Hallway Motion",
        )
        
        # Group devices
        manager.create_group("living_room", [motion.device_id])
        
        # Update all devices
        manager.update_all(dt=1/30)
    """
    
    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        config: Optional[DeviceManagerConfig] = None,
    ):
        """
        Initialize the device manager.
        
        Args:
            event_bus: Event bus for device communication
            config: Manager configuration
        """
        self._event_bus = event_bus or EventBus()
        self._config = config or DeviceManagerConfig()
        
        self._devices: Dict[str, IoTDevice] = {}
        self._groups: Dict[str, DeviceGroup] = {}
        
        # Device statistics
        self._stats = {
            "devices_created": 0,
            "devices_destroyed": 0,
            "updates": 0,
        }
        
        # Callbacks
        self._on_device_created: List[Callable] = []
        self._on_device_destroyed: List[Callable] = []
        self._on_state_changed: List[Callable] = []
    
    @property
    def event_bus(self) -> EventBus:
        return self._event_bus
    
    @property
    def device_count(self) -> int:
        return len(self._devices)
    
    @property
    def stats(self) -> Dict[str, int]:
        return self._stats.copy()
    
    def create_device(
        self,
        device_type: str,
        position: tuple = (0.0, 0.0, 0.0),
        name: Optional[str] = None,
        device_id: Optional[str] = None,
        **kwargs,
    ) -> IoTDevice:
        """
        Create a new IoT device.
        
        Args:
            device_type: Type of device (motion_sensor, contact_sensor, etc.)
            position: (x, y, z) position
            name: Human-readable name
            device_id: Custom ID (auto-generated if None)
            **kwargs: Additional device-specific parameters
            
        Returns:
            The created device
        """
        if device_type not in DEVICE_TYPES:
            raise ValueError(f"Unknown device type: {device_type}")
        
        if len(self._devices) >= self._config.max_devices:
            raise RuntimeError(f"Max devices ({self._config.max_devices}) reached")
        
        device_class = DEVICE_TYPES[device_type]
        
        # Build kwargs based on device type
        create_kwargs = {
            "location": position,
            "event_bus": self._event_bus,
        }
        
        if device_id:
            create_kwargs["device_id"] = device_id
        if name:
            create_kwargs["name"] = name
        
        # Add type-specific defaults
        if device_type == "motion_sensor":
            create_kwargs.setdefault("detection_radius", self._config.default_detection_radius)
            create_kwargs.setdefault("cooldown", self._config.default_cooldown)
        
        # Merge additional kwargs
        create_kwargs.update(kwargs)
        
        device = device_class(**create_kwargs)
        self._devices[device.device_id] = device
        self._stats["devices_created"] += 1
        
        # Notify callbacks
        for callback in self._on_device_created:
            callback(device)
        
        logger.info(f"Created {device_type}: {device.device_id} at {position}")
        
        return device
    
    def get_device(self, device_id: str) -> Optional[IoTDevice]:
        """Get a device by ID."""
        return self._devices.get(device_id)
    
    def get_devices_by_type(self, device_type: str) -> List[IoTDevice]:
        """Get all devices of a specific type."""
        return [d for d in self._devices.values() if d.device_type == device_type]
    
    def destroy_device(self, device_id: str) -> bool:
        """
        Destroy a device.
        
        Args:
            device_id: ID of the device to destroy
            
        Returns:
            True if device was found and destroyed
        """
        device = self._devices.pop(device_id, None)
        if device:
            # Remove from groups
            for group in self._groups.values():
                if device_id in group.device_ids:
                    group.device_ids.remove(device_id)
            
            self._stats["devices_destroyed"] += 1
            
            # Notify callbacks
            for callback in self._on_device_destroyed:
                callback(device)
            
            logger.info(f"Destroyed device: {device_id}")
            return True
        return False
    
    def create_group(self, name: str, device_ids: List[str] = None) -> DeviceGroup:
        """
        Create a device group.
        
        Args:
            name: Group name
            device_ids: Initial device IDs in the group
            
        Returns:
            The created group
        """
        group = DeviceGroup(name=name, device_ids=device_ids or [])
        self._groups[name] = group
        return group
    
    def add_to_group(self, group_name: str, device_id: str) -> bool:
        """Add a device to a group."""
        if group_name not in self._groups:
            return False
        if device_id not in self._devices:
            return False
        
        if device_id not in self._groups[group_name].device_ids:
            self._groups[group_name].device_ids.append(device_id)
        return True
    
    def get_group_devices(self, group_name: str) -> List[IoTDevice]:
        """Get all devices in a group."""
        if group_name not in self._groups:
            return []
        return [
            self._devices[did]
            for did in self._groups[group_name].device_ids
            if did in self._devices
        ]
    
    def update_all(self, dt: float) -> int:
        """
        Update all devices.
        
        Args:
            dt: Time delta in seconds
            
        Returns:
            Number of devices updated
        """
        count = 0
        for device in self._devices.values():
            device.update(dt)
            count += 1
        
        self._stats["updates"] += 1
        return count
    
    def check_motion(
        self,
        agent_id: str,
        position: tuple,
    ) -> List[IoTDevice]:
        """
        Check all motion sensors for detection of an agent.
        
        Args:
            agent_id: ID of the agent
            position: (x, y, z) position of the agent
            
        Returns:
            List of motion sensors that detected the agent
        """
        triggered = []
        for device in self.get_devices_by_type("motion_sensor"):
            if isinstance(device, MotionSensor):
                if device.detect_agent(agent_id, position):
                    triggered.append(device)
        return triggered
    
    def get_all_states(self) -> Dict[str, Dict[str, Any]]:
        """Get state of all devices."""
        return {
            device_id: device.get_state()
            for device_id, device in self._devices.items()
        }
    
    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of the device manager state."""
        type_counts = {}
        for device in self._devices.values():
            device_type = device.device_type
            type_counts[device_type] = type_counts.get(device_type, 0) + 1
        
        return {
            "total_devices": len(self._devices),
            "device_types": type_counts,
            "groups": list(self._groups.keys()),
            "stats": self._stats,
        }
    
    def to_prompt(self) -> str:
        """
        Generate a prompt-friendly summary for LLM agents.
        
        Returns:
            Text description of all device states
        """
        lines = ["Current IoT Device States:"]
        
        for device_id, device in self._devices.items():
            state = device.get_state()
            device_type = device.device_type
            name = state.get("name", device_id)
            
            if device_type == "motion_sensor":
                motion = "motion detected" if state.get("motion_detected") else "no motion"
                lines.append(f"  - {name}: {motion}")
            
            elif device_type == "contact_sensor":
                contact = "open" if state.get("is_open") else "closed"
                lines.append(f"  - {name}: {contact}")
            
            elif device_type == "smart_door":
                door_state = "open" if state.get("is_open") else "closed"
                locked = ", locked" if state.get("is_locked") else ""
                lines.append(f"  - {name}: {door_state}{locked}")
            
            elif device_type == "light_sensor":
                lux = state.get("lux_level", 0)
                lines.append(f"  - {name}: {lux:.0f} lux")
            
            else:
                lines.append(f"  - {name}: {state}")
        
        return "\n".join(lines)
    
    def reset(self) -> None:
        """Reset all devices to initial state."""
        for device in self._devices.values():
            device.reset()
    
    def clear(self) -> int:
        """Remove all devices."""
        count = len(self._devices)
        self._devices.clear()
        self._groups.clear()
        return count
    
    def on_device_created(self, callback: Callable[[IoTDevice], None]) -> None:
        """Register callback for device creation."""
        self._on_device_created.append(callback)
    
    def on_device_destroyed(self, callback: Callable[[IoTDevice], None]) -> None:
        """Register callback for device destruction."""
        self._on_device_destroyed.append(callback)
    
    def on_state_changed(self, callback: Callable[[IoTDevice, Dict], None]) -> None:
        """Register callback for device state changes."""
        self._on_state_changed.append(callback)


def create_smart_home_setup(
    event_bus: Optional[EventBus] = None,
) -> DeviceManager:
    """
    Create a typical smart home device setup.
    
    Returns:
        DeviceManager with pre-configured smart home devices
    """
    manager = DeviceManager(event_bus=event_bus)
    
    # Entrance
    manager.create_device("motion_sensor", (0, 0.5, 0), "Entrance Motion")
    manager.create_device("smart_door", (0, 0, 0), "Front Door")
    manager.create_device("contact_sensor", (0, 0, 0), "Front Door Contact")
    
    # Living Room
    manager.create_device("motion_sensor", (4, 0.5, 2), "Living Room Motion")
    manager.create_device("light_sensor", (4, 2, 2), "Living Room Light")
    
    # Kitchen
    manager.create_device("motion_sensor", (-3, 0.5, 2), "Kitchen Motion")
    manager.create_device("light_sensor", (-3, 2, 2), "Kitchen Light")
    
    # Bedroom
    manager.create_device("motion_sensor", (4, 0.5, -3), "Bedroom Motion")
    manager.create_device("smart_door", (2, 0, -3), "Bedroom Door")
    manager.create_device("contact_sensor", (2, 0, -3), "Bedroom Door Contact")
    
    # Bathroom
    manager.create_device("motion_sensor", (-3, 0.5, -3), "Bathroom Motion")
    manager.create_device("smart_door", (-1, 0, -3), "Bathroom Door")
    
    # Create groups
    manager.create_group("entrance")
    manager.create_group("living_room")
    manager.create_group("kitchen")
    manager.create_group("bedroom")
    manager.create_group("bathroom")
    
    return manager
