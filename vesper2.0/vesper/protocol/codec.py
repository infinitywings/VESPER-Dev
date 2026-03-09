"""
Message codec for encoding/decoding protocol messages.

Supports JSON encoding with optional CBOR support.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Type, Union

from vesper.protocol.messages import (
    Message,
    MessageType,
    EventMessage,
    CommandMessage,
    StateMessage,
    AckMessage,
)


logger = logging.getLogger(__name__)


class MessageCodec(ABC):
    """Abstract base class for message codecs."""
    
    @abstractmethod
    def encode(self, message: Message) -> bytes:
        """Encode a message to bytes."""
        pass
    
    @abstractmethod
    def decode(self, data: bytes) -> Message:
        """Decode bytes to a message."""
        pass
    
    @property
    @abstractmethod
    def content_type(self) -> str:
        """MIME content type for this codec."""
        pass


class JSONCodec(MessageCodec):
    """
    JSON-based message codec.
    
    Encodes messages as UTF-8 JSON strings.
    """
    
    # Mapping of message types to their classes
    MESSAGE_CLASSES: Dict[MessageType, Type[Message]] = {
        MessageType.EVENT: EventMessage,
        MessageType.COMMAND: CommandMessage,
        MessageType.STATE: StateMessage,
        MessageType.ACK: AckMessage,
    }
    
    def __init__(self, indent: Optional[int] = None, ensure_ascii: bool = False):
        """
        Initialize JSON codec.
        
        Args:
            indent: JSON indentation level (None for compact)
            ensure_ascii: If True, escape non-ASCII characters
        """
        self._indent = indent
        self._ensure_ascii = ensure_ascii
    
    @property
    def content_type(self) -> str:
        return "application/json"
    
    def encode(self, message: Message) -> bytes:
        """
        Encode a message to JSON bytes.
        
        Args:
            message: Message to encode
            
        Returns:
            UTF-8 encoded JSON bytes
        """
        data = message.to_dict()
        json_str = json.dumps(
            data,
            indent=self._indent,
            ensure_ascii=self._ensure_ascii,
            default=self._json_default,
        )
        return json_str.encode("utf-8")
    
    def decode(self, data: bytes) -> Message:
        """
        Decode JSON bytes to a message.
        
        Args:
            data: UTF-8 encoded JSON bytes
            
        Returns:
            Decoded message object
        """
        json_str = data.decode("utf-8")
        msg_dict = json.loads(json_str)
        return self._dict_to_message(msg_dict)
    
    def encode_str(self, message: Message) -> str:
        """Encode message to JSON string."""
        return self.encode(message).decode("utf-8")
    
    def decode_str(self, json_str: str) -> Message:
        """Decode JSON string to message."""
        return self.decode(json_str.encode("utf-8"))
    
    def _dict_to_message(self, data: Dict[str, Any]) -> Message:
        """Convert a dictionary to the appropriate message type."""
        msg_type_str = data.get("message_type", "EVENT")
        
        try:
            msg_type = MessageType(msg_type_str)
        except ValueError:
            logger.warning(f"Unknown message type: {msg_type_str}, using EVENT")
            msg_type = MessageType.EVENT
        
        msg_class = self.MESSAGE_CLASSES.get(msg_type, Message)
        
        # Handle specific message types
        if msg_type == MessageType.EVENT:
            return self._decode_event(data)
        elif msg_type == MessageType.COMMAND:
            return self._decode_command(data)
        elif msg_type == MessageType.STATE:
            return self._decode_state(data)
        elif msg_type == MessageType.ACK:
            return self._decode_ack(data)
        else:
            return Message.from_dict(data)
    
    def _decode_event(self, data: Dict[str, Any]) -> EventMessage:
        """Decode an event message."""
        base = Message.from_dict(data)
        return EventMessage(
            message_id=base.message_id,
            timestamp=base.timestamp,
            source_id=base.source_id,
            target_id=base.target_id,
            priority=base.priority,
            payload=base.payload,
            metadata=base.metadata,
            correlation_id=base.correlation_id,
            reply_to=base.reply_to,
            event_name=data.get("event_name", ""),
        )
    
    def _decode_command(self, data: Dict[str, Any]) -> CommandMessage:
        """Decode a command message."""
        base = Message.from_dict(data)
        return CommandMessage(
            message_id=base.message_id,
            timestamp=base.timestamp,
            source_id=base.source_id,
            target_id=base.target_id,
            priority=base.priority,
            payload=base.payload,
            metadata=base.metadata,
            correlation_id=base.correlation_id,
            reply_to=base.reply_to,
            command_name=data.get("command_name", ""),
            parameters=data.get("parameters", {}),
            requires_ack=data.get("requires_ack", True),
            timeout_ms=data.get("timeout_ms", 5000),
        )
    
    def _decode_state(self, data: Dict[str, Any]) -> StateMessage:
        """Decode a state message."""
        base = Message.from_dict(data)
        return StateMessage(
            message_id=base.message_id,
            timestamp=base.timestamp,
            source_id=base.source_id,
            target_id=base.target_id,
            priority=base.priority,
            payload=base.payload,
            metadata=base.metadata,
            correlation_id=base.correlation_id,
            reply_to=base.reply_to,
            state_data=data.get("state_data", {}),
            is_query=data.get("is_query", False),
        )
    
    def _decode_ack(self, data: Dict[str, Any]) -> AckMessage:
        """Decode an ack message."""
        from vesper.protocol.messages import AckStatus
        
        base = Message.from_dict(data)
        status_str = data.get("status", "success")
        try:
            status = AckStatus(status_str)
        except ValueError:
            status = AckStatus.SUCCESS
        
        return AckMessage(
            message_id=base.message_id,
            timestamp=base.timestamp,
            source_id=base.source_id,
            target_id=base.target_id,
            priority=base.priority,
            payload=base.payload,
            metadata=base.metadata,
            correlation_id=base.correlation_id,
            reply_to=base.reply_to,
            status=status,
            error_message=data.get("error_message"),
            result_data=data.get("result_data", {}),
        )
    
    @staticmethod
    def _json_default(obj: Any) -> Any:
        """Default JSON serializer for non-standard types."""
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        elif hasattr(obj, "__dict__"):
            return obj.__dict__
        else:
            return str(obj)


# Optional CBOR codec (requires cbor2 package)
try:
    import cbor2
    
    class CBORCodec(MessageCodec):
        """
        CBOR-based message codec.
        
        More compact than JSON, good for bandwidth-constrained networks.
        """
        
        @property
        def content_type(self) -> str:
            return "application/cbor"
        
        def encode(self, message: Message) -> bytes:
            """Encode a message to CBOR bytes."""
            data = message.to_dict()
            return cbor2.dumps(data)
        
        def decode(self, data: bytes) -> Message:
            """Decode CBOR bytes to a message."""
            msg_dict = cbor2.loads(data)
            # Reuse JSON codec's message parsing
            return JSONCodec()._dict_to_message(msg_dict)
    
    CBOR_AVAILABLE = True
    
except ImportError:
    CBOR_AVAILABLE = False
    CBORCodec = None  # type: ignore
