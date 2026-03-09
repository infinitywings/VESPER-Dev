"""
Time Manager for synchronized real-time simulation.

Features:
- Real-time clock synchronization
- Time acceleration/deceleration modes
- Day/night cycle tracking
- Time-based event scheduling
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class TimeOfDay(Enum):
    """Time of day periods."""
    NIGHT = "night"           # 00:00 - 06:00
    EARLY_MORNING = "early_morning"  # 06:00 - 08:00
    MORNING = "morning"       # 08:00 - 12:00
    AFTERNOON = "afternoon"   # 12:00 - 17:00
    EVENING = "evening"       # 17:00 - 21:00
    LATE_NIGHT = "late_night" # 21:00 - 00:00


class DayOfWeek(Enum):
    """Days of the week."""
    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6


@dataclass
class TimeConfig:
    """Configuration for the time manager."""
    
    # Synchronization mode
    sync_to_real_time: bool = True  # Match real-world time
    
    # Time scale (for testing/acceleration)
    time_scale: float = 1.0  # 1.0 = real-time, 2.0 = 2x speed
    
    # Starting time (if not syncing to real time)
    start_time: Optional[datetime] = None
    
    # Day/night cycle
    sunrise_hour: int = 6
    sunset_hour: int = 18
    
    # Update frequency
    update_interval: float = 1.0  # seconds between time updates


@dataclass
class ScheduledEvent:
    """A time-based scheduled event."""
    event_id: str
    trigger_time: datetime
    callback: Callable[[], None]
    recurring: bool = False
    interval: Optional[timedelta] = None
    data: Dict[str, Any] = field(default_factory=dict)


class TimeManager:
    """
    Manages simulation time with real-world synchronization.
    
    Provides:
    - Current simulation time (synced to real time or accelerated)
    - Time of day and day of week
    - Scheduled events
    - Light level based on time
    """
    
    def __init__(self, config: Optional[TimeConfig] = None):
        """
        Initialize the time manager.
        
        Args:
            config: Time configuration
        """
        self.config = config or TimeConfig()
        
        # Time state
        if self.config.sync_to_real_time:
            self._simulation_time = datetime.now()
        else:
            self._simulation_time = self.config.start_time or datetime.now()
        
        self._last_update = time.time()
        self._start_real_time = time.time()
        self._start_sim_time = self._simulation_time
        
        # Scheduled events
        self._scheduled_events: List[ScheduledEvent] = []
        self._event_counter = 0
        
        # Callbacks for time changes
        self._time_callbacks: List[Callable[[datetime], None]] = []
        self._period_change_callbacks: List[Callable[[TimeOfDay, TimeOfDay], None]] = []
        
        # Track current period
        self._current_period = self._get_time_of_day(self._simulation_time)
        
        logger.info(f"TimeManager initialized at {self._simulation_time}")
    
    def update(self, real_dt: Optional[float] = None) -> datetime:
        """
        Update simulation time.
        
        Args:
            real_dt: Real-world delta time in seconds (auto-calculated if None)
            
        Returns:
            Current simulation time
        """
        current_real = time.time()
        
        if real_dt is None:
            real_dt = current_real - self._last_update
        
        self._last_update = current_real
        
        # Calculate simulation time advancement
        sim_dt = timedelta(seconds=real_dt * self.config.time_scale)
        
        if self.config.sync_to_real_time and self.config.time_scale == 1.0:
            # Direct sync to real time
            self._simulation_time = datetime.now()
        else:
            # Advance by scaled delta
            self._simulation_time += sim_dt
        
        # Check for period change
        new_period = self._get_time_of_day(self._simulation_time)
        if new_period != self._current_period:
            old_period = self._current_period
            self._current_period = new_period
            self._notify_period_change(old_period, new_period)
        
        # Process scheduled events
        self._process_scheduled_events()
        
        # Notify time callbacks
        for callback in self._time_callbacks:
            try:
                callback(self._simulation_time)
            except Exception as e:
                logger.error(f"Time callback error: {e}")
        
        return self._simulation_time
    
    def _get_time_of_day(self, dt: datetime) -> TimeOfDay:
        """Determine time of day period."""
        hour = dt.hour
        
        if 0 <= hour < 6:
            return TimeOfDay.NIGHT
        elif 6 <= hour < 8:
            return TimeOfDay.EARLY_MORNING
        elif 8 <= hour < 12:
            return TimeOfDay.MORNING
        elif 12 <= hour < 17:
            return TimeOfDay.AFTERNOON
        elif 17 <= hour < 21:
            return TimeOfDay.EVENING
        else:
            return TimeOfDay.LATE_NIGHT
    
    def _notify_period_change(self, old_period: TimeOfDay, new_period: TimeOfDay):
        """Notify callbacks of period change."""
        logger.info(f"Time period changed: {old_period.value} -> {new_period.value}")
        
        for callback in self._period_change_callbacks:
            try:
                callback(old_period, new_period)
            except Exception as e:
                logger.error(f"Period change callback error: {e}")
    
    def _process_scheduled_events(self):
        """Process and trigger scheduled events."""
        current = self._simulation_time
        triggered = []
        
        for event in self._scheduled_events:
            if current >= event.trigger_time:
                triggered.append(event)
        
        for event in triggered:
            try:
                event.callback()
            except Exception as e:
                logger.error(f"Scheduled event {event.event_id} error: {e}")
            
            if event.recurring and event.interval:
                # Reschedule
                event.trigger_time = current + event.interval
            else:
                # Remove one-time event
                self._scheduled_events.remove(event)
    
    @property
    def current_time(self) -> datetime:
        """Get current simulation time."""
        return self._simulation_time
    
    @property
    def time_of_day(self) -> TimeOfDay:
        """Get current time of day period."""
        return self._current_period
    
    @property
    def day_of_week(self) -> DayOfWeek:
        """Get current day of week."""
        return DayOfWeek(self._simulation_time.weekday())
    
    @property
    def is_weekend(self) -> bool:
        """Check if it's a weekend."""
        return self._simulation_time.weekday() >= 5
    
    @property
    def is_daytime(self) -> bool:
        """Check if it's daytime (between sunrise and sunset)."""
        hour = self._simulation_time.hour
        return self.config.sunrise_hour <= hour < self.config.sunset_hour
    
    @property
    def light_level(self) -> float:
        """
        Get ambient light level (0.0 = dark, 1.0 = bright).
        
        Simulates natural daylight cycle.
        """
        hour = self._simulation_time.hour + self._simulation_time.minute / 60.0
        
        sunrise = self.config.sunrise_hour
        sunset = self.config.sunset_hour
        midday = (sunrise + sunset) / 2
        
        if hour < sunrise - 1 or hour > sunset + 1:
            # Night
            return 0.1
        elif hour < sunrise:
            # Dawn
            return 0.1 + 0.4 * (hour - (sunrise - 1))
        elif hour < sunrise + 1:
            # Early morning
            return 0.5 + 0.5 * (hour - sunrise)
        elif hour < sunset - 1:
            # Day (peak at midday)
            if hour < midday:
                return 0.8 + 0.2 * ((hour - sunrise - 1) / (midday - sunrise - 1))
            else:
                return 1.0 - 0.2 * ((hour - midday) / (sunset - midday - 1))
        elif hour < sunset:
            # Late afternoon
            return 0.8 - 0.3 * (hour - (sunset - 1))
        else:
            # Dusk
            return 0.5 - 0.4 * (hour - sunset)
    
    def schedule_event(
        self,
        callback: Callable[[], None],
        delay: Optional[timedelta] = None,
        at_time: Optional[datetime] = None,
        recurring: bool = False,
        interval: Optional[timedelta] = None,
        event_id: Optional[str] = None,
    ) -> str:
        """
        Schedule an event.
        
        Args:
            callback: Function to call when triggered
            delay: Delay from now (mutually exclusive with at_time)
            at_time: Specific time to trigger
            recurring: Whether to repeat
            interval: Interval for recurring events
            event_id: Optional event identifier
            
        Returns:
            Event ID
        """
        if event_id is None:
            self._event_counter += 1
            event_id = f"event_{self._event_counter}"
        
        if at_time:
            trigger_time = at_time
        elif delay:
            trigger_time = self._simulation_time + delay
        else:
            trigger_time = self._simulation_time
        
        event = ScheduledEvent(
            event_id=event_id,
            trigger_time=trigger_time,
            callback=callback,
            recurring=recurring,
            interval=interval,
        )
        
        self._scheduled_events.append(event)
        logger.debug(f"Scheduled event {event_id} for {trigger_time}")
        
        return event_id
    
    def cancel_event(self, event_id: str) -> bool:
        """Cancel a scheduled event."""
        for event in self._scheduled_events:
            if event.event_id == event_id:
                self._scheduled_events.remove(event)
                return True
        return False
    
    def schedule_daily(
        self,
        callback: Callable[[], None],
        hour: int,
        minute: int = 0,
        event_id: Optional[str] = None,
    ) -> str:
        """
        Schedule a daily recurring event.
        
        Args:
            callback: Function to call
            hour: Hour of day (0-23)
            minute: Minute of hour (0-59)
            event_id: Optional event identifier
            
        Returns:
            Event ID
        """
        # Calculate next occurrence
        today = self._simulation_time.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        
        if today <= self._simulation_time:
            today += timedelta(days=1)
        
        return self.schedule_event(
            callback=callback,
            at_time=today,
            recurring=True,
            interval=timedelta(days=1),
            event_id=event_id,
        )
    
    def on_time_update(self, callback: Callable[[datetime], None]):
        """Register callback for time updates."""
        self._time_callbacks.append(callback)
    
    def on_period_change(self, callback: Callable[[TimeOfDay, TimeOfDay], None]):
        """Register callback for time period changes."""
        self._period_change_callbacks.append(callback)
    
    def set_time(self, new_time: datetime):
        """Manually set simulation time."""
        self._simulation_time = new_time
        self._current_period = self._get_time_of_day(new_time)
        logger.info(f"Time manually set to {new_time}")
    
    def set_time_scale(self, scale: float):
        """Set time scale factor."""
        self.config.time_scale = max(0.0, scale)
        logger.info(f"Time scale set to {scale}x")
    
    def format_time(self, include_date: bool = False) -> str:
        """Format current time as string."""
        if include_date:
            return self._simulation_time.strftime("%Y-%m-%d %H:%M:%S")
        return self._simulation_time.strftime("%H:%M:%S")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert time state to dictionary."""
        return {
            "current_time": self._simulation_time.isoformat(),
            "time_of_day": self._current_period.value,
            "day_of_week": self.day_of_week.name.lower(),
            "is_weekend": self.is_weekend,
            "is_daytime": self.is_daytime,
            "light_level": round(self.light_level, 2),
            "time_scale": self.config.time_scale,
            "scheduled_events": len(self._scheduled_events),
        }
