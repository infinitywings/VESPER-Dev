"""
Event Bus: Pub/Sub system for IoT device communication.

Provides a thread-safe event system with priority support and logging.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from queue import PriorityQueue, Empty
from typing import Any, Callable, Optional
from enum import IntEnum


logger = logging.getLogger(__name__)


class EventPriority(IntEnum):
    """Event priority levels."""
    LOW = 3
    NORMAL = 2
    HIGH = 1
    CRITICAL = 0


@dataclass(order=True)
class Event:
    """
    An event in the system.
    
    Attributes:
        event_type: Type/name of the event (e.g., "motion_detected")
        payload: Event data
        priority: Event priority for queue ordering
        timestamp: Unix timestamp when event was created
        event_id: Unique identifier for the event
        source_id: ID of the device/entity that generated the event
    """
    priority: int = field(compare=True)
    timestamp: float = field(compare=True)
    event_type: str = field(compare=False)
    payload: dict[str, Any] = field(compare=False, default_factory=dict)
    event_id: str = field(compare=False, default_factory=lambda: str(uuid.uuid4()))
    source_id: Optional[str] = field(compare=False, default=None)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source_id": self.source_id,
            "timestamp": self.timestamp,
            "priority": self.priority,
            "payload": self.payload,
        }
    
    def to_json(self) -> str:
        """Convert event to JSON string."""
        return json.dumps(self.to_dict())
    
    @classmethod
    def create(
        cls,
        event_type: str,
        payload: Optional[dict[str, Any]] = None,
        priority: EventPriority = EventPriority.NORMAL,
        source_id: Optional[str] = None,
    ) -> Event:
        """
        Factory method to create an event.
        
        Args:
            event_type: Type of the event
            payload: Event data
            priority: Event priority
            source_id: ID of the source device
            
        Returns:
            New Event instance
        """
        return cls(
            priority=priority,
            timestamp=time.time(),
            event_type=event_type,
            payload=payload or {},
            source_id=source_id,
        )


# Type alias for event handlers
EventHandler = Callable[[Event], None]


class EventBus:
    """
    Thread-safe pub/sub event system.
    
    Supports:
    - Multiple subscribers per event type
    - Wildcard subscriptions ("*" subscribes to all events)
    - Priority-based event ordering
    - Event logging to file
    - Thread-safe operations
    
    Example:
        bus = EventBus()
        bus.subscribe("motion_detected", lambda e: print(e))
        bus.publish(Event.create("motion_detected", {"agent_id": "agent_1"}))
        bus.process_events()  # Calls the handler
    """
    
    def __init__(
        self,
        max_queue_size: int = 1000,
        enable_logging: bool = False,
        log_file: Optional[str | Path] = None,
    ):
        """
        Initialize the event bus.
        
        Args:
            max_queue_size: Maximum number of events in the queue
            enable_logging: Whether to log events to file
            log_file: Path to the log file
        """
        self._max_queue_size = max_queue_size
        self._queue: PriorityQueue[Event] = PriorityQueue(maxsize=max_queue_size)
        self._subscribers: dict[str, list[EventHandler]] = {}
        self._lock = threading.RLock()
        self._enable_logging = enable_logging
        self._log_file = Path(log_file) if log_file else None
        self._running = False
        self._stats = {
            "events_published": 0,
            "events_processed": 0,
            "events_dropped": 0,
        }
        
        if self._enable_logging and self._log_file:
            self._log_file.parent.mkdir(parents=True, exist_ok=True)
    
    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """
        Subscribe to an event type.
        
        Args:
            event_type: Type of event to subscribe to. Use "*" for all events.
            handler: Callback function to handle the event.
        """
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(handler)
            logger.debug(f"Subscribed handler to '{event_type}'")
    
    def unsubscribe(self, event_type: str, handler: EventHandler) -> bool:
        """
        Unsubscribe from an event type.
        
        Args:
            event_type: Type of event to unsubscribe from.
            handler: Handler to remove.
            
        Returns:
            True if handler was found and removed, False otherwise.
        """
        with self._lock:
            if event_type in self._subscribers:
                try:
                    self._subscribers[event_type].remove(handler)
                    logger.debug(f"Unsubscribed handler from '{event_type}'")
                    return True
                except ValueError:
                    pass
            return False
    
    def publish(self, event: Event) -> bool:
        """
        Publish an event to the bus.
        
        Args:
            event: Event to publish.
            
        Returns:
            True if event was queued, False if queue is full.
        """
        try:
            self._queue.put_nowait(event)
            self._stats["events_published"] += 1
            
            if self._enable_logging:
                self._log_event(event)
            
            logger.debug(f"Published event: {event.event_type} (id={event.event_id[:8]})")
            return True
            
        except Exception:
            self._stats["events_dropped"] += 1
            logger.warning(f"Event queue full, dropped event: {event.event_type}")
            return False
    
    def emit(
        self,
        event_type: str,
        payload: Optional[dict[str, Any]] = None,
        priority: EventPriority = EventPriority.NORMAL,
        source_id: Optional[str] = None,
    ) -> bool:
        """
        Convenience method to create and publish an event.
        
        Args:
            event_type: Type of the event
            payload: Event data
            priority: Event priority
            source_id: ID of the source device
            
        Returns:
            True if event was queued, False otherwise.
        """
        event = Event.create(
            event_type=event_type,
            payload=payload,
            priority=priority,
            source_id=source_id,
        )
        return self.publish(event)
    
    def process_events(self, max_events: Optional[int] = None) -> int:
        """
        Process pending events in the queue.
        
        Args:
            max_events: Maximum number of events to process. If None, process all.
            
        Returns:
            Number of events processed.
        """
        processed = 0
        
        while max_events is None or processed < max_events:
            try:
                event = self._queue.get_nowait()
            except Empty:
                break
            
            self._dispatch_event(event)
            processed += 1
            self._stats["events_processed"] += 1
        
        return processed
    
    def _dispatch_event(self, event: Event) -> None:
        """Dispatch an event to all matching handlers."""
        handlers: list[EventHandler] = []
        
        with self._lock:
            # Get handlers for this specific event type
            if event.event_type in self._subscribers:
                handlers.extend(self._subscribers[event.event_type])
            
            # Get wildcard handlers
            if "*" in self._subscribers:
                handlers.extend(self._subscribers["*"])
        
        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error(f"Error in event handler: {e}", exc_info=True)
    
    def _log_event(self, event: Event) -> None:
        """Log event to file."""
        if not self._log_file:
            return
        
        try:
            with open(self._log_file, "a") as f:
                f.write(event.to_json() + "\n")
        except Exception as e:
            logger.error(f"Failed to log event: {e}")
    
    @property
    def pending_count(self) -> int:
        """Number of events waiting to be processed."""
        return self._queue.qsize()
    
    @property
    def stats(self) -> dict[str, int]:
        """Get event bus statistics."""
        return self._stats.copy()
    
    def clear(self) -> int:
        """
        Clear all pending events.
        
        Returns:
            Number of events cleared.
        """
        cleared = 0
        while True:
            try:
                self._queue.get_nowait()
                cleared += 1
            except Empty:
                break
        return cleared
    
    def get_subscribers(self, event_type: str) -> list[EventHandler]:
        """Get list of handlers for an event type."""
        with self._lock:
            return self._subscribers.get(event_type, []).copy()
