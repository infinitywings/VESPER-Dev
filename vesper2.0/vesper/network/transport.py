"""
Transport layer abstractions for network communication.

Provides interfaces for local, simulated, and real network transports.
"""

from __future__ import annotations

import logging
import random
import time
import threading
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from queue import Queue, Empty
from typing import Any, Callable, Dict, List, Optional, Tuple

from vesper.protocol.messages import Message
from vesper.protocol.codec import MessageCodec, JSONCodec


logger = logging.getLogger(__name__)


class TransportState(str, Enum):
    """Transport connection state."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class TransportConfig:
    """Configuration for transport layer."""
    # Simulated network parameters
    latency_min_ms: float = 0.0
    latency_max_ms: float = 0.0
    packet_loss_rate: float = 0.0  # 0.0 to 1.0
    bandwidth_bps: Optional[int] = None  # Bytes per second limit
    
    # Connection parameters
    reconnect_delay_ms: int = 1000
    max_reconnect_attempts: int = 5
    heartbeat_interval_ms: int = 30000


@dataclass
class TransportStats:
    """Transport layer statistics."""
    messages_sent: int = 0
    messages_received: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    packets_dropped: int = 0
    connection_errors: int = 0
    avg_latency_ms: float = 0.0


# Callback types
MessageCallback = Callable[[Message], None]
ConnectionCallback = Callable[[TransportState], None]


class Transport(ABC):
    """
    Abstract transport interface.
    
    Provides a common interface for different transport implementations:
    - LocalTransport: In-process message passing
    - SimulatedTransport: Simulated network with latency/packet loss
    - WebSocketTransport: Real WebSocket connection
    - MQTTTransport: MQTT broker connection
    """
    
    def __init__(self, config: Optional[TransportConfig] = None):
        """Initialize transport with optional configuration."""
        self._config = config or TransportConfig()
        self._state = TransportState.DISCONNECTED
        self._codec: MessageCodec = JSONCodec()
        self._stats = TransportStats()
        self._message_callbacks: List[MessageCallback] = []
        self._connection_callbacks: List[ConnectionCallback] = []
    
    @property
    def state(self) -> TransportState:
        """Current connection state."""
        return self._state
    
    @property
    def is_connected(self) -> bool:
        """Whether transport is connected."""
        return self._state == TransportState.CONNECTED
    
    @property
    def stats(self) -> TransportStats:
        """Transport statistics."""
        return self._stats
    
    @property
    def codec(self) -> MessageCodec:
        """Message codec."""
        return self._codec
    
    @codec.setter
    def codec(self, value: MessageCodec) -> None:
        """Set message codec."""
        self._codec = value
    
    def on_message(self, callback: MessageCallback) -> None:
        """Register a message received callback."""
        self._message_callbacks.append(callback)
    
    def on_connection_change(self, callback: ConnectionCallback) -> None:
        """Register a connection state change callback."""
        self._connection_callbacks.append(callback)
    
    def _set_state(self, state: TransportState) -> None:
        """Update connection state and notify callbacks."""
        if self._state != state:
            old_state = self._state
            self._state = state
            logger.debug(f"Transport state: {old_state.value} -> {state.value}")
            for callback in self._connection_callbacks:
                try:
                    callback(state)
                except Exception as e:
                    logger.error(f"Connection callback error: {e}")
    
    def _notify_message(self, message: Message) -> None:
        """Notify all message callbacks."""
        self._stats.messages_received += 1
        for callback in self._message_callbacks:
            try:
                callback(message)
            except Exception as e:
                logger.error(f"Message callback error: {e}")
    
    @abstractmethod
    def connect(self) -> bool:
        """
        Connect the transport.
        
        Returns:
            True if connection successful
        """
        pass
    
    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect the transport."""
        pass
    
    @abstractmethod
    def send(self, message: Message) -> bool:
        """
        Send a message.
        
        Args:
            message: Message to send
            
        Returns:
            True if message was sent successfully
        """
        pass
    
    @abstractmethod
    def receive(self, timeout_ms: Optional[int] = None) -> Optional[Message]:
        """
        Receive a message (blocking).
        
        Args:
            timeout_ms: Timeout in milliseconds (None = block forever)
            
        Returns:
            Received message or None if timeout
        """
        pass


class LocalTransport(Transport):
    """
    In-process transport for local message passing.
    
    Messages are passed directly through queues without serialization.
    Useful for testing and single-process simulations.
    """
    
    # Shared registry of local transports for routing
    _registry: Dict[str, "LocalTransport"] = {}
    _registry_lock = threading.Lock()
    
    def __init__(
        self,
        node_id: str,
        config: Optional[TransportConfig] = None,
    ):
        """
        Initialize local transport.
        
        Args:
            node_id: Unique identifier for this transport endpoint
            config: Transport configuration
        """
        super().__init__(config)
        self._node_id = node_id
        self._inbox: Queue[Message] = Queue()
    
    @property
    def node_id(self) -> str:
        """This transport's node ID."""
        return self._node_id
    
    def connect(self) -> bool:
        """Register in the local transport registry."""
        with self._registry_lock:
            if self._node_id in self._registry:
                logger.warning(f"Node {self._node_id} already registered")
            self._registry[self._node_id] = self
        self._set_state(TransportState.CONNECTED)
        return True
    
    def disconnect(self) -> None:
        """Unregister from the local transport registry."""
        with self._registry_lock:
            self._registry.pop(self._node_id, None)
        self._set_state(TransportState.DISCONNECTED)
    
    def send(self, message: Message) -> bool:
        """Send a message to a target node."""
        if not self.is_connected:
            logger.warning("Cannot send: transport not connected")
            return False
        
        target_id = message.target_id
        if not target_id:
            # Broadcast to all other nodes
            with self._registry_lock:
                targets = [t for nid, t in self._registry.items() if nid != self._node_id]
            for target in targets:
                target._inbox.put(message)
            self._stats.messages_sent += len(targets)
            return True
        
        # Send to specific target
        with self._registry_lock:
            target = self._registry.get(target_id)
        
        if target:
            target._inbox.put(message)
            self._stats.messages_sent += 1
            return True
        else:
            logger.warning(f"Target not found: {target_id}")
            return False
    
    def receive(self, timeout_ms: Optional[int] = None) -> Optional[Message]:
        """Receive a message from the inbox."""
        try:
            timeout = timeout_ms / 1000.0 if timeout_ms else None
            message = self._inbox.get(timeout=timeout)
            self._notify_message(message)
            return message
        except Empty:
            return None
    
    def pending_count(self) -> int:
        """Number of messages waiting in inbox."""
        return self._inbox.qsize()


@dataclass
class DelayedMessage:
    """A message with scheduled delivery time."""
    message: Message
    delivery_time: float
    target_transport: "SimulatedTransport"


class SimulatedTransport(Transport):
    """
    Simulated network transport with configurable characteristics.
    
    Supports:
    - Configurable latency (min/max)
    - Packet loss simulation
    - Bandwidth limiting
    - Network delay modeling
    
    Useful for testing network edge cases and realistic scenarios.
    """
    
    # Shared message scheduler
    _scheduler_queue: List[DelayedMessage] = []
    _scheduler_lock = threading.Lock()
    _scheduler_thread: Optional[threading.Thread] = None
    _scheduler_running = False
    
    # Registry for routing
    _registry: Dict[str, "SimulatedTransport"] = {}
    _registry_lock = threading.Lock()
    
    def __init__(
        self,
        node_id: str,
        config: Optional[TransportConfig] = None,
    ):
        """
        Initialize simulated transport.
        
        Args:
            node_id: Unique identifier for this endpoint
            config: Transport configuration with network simulation params
        """
        super().__init__(config)
        self._node_id = node_id
        self._inbox: Queue[Message] = Queue()
        self._rng = random.Random()
    
    @classmethod
    def start_scheduler(cls) -> None:
        """Start the background message scheduler."""
        if cls._scheduler_running:
            return
        
        cls._scheduler_running = True
        cls._scheduler_thread = threading.Thread(
            target=cls._run_scheduler,
            daemon=True,
        )
        cls._scheduler_thread.start()
    
    @classmethod
    def stop_scheduler(cls) -> None:
        """Stop the background message scheduler."""
        cls._scheduler_running = False
        if cls._scheduler_thread:
            cls._scheduler_thread.join(timeout=1.0)
            cls._scheduler_thread = None
    
    @classmethod
    def _run_scheduler(cls) -> None:
        """Background thread that delivers delayed messages."""
        while cls._scheduler_running:
            current_time = time.time()
            to_deliver = []
            
            with cls._scheduler_lock:
                # Find messages ready for delivery
                remaining = []
                for dm in cls._scheduler_queue:
                    if dm.delivery_time <= current_time:
                        to_deliver.append(dm)
                    else:
                        remaining.append(dm)
                cls._scheduler_queue = remaining
            
            # Deliver messages
            for dm in to_deliver:
                dm.target_transport._inbox.put(dm.message)
            
            time.sleep(0.001)  # 1ms resolution
    
    @property
    def node_id(self) -> str:
        """This transport's node ID."""
        return self._node_id
    
    def connect(self) -> bool:
        """Connect and register in the simulated network."""
        with self._registry_lock:
            self._registry[self._node_id] = self
        
        # Ensure scheduler is running
        self.start_scheduler()
        
        self._set_state(TransportState.CONNECTED)
        return True
    
    def disconnect(self) -> None:
        """Disconnect from the simulated network."""
        with self._registry_lock:
            self._registry.pop(self._node_id, None)
        self._set_state(TransportState.DISCONNECTED)
    
    def send(self, message: Message) -> bool:
        """Send a message with simulated network effects."""
        if not self.is_connected:
            return False
        
        # Simulate packet loss
        if self._rng.random() < self._config.packet_loss_rate:
            self._stats.packets_dropped += 1
            logger.debug(f"Packet dropped (simulated): {message.message_id[:8]}")
            return True  # Message "sent" but lost
        
        # Calculate latency
        latency_ms = self._rng.uniform(
            self._config.latency_min_ms,
            self._config.latency_max_ms,
        )
        delivery_time = time.time() + (latency_ms / 1000.0)
        
        # Find target(s)
        target_id = message.target_id
        if target_id:
            with self._registry_lock:
                target = self._registry.get(target_id)
            targets = [target] if target else []
        else:
            with self._registry_lock:
                targets = [t for nid, t in self._registry.items() if nid != self._node_id]
        
        # Schedule delayed delivery
        with self._scheduler_lock:
            for target in targets:
                if target:
                    self._scheduler_queue.append(DelayedMessage(
                        message=message,
                        delivery_time=delivery_time,
                        target_transport=target,
                    ))
        
        self._stats.messages_sent += len(targets)
        
        # Update average latency
        total = self._stats.messages_sent
        self._stats.avg_latency_ms = (
            (self._stats.avg_latency_ms * (total - 1) + latency_ms) / total
        )
        
        return True
    
    def receive(self, timeout_ms: Optional[int] = None) -> Optional[Message]:
        """Receive a message from the inbox."""
        try:
            timeout = timeout_ms / 1000.0 if timeout_ms else None
            message = self._inbox.get(timeout=timeout)
            self._notify_message(message)
            return message
        except Empty:
            return None
    
    def pending_count(self) -> int:
        """Number of messages waiting in inbox."""
        return self._inbox.qsize()
    
    def set_latency(self, min_ms: float, max_ms: float) -> None:
        """Update latency settings."""
        self._config.latency_min_ms = min_ms
        self._config.latency_max_ms = max_ms
    
    def set_packet_loss(self, rate: float) -> None:
        """Update packet loss rate (0.0 to 1.0)."""
        self._config.packet_loss_rate = max(0.0, min(1.0, rate))
