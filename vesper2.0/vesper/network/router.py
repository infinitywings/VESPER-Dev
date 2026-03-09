"""
Message router for directing messages between nodes.

Handles message routing, filtering, and delivery.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from vesper.protocol.messages import Message, MessageType
from vesper.network.transport import Transport


logger = logging.getLogger(__name__)


@dataclass
class Route:
    """A routing rule for messages."""
    pattern: str  # Target pattern (supports wildcards)
    transport: Transport
    priority: int = 0
    message_types: Optional[Set[MessageType]] = None


@dataclass
class Subscription:
    """A subscription to messages matching a pattern."""
    pattern: str  # Source/event pattern
    callback: Callable[[Message], None]
    message_types: Optional[Set[MessageType]] = None


class MessageRouter:
    """
    Routes messages between transports and subscribers.
    
    Features:
    - Pattern-based routing with wildcards
    - Multiple transport support
    - Message filtering by type
    - Subscription management
    
    Example:
        router = MessageRouter()
        router.add_route("device_*", device_transport)
        router.add_route("agent_*", agent_transport)
        router.subscribe("motion_*", handle_motion)
        router.route(message)
    """
    
    def __init__(self, default_transport: Optional[Transport] = None):
        """
        Initialize the message router.
        
        Args:
            default_transport: Default transport for unmatched routes
        """
        self._default_transport = default_transport
        self._routes: List[Route] = []
        self._subscriptions: List[Subscription] = []
        self._stats = {
            "messages_routed": 0,
            "messages_dropped": 0,
            "subscriptions_notified": 0,
        }
    
    @property
    def stats(self) -> Dict[str, int]:
        """Routing statistics."""
        return self._stats.copy()
    
    def add_route(
        self,
        pattern: str,
        transport: Transport,
        priority: int = 0,
        message_types: Optional[Set[MessageType]] = None,
    ) -> None:
        """
        Add a routing rule.
        
        Args:
            pattern: Target ID pattern (supports * and ? wildcards)
            transport: Transport to use for matching targets
            priority: Higher priority routes are checked first
            message_types: Optional filter by message type
        """
        route = Route(
            pattern=pattern,
            transport=transport,
            priority=priority,
            message_types=message_types,
        )
        self._routes.append(route)
        self._routes.sort(key=lambda r: r.priority, reverse=True)
        logger.debug(f"Added route: {pattern} -> {transport.__class__.__name__}")
    
    def remove_route(self, pattern: str) -> bool:
        """
        Remove a routing rule.
        
        Args:
            pattern: Pattern to remove
            
        Returns:
            True if route was found and removed
        """
        original_len = len(self._routes)
        self._routes = [r for r in self._routes if r.pattern != pattern]
        return len(self._routes) < original_len
    
    def subscribe(
        self,
        pattern: str,
        callback: Callable[[Message], None],
        message_types: Optional[Set[MessageType]] = None,
    ) -> None:
        """
        Subscribe to messages matching a pattern.
        
        Args:
            pattern: Source ID or event name pattern
            callback: Function to call for matching messages
            message_types: Optional filter by message type
        """
        sub = Subscription(
            pattern=pattern,
            callback=callback,
            message_types=message_types,
        )
        self._subscriptions.append(sub)
        logger.debug(f"Added subscription: {pattern}")
    
    def unsubscribe(self, pattern: str, callback: Optional[Callable] = None) -> int:
        """
        Remove subscriptions.
        
        Args:
            pattern: Pattern to unsubscribe from
            callback: Specific callback to remove (None = all)
            
        Returns:
            Number of subscriptions removed
        """
        original_len = len(self._subscriptions)
        if callback:
            self._subscriptions = [
                s for s in self._subscriptions
                if not (s.pattern == pattern and s.callback == callback)
            ]
        else:
            self._subscriptions = [
                s for s in self._subscriptions
                if s.pattern != pattern
            ]
        return original_len - len(self._subscriptions)
    
    def route(self, message: Message) -> bool:
        """
        Route a message to appropriate transport(s).
        
        Args:
            message: Message to route
            
        Returns:
            True if message was routed successfully
        """
        target_id = message.target_id
        
        # Notify subscriptions first
        self._notify_subscriptions(message)
        
        # Find matching route
        transport = self._find_transport(target_id, message.message_type)
        
        if transport:
            success = transport.send(message)
            if success:
                self._stats["messages_routed"] += 1
            else:
                self._stats["messages_dropped"] += 1
            return success
        else:
            logger.warning(f"No route found for target: {target_id}")
            self._stats["messages_dropped"] += 1
            return False
    
    def _find_transport(
        self,
        target_id: Optional[str],
        message_type: MessageType,
    ) -> Optional[Transport]:
        """Find the appropriate transport for a target."""
        if not target_id:
            return self._default_transport
        
        for route in self._routes:
            # Check message type filter
            if route.message_types and message_type not in route.message_types:
                continue
            
            # Check pattern match
            if fnmatch.fnmatch(target_id, route.pattern):
                return route.transport
        
        return self._default_transport
    
    def _notify_subscriptions(self, message: Message) -> None:
        """Notify matching subscriptions."""
        source_id = message.source_id or ""
        
        # For events, also check event name
        event_name = ""
        if hasattr(message, 'event_name'):
            event_name = message.event_name
        elif message.message_type == MessageType.EVENT:
            event_name = message.payload.get('event_name', '')
        
        for sub in self._subscriptions:
            # Check message type filter
            if sub.message_types and message.message_type not in sub.message_types:
                continue
            
            # Check pattern against source_id and event_name
            if (fnmatch.fnmatch(source_id, sub.pattern) or 
                fnmatch.fnmatch(event_name, sub.pattern) or
                sub.pattern == "*"):
                try:
                    sub.callback(message)
                    self._stats["subscriptions_notified"] += 1
                except Exception as e:
                    logger.error(f"Subscription callback error: {e}")
    
    def broadcast(self, message: Message, exclude: Optional[Set[str]] = None) -> int:
        """
        Broadcast a message to all routes.
        
        Args:
            message: Message to broadcast
            exclude: Set of patterns to exclude
            
        Returns:
            Number of transports message was sent to
        """
        exclude = exclude or set()
        sent_to: Set[Transport] = set()
        
        for route in self._routes:
            if route.pattern in exclude:
                continue
            if route.transport not in sent_to:
                if route.transport.send(message):
                    sent_to.add(route.transport)
        
        self._stats["messages_routed"] += len(sent_to)
        return len(sent_to)
