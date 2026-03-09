"""
Contact Sensor device.

Detects open/closed state of doors, windows, or other objects.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from vesper.core.event_bus import EventBus, EventPriority
from vesper.devices.base import IoTDevice


logger = logging.getLogger(__name__)


class ContactSensor(IoTDevice):
    """
    Contact sensor for detecting open/closed states.
    
    Monitors the state of attached objects (doors, windows, drawers)
    and emits events when the state changes.
    
    Events:
        - opened: {target_id, timestamp}
        - closed: {target_id, timestamp}
        - contact_changed: {target_id, is_open, timestamp}
    
    Example:
        sensor = ContactSensor(
            location=(0.0, 1.0, 0.0),
            target_object_id="front_door",
            event_bus=bus,
        )
        sensor.set_open(True)  # Emits "opened" event
    """
    
    DEVICE_TYPE = "contact_sensor"
    EVENT_OPENED = "opened"
    EVENT_CLOSED = "closed"
    EVENT_CONTACT_CHANGED = "contact_changed"
    
    def __init__(
        self,
        location: tuple[float, float, float] = (0.0, 0.0, 0.0),
        target_object_id: Optional[str] = None,
        debounce: float = 0.1,
        initial_state_open: bool = False,
        device_id: Optional[str] = None,
        event_bus: Optional[EventBus] = None,
        name: Optional[str] = None,
    ):
        """
        Initialize contact sensor.
        
        Args:
            location: (x, y, z) position in world coordinates
            target_object_id: ID of the object being monitored
            debounce: Debounce time in seconds to prevent rapid toggling
            initial_state_open: Whether the initial state is open
            device_id: Optional custom ID
            event_bus: Event bus for emitting events
            name: Human-readable name
        """
        super().__init__(
            device_type=self.DEVICE_TYPE,
            location=location,
            device_id=device_id,
            event_bus=event_bus,
            name=name,
        )
        
        self._target_object_id = target_object_id
        self._debounce = debounce
        self._is_open = initial_state_open
        
        self._last_change_time: float = 0.0
        self._change_count: int = 0
        self._pending_state: Optional[bool] = None
        self._pending_time: float = 0.0
    
    @property
    def target_object_id(self) -> Optional[str]:
        """ID of the monitored object."""
        return self._target_object_id
    
    @target_object_id.setter
    def target_object_id(self, value: str) -> None:
        self._target_object_id = value
    
    @property
    def is_open(self) -> bool:
        """Current open/closed state."""
        return self._is_open
    
    @property
    def debounce(self) -> float:
        """Debounce time in seconds."""
        return self._debounce
    
    @debounce.setter
    def debounce(self, value: float) -> None:
        if value < 0:
            raise ValueError("Debounce cannot be negative")
        self._debounce = value
    
    @property
    def change_count(self) -> int:
        """Total number of state changes."""
        return self._change_count
    
    @property
    def last_change_time(self) -> float:
        """Timestamp of last state change."""
        return self._last_change_time
    
    def set_open(self, is_open: bool, force: bool = False) -> bool:
        """
        Set the open/closed state.
        
        Args:
            is_open: True for open, False for closed
            force: If True, bypass debounce check
            
        Returns:
            True if state was changed
        """
        if not self.is_active:
            return False
        
        # No change needed
        if is_open == self._is_open:
            return False
        
        current_time = time.time()
        
        # Check debounce
        if not force and (current_time - self._last_change_time) < self._debounce:
            # Store pending state for debounce processing
            self._pending_state = is_open
            self._pending_time = current_time
            return False
        
        # Apply state change
        self._apply_state_change(is_open, current_time)
        return True
    
    def _apply_state_change(self, is_open: bool, timestamp: float) -> None:
        """Apply a state change and emit events."""
        old_state = self._is_open
        self._is_open = is_open
        self._last_change_time = timestamp
        self._change_count += 1
        self._pending_state = None
        
        # Emit specific event
        event_type = self.EVENT_OPENED if is_open else self.EVENT_CLOSED
        self.emit_event(
            event_type=event_type,
            payload={
                "target_id": self._target_object_id,
                "timestamp": timestamp,
            },
            priority=EventPriority.NORMAL,
        )
        
        # Emit generic change event
        self.emit_event(
            event_type=self.EVENT_CONTACT_CHANGED,
            payload={
                "target_id": self._target_object_id,
                "is_open": is_open,
                "previous_state": old_state,
                "timestamp": timestamp,
            },
            priority=EventPriority.NORMAL,
        )
        
        logger.debug(
            f"Contact sensor {self.name}: {'opened' if is_open else 'closed'} "
            f"(target={self._target_object_id})"
        )
    
    def toggle(self) -> bool:
        """
        Toggle the open/closed state.
        
        Returns:
            True if state was changed
        """
        return self.set_open(not self._is_open)
    
    def update(self, dt: float) -> None:
        """Update sensor state for simulation tick."""
        self._state.last_update_time += dt
        
        # Process pending debounced state change
        if self._pending_state is not None:
            current_time = time.time()
            if (current_time - self._pending_time) >= self._debounce:
                self._apply_state_change(self._pending_state, current_time)
    
    def get_state(self) -> dict[str, Any]:
        """Get current sensor state."""
        return {
            **self.get_base_state(),
            "target_object_id": self._target_object_id,
            "is_open": self._is_open,
            "debounce": self._debounce,
            "change_count": self._change_count,
            "last_change_time": self._last_change_time,
            "has_pending_change": self._pending_state is not None,
        }
    
    def reset(self, initial_state_open: bool = False) -> None:
        """Reset sensor state."""
        self._is_open = initial_state_open
        self._last_change_time = 0.0
        self._change_count = 0
        self._pending_state = None
        self._pending_time = 0.0
