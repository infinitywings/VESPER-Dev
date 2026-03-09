"""
Unit tests for the EventBus system.
"""

import pytest
import time
from threading import Thread
from vesper.core.event_bus import EventBus, Event, EventPriority


class TestEvent:
    """Tests for the Event class."""
    
    def test_create_event(self):
        """Test event creation."""
        event = Event.create(
            event_type="test_event",
            payload={"key": "value"},
            source_id="device_1",
        )
        
        assert event.event_type == "test_event"
        assert event.payload == {"key": "value"}
        assert event.source_id == "device_1"
        assert event.priority == EventPriority.NORMAL
        assert event.event_id is not None
        assert event.timestamp > 0
    
    def test_event_priority(self):
        """Test event priorities."""
        low = Event.create("low", priority=EventPriority.LOW)
        normal = Event.create("normal", priority=EventPriority.NORMAL)
        high = Event.create("high", priority=EventPriority.HIGH)
        critical = Event.create("critical", priority=EventPriority.CRITICAL)
        
        # Lower priority value = higher priority
        assert critical.priority < high.priority
        assert high.priority < normal.priority
        assert normal.priority < low.priority
    
    def test_event_to_dict(self):
        """Test event serialization."""
        event = Event.create("test", payload={"x": 1})
        data = event.to_dict()
        
        assert data["event_type"] == "test"
        assert data["payload"] == {"x": 1}
        assert "event_id" in data
        assert "timestamp" in data
    
    def test_event_to_json(self):
        """Test JSON serialization."""
        event = Event.create("test", payload={"x": 1})
        json_str = event.to_json()
        
        assert '"event_type": "test"' in json_str
        assert '"x": 1' in json_str


class TestEventBus:
    """Tests for the EventBus class."""
    
    def test_create_bus(self):
        """Test bus creation."""
        bus = EventBus()
        assert bus.pending_count == 0
        assert bus.stats["events_published"] == 0
    
    def test_subscribe_and_publish(self):
        """Test basic pub/sub."""
        bus = EventBus()
        received = []
        
        def handler(event):
            received.append(event)
        
        bus.subscribe("test_event", handler)
        bus.emit("test_event", payload={"value": 42})
        bus.process_events()
        
        assert len(received) == 1
        assert received[0].event_type == "test_event"
        assert received[0].payload["value"] == 42
    
    def test_wildcard_subscription(self):
        """Test wildcard subscriber receives all events."""
        bus = EventBus()
        received = []
        
        bus.subscribe("*", lambda e: received.append(e))
        
        bus.emit("event_a")
        bus.emit("event_b")
        bus.emit("event_c")
        bus.process_events()
        
        assert len(received) == 3
    
    def test_multiple_subscribers(self):
        """Test multiple handlers for same event."""
        bus = EventBus()
        results = []
        
        bus.subscribe("event", lambda e: results.append("a"))
        bus.subscribe("event", lambda e: results.append("b"))
        bus.subscribe("event", lambda e: results.append("c"))
        
        bus.emit("event")
        bus.process_events()
        
        assert results == ["a", "b", "c"]
    
    def test_unsubscribe(self):
        """Test handler removal."""
        bus = EventBus()
        received = []
        
        def handler(event):
            received.append(event)
        
        bus.subscribe("event", handler)
        bus.emit("event")
        bus.process_events()
        assert len(received) == 1
        
        # Unsubscribe
        result = bus.unsubscribe("event", handler)
        assert result is True
        
        bus.emit("event")
        bus.process_events()
        assert len(received) == 1  # No new events
    
    def test_priority_ordering(self):
        """Test events processed in priority order."""
        bus = EventBus()
        order = []
        
        bus.subscribe("*", lambda e: order.append(e.event_type))
        
        # Publish in reverse priority order
        bus.emit("low", priority=EventPriority.LOW)
        bus.emit("normal", priority=EventPriority.NORMAL)
        bus.emit("high", priority=EventPriority.HIGH)
        bus.emit("critical", priority=EventPriority.CRITICAL)
        
        bus.process_events()
        
        # Should be processed in priority order
        assert order == ["critical", "high", "normal", "low"]
    
    def test_max_events_processing(self):
        """Test processing limited number of events."""
        bus = EventBus()
        
        for i in range(10):
            bus.emit("event", payload={"i": i})
        
        assert bus.pending_count == 10
        
        processed = bus.process_events(max_events=3)
        
        assert processed == 3
        assert bus.pending_count == 7
    
    def test_stats_tracking(self):
        """Test statistics are tracked correctly."""
        bus = EventBus()
        
        bus.emit("event1")
        bus.emit("event2")
        bus.emit("event3")
        
        assert bus.stats["events_published"] == 3
        assert bus.stats["events_processed"] == 0
        
        bus.process_events()
        
        assert bus.stats["events_processed"] == 3
    
    def test_clear_queue(self):
        """Test clearing pending events."""
        bus = EventBus()
        
        for _ in range(5):
            bus.emit("event")
        
        cleared = bus.clear()
        
        assert cleared == 5
        assert bus.pending_count == 0
    
    def test_handler_exception_safety(self):
        """Test that handler exceptions don't break processing."""
        bus = EventBus()
        received = []
        
        def bad_handler(event):
            raise RuntimeError("Handler error")
        
        def good_handler(event):
            received.append(event)
        
        bus.subscribe("event", bad_handler)
        bus.subscribe("event", good_handler)
        
        bus.emit("event")
        # Should not raise
        bus.process_events()
        
        # Good handler should still be called
        assert len(received) == 1
    
    def test_thread_safety(self):
        """Test concurrent publish/subscribe operations."""
        bus = EventBus()
        received = []
        
        bus.subscribe("event", lambda e: received.append(e))
        
        def publisher():
            for _ in range(100):
                bus.emit("event")
        
        threads = [Thread(target=publisher) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        bus.process_events()
        
        assert len(received) == 500
    
    def test_get_subscribers(self):
        """Test getting list of subscribers."""
        bus = EventBus()
        
        handler1 = lambda e: None
        handler2 = lambda e: None
        
        bus.subscribe("event", handler1)
        bus.subscribe("event", handler2)
        
        subscribers = bus.get_subscribers("event")
        
        assert len(subscribers) == 2
        assert handler1 in subscribers
        assert handler2 in subscribers
