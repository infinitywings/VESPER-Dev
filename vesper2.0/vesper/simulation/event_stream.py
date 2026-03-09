"""
Event Stream for real-time simulation event handling.

Coordinates all simulation events including:
- Time-based events
- Task state changes
- IoT device triggers
- Humanoid state updates
- Sensor detections
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class EventType(Enum):
    """Types of events in the simulation."""
    # Time events
    TIME_UPDATE = "time_update"
    TIME_PERIOD_CHANGE = "time_period_change"
    
    # Task events
    TASK_SCHEDULED = "task_scheduled"
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    TASK_COMPLETED = "task_completed"
    TASK_INTERRUPTED = "task_interrupted"
    TASK_FAILED = "task_failed"
    
    # Humanoid events
    HUMANOID_MOVED = "humanoid_moved"
    HUMANOID_ROOM_CHANGED = "humanoid_room_changed"
    HUMANOID_ACTION = "humanoid_action"
    
    # IoT events
    IOT_DEVICE_STATE = "iot_device_state"
    IOT_SENSOR_TRIGGER = "iot_sensor_trigger"
    IOT_AUTOMATION = "iot_automation"
    
    # Camera events
    CAMERA_DETECTION = "camera_detection"
    CAMERA_TRACKING = "camera_tracking"
    
    # Motion sensor events
    MOTION_DETECTED = "motion_detected"
    MOTION_CLEARED = "motion_cleared"
    
    # System events
    SIMULATION_START = "simulation_start"
    SIMULATION_PAUSE = "simulation_pause"
    SIMULATION_RESUME = "simulation_resume"
    SIMULATION_STOP = "simulation_stop"
    ERROR = "error"


@dataclass
class Event:
    """
    A simulation event.
    """
    event_type: EventType
    timestamp: datetime
    source: str  # Source component/entity ID
    data: Dict[str, Any] = field(default_factory=dict)
    
    # Metadata
    priority: int = 0  # Higher = more important
    sequence_id: int = 0  # Auto-assigned sequence number
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "data": self.data,
            "priority": self.priority,
            "sequence_id": self.sequence_id,
        }
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        """Create event from dictionary."""
        return cls(
            event_type=EventType(data["type"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            source=data["source"],
            data=data.get("data", {}),
            priority=data.get("priority", 0),
            sequence_id=data.get("sequence_id", 0),
        )


EventHandler = Callable[[Event], None]
AsyncEventHandler = Callable[[Event], Any]  # Can be async


class EventStream:
    """
    Central event stream for simulation.
    
    Handles event publishing, subscription, and routing.
    Thread-safe for concurrent access.
    """
    
    def __init__(self, max_history: int = 1000):
        """
        Initialize the event stream.
        
        Args:
            max_history: Maximum events to keep in history
        """
        self._sequence_counter = 0
        self._lock = threading.Lock()
        
        # Subscribers by event type
        self._subscribers: Dict[EventType, List[EventHandler]] = {}
        self._async_subscribers: Dict[EventType, List[AsyncEventHandler]] = {}
        
        # Global subscribers (receive all events)
        self._global_subscribers: List[EventHandler] = []
        self._async_global_subscribers: List[AsyncEventHandler] = []
        
        # Event history
        self._history: List[Event] = []
        self._max_history = max_history
        
        # Event queue for async processing
        self._event_queue: queue.Queue = queue.Queue()
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        
        logger.info("EventStream initialized")
    
    def start(self):
        """Start the event stream worker thread."""
        if self._running:
            return
        
        self._running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        logger.info("EventStream worker started")
    
    def stop(self):
        """Stop the event stream worker thread."""
        self._running = False
        if self._worker_thread:
            self._event_queue.put(None)  # Sentinel to unblock
            self._worker_thread.join(timeout=1.0)
            self._worker_thread = None
        logger.info("EventStream worker stopped")
    
    def _worker_loop(self):
        """Background worker for processing queued events."""
        while self._running:
            try:
                event = self._event_queue.get(timeout=0.1)
                if event is None:
                    break
                self._dispatch_event(event)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Event worker error: {e}")
    
    def subscribe(
        self,
        event_type: EventType,
        handler: EventHandler,
    ):
        """
        Subscribe to a specific event type.
        
        Args:
            event_type: Type of event to subscribe to
            handler: Callback function
        """
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(handler)
    
    def subscribe_async(
        self,
        event_type: EventType,
        handler: AsyncEventHandler,
    ):
        """Subscribe with an async handler."""
        with self._lock:
            if event_type not in self._async_subscribers:
                self._async_subscribers[event_type] = []
            self._async_subscribers[event_type].append(handler)
    
    def subscribe_all(self, handler: EventHandler):
        """Subscribe to all event types."""
        with self._lock:
            self._global_subscribers.append(handler)
    
    def subscribe_all_async(self, handler: AsyncEventHandler):
        """Subscribe to all event types with async handler."""
        with self._lock:
            self._async_global_subscribers.append(handler)
    
    def unsubscribe(
        self,
        event_type: EventType,
        handler: EventHandler,
    ):
        """Unsubscribe from an event type."""
        with self._lock:
            if event_type in self._subscribers:
                try:
                    self._subscribers[event_type].remove(handler)
                except ValueError:
                    pass
    
    def publish(
        self,
        event_type: EventType,
        source: str,
        data: Optional[Dict[str, Any]] = None,
        priority: int = 0,
        timestamp: Optional[datetime] = None,
        async_dispatch: bool = False,
    ) -> Event:
        """
        Publish an event.
        
        Args:
            event_type: Type of event
            source: Source component ID
            data: Event data
            priority: Event priority (higher = more important)
            timestamp: Event timestamp (defaults to now)
            async_dispatch: Whether to dispatch asynchronously
            
        Returns:
            The published event
        """
        with self._lock:
            self._sequence_counter += 1
            sequence_id = self._sequence_counter
        
        event = Event(
            event_type=event_type,
            timestamp=timestamp or datetime.now(),
            source=source,
            data=data or {},
            priority=priority,
            sequence_id=sequence_id,
        )
        
        # Add to history
        with self._lock:
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]
        
        # Dispatch
        if async_dispatch and self._running:
            self._event_queue.put(event)
        else:
            self._dispatch_event(event)
        
        return event
    
    def _dispatch_event(self, event: Event):
        """Dispatch event to subscribers."""
        handlers = []
        
        with self._lock:
            # Get type-specific handlers
            if event.event_type in self._subscribers:
                handlers.extend(self._subscribers[event.event_type])
            
            # Add global handlers
            handlers.extend(self._global_subscribers)
        
        # Call handlers
        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error(f"Event handler error: {e}")
    
    async def _dispatch_event_async(self, event: Event):
        """Dispatch event to async subscribers."""
        handlers = []
        
        with self._lock:
            if event.event_type in self._async_subscribers:
                handlers.extend(self._async_subscribers[event.event_type])
            handlers.extend(self._async_global_subscribers)
        
        for handler in handlers:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Async event handler error: {e}")
    
    def get_history(
        self,
        event_type: Optional[EventType] = None,
        source: Optional[str] = None,
        limit: int = 100,
        since: Optional[datetime] = None,
    ) -> List[Event]:
        """
        Get events from history.
        
        Args:
            event_type: Filter by event type
            source: Filter by source
            limit: Maximum events to return
            since: Only events after this time
            
        Returns:
            List of matching events
        """
        with self._lock:
            events = list(self._history)
        
        # Apply filters
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        if source:
            events = [e for e in events if e.source == source]
        if since:
            events = [e for e in events if e.timestamp >= since]
        
        # Return most recent
        return events[-limit:]
    
    def clear_history(self):
        """Clear event history."""
        with self._lock:
            self._history.clear()
    
    # Convenience methods for common events
    
    def publish_time_update(self, current_time: datetime):
        """Publish a time update event."""
        return self.publish(
            EventType.TIME_UPDATE,
            "time_manager",
            {"time": current_time.isoformat()},
        )
    
    def publish_task_started(self, task_id: str, task_name: str, location: str):
        """Publish a task started event."""
        return self.publish(
            EventType.TASK_STARTED,
            "task_executor",
            {"task_id": task_id, "name": task_name, "location": location},
            priority=1,
        )
    
    def publish_task_completed(self, task_id: str, task_name: str, duration: float):
        """Publish a task completed event."""
        return self.publish(
            EventType.TASK_COMPLETED,
            "task_executor",
            {"task_id": task_id, "name": task_name, "duration_seconds": duration},
            priority=1,
        )
    
    def publish_humanoid_moved(
        self,
        humanoid_id: str,
        position: tuple,
        room: Optional[str] = None,
    ):
        """Publish a humanoid movement event."""
        return self.publish(
            EventType.HUMANOID_MOVED,
            humanoid_id,
            {"position": position, "room": room},
        )
    
    def publish_room_change(
        self,
        humanoid_id: str,
        from_room: str,
        to_room: str,
    ):
        """Publish a room change event."""
        return self.publish(
            EventType.HUMANOID_ROOM_CHANGED,
            humanoid_id,
            {"from_room": from_room, "to_room": to_room},
            priority=2,
        )
    
    def publish_motion_detected(
        self,
        sensor_id: str,
        target_id: str,
        distance: float,
        confidence: float,
    ):
        """Publish a motion detection event."""
        return self.publish(
            EventType.MOTION_DETECTED,
            sensor_id,
            {
                "target_id": target_id,
                "distance": distance,
                "confidence": confidence,
            },
            priority=3,
        )
    
    def publish_iot_state(
        self,
        device_id: str,
        state: Dict[str, Any],
    ):
        """Publish an IoT device state change."""
        return self.publish(
            EventType.IOT_DEVICE_STATE,
            device_id,
            {"state": state},
            priority=2,
        )


class EventLogger:
    """
    Logs events to file for analysis.
    """
    
    def __init__(self, log_path: str, event_stream: EventStream):
        """
        Initialize event logger.
        
        Args:
            log_path: Path to log file
            event_stream: EventStream to subscribe to
        """
        self.log_path = log_path
        self.event_stream = event_stream
        
        # Subscribe to all events
        event_stream.subscribe_all(self._handle_event)
        
        logger.info(f"EventLogger initialized, writing to {log_path}")
    
    def _handle_event(self, event: Event):
        """Handle incoming event."""
        try:
            with open(self.log_path, "a") as f:
                f.write(event.to_json() + "\n")
        except Exception as e:
            logger.error(f"Failed to log event: {e}")
    
    def read_log(self, limit: int = 100) -> List[Event]:
        """Read events from log file."""
        events = []
        try:
            with open(self.log_path, "r") as f:
                lines = f.readlines()[-limit:]
                for line in lines:
                    try:
                        data = json.loads(line.strip())
                        events.append(Event.from_dict(data))
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            pass
        return events


class SimulationCoordinator:
    """
    Coordinates all simulation components through events.
    """
    
    def __init__(self):
        """Initialize the simulation coordinator."""
        self.event_stream = EventStream()
        
        # Component references (set by components registering)
        self._components: Dict[str, Any] = {}
        
        # Running state
        self._running = False
        self._paused = False
    
    def register_component(self, name: str, component: Any):
        """Register a simulation component."""
        self._components[name] = component
        logger.info(f"Registered component: {name}")
    
    def get_component(self, name: str) -> Optional[Any]:
        """Get a registered component."""
        return self._components.get(name)
    
    def start(self):
        """Start the simulation."""
        self._running = True
        self._paused = False
        self.event_stream.start()
        self.event_stream.publish(
            EventType.SIMULATION_START,
            "coordinator",
            {"time": datetime.now().isoformat()},
        )
    
    def pause(self):
        """Pause the simulation."""
        self._paused = True
        self.event_stream.publish(
            EventType.SIMULATION_PAUSE,
            "coordinator",
            {"time": datetime.now().isoformat()},
        )
    
    def resume(self):
        """Resume the simulation."""
        self._paused = False
        self.event_stream.publish(
            EventType.SIMULATION_RESUME,
            "coordinator",
            {"time": datetime.now().isoformat()},
        )
    
    def stop(self):
        """Stop the simulation."""
        self._running = False
        self.event_stream.publish(
            EventType.SIMULATION_STOP,
            "coordinator",
            {"time": datetime.now().isoformat()},
        )
        self.event_stream.stop()
    
    @property
    def is_running(self) -> bool:
        """Check if simulation is running."""
        return self._running and not self._paused
