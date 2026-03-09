"""
Smart Door device.

Actuatable door with open/close commands and state tracking.
"""

from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Any, Optional

from vesper.core.event_bus import EventBus, EventPriority
from vesper.devices.base import IoTDevice


logger = logging.getLogger(__name__)


class DoorState(str, Enum):
    """Door state enumeration."""
    CLOSED = "closed"
    OPENING = "opening"
    OPEN = "open"
    CLOSING = "closing"


class SmartDoor(IoTDevice):
    """
    Smart door with actuation capabilities.
    
    Supports open/close commands with transition animations,
    lock state, and auto-close functionality.
    
    Events:
        - door_status: {is_open, is_locked, state, timestamp}
        - door_opened: {timestamp}
        - door_closed: {timestamp}
        - door_locked: {timestamp}
        - door_unlocked: {timestamp}
    
    Example:
        door = SmartDoor(
            location=(0.0, 0.0, 0.0),
            transition_time=1.5,
            event_bus=bus,
        )
        door.open()  # Starts opening animation
        # After tick updates...
        door.is_fully_open  # True when complete
    """
    
    DEVICE_TYPE = "smart_door"
    EVENT_DOOR_STATUS = "door_status"
    EVENT_DOOR_OPENED = "door_opened"
    EVENT_DOOR_CLOSED = "door_closed"
    EVENT_DOOR_LOCKED = "door_locked"
    EVENT_DOOR_UNLOCKED = "door_unlocked"
    
    def __init__(
        self,
        location: tuple[float, float, float] = (0.0, 0.0, 0.0),
        transition_time: float = 1.5,
        auto_close: float = 0.0,
        initial_locked: bool = False,
        initial_open: bool = False,
        device_id: Optional[str] = None,
        event_bus: Optional[EventBus] = None,
        name: Optional[str] = None,
    ):
        """
        Initialize smart door.
        
        Args:
            location: (x, y, z) position in world coordinates
            transition_time: Time for open/close animation in seconds
            auto_close: Auto-close delay after opening (0 = disabled)
            initial_locked: Whether door starts locked
            initial_open: Whether door starts open
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
        
        self._transition_time = transition_time
        self._auto_close = auto_close
        self._is_locked = initial_locked
        
        # Door state
        self._state_enum = DoorState.OPEN if initial_open else DoorState.CLOSED
        self._open_progress = 1.0 if initial_open else 0.0  # 0 = closed, 1 = open
        
        self._last_state_change: float = 0.0
        self._auto_close_timer: float = 0.0
        self._command_count: int = 0
    
    @property
    def is_locked(self) -> bool:
        """Whether the door is locked."""
        return self._is_locked
    
    @property
    def is_open(self) -> bool:
        """Whether the door is open (fully or partially)."""
        return self._state_enum in (DoorState.OPEN, DoorState.OPENING)
    
    @property
    def is_fully_open(self) -> bool:
        """Whether the door is fully open."""
        return self._state_enum == DoorState.OPEN and self._open_progress >= 1.0
    
    @property
    def is_fully_closed(self) -> bool:
        """Whether the door is fully closed."""
        return self._state_enum == DoorState.CLOSED and self._open_progress <= 0.0
    
    @property
    def door_state(self) -> DoorState:
        """Current door state."""
        return self._state_enum
    
    @property
    def open_progress(self) -> float:
        """Door open progress (0.0 = closed, 1.0 = open)."""
        return self._open_progress
    
    @property
    def transition_time(self) -> float:
        """Time for door to fully open/close."""
        return self._transition_time
    
    @property
    def auto_close(self) -> float:
        """Auto-close delay in seconds (0 = disabled)."""
        return self._auto_close
    
    @property
    def command_count(self) -> int:
        """Total number of commands received."""
        return self._command_count
    
    def open(self) -> bool:
        """
        Command the door to open.
        
        Returns:
            True if command was accepted
        """
        if not self.is_active:
            logger.debug(f"Door {self.name} inactive, ignoring open command")
            return False
        
        if self._is_locked:
            logger.debug(f"Door {self.name} is locked, cannot open")
            return False
        
        if self._state_enum in (DoorState.OPEN, DoorState.OPENING):
            return False
        
        self._state_enum = DoorState.OPENING
        self._last_state_change = time.time()
        self._command_count += 1
        
        logger.debug(f"Door {self.name} opening")
        self._emit_status_event()
        
        return True
    
    def close(self) -> bool:
        """
        Command the door to close.
        
        Returns:
            True if command was accepted
        """
        if not self.is_active:
            return False
        
        if self._state_enum in (DoorState.CLOSED, DoorState.CLOSING):
            return False
        
        self._state_enum = DoorState.CLOSING
        self._last_state_change = time.time()
        self._auto_close_timer = 0.0
        self._command_count += 1
        
        logger.debug(f"Door {self.name} closing")
        self._emit_status_event()
        
        return True
    
    def lock(self) -> bool:
        """
        Lock the door. Door must be closed first.
        
        Returns:
            True if lock was successful
        """
        if not self.is_fully_closed:
            logger.debug(f"Door {self.name} must be closed to lock")
            return False
        
        if self._is_locked:
            return False
        
        self._is_locked = True
        self._command_count += 1
        
        self.emit_event(
            event_type=self.EVENT_DOOR_LOCKED,
            payload={"timestamp": time.time()},
            priority=EventPriority.NORMAL,
        )
        
        logger.debug(f"Door {self.name} locked")
        return True
    
    def unlock(self) -> bool:
        """
        Unlock the door.
        
        Returns:
            True if unlock was successful
        """
        if not self._is_locked:
            return False
        
        self._is_locked = False
        self._command_count += 1
        
        self.emit_event(
            event_type=self.EVENT_DOOR_UNLOCKED,
            payload={"timestamp": time.time()},
            priority=EventPriority.NORMAL,
        )
        
        logger.debug(f"Door {self.name} unlocked")
        return True
    
    def _emit_status_event(self) -> None:
        """Emit a door status event."""
        self.emit_event(
            event_type=self.EVENT_DOOR_STATUS,
            payload={
                "is_open": self.is_open,
                "is_locked": self._is_locked,
                "state": self._state_enum.value,
                "open_progress": self._open_progress,
                "timestamp": time.time(),
            },
            priority=EventPriority.NORMAL,
        )
    
    def update(self, dt: float) -> None:
        """Update door state for simulation tick."""
        self._state.last_update_time += dt
        
        # Handle opening animation
        if self._state_enum == DoorState.OPENING:
            self._open_progress += dt / self._transition_time
            if self._open_progress >= 1.0:
                self._open_progress = 1.0
                self._state_enum = DoorState.OPEN
                self._auto_close_timer = 0.0
                
                self.emit_event(
                    event_type=self.EVENT_DOOR_OPENED,
                    payload={"timestamp": time.time()},
                    priority=EventPriority.NORMAL,
                )
                logger.debug(f"Door {self.name} fully open")
        
        # Handle closing animation
        elif self._state_enum == DoorState.CLOSING:
            self._open_progress -= dt / self._transition_time
            if self._open_progress <= 0.0:
                self._open_progress = 0.0
                self._state_enum = DoorState.CLOSED
                
                self.emit_event(
                    event_type=self.EVENT_DOOR_CLOSED,
                    payload={"timestamp": time.time()},
                    priority=EventPriority.NORMAL,
                )
                logger.debug(f"Door {self.name} fully closed")
        
        # Handle auto-close
        elif self._state_enum == DoorState.OPEN and self._auto_close > 0:
            self._auto_close_timer += dt
            if self._auto_close_timer >= self._auto_close:
                self.close()
    
    def get_state(self) -> dict[str, Any]:
        """Get current door state."""
        return {
            **self.get_base_state(),
            "door_state": self._state_enum.value,
            "is_locked": self._is_locked,
            "is_open": self.is_open,
            "is_fully_open": self.is_fully_open,
            "is_fully_closed": self.is_fully_closed,
            "open_progress": self._open_progress,
            "transition_time": self._transition_time,
            "auto_close": self._auto_close,
            "command_count": self._command_count,
        }
    
    def reset(self, open_state: bool = False, locked: bool = False) -> None:
        """Reset door to initial state."""
        self._state_enum = DoorState.OPEN if open_state else DoorState.CLOSED
        self._open_progress = 1.0 if open_state else 0.0
        self._is_locked = locked
        self._last_state_change = 0.0
        self._auto_close_timer = 0.0
        self._command_count = 0
