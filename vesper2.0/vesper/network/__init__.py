"""
Network module for Vesper IoT communication.

Provides transport abstractions and simulated network capabilities.
"""

from vesper.network.transport import (
    Transport,
    TransportState,
    LocalTransport,
    SimulatedTransport,
)
from vesper.network.router import MessageRouter
from vesper.network.broker import MessageBroker

# MQTT support (optional - requires paho-mqtt)
try:
    from vesper.network.mqtt import (
        MQTTTransport,
        MQTTConfig,
        MQTTEventBridge,
        QoS,
    )
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False
    MQTTTransport = None
    MQTTConfig = None
    MQTTEventBridge = None
    QoS = None

__all__ = [
    "Transport",
    "TransportState",
    "LocalTransport",
    "SimulatedTransport",
    "MessageRouter",
    "MessageBroker",
    # MQTT
    "MQTTTransport",
    "MQTTConfig",
    "MQTTEventBridge",
    "QoS",
    "MQTT_AVAILABLE",
]
