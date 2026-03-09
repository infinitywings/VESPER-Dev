"""
Protocol message types for IoT communication.

Defines the core message types: EVENT, COMMAND, STATE, ACK
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Dict, List


class MessageType(str, Enum):
    """Protocol message types."""
    EVENT = "EVENT"      # Device-generated events (motion detected, door opened)
    COMMAND = "COMMAND"  # Commands to devices (open door, turn on light)
    STATE = "STATE"      # State updates/queries
    ACK = "ACK"          # Acknowledgment of received messages


class MessagePriority(str, Enum):
    """Message priority levels for transmission."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Message:
    """
    Base protocol message.
    
    All messages in the system inherit from this base class.
    Messages are serializable and can be transmitted over the network.
    """
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    message_type: MessageType = MessageType.EVENT
    timestamp: float = field(default_factory=time.time)
    source_id: Optional[str] = None
    target_id: Optional[str] = None
    priority: MessagePriority = MessagePriority.NORMAL
    payload: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # For tracking message chains (request-response)
    correlation_id: Optional[str] = None
    reply_to: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize message to dictionary."""
        return {
            "message_id": self.message_id,
            "message_type": self.message_type.value,
            "timestamp": self.timestamp,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "priority": self.priority.value,
            "payload": self.payload,
            "metadata": self.metadata,
            "correlation_id": self.correlation_id,
            "reply_to": self.reply_to,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Message:
        """Deserialize message from dictionary."""
        return cls(
            message_id=data.get("message_id", str(uuid.uuid4())),
            message_type=MessageType(data.get("message_type", "EVENT")),
            timestamp=data.get("timestamp", time.time()),
            source_id=data.get("source_id"),
            target_id=data.get("target_id"),
            priority=MessagePriority(data.get("priority", "normal")),
            payload=data.get("payload", {}),
            metadata=data.get("metadata", {}),
            correlation_id=data.get("correlation_id"),
            reply_to=data.get("reply_to"),
        )


@dataclass
class EventMessage(Message):
    """
    Event message for device-generated events.
    
    Examples:
        - Motion sensor detected movement
        - Door was opened
        - Light level changed
    """
    message_type: MessageType = field(default=MessageType.EVENT, init=False)
    event_name: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data["event_name"] = self.event_name
        return data
    
    @classmethod
    def create(
        cls,
        event_name: str,
        source_id: str,
        payload: Optional[Dict[str, Any]] = None,
        priority: MessagePriority = MessagePriority.NORMAL,
    ) -> EventMessage:
        """Create an event message."""
        return cls(
            event_name=event_name,
            source_id=source_id,
            payload=payload or {},
            priority=priority,
        )


@dataclass
class CommandMessage(Message):
    """
    Command message for controlling devices.
    
    Examples:
        - Open the door
        - Turn on the light
        - Lock the smart lock
    """
    message_type: MessageType = field(default=MessageType.COMMAND, init=False)
    command_name: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    requires_ack: bool = True
    timeout_ms: int = 5000  # Timeout for acknowledgment
    
    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data["command_name"] = self.command_name
        data["parameters"] = self.parameters
        data["requires_ack"] = self.requires_ack
        data["timeout_ms"] = self.timeout_ms
        return data
    
    @classmethod
    def create(
        cls,
        command_name: str,
        target_id: str,
        parameters: Optional[Dict[str, Any]] = None,
        source_id: Optional[str] = None,
        requires_ack: bool = True,
    ) -> CommandMessage:
        """Create a command message."""
        return cls(
            command_name=command_name,
            target_id=target_id,
            source_id=source_id,
            parameters=parameters or {},
            requires_ack=requires_ack,
        )


@dataclass
class StateMessage(Message):
    """
    State message for state updates and queries.
    
    Used for:
        - Reporting current device state
        - Requesting device state
        - Broadcasting state changes
    """
    message_type: MessageType = field(default=MessageType.STATE, init=False)
    state_data: Dict[str, Any] = field(default_factory=dict)
    is_query: bool = False  # True if requesting state, False if reporting
    
    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data["state_data"] = self.state_data
        data["is_query"] = self.is_query
        return data
    
    @classmethod
    def create_update(
        cls,
        source_id: str,
        state_data: Dict[str, Any],
        target_id: Optional[str] = None,
    ) -> StateMessage:
        """Create a state update message."""
        return cls(
            source_id=source_id,
            target_id=target_id,
            state_data=state_data,
            is_query=False,
        )
    
    @classmethod
    def create_query(
        cls,
        source_id: str,
        target_id: str,
        fields: Optional[List[str]] = None,
    ) -> StateMessage:
        """Create a state query message."""
        return cls(
            source_id=source_id,
            target_id=target_id,
            state_data={"query_fields": fields or ["*"]},
            is_query=True,
        )


class AckStatus(str, Enum):
    """Acknowledgment status codes."""
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    INVALID = "invalid"
    UNAUTHORIZED = "unauthorized"


@dataclass
class AckMessage(Message):
    """
    Acknowledgment message for confirming receipt/execution.
    
    Sent in response to commands and important events.
    """
    message_type: MessageType = field(default=MessageType.ACK, init=False)
    status: AckStatus = AckStatus.SUCCESS
    error_message: Optional[str] = None
    result_data: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data["status"] = self.status.value
        data["error_message"] = self.error_message
        data["result_data"] = self.result_data
        return data
    
    @classmethod
    def create_success(
        cls,
        original_message: Message,
        source_id: str,
        result_data: Optional[Dict[str, Any]] = None,
    ) -> AckMessage:
        """Create a success acknowledgment."""
        return cls(
            source_id=source_id,
            target_id=original_message.source_id,
            correlation_id=original_message.message_id,
            status=AckStatus.SUCCESS,
            result_data=result_data or {},
        )
    
    @classmethod
    def create_failure(
        cls,
        original_message: Message,
        source_id: str,
        error_message: str,
        status: AckStatus = AckStatus.FAILURE,
    ) -> AckMessage:
        """Create a failure acknowledgment."""
        return cls(
            source_id=source_id,
            target_id=original_message.source_id,
            correlation_id=original_message.message_id,
            status=status,
            error_message=error_message,
        )
