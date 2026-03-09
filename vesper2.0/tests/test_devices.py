"""
Unit tests for IoT device models.
"""

import pytest
import time
from unittest.mock import Mock, patch

from vesper.core.event_bus import EventBus, Event
from vesper.devices import (
    IoTDevice,
    MotionSensor,
    ContactSensor,
    SmartDoor,
    LightSensor,
)
from vesper.devices.smart_door import DoorState


class TestMotionSensor:
    """Tests for MotionSensor."""
    
    def test_create_sensor(self):
        """Test sensor creation."""
        sensor = MotionSensor(
            location=(1.0, 2.0, 3.0),
            detection_radius=5.0,
            cooldown=1.0,
        )
        
        assert sensor.device_type == "motion_sensor"
        assert sensor.location == (1.0, 2.0, 3.0)
        assert sensor.detection_radius == 5.0
        assert sensor.cooldown == 1.0
        assert sensor.is_active is True
    
    def test_detect_agent_in_range(self):
        """Test detection when agent is in range."""
        bus = EventBus()
        events = []
        bus.subscribe("motion_detected", lambda e: events.append(e))
        
        sensor = MotionSensor(
            location=(0.0, 0.0, 0.0),
            detection_radius=5.0,
            event_bus=bus,
        )
        
        # Agent at distance 3.0 (within radius)
        result = sensor.detect_agent("agent_1", (3.0, 0.0, 0.0))
        bus.process_events()
        
        assert result is True
        assert len(events) == 1
        assert events[0].payload["agent_id"] == "agent_1"
    
    def test_detect_agent_out_of_range(self):
        """Test no detection when agent is out of range."""
        bus = EventBus()
        events = []
        bus.subscribe("motion_detected", lambda e: events.append(e))
        
        sensor = MotionSensor(
            location=(0.0, 0.0, 0.0),
            detection_radius=5.0,
            event_bus=bus,
        )
        
        # Agent at distance 10.0 (outside radius)
        result = sensor.detect_agent("agent_1", (10.0, 0.0, 0.0))
        bus.process_events()
        
        assert result is False
        assert len(events) == 0
    
    def test_cooldown(self):
        """Test cooldown prevents rapid triggers."""
        bus = EventBus()
        sensor = MotionSensor(
            location=(0.0, 0.0, 0.0),
            detection_radius=5.0,
            cooldown=10.0,  # Long cooldown
            event_bus=bus,
        )
        
        # First detection works
        assert sensor.detect_agent("agent_1") is True
        
        # Second detection blocked by cooldown
        assert sensor.detect_agent("agent_1") is False
        assert sensor.is_in_cooldown is True
    
    def test_reset(self):
        """Test sensor reset."""
        sensor = MotionSensor()
        sensor.detect_agent("agent_1")
        
        assert sensor.trigger_count == 1
        
        sensor.reset()
        
        assert sensor.trigger_count == 0
        assert sensor.motion_detected is False


class TestContactSensor:
    """Tests for ContactSensor."""
    
    def test_create_sensor(self):
        """Test sensor creation."""
        sensor = ContactSensor(
            target_object_id="door_1",
            initial_state_open=False,
        )
        
        assert sensor.device_type == "contact_sensor"
        assert sensor.target_object_id == "door_1"
        assert sensor.is_open is False
    
    def test_open_close(self):
        """Test open/close state changes."""
        bus = EventBus()
        events = []
        bus.subscribe("*", lambda e: events.append(e))
        
        sensor = ContactSensor(
            target_object_id="door_1",
            event_bus=bus,
        )
        
        # Open the door
        sensor.set_open(True, force=True)
        bus.process_events()
        
        assert sensor.is_open is True
        assert any(e.event_type == "opened" for e in events)
        
        # Close the door
        sensor.set_open(False, force=True)
        bus.process_events()
        
        assert sensor.is_open is False
        assert any(e.event_type == "closed" for e in events)
    
    def test_toggle(self):
        """Test toggle functionality."""
        sensor = ContactSensor(debounce=0)  # Disable debounce for test
        
        assert sensor.is_open is False
        sensor.toggle()
        assert sensor.is_open is True
        sensor.toggle()
        assert sensor.is_open is False
    
    def test_change_count(self):
        """Test change counting."""
        sensor = ContactSensor(debounce=0)  # Disable debounce for test
        
        for _ in range(5):
            sensor.toggle()
        
        assert sensor.change_count == 5


class TestSmartDoor:
    """Tests for SmartDoor."""
    
    def test_create_door(self):
        """Test door creation."""
        door = SmartDoor(
            transition_time=2.0,
            auto_close=5.0,
        )
        
        assert door.device_type == "smart_door"
        assert door.transition_time == 2.0
        assert door.auto_close == 5.0
        assert door.is_fully_closed is True
    
    def test_open_close_commands(self):
        """Test open/close command handling."""
        bus = EventBus()
        door = SmartDoor(event_bus=bus)
        
        # Open command
        assert door.open() is True
        assert door.door_state == DoorState.OPENING
        
        # Simulate full open
        door.update(door.transition_time)
        assert door.door_state == DoorState.OPEN
        assert door.is_fully_open is True
        
        # Close command
        assert door.close() is True
        assert door.door_state == DoorState.CLOSING
        
        # Simulate full close
        door.update(door.transition_time)
        assert door.door_state == DoorState.CLOSED
        assert door.is_fully_closed is True
    
    def test_locked_door(self):
        """Test locked door cannot open."""
        door = SmartDoor(initial_locked=True)
        
        assert door.is_locked is True
        assert door.open() is False  # Should fail
    
    def test_lock_unlock(self):
        """Test lock/unlock operations."""
        door = SmartDoor()
        
        # Can't lock open door
        door.open()
        door.update(door.transition_time)
        assert door.lock() is False
        
        # Close and lock
        door.close()
        door.update(door.transition_time)
        assert door.lock() is True
        assert door.is_locked is True
        
        # Unlock
        assert door.unlock() is True
        assert door.is_locked is False
    
    def test_auto_close(self):
        """Test auto-close functionality."""
        door = SmartDoor(
            transition_time=0.5,
            auto_close=1.0,
        )
        
        door.open()
        door.update(0.5)  # Finish opening
        assert door.is_fully_open is True
        
        # Wait for auto-close
        door.update(1.1)  # Trigger auto-close
        assert door.door_state == DoorState.CLOSING


class TestLightSensor:
    """Tests for LightSensor."""
    
    def test_create_sensor(self):
        """Test sensor creation."""
        sensor = LightSensor(
            sample_rate=5.0,
            threshold=10.0,
            initial_lux=100.0,
        )
        
        assert sensor.device_type == "light_sensor"
        assert sensor.sample_rate == 5.0
        assert sensor.threshold == 10.0
        assert sensor.current_lux == 100.0
    
    def test_set_lux_triggers_event(self):
        """Test lux change triggers event."""
        bus = EventBus()
        events = []
        bus.subscribe("lux_level", lambda e: events.append(e))
        
        sensor = LightSensor(
            threshold=10.0,
            initial_lux=100.0,
            event_bus=bus,
        )
        
        # Change exceeds threshold
        sensor.set_lux(150.0)
        bus.process_events()
        
        assert len(events) == 1
        assert events[0].payload["lux"] == 150.0
    
    def test_set_lux_below_threshold(self):
        """Test small lux change doesn't trigger event."""
        bus = EventBus()
        events = []
        bus.subscribe("lux_level", lambda e: events.append(e))
        
        sensor = LightSensor(
            threshold=10.0,
            initial_lux=100.0,
            event_bus=bus,
        )
        
        # Change below threshold
        sensor.set_lux(105.0)
        bus.process_events()
        
        assert len(events) == 0
    
    def test_min_max_tracking(self):
        """Test min/max lux tracking."""
        sensor = LightSensor(initial_lux=100.0, threshold=0)
        
        sensor.set_lux(50.0)
        sensor.set_lux(200.0)
        sensor.set_lux(75.0)
        
        assert sensor.min_lux_recorded == 50.0
        assert sensor.max_lux_recorded == 200.0


class TestDeviceBase:
    """Tests for base IoTDevice functionality."""
    
    def test_device_id_generation(self):
        """Test unique ID generation."""
        sensor1 = MotionSensor()
        sensor2 = MotionSensor()
        
        assert sensor1.device_id != sensor2.device_id
    
    def test_custom_device_id(self):
        """Test custom device ID."""
        sensor = MotionSensor(device_id="custom_id_123")
        assert sensor.device_id == "custom_id_123"
    
    def test_device_repr(self):
        """Test string representation."""
        sensor = MotionSensor(name="Kitchen Motion")
        repr_str = repr(sensor)
        
        assert "MotionSensor" in repr_str
        assert "Kitchen Motion" in repr_str
    
    def test_inactive_device(self):
        """Test inactive device doesn't emit events."""
        bus = EventBus()
        events = []
        bus.subscribe("*", lambda e: events.append(e))
        
        sensor = MotionSensor(event_bus=bus)
        sensor.is_active = False
        
        sensor.detect_agent("agent_1")
        bus.process_events()
        
        assert len(events) == 0
    
    def test_get_base_state(self):
        """Test base state includes common fields."""
        sensor = MotionSensor(
            location=(1.0, 2.0, 3.0),
            name="Test Sensor",
        )
        
        state = sensor.get_base_state()
        
        assert state["device_type"] == "motion_sensor"
        assert state["location"] == (1.0, 2.0, 3.0)
        assert state["name"] == "Test Sensor"
        assert state["is_active"] is True
