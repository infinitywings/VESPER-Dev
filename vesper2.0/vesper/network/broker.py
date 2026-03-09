"""
Message broker for pub/sub communication.

Provides topic-based message distribution.
"""

from __future__ import annotations

import fnmatch
import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set
from queue import Queue, Empty

from vesper.protocol.messages import Message, MessageType, EventMessage


logger = logging.getLogger(__name__)


# Callback type
TopicCallback = Callable[[str, Message], None]


@dataclass
class TopicSubscription:
    """A subscription to a topic."""
    subscriber_id: str
    pattern: str  # Topic pattern (supports wildcards)
    callback: TopicCallback
    queue: Optional[Queue[Message]] = None  # Optional message queue


class MessageBroker:
    """
    Topic-based message broker for pub/sub communication.
    
    Similar to MQTT broker functionality:
    - Topic-based publish/subscribe
    - Wildcard subscriptions (*, #)
    - Message queuing per subscriber
    - Retained messages per topic
    
    Example:
        broker = MessageBroker()
        
        # Subscribe to topics
        broker.subscribe("client_1", "sensors/+/temperature", callback)
        broker.subscribe("client_2", "sensors/#", callback)
        
        # Publish message
        broker.publish("sensors/kitchen/temperature", message)
    """
    
    def __init__(self, enable_retained: bool = True):
        """
        Initialize the message broker.
        
        Args:
            enable_retained: Whether to store retained messages
        """
        self._enable_retained = enable_retained
        self._subscriptions: Dict[str, List[TopicSubscription]] = defaultdict(list)
        self._retained: Dict[str, Message] = {}  # topic -> last retained message
        self._subscriber_queues: Dict[str, Queue[Message]] = {}
        self._lock = threading.RLock()
        
        self._stats = {
            "messages_published": 0,
            "messages_delivered": 0,
            "subscribers": 0,
            "topics": 0,
        }
    
    @property
    def stats(self) -> Dict[str, int]:
        """Broker statistics."""
        with self._lock:
            self._stats["subscribers"] = sum(
                len(subs) for subs in self._subscriptions.values()
            )
            self._stats["topics"] = len(self._retained) if self._enable_retained else 0
        return self._stats.copy()
    
    def subscribe(
        self,
        subscriber_id: str,
        topic_pattern: str,
        callback: Optional[TopicCallback] = None,
        use_queue: bool = False,
    ) -> None:
        """
        Subscribe to a topic pattern.
        
        Patterns support:
        - Exact match: "sensors/kitchen/temperature"
        - Single-level wildcard (+): "sensors/+/temperature"
        - Multi-level wildcard (#): "sensors/#"
        
        Args:
            subscriber_id: Unique identifier for the subscriber
            topic_pattern: Topic pattern to subscribe to
            callback: Function to call on message (topic, message)
            use_queue: Whether to queue messages for polling
        """
        queue = None
        if use_queue:
            queue = Queue()
            self._subscriber_queues[subscriber_id] = queue
        
        sub = TopicSubscription(
            subscriber_id=subscriber_id,
            pattern=topic_pattern,
            callback=callback or (lambda t, m: None),
            queue=queue,
        )
        
        with self._lock:
            self._subscriptions[topic_pattern].append(sub)
        
        logger.debug(f"Subscribed {subscriber_id} to {topic_pattern}")
        
        # Deliver retained messages if any match
        if self._enable_retained:
            self._deliver_retained(sub)
    
    def unsubscribe(
        self,
        subscriber_id: str,
        topic_pattern: Optional[str] = None,
    ) -> int:
        """
        Unsubscribe from topic(s).
        
        Args:
            subscriber_id: Subscriber to unsubscribe
            topic_pattern: Specific pattern (None = all)
            
        Returns:
            Number of subscriptions removed
        """
        removed = 0
        with self._lock:
            if topic_pattern:
                patterns = [topic_pattern]
            else:
                patterns = list(self._subscriptions.keys())
            
            for pattern in patterns:
                original = len(self._subscriptions[pattern])
                self._subscriptions[pattern] = [
                    s for s in self._subscriptions[pattern]
                    if s.subscriber_id != subscriber_id
                ]
                removed += original - len(self._subscriptions[pattern])
                
                # Clean up empty patterns
                if not self._subscriptions[pattern]:
                    del self._subscriptions[pattern]
        
        # Clean up queue
        if subscriber_id in self._subscriber_queues:
            del self._subscriber_queues[subscriber_id]
        
        return removed
    
    def publish(
        self,
        topic: str,
        message: Message,
        retain: bool = False,
    ) -> int:
        """
        Publish a message to a topic.
        
        Args:
            topic: Topic to publish to
            message: Message to publish
            retain: Whether to retain this message
            
        Returns:
            Number of subscribers notified
        """
        self._stats["messages_published"] += 1
        
        # Store retained message
        if retain and self._enable_retained:
            self._retained[topic] = message
        
        # Find matching subscriptions
        matching_subs = self._find_matching_subscriptions(topic)
        
        # Deliver to subscribers
        delivered = 0
        for sub in matching_subs:
            try:
                if sub.queue:
                    sub.queue.put(message)
                sub.callback(topic, message)
                delivered += 1
            except Exception as e:
                logger.error(f"Delivery error to {sub.subscriber_id}: {e}")
        
        self._stats["messages_delivered"] += delivered
        return delivered
    
    def publish_event(
        self,
        topic: str,
        event_name: str,
        source_id: str,
        payload: Optional[Dict[str, Any]] = None,
        retain: bool = False,
    ) -> int:
        """
        Convenience method to publish an event message.
        
        Args:
            topic: Topic to publish to
            event_name: Name of the event
            source_id: Source device/agent ID
            payload: Event data
            retain: Whether to retain
            
        Returns:
            Number of subscribers notified
        """
        message = EventMessage.create(
            event_name=event_name,
            source_id=source_id,
            payload=payload,
        )
        return self.publish(topic, message, retain)
    
    def poll(
        self,
        subscriber_id: str,
        timeout_ms: Optional[int] = None,
    ) -> Optional[Message]:
        """
        Poll for queued messages.
        
        Args:
            subscriber_id: Subscriber to poll for
            timeout_ms: Timeout in milliseconds
            
        Returns:
            Next queued message or None
        """
        queue = self._subscriber_queues.get(subscriber_id)
        if not queue:
            return None
        
        try:
            timeout = timeout_ms / 1000.0 if timeout_ms else None
            return queue.get(timeout=timeout)
        except Empty:
            return None
    
    def get_retained(self, topic: str) -> Optional[Message]:
        """Get retained message for a topic."""
        return self._retained.get(topic)
    
    def clear_retained(self, topic_pattern: Optional[str] = None) -> int:
        """
        Clear retained messages.
        
        Args:
            topic_pattern: Pattern to clear (None = all)
            
        Returns:
            Number of messages cleared
        """
        if not topic_pattern:
            count = len(self._retained)
            self._retained.clear()
            return count
        
        to_remove = [
            topic for topic in self._retained
            if self._topic_matches(topic, topic_pattern)
        ]
        for topic in to_remove:
            del self._retained[topic]
        return len(to_remove)
    
    def _find_matching_subscriptions(self, topic: str) -> List[TopicSubscription]:
        """Find all subscriptions matching a topic."""
        matching = []
        with self._lock:
            for pattern, subs in self._subscriptions.items():
                if self._topic_matches(topic, pattern):
                    matching.extend(subs)
        return matching
    
    def _deliver_retained(self, subscription: TopicSubscription) -> None:
        """Deliver retained messages to a new subscription."""
        for topic, message in self._retained.items():
            if self._topic_matches(topic, subscription.pattern):
                try:
                    if subscription.queue:
                        subscription.queue.put(message)
                    subscription.callback(topic, message)
                except Exception as e:
                    logger.error(f"Retained delivery error: {e}")
    
    @staticmethod
    def _topic_matches(topic: str, pattern: str) -> bool:
        """
        Check if a topic matches a pattern.
        
        Supports MQTT-style wildcards:
        - '+' matches exactly one level
        - '#' matches any remaining levels (must be last)
        """
        topic_parts = topic.split("/")
        pattern_parts = pattern.split("/")
        
        ti = 0
        pi = 0
        
        while pi < len(pattern_parts):
            pattern_part = pattern_parts[pi]
            
            if pattern_part == "#":
                # '#' matches everything remaining
                return True
            
            if ti >= len(topic_parts):
                return False
            
            topic_part = topic_parts[ti]
            
            if pattern_part == "+":
                # '+' matches exactly one level
                ti += 1
                pi += 1
            elif pattern_part == topic_part:
                ti += 1
                pi += 1
            else:
                return False
        
        # Both must be exhausted
        return ti == len(topic_parts)
