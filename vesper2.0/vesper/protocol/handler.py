"""
Protocol handler for processing messages.

Routes messages to appropriate handlers and manages message lifecycles.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set
from queue import Queue, Empty
import threading

from vesper.protocol.messages import (
    Message,
    MessageType,
    EventMessage,
    CommandMessage,
    StateMessage,
    AckMessage,
    AckStatus,
    MessagePriority,
)


logger = logging.getLogger(__name__)


# Type aliases
MessageHandler = Callable[[Message], Optional[Message]]
CommandHandler = Callable[[CommandMessage], AckMessage]


@dataclass
class PendingCommand:
    """Tracks a pending command waiting for acknowledgment."""
    command: CommandMessage
    sent_time: float
    callback: Optional[Callable[[AckMessage], None]] = None


@dataclass
class HandlerRegistration:
    """Registration info for a message handler."""
    handler: MessageHandler
    message_types: Set[MessageType]
    source_filter: Optional[str] = None  # Filter by source_id pattern
    priority: int = 0  # Higher priority handlers called first


class ProtocolHandler:
    """
    Central protocol message handler.
    
    Responsibilities:
    - Route messages to registered handlers
    - Track pending commands and their acknowledgments
    - Handle timeouts and retries
    - Manage message queues
    
    Example:
        handler = ProtocolHandler()
        
        # Register a command handler
        @handler.on_command("open_door")
        def handle_open_door(cmd: CommandMessage) -> AckMessage:
            # Process command
            return AckMessage.create_success(cmd, "door_1")
        
        # Process incoming message
        response = handler.process(incoming_message)
    """
    
    def __init__(
        self,
        node_id: str = "simulator",
        command_timeout_ms: int = 5000,
        max_retries: int = 3,
    ):
        """
        Initialize the protocol handler.
        
        Args:
            node_id: Identifier for this node in the network
            command_timeout_ms: Default timeout for command acknowledgments
            max_retries: Maximum number of command retries
        """
        self._node_id = node_id
        self._command_timeout_ms = command_timeout_ms
        self._max_retries = max_retries
        
        # Handler registrations
        self._handlers: List[HandlerRegistration] = []
        self._command_handlers: Dict[str, CommandHandler] = {}
        self._event_handlers: Dict[str, List[MessageHandler]] = defaultdict(list)
        
        # Pending commands awaiting ACK
        self._pending_commands: Dict[str, PendingCommand] = {}
        self._pending_lock = threading.Lock()
        
        # Message queue for async processing
        self._message_queue: Queue[Message] = Queue()
        
        # Statistics
        self._stats = {
            "messages_received": 0,
            "messages_sent": 0,
            "commands_sent": 0,
            "commands_acked": 0,
            "commands_timeout": 0,
            "errors": 0,
        }
    
    @property
    def node_id(self) -> str:
        """This node's identifier."""
        return self._node_id
    
    @property
    def stats(self) -> Dict[str, int]:
        """Get protocol statistics."""
        return self._stats.copy()
    
    def register_handler(
        self,
        handler: MessageHandler,
        message_types: Optional[Set[MessageType]] = None,
        source_filter: Optional[str] = None,
        priority: int = 0,
    ) -> None:
        """
        Register a general message handler.
        
        Args:
            handler: Callback function for messages
            message_types: Set of message types to handle (None = all)
            source_filter: Optional source_id filter pattern
            priority: Handler priority (higher = called first)
        """
        registration = HandlerRegistration(
            handler=handler,
            message_types=message_types or set(MessageType),
            source_filter=source_filter,
            priority=priority,
        )
        self._handlers.append(registration)
        self._handlers.sort(key=lambda r: r.priority, reverse=True)
        logger.debug(f"Registered handler for {message_types}")
    
    def on_command(self, command_name: str) -> Callable:
        """
        Decorator to register a command handler.
        
        Example:
            @handler.on_command("open_door")
            def handle_open(cmd: CommandMessage) -> AckMessage:
                ...
        """
        def decorator(func: CommandHandler) -> CommandHandler:
            self._command_handlers[command_name] = func
            logger.debug(f"Registered command handler: {command_name}")
            return func
        return decorator
    
    def on_event(self, event_name: str) -> Callable:
        """
        Decorator to register an event handler.
        
        Example:
            @handler.on_event("motion_detected")
            def handle_motion(event: EventMessage) -> None:
                ...
        """
        def decorator(func: MessageHandler) -> MessageHandler:
            self._event_handlers[event_name].append(func)
            logger.debug(f"Registered event handler: {event_name}")
            return func
        return decorator
    
    def process(self, message: Message) -> Optional[Message]:
        """
        Process an incoming message.
        
        Routes the message to appropriate handlers based on type.
        
        Args:
            message: The message to process
            
        Returns:
            Response message if applicable (e.g., ACK for commands)
        """
        self._stats["messages_received"] += 1
        logger.debug(f"Processing {message.message_type.value}: {message.message_id[:8]}")
        
        try:
            if message.message_type == MessageType.COMMAND:
                return self._handle_command(message)
            elif message.message_type == MessageType.EVENT:
                return self._handle_event(message)
            elif message.message_type == MessageType.STATE:
                return self._handle_state(message)
            elif message.message_type == MessageType.ACK:
                return self._handle_ack(message)
            else:
                return self._handle_generic(message)
                
        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"Error processing message: {e}", exc_info=True)
            
            # Return error ACK for commands
            if message.message_type == MessageType.COMMAND:
                return AckMessage.create_failure(
                    message,
                    self._node_id,
                    str(e),
                    AckStatus.FAILURE,
                )
            return None
    
    def _handle_command(self, message: Message) -> AckMessage:
        """Handle a command message."""
        cmd = message if isinstance(message, CommandMessage) else message
        command_name = getattr(cmd, 'command_name', cmd.payload.get('command_name', ''))
        
        # Look for registered command handler
        if command_name in self._command_handlers:
            handler = self._command_handlers[command_name]
            return handler(cmd)
        
        # No handler found
        logger.warning(f"No handler for command: {command_name}")
        return AckMessage.create_failure(
            message,
            self._node_id,
            f"Unknown command: {command_name}",
            AckStatus.INVALID,
        )
    
    def _handle_event(self, message: Message) -> None:
        """Handle an event message."""
        event = message if isinstance(message, EventMessage) else message
        event_name = getattr(event, 'event_name', event.payload.get('event_name', ''))
        
        # Call specific event handlers
        for handler in self._event_handlers.get(event_name, []):
            try:
                handler(event)
            except Exception as e:
                logger.error(f"Event handler error: {e}")
        
        # Call wildcard event handlers
        for handler in self._event_handlers.get("*", []):
            try:
                handler(event)
            except Exception as e:
                logger.error(f"Wildcard event handler error: {e}")
        
        # Call general handlers
        self._call_general_handlers(message)
        
        return None
    
    def _handle_state(self, message: Message) -> Optional[Message]:
        """Handle a state message."""
        state = message if isinstance(message, StateMessage) else message
        
        # Process through general handlers
        self._call_general_handlers(message)
        
        return None
    
    def _handle_ack(self, message: Message) -> None:
        """Handle an acknowledgment message."""
        ack = message if isinstance(message, AckMessage) else message
        correlation_id = ack.correlation_id
        
        if not correlation_id:
            logger.warning("ACK received without correlation_id")
            return None
        
        with self._pending_lock:
            pending = self._pending_commands.pop(correlation_id, None)
        
        if pending:
            self._stats["commands_acked"] += 1
            if pending.callback:
                try:
                    pending.callback(ack)
                except Exception as e:
                    logger.error(f"ACK callback error: {e}")
        else:
            logger.debug(f"ACK for unknown command: {correlation_id[:8]}")
        
        return None
    
    def _handle_generic(self, message: Message) -> None:
        """Handle a generic message type."""
        self._call_general_handlers(message)
        return None
    
    def _call_general_handlers(self, message: Message) -> None:
        """Call all matching general handlers."""
        for registration in self._handlers:
            if message.message_type in registration.message_types:
                if self._matches_filter(message, registration.source_filter):
                    try:
                        registration.handler(message)
                    except Exception as e:
                        logger.error(f"Handler error: {e}")
    
    def _matches_filter(self, message: Message, filter_pattern: Optional[str]) -> bool:
        """Check if message matches the source filter."""
        if not filter_pattern:
            return True
        if not message.source_id:
            return filter_pattern == "*"
        
        # Simple glob matching
        if filter_pattern == "*":
            return True
        if filter_pattern.endswith("*"):
            return message.source_id.startswith(filter_pattern[:-1])
        return message.source_id == filter_pattern
    
    def send_command(
        self,
        command: CommandMessage,
        callback: Optional[Callable[[AckMessage], None]] = None,
    ) -> str:
        """
        Send a command and track for acknowledgment.
        
        Args:
            command: Command message to send
            callback: Optional callback for when ACK is received
            
        Returns:
            The command's message_id
        """
        command.source_id = command.source_id or self._node_id
        
        if command.requires_ack:
            with self._pending_lock:
                self._pending_commands[command.message_id] = PendingCommand(
                    command=command,
                    sent_time=time.time(),
                    callback=callback,
                )
        
        self._stats["commands_sent"] += 1
        self._message_queue.put(command)
        
        return command.message_id
    
    def check_timeouts(self) -> List[CommandMessage]:
        """
        Check for timed-out commands.
        
        Returns:
            List of commands that timed out
        """
        timed_out = []
        current_time = time.time()
        
        with self._pending_lock:
            for msg_id, pending in list(self._pending_commands.items()):
                elapsed_ms = (current_time - pending.sent_time) * 1000
                if elapsed_ms > pending.command.timeout_ms:
                    timed_out.append(pending.command)
                    del self._pending_commands[msg_id]
                    self._stats["commands_timeout"] += 1
                    
                    # Call callback with timeout ACK
                    if pending.callback:
                        timeout_ack = AckMessage.create_failure(
                            pending.command,
                            self._node_id,
                            "Command timed out",
                            AckStatus.TIMEOUT,
                        )
                        try:
                            pending.callback(timeout_ack)
                        except Exception as e:
                            logger.error(f"Timeout callback error: {e}")
        
        return timed_out
    
    def get_pending_messages(self, max_count: int = 100) -> List[Message]:
        """
        Get pending outgoing messages.
        
        Args:
            max_count: Maximum number of messages to retrieve
            
        Returns:
            List of messages to send
        """
        messages = []
        for _ in range(max_count):
            try:
                msg = self._message_queue.get_nowait()
                messages.append(msg)
            except Empty:
                break
        return messages
