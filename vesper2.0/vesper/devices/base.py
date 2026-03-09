"""
Base IoT Device class.

All IoT devices inherit from this base class.
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from vesper.core.event_bus import EventBus, EventPriority


logger = logging.getLogger(__name__)


@dataclass
class DeviceState:
    """Holds the mutable state of a device."""
    is_active: bool = True
    last_update_time: float = 0.0
    custom: dict[str, Any] = field(default_factory=dict)


class IoTDevice(ABC):
    """
    Abstract base class for all IoT devices.
    
    Provides common functionality:
    - Unique ID generation
    - Location tracking
    - State management
    - Event emission
    - Simulation tick updates
    
    Subclasses must implement:
    - update(dt): Called each simulation tick
    - get_state(): Return current device state
    """
    
    def __init__(
        self,
        device_type: str,
        location: tuple[float, float, float] = (0.0, 0.0, 0.0),
        device_id: Optional[str] = None,
        event_bus: Optional[EventBus] = None,
        name: Optional[str] = None,
    ):
        """
        Initialize the device.
        
        Args:
            device_type: Type identifier (e.g., "motion_sensor")
            location: (x, y, z) position in world coordinates
            device_id: Optional custom ID. If None, auto-generated.
            event_bus: Event bus for emitting events. Required for most operations.
            name: Human-readable name for the device.
        """
        self._device_id = device_id or str(uuid.uuid4())
        self._device_type = device_type
        self._location = location
        self._event_bus = event_bus
        self._name = name or f"{device_type}_{self._device_id[:8]}"
        self._state = DeviceState()
        
        logger.debug(f"Device created: {self._name} at {location}")
    
    @property
    def device_id(self) -> str:
        """Unique identifier for this device."""
        return self._device_id
    
    @property
    def device_type(self) -> str:
        """Type identifier for this device."""
        return self._device_type
    
    @property
    def location(self) -> tuple[float, float, float]:
        """Current location of the device."""
        return self._location
    
    @location.setter
    def location(self, value: tuple[float, float, float]) -> None:
        """Set device location."""
        self._location = value
    
    @property
    def name(self) -> str:
        """Human-readable name."""
        return self._name
    
    @property
    def is_active(self) -> bool:
        """Whether the device is currently active."""
        return self._state.is_active
    
    @is_active.setter
    def is_active(self, value: bool) -> None:
        """Set device active state."""
        self._state.is_active = value
    
    @property
    def event_bus(self) -> Optional[EventBus]:
        """Event bus for this device."""
        return self._event_bus
    
    @event_bus.setter
    def event_bus(self, bus: EventBus) -> None:
        """Set the event bus."""
        self._event_bus = bus
    
    def emit_event(
        self,
        event_type: str,
        payload: Optional[dict[str, Any]] = None,
        priority: EventPriority = EventPriority.NORMAL,
    ) -> bool:
        """
        Emit an event from this device.
        
        Args:
            event_type: Type of event to emit
            payload: Event data
            priority: Event priority
            
        Returns:
            True if event was emitted, False if no event bus.
        """
        if self._event_bus is None:
            logger.warning(f"Device {self._name} has no event bus, event not emitted")
            return False
        
        full_payload = {
            "device_id": self._device_id,
            "device_type": self._device_type,
            "device_name": self._name,
            "location": self._location,
            **(payload or {}),
        }
        
        return self._event_bus.emit(
            event_type=event_type,
            payload=full_payload,
            priority=priority,
            source_id=self._device_id,
        )
    
    @abstractmethod
    def update(self, dt: float) -> None:
        """
        Update device state for a simulation tick.
        
        Args:
            dt: Time delta in seconds since last update.
        """
        pass
    
    @abstractmethod
    def get_state(self) -> dict[str, Any]:
        """
        Get the current state of the device.
        
        Returns:
            Dictionary containing device state.
        """
        pass
    
    def get_base_state(self) -> dict[str, Any]:
        """Get base state common to all devices."""
        return {
            "device_id": self._device_id,
            "device_type": self._device_type,
            "name": self._name,
            "location": self._location,
            "is_active": self._state.is_active,
            "last_update_time": self._state.last_update_time,
        }
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id={self._device_id[:8]}, name={self._name})"
