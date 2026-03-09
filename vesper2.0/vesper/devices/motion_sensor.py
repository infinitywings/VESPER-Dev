"""
Motion Sensor device.

Detects agent movement within a configurable detection radius.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from vesper.core.event_bus import EventBus, EventPriority
from vesper.devices.base import IoTDevice


logger = logging.getLogger(__name__)


class MotionSensor(IoTDevice):
    """
    Motion detection sensor.
    
    Detects when agents enter its detection radius and emits
    "motion_detected" events with a configurable cooldown.
    
    Events:
        - motion_detected: {agent_id, distance, timestamp}
    
    Example:
        sensor = MotionSensor(
            location=(1.0, 0.0, 2.0),
            detection_radius=3.0,
            cooldown=2.0,
            event_bus=bus,
        )
        sensor.detect_agent("agent_1", (1.5, 0.0, 2.5))
    """
    
    DEVICE_TYPE = "motion_sensor"
    EVENT_MOTION_DETECTED = "motion_detected"
    
    def __init__(
        self,
        location: tuple[float, float, float] = (0.0, 0.0, 0.0),
        detection_radius: float = 3.0,
        cooldown: float = 2.0,
        fov_vertical: float = 90.0,
        device_id: Optional[str] = None,
        event_bus: Optional[EventBus] = None,
        name: Optional[str] = None,
    ):
        """
        Initialize motion sensor.
        
        Args:
            location: (x, y, z) position in world coordinates
            detection_radius: Detection range in meters
            cooldown: Minimum seconds between triggers
            fov_vertical: Vertical field of view in degrees
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
        
        self._detection_radius = detection_radius
        self._cooldown = cooldown
        self._fov_vertical = fov_vertical
        
        self._last_trigger_time: float = 0.0
        self._motion_detected: bool = False
        self._detected_agents: set[str] = set()
        self._trigger_count: int = 0
    
    @property
    def detection_radius(self) -> float:
        """Detection range in meters."""
        return self._detection_radius
    
    @detection_radius.setter
    def detection_radius(self, value: float) -> None:
        if value <= 0:
            raise ValueError("Detection radius must be positive")
        self._detection_radius = value
    
    @property
    def cooldown(self) -> float:
        """Cooldown between triggers in seconds."""
        return self._cooldown
    
    @cooldown.setter
    def cooldown(self, value: float) -> None:
        if value < 0:
            raise ValueError("Cooldown cannot be negative")
        self._cooldown = value
    
    @property
    def is_in_cooldown(self) -> bool:
        """Whether sensor is in cooldown period."""
        return (time.time() - self._last_trigger_time) < self._cooldown
    
    @property
    def motion_detected(self) -> bool:
        """Whether motion is currently detected."""
        return self._motion_detected
    
    @property
    def trigger_count(self) -> int:
        """Total number of times the sensor has triggered."""
        return self._trigger_count
    
    def check_agent_in_range(
        self,
        agent_location: tuple[float, float, float],
    ) -> tuple[bool, float]:
        """
        Check if an agent is within detection range.
        
        Args:
            agent_location: (x, y, z) position of the agent
            
        Returns:
            Tuple of (in_range, distance)
        """
        distance = self._calculate_distance(agent_location)
        in_range = distance <= self._detection_radius
        return in_range, distance
    
    def detect_agent(
        self,
        agent_id: str,
        agent_location: Optional[tuple[float, float, float]] = None,
    ) -> bool:
        """
        Process potential motion detection for an agent.
        
        Args:
            agent_id: Unique identifier of the agent
            agent_location: Optional agent position. If None, assumed in range.
            
        Returns:
            True if motion event was triggered.
        """
        if not self.is_active:
            return False
        
        # Check if in cooldown
        if self.is_in_cooldown:
            return False
        
        # Check distance if location provided
        distance = 0.0
        if agent_location:
            in_range, distance = self.check_agent_in_range(agent_location)
            if not in_range:
                return False
        
        # Trigger motion detection
        self._last_trigger_time = time.time()
        self._motion_detected = True
        self._detected_agents.add(agent_id)
        self._trigger_count += 1
        
        # Emit event
        self.emit_event(
            event_type=self.EVENT_MOTION_DETECTED,
            payload={
                "agent_id": agent_id,
                "distance": distance,
                "detection_radius": self._detection_radius,
                "timestamp": self._last_trigger_time,
            },
            priority=EventPriority.HIGH,
        )
        
        logger.debug(f"Motion detected by {self.name}: agent={agent_id}, distance={distance:.2f}m")
        
        return True
    
    def _calculate_distance(self, point: tuple[float, float, float]) -> float:
        """Calculate Euclidean distance to a point."""
        return sum((self.location[i] - point[i]) ** 2 for i in range(3)) ** 0.5
    
    def update(self, dt: float) -> None:
        """Update sensor state for simulation tick."""
        self._state.last_update_time += dt
        
        # Clear motion detected flag after cooldown
        if self._motion_detected and not self.is_in_cooldown:
            self._motion_detected = False
            self._detected_agents.clear()
    
    def get_state(self) -> dict[str, Any]:
        """Get current sensor state."""
        return {
            **self.get_base_state(),
            "detection_radius": self._detection_radius,
            "cooldown": self._cooldown,
            "fov_vertical": self._fov_vertical,
            "motion_detected": self._motion_detected,
            "detected_agents": list(self._detected_agents),
            "trigger_count": self._trigger_count,
            "is_in_cooldown": self.is_in_cooldown,
            "last_trigger_time": self._last_trigger_time,
        }
    
    def reset(self) -> None:
        """Reset sensor state."""
        self._last_trigger_time = 0.0
        self._motion_detected = False
        self._detected_agents.clear()
        self._trigger_count = 0
