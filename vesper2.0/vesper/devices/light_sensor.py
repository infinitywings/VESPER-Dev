"""
Light Sensor device.

Measures ambient light levels and emits events on changes.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from vesper.core.event_bus import EventBus, EventPriority
from vesper.devices.base import IoTDevice


logger = logging.getLogger(__name__)


class LightSensor(IoTDevice):
    """
    Ambient light sensor.
    
    Measures lux levels and emits events when significant
    changes are detected. Supports configurable sample rate
    and threshold for event emission.
    
    Events:
        - lux_level: {lux, previous_lux, delta, timestamp}
    
    Example:
        sensor = LightSensor(
            location=(2.0, 2.5, 3.0),
            sample_rate=10.0,
            threshold=5.0,
            event_bus=bus,
        )
        sensor.set_lux(350.0)  # Simulates light level update
    """
    
    DEVICE_TYPE = "light_sensor"
    EVENT_LUX_LEVEL = "lux_level"
    
    def __init__(
        self,
        location: tuple[float, float, float] = (0.0, 0.0, 0.0),
        sample_rate: float = 10.0,
        threshold: float = 5.0,
        initial_lux: float = 0.0,
        device_id: Optional[str] = None,
        event_bus: Optional[EventBus] = None,
        name: Optional[str] = None,
    ):
        """
        Initialize light sensor.
        
        Args:
            location: (x, y, z) position in world coordinates
            sample_rate: Sample rate in Hz (how often to check)
            threshold: Minimum lux change to trigger event
            initial_lux: Starting lux value
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
        
        self._sample_rate = sample_rate
        self._sample_interval = 1.0 / sample_rate
        self._threshold = threshold
        
        self._current_lux = initial_lux
        self._last_reported_lux = initial_lux
        self._last_sample_time: float = 0.0
        self._time_since_sample: float = 0.0
        
        self._min_lux_recorded = initial_lux
        self._max_lux_recorded = initial_lux
        self._sample_count: int = 0
    
    @property
    def sample_rate(self) -> float:
        """Sample rate in Hz."""
        return self._sample_rate
    
    @sample_rate.setter
    def sample_rate(self, value: float) -> None:
        if value <= 0:
            raise ValueError("Sample rate must be positive")
        self._sample_rate = value
        self._sample_interval = 1.0 / value
    
    @property
    def threshold(self) -> float:
        """Minimum lux change to trigger event."""
        return self._threshold
    
    @threshold.setter
    def threshold(self, value: float) -> None:
        if value < 0:
            raise ValueError("Threshold cannot be negative")
        self._threshold = value
    
    @property
    def current_lux(self) -> float:
        """Current light level in lux."""
        return self._current_lux
    
    @property
    def min_lux_recorded(self) -> float:
        """Minimum lux value recorded."""
        return self._min_lux_recorded
    
    @property
    def max_lux_recorded(self) -> float:
        """Maximum lux value recorded."""
        return self._max_lux_recorded
    
    @property
    def sample_count(self) -> int:
        """Total number of samples taken."""
        return self._sample_count
    
    def set_lux(self, lux: float, emit_if_changed: bool = True) -> bool:
        """
        Set the current lux value (simulates a reading).
        
        Args:
            lux: New lux value
            emit_if_changed: Emit event if change exceeds threshold
            
        Returns:
            True if an event was emitted
        """
        if not self.is_active:
            return False
        
        if lux < 0:
            lux = 0.0
        
        previous_lux = self._current_lux
        self._current_lux = lux
        
        # Update min/max
        self._min_lux_recorded = min(self._min_lux_recorded, lux)
        self._max_lux_recorded = max(self._max_lux_recorded, lux)
        
        # Check if change exceeds threshold
        delta = abs(lux - self._last_reported_lux)
        if emit_if_changed and delta >= self._threshold:
            return self._emit_lux_event(previous_lux)
        
        return False
    
    def _emit_lux_event(self, previous_lux: float) -> bool:
        """Emit a lux level event."""
        current_time = time.time()
        delta = self._current_lux - self._last_reported_lux
        
        success = self.emit_event(
            event_type=self.EVENT_LUX_LEVEL,
            payload={
                "lux": self._current_lux,
                "previous_lux": previous_lux,
                "last_reported_lux": self._last_reported_lux,
                "delta": delta,
                "timestamp": current_time,
            },
            priority=EventPriority.LOW,
        )
        
        if success:
            self._last_reported_lux = self._current_lux
            self._last_sample_time = current_time
            self._sample_count += 1
            
            logger.debug(
                f"Light sensor {self.name}: {self._current_lux:.1f} lux "
                f"(delta={delta:+.1f})"
            )
        
        return success
    
    def force_sample(self) -> bool:
        """
        Force an immediate sample and event emission.
        
        Returns:
            True if event was emitted
        """
        if not self.is_active:
            return False
        
        return self._emit_lux_event(self._current_lux)
    
    def update(self, dt: float) -> None:
        """Update sensor state for simulation tick."""
        self._state.last_update_time += dt
        self._time_since_sample += dt
        
        # Check if it's time for a sample
        if self._time_since_sample >= self._sample_interval:
            self._time_since_sample = 0.0
            
            # Check if lux has changed enough
            delta = abs(self._current_lux - self._last_reported_lux)
            if delta >= self._threshold:
                self._emit_lux_event(self._last_reported_lux)
    
    def get_state(self) -> dict[str, Any]:
        """Get current sensor state."""
        return {
            **self.get_base_state(),
            "current_lux": self._current_lux,
            "last_reported_lux": self._last_reported_lux,
            "sample_rate": self._sample_rate,
            "threshold": self._threshold,
            "min_lux_recorded": self._min_lux_recorded,
            "max_lux_recorded": self._max_lux_recorded,
            "sample_count": self._sample_count,
        }
    
    def reset(self, initial_lux: float = 0.0) -> None:
        """Reset sensor state."""
        self._current_lux = initial_lux
        self._last_reported_lux = initial_lux
        self._last_sample_time = 0.0
        self._time_since_sample = 0.0
        self._min_lux_recorded = initial_lux
        self._max_lux_recorded = initial_lux
        self._sample_count = 0
