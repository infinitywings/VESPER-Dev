"""
MQTT Transport for VESPER IoT devices.

Provides real MQTT connectivity using paho-mqtt.
Supports both local brokers (Mosquitto) and cloud brokers.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from enum import IntEnum

try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False
    mqtt = None

from vesper.protocol.messages import Message, MessageType, EventMessage, CommandMessage


logger = logging.getLogger(__name__)


class QoS(IntEnum):
    """MQTT Quality of Service levels."""
    AT_MOST_ONCE = 0   # Fire and forget
    AT_LEAST_ONCE = 1  # Acknowledged delivery
    EXACTLY_ONCE = 2   # Assured delivery


@dataclass
class MQTTConfig:
    """Configuration for MQTT connection."""
    # Broker settings
    broker_host: str = "localhost"
    broker_port: int = 1883
    
    # Authentication (optional)
    username: Optional[str] = None
    password: Optional[str] = None
    
    # TLS/SSL (optional)
    use_tls: bool = False
    ca_certs: Optional[str] = None
    certfile: Optional[str] = None
    keyfile: Optional[str] = None
    
    # Client settings
    client_id: Optional[str] = None
    clean_session: bool = True
    keepalive: int = 60
    
    # Topic prefix for VESPER
    topic_prefix: str = "vesper"
    
    # Default QoS
    default_qos: QoS = QoS.AT_LEAST_ONCE
    
    # Reconnection
    auto_reconnect: bool = True
    reconnect_delay: float = 5.0


# Type alias for message callbacks
MessageCallback = Callable[[str, Message], None]


class MQTTTransport:
    """
    MQTT transport layer for VESPER IoT communication.
    
    Provides:
    - Connection to MQTT broker (Mosquitto, HiveMQ, AWS IoT, etc.)
    - Topic-based pub/sub with wildcards
    - Message serialization/deserialization
    - Automatic reconnection
    - QoS levels support
    
    Topic Structure:
        vesper/devices/{device_id}/events     - Device events
        vesper/devices/{device_id}/commands   - Commands to device
        vesper/devices/{device_id}/state      - Device state
        vesper/agents/{agent_id}/actions      - Agent actions
        vesper/sensors/motion/#               - All motion sensor events
    
    Example:
        config = MQTTConfig(broker_host="localhost", broker_port=1883)
        transport = MQTTTransport(config)
        
        # Connect
        transport.connect()
        
        # Subscribe to events
        transport.subscribe("vesper/devices/+/events", callback)
        
        # Publish event
        transport.publish_event("motion_sensor_1", {"detected": True})
        
        # Disconnect
        transport.disconnect()
    """
    
    def __init__(self, config: Optional[MQTTConfig] = None):
        """
        Initialize MQTT transport.
        
        Args:
            config: MQTT configuration
        """
        if not MQTT_AVAILABLE:
            raise ImportError(
                "paho-mqtt is required for MQTT transport. "
                "Install with: pip install paho-mqtt"
            )
        
        self.config = config or MQTTConfig()
        
        # Generate client ID if not provided
        if not self.config.client_id:
            import uuid
            self.config.client_id = f"vesper-{uuid.uuid4().hex[:8]}"
        
        # MQTT client
        self._client = mqtt.Client(
            client_id=self.config.client_id,
            clean_session=self.config.clean_session,
        )
        
        # Setup callbacks
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.on_subscribe = self._on_subscribe
        
        # Authentication
        if self.config.username:
            self._client.username_pw_set(
                self.config.username,
                self.config.password
            )
        
        # TLS
        if self.config.use_tls:
            self._client.tls_set(
                ca_certs=self.config.ca_certs,
                certfile=self.config.certfile,
                keyfile=self.config.keyfile,
            )
        
        # State
        self._is_connected = False
        self._subscriptions: Dict[str, List[MessageCallback]] = {}
        self._pending_subscriptions: List[str] = []
        
        # Statistics
        self._stats = {
            "messages_sent": 0,
            "messages_received": 0,
            "reconnections": 0,
        }
        
        # Thread safety
        self._lock = threading.RLock()
    
    @property
    def is_connected(self) -> bool:
        return self._is_connected
    
    @property
    def stats(self) -> Dict[str, int]:
        return self._stats.copy()
    
    def connect(self, blocking: bool = True) -> bool:
        """
        Connect to the MQTT broker.
        
        Args:
            blocking: Wait for connection to complete
            
        Returns:
            True if connection successful
        """
        try:
            logger.info(
                f"Connecting to MQTT broker: "
                f"{self.config.broker_host}:{self.config.broker_port}"
            )
            
            self._client.connect(
                self.config.broker_host,
                self.config.broker_port,
                self.config.keepalive,
            )
            
            # Start network loop in background thread
            self._client.loop_start()
            
            if blocking:
                # Wait for connection
                timeout = 10.0
                start = time.time()
                while not self._is_connected and (time.time() - start) < timeout:
                    time.sleep(0.1)
                
                if not self._is_connected:
                    logger.error("Connection timeout")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            return False
    
    def disconnect(self) -> None:
        """Disconnect from the MQTT broker."""
        logger.info("Disconnecting from MQTT broker")
        self._client.loop_stop()
        self._client.disconnect()
        self._is_connected = False
    
    def subscribe(
        self,
        topic: str,
        callback: MessageCallback,
        qos: Optional[QoS] = None,
    ) -> bool:
        """
        Subscribe to a topic.
        
        Args:
            topic: MQTT topic pattern (supports +, # wildcards)
            callback: Function to call when message received
            qos: Quality of Service level
            
        Returns:
            True if subscription successful
        """
        qos = qos or self.config.default_qos
        
        with self._lock:
            if topic not in self._subscriptions:
                self._subscriptions[topic] = []
            self._subscriptions[topic].append(callback)
        
        if self._is_connected:
            result, mid = self._client.subscribe(topic, qos)
            if result == mqtt.MQTT_ERR_SUCCESS:
                logger.debug(f"Subscribed to: {topic}")
                return True
            else:
                logger.error(f"Failed to subscribe to {topic}: {result}")
                return False
        else:
            # Queue for later
            self._pending_subscriptions.append(topic)
            return True
    
    def unsubscribe(self, topic: str) -> bool:
        """Unsubscribe from a topic."""
        with self._lock:
            if topic in self._subscriptions:
                del self._subscriptions[topic]
        
        if self._is_connected:
            self._client.unsubscribe(topic)
        
        return True
    
    def publish(
        self,
        topic: str,
        payload: Any,
        qos: Optional[QoS] = None,
        retain: bool = False,
    ) -> bool:
        """
        Publish a message to a topic.
        
        Args:
            topic: MQTT topic
            payload: Message payload (will be JSON serialized)
            qos: Quality of Service level
            retain: Retain message on broker
            
        Returns:
            True if publish successful
        """
        if not self._is_connected:
            logger.warning("Cannot publish: not connected")
            return False
        
        qos = qos or self.config.default_qos
        
        # Serialize payload
        if isinstance(payload, Message):
            data = json.dumps(payload.to_dict())
        elif isinstance(payload, dict):
            data = json.dumps(payload)
        elif isinstance(payload, str):
            data = payload
        else:
            data = json.dumps({"value": payload})
        
        result = self._client.publish(topic, data, qos, retain)
        
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            self._stats["messages_sent"] += 1
            logger.debug(f"Published to {topic}: {data[:100]}")
            return True
        else:
            logger.error(f"Failed to publish to {topic}: {result.rc}")
            return False
    
    # Convenience methods for VESPER
    
    def publish_event(
        self,
        device_id: str,
        event_name: str,
        payload: Dict[str, Any],
    ) -> bool:
        """Publish a device event."""
        topic = f"{self.config.topic_prefix}/devices/{device_id}/events"
        message = {
            "event": event_name,
            "device_id": device_id,
            "timestamp": time.time(),
            "payload": payload,
        }
        return self.publish(topic, message)
    
    def publish_command(
        self,
        device_id: str,
        command: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Send a command to a device."""
        topic = f"{self.config.topic_prefix}/devices/{device_id}/commands"
        message = {
            "command": command,
            "device_id": device_id,
            "timestamp": time.time(),
            "params": params or {},
        }
        return self.publish(topic, message)
    
    def publish_state(
        self,
        device_id: str,
        state: Dict[str, Any],
        retain: bool = True,
    ) -> bool:
        """Publish device state (retained)."""
        topic = f"{self.config.topic_prefix}/devices/{device_id}/state"
        message = {
            "device_id": device_id,
            "timestamp": time.time(),
            "state": state,
        }
        return self.publish(topic, message, retain=retain)
    
    def subscribe_to_device_events(
        self,
        device_id: str,
        callback: MessageCallback,
    ) -> bool:
        """Subscribe to events from a specific device."""
        topic = f"{self.config.topic_prefix}/devices/{device_id}/events"
        return self.subscribe(topic, callback)
    
    def subscribe_to_all_events(self, callback: MessageCallback) -> bool:
        """Subscribe to all device events."""
        topic = f"{self.config.topic_prefix}/devices/+/events"
        return self.subscribe(topic, callback)
    
    def subscribe_to_commands(
        self,
        device_id: str,
        callback: MessageCallback,
    ) -> bool:
        """Subscribe to commands for a device."""
        topic = f"{self.config.topic_prefix}/devices/{device_id}/commands"
        return self.subscribe(topic, callback)
    
    # MQTT callbacks
    
    def _on_connect(self, client, userdata, flags, rc):
        """Handle connection event."""
        if rc == 0:
            self._is_connected = True
            logger.info("Connected to MQTT broker")
            
            # Resubscribe to topics
            with self._lock:
                for topic in self._subscriptions.keys():
                    client.subscribe(topic, self.config.default_qos)
                
                for topic in self._pending_subscriptions:
                    client.subscribe(topic, self.config.default_qos)
                self._pending_subscriptions.clear()
        else:
            logger.error(f"Connection failed with code: {rc}")
    
    def _on_disconnect(self, client, userdata, rc):
        """Handle disconnection event."""
        self._is_connected = False
        
        if rc != 0:
            logger.warning(f"Unexpected disconnect (rc={rc})")
            self._stats["reconnections"] += 1
            
            if self.config.auto_reconnect:
                logger.info("Will attempt to reconnect...")
    
    def _on_message(self, client, userdata, msg):
        """Handle incoming message."""
        self._stats["messages_received"] += 1
        
        try:
            # Parse payload
            payload = json.loads(msg.payload.decode())
        except json.JSONDecodeError:
            payload = {"raw": msg.payload.decode()}
        
        # Create message object
        message = Message(
            payload=payload,
            message_type=MessageType.EVENT,
        )
        
        # Find matching callbacks
        with self._lock:
            for pattern, callbacks in self._subscriptions.items():
                if self._topic_matches(pattern, msg.topic):
                    for callback in callbacks:
                        try:
                            callback(msg.topic, message)
                        except Exception as e:
                            logger.error(f"Callback error: {e}")
    
    def _on_subscribe(self, client, userdata, mid, granted_qos):
        """Handle subscription acknowledgment."""
        logger.debug(f"Subscription acknowledged (mid={mid})")
    
    def _topic_matches(self, pattern: str, topic: str) -> bool:
        """Check if topic matches pattern with wildcards."""
        pattern_parts = pattern.split("/")
        topic_parts = topic.split("/")
        
        i = 0
        for i, part in enumerate(pattern_parts):
            if part == "#":
                return True
            if i >= len(topic_parts):
                return False
            if part != "+" and part != topic_parts[i]:
                return False
        
        return i + 1 == len(topic_parts)
    
    def __enter__(self) -> "MQTTTransport":
        self.connect()
        return self
    
    def __exit__(self, *args) -> None:
        self.disconnect()


class MQTTEventBridge:
    """
    Bridge between VESPER EventBus and MQTT.
    
    Automatically publishes EventBus events to MQTT and
    subscribes to MQTT messages to inject into EventBus.
    
    Example:
        from vesper.core.event_bus import EventBus
        from vesper.network.mqtt import MQTTEventBridge, MQTTConfig
        
        event_bus = EventBus()
        mqtt_config = MQTTConfig(broker_host="localhost")
        
        bridge = MQTTEventBridge(event_bus, mqtt_config)
        bridge.start()
        
        # Events on the bus will be published to MQTT
        event_bus.emit("motion_detected", {"sensor": "sensor_1"})
        
        bridge.stop()
    """
    
    def __init__(
        self,
        event_bus,
        mqtt_config: Optional[MQTTConfig] = None,
        publish_events: bool = True,
        subscribe_commands: bool = True,
    ):
        """
        Initialize the bridge.
        
        Args:
            event_bus: VESPER EventBus instance
            mqtt_config: MQTT configuration
            publish_events: Publish EventBus events to MQTT
            subscribe_commands: Subscribe to MQTT commands
        """
        from vesper.core.event_bus import EventBus
        
        self._event_bus: EventBus = event_bus
        self._mqtt = MQTTTransport(mqtt_config)
        self._publish_events = publish_events
        self._subscribe_commands = subscribe_commands
        self._is_running = False
    
    def start(self) -> bool:
        """Start the bridge."""
        if not self._mqtt.connect():
            return False
        
        # Subscribe EventBus to forward events to MQTT
        if self._publish_events:
            self._event_bus.subscribe("*", self._on_event_bus_event)
        
        # Subscribe to MQTT commands
        if self._subscribe_commands:
            self._mqtt.subscribe(
                f"{self._mqtt.config.topic_prefix}/devices/+/commands",
                self._on_mqtt_command
            )
        
        self._is_running = True
        logger.info("MQTT Event Bridge started")
        return True
    
    def stop(self) -> None:
        """Stop the bridge."""
        self._is_running = False
        self._mqtt.disconnect()
        logger.info("MQTT Event Bridge stopped")
    
    def _on_event_bus_event(self, event) -> None:
        """Forward EventBus event to MQTT."""
        if not self._is_running:
            return
        
        source_id = event.source_id or "unknown"
        self._mqtt.publish_event(
            device_id=source_id,
            event_name=event.event_type,
            payload=event.payload,
        )
    
    def _on_mqtt_command(self, topic: str, message: Message) -> None:
        """Forward MQTT command to EventBus."""
        if not self._is_running:
            return
        
        payload = message.payload
        command = payload.get("command", "unknown")
        device_id = payload.get("device_id")
        params = payload.get("params", {})
        
        self._event_bus.emit(
            event_type=f"command_{command}",
            payload={"device_id": device_id, "params": params},
        )
    
    @property
    def is_running(self) -> bool:
        return self._is_running
    
    @property
    def mqtt(self) -> MQTTTransport:
        return self._mqtt
