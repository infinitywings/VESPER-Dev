"""
Unit tests for protocol messages and codec.
"""

import pytest
import time

from vesper.protocol.messages import (
    Message,
    MessageType,
    MessagePriority,
    EventMessage,
    CommandMessage,
    StateMessage,
    AckMessage,
    AckStatus,
)
from vesper.protocol.codec import JSONCodec


class TestMessage:
    """Tests for base Message class."""
    
    def test_create_message(self):
        """Test message creation."""
        msg = Message(
            source_id="device_1",
            target_id="agent_1",
            payload={"key": "value"},
        )
        
        assert msg.source_id == "device_1"
        assert msg.target_id == "agent_1"
        assert msg.payload == {"key": "value"}
        assert msg.message_type == MessageType.EVENT
        assert msg.message_id is not None
    
    def test_message_to_dict(self):
        """Test serialization to dict."""
        msg = Message(source_id="test")
        data = msg.to_dict()
        
        assert data["source_id"] == "test"
        assert data["message_type"] == "EVENT"
        assert "message_id" in data
        assert "timestamp" in data
    
    def test_message_from_dict(self):
        """Test deserialization from dict."""
        data = {
            "message_id": "abc123",
            "message_type": "COMMAND",
            "source_id": "agent",
            "target_id": "device",
            "payload": {"action": "open"},
        }
        
        msg = Message.from_dict(data)
        
        assert msg.message_id == "abc123"
        assert msg.message_type == MessageType.COMMAND
        assert msg.source_id == "agent"


class TestEventMessage:
    """Tests for EventMessage."""
    
    def test_create_event(self):
        """Test event creation."""
        event = EventMessage.create(
            event_name="motion_detected",
            source_id="sensor_1",
            payload={"agent_id": "agent_1"},
        )
        
        assert event.event_name == "motion_detected"
        assert event.source_id == "sensor_1"
        assert event.message_type == MessageType.EVENT
    
    def test_event_to_dict(self):
        """Test event serialization."""
        event = EventMessage.create("test_event", "source")
        data = event.to_dict()
        
        assert data["event_name"] == "test_event"
        assert data["message_type"] == "EVENT"


class TestCommandMessage:
    """Tests for CommandMessage."""
    
    def test_create_command(self):
        """Test command creation."""
        cmd = CommandMessage.create(
            command_name="open_door",
            target_id="door_1",
            parameters={"speed": "fast"},
        )
        
        assert cmd.command_name == "open_door"
        assert cmd.target_id == "door_1"
        assert cmd.parameters == {"speed": "fast"}
        assert cmd.requires_ack is True
    
    def test_command_timeout(self):
        """Test command timeout setting."""
        cmd = CommandMessage.create("test", "target")
        assert cmd.timeout_ms == 5000  # Default
        
        cmd.timeout_ms = 10000
        assert cmd.timeout_ms == 10000


class TestStateMessage:
    """Tests for StateMessage."""
    
    def test_create_state_update(self):
        """Test state update creation."""
        state = StateMessage.create_update(
            source_id="device_1",
            state_data={"temperature": 22.5, "humidity": 45},
        )
        
        assert state.source_id == "device_1"
        assert state.state_data["temperature"] == 22.5
        assert state.is_query is False
    
    def test_create_state_query(self):
        """Test state query creation."""
        query = StateMessage.create_query(
            source_id="agent_1",
            target_id="device_1",
            fields=["temperature"],
        )
        
        assert query.is_query is True
        assert query.target_id == "device_1"


class TestAckMessage:
    """Tests for AckMessage."""
    
    def test_create_success_ack(self):
        """Test success acknowledgment."""
        cmd = CommandMessage.create("test", "target")
        ack = AckMessage.create_success(cmd, "device_1")
        
        assert ack.status == AckStatus.SUCCESS
        assert ack.correlation_id == cmd.message_id
        assert ack.error_message is None
    
    def test_create_failure_ack(self):
        """Test failure acknowledgment."""
        cmd = CommandMessage.create("test", "target")
        ack = AckMessage.create_failure(cmd, "device_1", "Command failed")
        
        assert ack.status == AckStatus.FAILURE
        assert ack.error_message == "Command failed"


class TestJSONCodec:
    """Tests for JSONCodec."""
    
    def test_encode_decode_message(self):
        """Test round-trip encoding."""
        codec = JSONCodec()
        original = Message(
            source_id="test",
            payload={"key": "value"},
        )
        
        encoded = codec.encode(original)
        decoded = codec.decode(encoded)
        
        assert decoded.source_id == original.source_id
        assert decoded.payload == original.payload
    
    def test_encode_decode_event(self):
        """Test event encoding."""
        codec = JSONCodec()
        event = EventMessage.create("motion", "sensor_1", {"x": 1})
        
        encoded = codec.encode(event)
        decoded = codec.decode(encoded)
        
        assert isinstance(decoded, EventMessage)
        assert decoded.event_name == "motion"
    
    def test_encode_decode_command(self):
        """Test command encoding."""
        codec = JSONCodec()
        cmd = CommandMessage.create("open", "door_1", {"force": True})
        
        encoded = codec.encode(cmd)
        decoded = codec.decode(encoded)
        
        assert isinstance(decoded, CommandMessage)
        assert decoded.command_name == "open"
        assert decoded.parameters == {"force": True}
    
    def test_content_type(self):
        """Test content type property."""
        codec = JSONCodec()
        assert codec.content_type == "application/json"
    
    def test_encode_str(self):
        """Test string encoding."""
        codec = JSONCodec()
        msg = Message(source_id="test")
        
        json_str = codec.encode_str(msg)
        decoded = codec.decode_str(json_str)
        
        assert decoded.source_id == "test"
