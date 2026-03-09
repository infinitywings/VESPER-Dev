"""
VESPER MQTT Bridge — Real MQTT (Mosquitto) integration.

Connects the internal MessageBroker / EventBus to a real Mosquitto MQTT
broker, producing authentic MQTT 3.1.1 wire-protocol traffic that is
visible in Wireshark pcaps.

Architecture:
    IoTBridge
      ├── MessageBroker  (in-memory pub/sub)
      └── MQTTBridge     (this module)
              │
              ▼
         Mosquitto (Docker)   ← real MQTT 3.1.1 wire traffic
              │
              ▼
         tshark / dumpcap → .pcap files

Topic schema (mirrors existing MessageBroker topics):
    vesper/devices/{room}/{device_type}/events    — device events
    vesper/devices/{room}/{device_type}/state      — device state
    vesper/devices/{room}/{device_type}/command     — device commands
    vesper/automation/trigger                       — automation events
    vesper/habitat/agent/{agent_id}/position        — agent positions

Usage:
    bridge = MQTTBridge(host="127.0.0.1", port=1883)
    bridge.start()
    bridge.publish("vesper/devices/kitchen/motion_sensor/events", payload)
    bridge.subscribe("vesper/devices/#", callback)
    bridge.stop()
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import paho.mqtt.client as mqtt
    HAS_PAHO = True
except ImportError:
    HAS_PAHO = False
    logger.info("paho-mqtt not installed — MQTTBridge will operate in stub mode")


# ─── Configuration ────────────────────────────────────────────────────────────

@dataclass
class MQTTBridgeConfig:
    """Configuration for the MQTT bridge."""
    host: str = "127.0.0.1"
    port: int = 1883
    client_id: str = "vesper-bridge"
    username: str = ""
    password: str = ""
    keepalive: int = 60
    qos: int = 1
    clean_session: bool = True
    topic_prefix: str = "vesper"
    # Reconnect settings
    reconnect_delay_s: float = 1.0
    max_reconnect_delay_s: float = 30.0
    # Logging
    log_messages: bool = False


# Type alias for subscription callbacks
MQTTCallback = Callable[[str, dict], None]


# ─── MQTT Bridge ──────────────────────────────────────────────────────────────

class MQTTBridge:
    """
    Bridges VESPER's internal event system to a real Mosquitto MQTT broker.

    When paho-mqtt is not installed, operates in stub mode (logs but does not
    send real MQTT traffic).  This allows the rest of the system to function
    without a hard dependency.
    """

    def __init__(self, config: Optional[MQTTBridgeConfig] = None):
        self.config = config or MQTTBridgeConfig()
        self._connected = False
        self._client: Optional[Any] = None
        self._subscriptions: Dict[str, List[MQTTCallback]] = {}
        self._lock = threading.Lock()
        self._stats = {
            "messages_published": 0,
            "messages_received": 0,
            "connect_count": 0,
            "errors": 0,
        }

        if HAS_PAHO:
            self._client = mqtt.Client(
                client_id=self.config.client_id,
                clean_session=self.config.clean_session,
                protocol=mqtt.MQTTv311,
            )
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message

            if self.config.username:
                self._client.username_pw_set(
                    self.config.username, self.config.password
                )
        else:
            logger.warning(
                "paho-mqtt not installed — MQTTBridge running in stub mode. "
                "Install with: pip install paho-mqtt"
            )

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self) -> bool:
        """Connect to the MQTT broker. Returns True if connected."""
        if not HAS_PAHO or not self._client:
            logger.info("MQTTBridge stub mode — no real MQTT connection")
            return False

        try:
            self._client.connect(
                self.config.host,
                self.config.port,
                self.config.keepalive,
            )
            self._client.loop_start()
            # Wait briefly for connection
            deadline = time.time() + 5.0
            while not self._connected and time.time() < deadline:
                time.sleep(0.1)

            if self._connected:
                logger.info(
                    f"MQTTBridge connected to {self.config.host}:{self.config.port}"
                )
            else:
                logger.warning("MQTTBridge connection timeout — will retry in background")
            return self._connected

        except Exception as e:
            logger.error(f"MQTTBridge connection failed: {e}")
            self._stats["errors"] += 1
            return False

    def stop(self):
        """Disconnect from the MQTT broker."""
        if self._client and HAS_PAHO:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception as e:
                logger.debug(f"MQTTBridge disconnect error: {e}")
            self._connected = False
            logger.info("MQTTBridge disconnected")

    @property
    def connected(self) -> bool:
        return self._connected

    # ── Callbacks ─────────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected = True
            self._stats["connect_count"] += 1
            logger.info("MQTTBridge: connected to broker")
            # Re-subscribe after reconnect
            with self._lock:
                for topic in self._subscriptions:
                    client.subscribe(topic, qos=self.config.qos)
        else:
            logger.error(f"MQTTBridge: connect failed (rc={rc})")

    def _on_disconnect(self, client, userdata, rc):
        self._connected = False
        if rc != 0:
            logger.warning(f"MQTTBridge: unexpected disconnect (rc={rc})")

    def _on_message(self, client, userdata, msg):
        self._stats["messages_received"] += 1
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {"raw": msg.payload.decode("utf-8", errors="replace")}

        if self.config.log_messages:
            logger.debug(f"MQTT RX: {topic} -> {payload}")

        with self._lock:
            for sub_topic, callbacks in self._subscriptions.items():
                if mqtt.topic_matches_sub(sub_topic, topic):
                    for cb in callbacks:
                        try:
                            cb(topic, payload)
                        except Exception as e:
                            logger.error(f"MQTT callback error: {e}")

    # ── Publish / Subscribe ───────────────────────────────────────────

    def publish(
        self,
        topic: str,
        payload: dict | str | bytes,
        qos: Optional[int] = None,
        retain: bool = False,
    ) -> bool:
        """
        Publish a message to the MQTT broker.

        Args:
            topic: MQTT topic (e.g. "vesper/devices/kitchen/motion_sensor/events")
            payload: Message payload (dict will be JSON-serialized)
            qos: Override default QoS
            retain: Set MQTT retained flag

        Returns:
            True if published (or queued), False on error.
        """
        if isinstance(payload, dict):
            data = json.dumps(payload).encode("utf-8")
        elif isinstance(payload, str):
            data = payload.encode("utf-8")
        else:
            data = payload

        if not HAS_PAHO or not self._client:
            if self.config.log_messages:
                logger.debug(f"MQTT STUB PUB: {topic} -> {data[:100]}")
            self._stats["messages_published"] += 1
            return True

        try:
            info = self._client.publish(
                topic,
                data,
                qos=qos if qos is not None else self.config.qos,
                retain=retain,
            )
            self._stats["messages_published"] += 1

            if self.config.log_messages:
                logger.debug(f"MQTT PUB: {topic} ({len(data)} bytes)")
            return True

        except Exception as e:
            logger.error(f"MQTT publish error: {e}")
            self._stats["errors"] += 1
            return False

    def subscribe(self, topic: str, callback: MQTTCallback) -> None:
        """
        Subscribe to an MQTT topic.

        Args:
            topic: MQTT topic pattern (supports MQTT wildcards + and #)
            callback: Function(topic: str, payload: dict) called on match
        """
        with self._lock:
            if topic not in self._subscriptions:
                self._subscriptions[topic] = []
            self._subscriptions[topic].append(callback)

        if self._client and self._connected:
            self._client.subscribe(topic, qos=self.config.qos)

        logger.debug(f"MQTTBridge subscribed: {topic}")

    def unsubscribe(self, topic: str, callback: Optional[MQTTCallback] = None) -> None:
        """Unsubscribe from a topic."""
        with self._lock:
            if topic in self._subscriptions:
                if callback:
                    self._subscriptions[topic] = [
                        cb for cb in self._subscriptions[topic] if cb is not callback
                    ]
                    if not self._subscriptions[topic]:
                        del self._subscriptions[topic]
                else:
                    del self._subscriptions[topic]

        if self._client and self._connected:
            self._client.unsubscribe(topic)

    # ── Convenience: bridge from EventBus ─────────────────────────────

    def bridge_event(self, event) -> bool:
        """
        Publish an EventBus Event to MQTT.

        Maps Event fields to an MQTT topic:
            vesper/events/{event_type}

        And publishes the full event payload as JSON.
        """
        topic = f"{self.config.topic_prefix}/events/{event.event_type}"
        payload = event.to_dict() if hasattr(event, "to_dict") else {"data": str(event)}
        return self.publish(topic, payload)

    def bridge_device_event(
        self,
        room: str,
        device_type: str,
        event_type: str,
        payload: dict,
    ) -> bool:
        """
        Publish a device event using the standard topic schema.

        Topic: vesper/devices/{room}/{device_type}/{event_type}
        """
        room_slug = room.lower().replace(" ", "_")
        topic = (
            f"{self.config.topic_prefix}/devices/"
            f"{room_slug}/{device_type}/{event_type}"
        )
        return self.publish(topic, payload)

    def bridge_device_state(
        self,
        room: str,
        device_type: str,
        state: dict,
    ) -> bool:
        """Publish a retained device state message."""
        room_slug = room.lower().replace(" ", "_")
        topic = (
            f"{self.config.topic_prefix}/devices/"
            f"{room_slug}/{device_type}/state"
        )
        return self.publish(topic, state, retain=True)

    # ── Stats ─────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        return self._stats.copy()


# ─── Docker Mosquitto helper ─────────────────────────────────────────────────

def ensure_mosquitto_running(
    port: int = 1883,
    container_name: str = "vesper-mqtt",
    network: str = "vesper-home-network",
    ip_address: str = "172.20.0.2",
) -> bool:
    """
    Ensure a Mosquitto broker container is running.

    If already running, returns True immediately.
    Otherwise starts one with the eclipse-mosquitto image.
    """
    import subprocess

    # Check if already running
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
        capture_output=True, text=True,
    )
    if result.returncode == 0 and "true" in result.stdout.lower():
        logger.info(f"Mosquitto already running: {container_name}")
        return True

    # Remove stale container
    subprocess.run(
        ["docker", "rm", "-f", container_name],
        capture_output=True, timeout=10,
    )

    # Start Mosquitto
    cmd = [
        "docker", "run", "-d",
        "--name", container_name,
        "-p", f"{port}:1883",
        "-p", "9001:9001",
    ]

    # Attach to vesper network if it exists
    net_check = subprocess.run(
        ["docker", "network", "inspect", network],
        capture_output=True,
    )
    if net_check.returncode == 0:
        cmd += ["--network", network, "--ip", ip_address]

    cmd += [
        "eclipse-mosquitto:2",
        "mosquitto", "-c", "/mosquitto-no-auth.conf",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        # Try with a simple config that allows anonymous access
        # Create a minimal config
        import tempfile
        conf = tempfile.NamedTemporaryFile(
            mode="w", suffix=".conf", delete=False, prefix="vesper_mqtt_"
        )
        conf.write("listener 1883\nallow_anonymous true\n")
        conf.close()

        cmd_alt = [
            "docker", "run", "-d",
            "--name", container_name,
            "-p", f"{port}:1883",
            "-v", f"{conf.name}:/mosquitto/config/mosquitto.conf:ro",
            "eclipse-mosquitto:2",
        ]
        if net_check.returncode == 0:
            cmd_alt.insert(4, "--network")
            cmd_alt.insert(5, network)
            cmd_alt.insert(6, "--ip")
            cmd_alt.insert(7, ip_address)

        result = subprocess.run(cmd_alt, capture_output=True, text=True, timeout=30)

    if result.returncode == 0:
        logger.info(f"Mosquitto started: {container_name} on port {port}")
        return True
    else:
        logger.error(f"Failed to start Mosquitto: {result.stderr}")
        return False
