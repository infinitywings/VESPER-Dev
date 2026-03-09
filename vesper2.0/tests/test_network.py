"""
Unit tests for network transport and routing.
"""

import pytest
import time
import threading

from vesper.protocol.messages import (
    Message,
    MessageType,
    EventMessage,
    CommandMessage,
)
from vesper.network.transport import (
    LocalTransport,
    SimulatedTransport,
    TransportState,
    TransportConfig,
)
from vesper.network.router import MessageRouter
from vesper.network.broker import MessageBroker


class TestLocalTransport:
    """Tests for LocalTransport."""
    
    def test_create_transport(self):
        """Test transport creation."""
        transport = LocalTransport("node_1")
        assert transport.node_id == "node_1"
        assert transport.state == TransportState.DISCONNECTED
    
    def test_connect_disconnect(self):
        """Test connection lifecycle."""
        transport = LocalTransport("node_1")
        
        assert transport.connect() is True
        assert transport.is_connected is True
        
        transport.disconnect()
        assert transport.state == TransportState.DISCONNECTED
    
    def test_send_receive(self):
        """Test message passing between transports."""
        t1 = LocalTransport("node_1")
        t2 = LocalTransport("node_2")
        
        t1.connect()
        t2.connect()
        
        try:
            msg = Message(source_id="node_1", target_id="node_2", payload={"data": 123})
            assert t1.send(msg) is True
            
            received = t2.receive(timeout_ms=1000)
            assert received is not None
            assert received.payload["data"] == 123
        finally:
            t1.disconnect()
            t2.disconnect()
    
    def test_broadcast(self):
        """Test broadcasting to all nodes."""
        t1 = LocalTransport("node_1")
        t2 = LocalTransport("node_2")
        t3 = LocalTransport("node_3")
        
        for t in [t1, t2, t3]:
            t.connect()
        
        try:
            # Broadcast (no target_id)
            msg = Message(source_id="node_1", payload={"broadcast": True})
            t1.send(msg)
            
            # Both t2 and t3 should receive
            assert t2.receive(timeout_ms=100) is not None
            assert t3.receive(timeout_ms=100) is not None
            assert t1.pending_count() == 0  # Sender doesn't get it
        finally:
            for t in [t1, t2, t3]:
                t.disconnect()
    
    def test_stats(self):
        """Test statistics tracking."""
        t1 = LocalTransport("node_1")
        t2 = LocalTransport("node_2")
        
        t1.connect()
        t2.connect()
        
        try:
            msg = Message(source_id="node_1", target_id="node_2")
            t1.send(msg)
            t2.receive(timeout_ms=100)
            
            assert t1.stats.messages_sent == 1
            assert t2.stats.messages_received == 1
        finally:
            t1.disconnect()
            t2.disconnect()


class TestSimulatedTransport:
    """Tests for SimulatedTransport."""
    
    def test_create_transport(self):
        """Test simulated transport creation."""
        config = TransportConfig(latency_min_ms=10, latency_max_ms=50)
        transport = SimulatedTransport("node_1", config)
        assert transport.node_id == "node_1"
    
    def test_send_with_latency(self):
        """Test message delivery with latency."""
        config = TransportConfig(latency_min_ms=50, latency_max_ms=50)
        t1 = SimulatedTransport("node_1", config)
        t2 = SimulatedTransport("node_2", config)
        
        t1.connect()
        t2.connect()
        
        try:
            msg = Message(source_id="node_1", target_id="node_2")
            t1.send(msg)
            
            # Should not receive immediately
            immediate = t2.receive(timeout_ms=10)
            assert immediate is None
            
            # Should receive after latency
            time.sleep(0.1)  # Wait for latency + delivery
            delayed = t2.receive(timeout_ms=100)
            assert delayed is not None
        finally:
            t1.disconnect()
            t2.disconnect()
            SimulatedTransport.stop_scheduler()
    
    def test_packet_loss(self):
        """Test packet loss simulation."""
        config = TransportConfig(
            latency_min_ms=0,
            latency_max_ms=0,
            packet_loss_rate=1.0,  # 100% loss
        )
        t1 = SimulatedTransport("node_1", config)
        t2 = SimulatedTransport("node_2", config)
        
        t1.connect()
        t2.connect()
        
        try:
            msg = Message(source_id="node_1", target_id="node_2")
            t1.send(msg)
            
            time.sleep(0.05)
            received = t2.receive(timeout_ms=50)
            
            # All packets should be lost
            assert received is None
            assert t1.stats.packets_dropped == 1
        finally:
            t1.disconnect()
            t2.disconnect()
            SimulatedTransport.stop_scheduler()


class TestMessageRouter:
    """Tests for MessageRouter."""
    
    def test_add_route(self):
        """Test adding routes."""
        router = MessageRouter()
        t1 = LocalTransport("device_handler")
        t1.connect()
        
        router.add_route("device_*", t1)
        
        # Verify route was added
        assert len(router._routes) == 1
        
        t1.disconnect()
    
    def test_route_message(self):
        """Test routing a message."""
        router = MessageRouter()
        t1 = LocalTransport("handler")
        t2 = LocalTransport("device_1")
        
        t1.connect()
        t2.connect()
        
        try:
            router.add_route("handler", t1)
            
            msg = Message(source_id="device_1", target_id="handler")
            success = router.route(msg)
            
            assert success is True
            assert router.stats["messages_routed"] == 1
        finally:
            t1.disconnect()
            t2.disconnect()
    
    def test_wildcard_routing(self):
        """Test wildcard pattern matching."""
        router = MessageRouter()
        t1 = LocalTransport("device_handler")
        t2 = LocalTransport("device_123")  # Target must exist
        t1.connect()
        t2.connect()
        
        try:
            router.add_route("device_*", t1)
            
            msg = Message(target_id="device_123")
            success = router.route(msg)
            
            assert success is True
        finally:
            t1.disconnect()
            t2.disconnect()
    
    def test_subscription(self):
        """Test message subscriptions."""
        router = MessageRouter()
        received = []
        
        router.subscribe("sensor_*", lambda m: received.append(m))
        
        # Manually trigger subscription notification
        msg = Message(source_id="sensor_1", payload={"temp": 25})
        router._notify_subscriptions(msg)
        
        assert len(received) == 1
        assert received[0].payload["temp"] == 25


class TestMessageBroker:
    """Tests for MessageBroker."""
    
    def test_subscribe_publish(self):
        """Test basic pub/sub."""
        broker = MessageBroker()
        received = []
        
        broker.subscribe("client_1", "sensors/temperature", 
                        lambda t, m: received.append((t, m)))
        
        msg = Message(payload={"value": 22.5})
        count = broker.publish("sensors/temperature", msg)
        
        assert count == 1
        assert len(received) == 1
        assert received[0][0] == "sensors/temperature"
    
    def test_wildcard_subscription(self):
        """Test MQTT-style wildcards."""
        broker = MessageBroker()
        received = []
        
        # Single-level wildcard
        broker.subscribe("client_1", "sensors/+/temperature",
                        lambda t, m: received.append(t))
        
        broker.publish("sensors/kitchen/temperature", Message())
        broker.publish("sensors/bedroom/temperature", Message())
        broker.publish("sensors/kitchen/humidity", Message())  # No match
        
        assert len(received) == 2
        assert "sensors/kitchen/temperature" in received
        assert "sensors/bedroom/temperature" in received
    
    def test_multi_level_wildcard(self):
        """Test # wildcard."""
        broker = MessageBroker()
        received = []
        
        broker.subscribe("client_1", "sensors/#",
                        lambda t, m: received.append(t))
        
        broker.publish("sensors/kitchen/temperature", Message())
        broker.publish("sensors/bedroom/humidity", Message())
        broker.publish("other/topic", Message())  # No match
        
        assert len(received) == 2
    
    def test_retained_messages(self):
        """Test message retention."""
        broker = MessageBroker(enable_retained=True)
        
        msg = Message(payload={"last_value": 100})
        broker.publish("status/device_1", msg, retain=True)
        
        # Verify retained
        retained = broker.get_retained("status/device_1")
        assert retained is not None
        assert retained.payload["last_value"] == 100
    
    def test_queue_mode(self):
        """Test message queuing."""
        broker = MessageBroker()
        
        broker.subscribe("client_1", "events", use_queue=True)
        
        broker.publish("events", Message(payload={"seq": 1}))
        broker.publish("events", Message(payload={"seq": 2}))
        
        msg1 = broker.poll("client_1", timeout_ms=100)
        msg2 = broker.poll("client_1", timeout_ms=100)
        
        assert msg1.payload["seq"] == 1
        assert msg2.payload["seq"] == 2
    
    def test_unsubscribe(self):
        """Test unsubscription."""
        broker = MessageBroker()
        received = []
        
        broker.subscribe("client_1", "topic", lambda t, m: received.append(m))
        broker.publish("topic", Message())
        
        assert len(received) == 1
        
        broker.unsubscribe("client_1", "topic")
        broker.publish("topic", Message())
        
        assert len(received) == 1  # No new messages


class TestTopicMatching:
    """Tests for MQTT topic pattern matching."""
    
    def test_exact_match(self):
        """Test exact topic matching."""
        assert MessageBroker._topic_matches("a/b/c", "a/b/c") is True
        assert MessageBroker._topic_matches("a/b/c", "a/b/d") is False
    
    def test_single_level_wildcard(self):
        """Test + wildcard."""
        assert MessageBroker._topic_matches("a/b/c", "a/+/c") is True
        assert MessageBroker._topic_matches("a/x/c", "a/+/c") is True
        assert MessageBroker._topic_matches("a/b/c/d", "a/+/c") is False
    
    def test_multi_level_wildcard(self):
        """Test # wildcard."""
        assert MessageBroker._topic_matches("a/b/c", "a/#") is True
        assert MessageBroker._topic_matches("a/b/c/d/e", "a/#") is True
        assert MessageBroker._topic_matches("b/c", "a/#") is False
    
    def test_combined_wildcards(self):
        """Test combined + and # wildcards."""
        assert MessageBroker._topic_matches("a/b/c/d", "a/+/c/#") is True
        assert MessageBroker._topic_matches("a/x/c/y/z", "a/+/c/#") is True
