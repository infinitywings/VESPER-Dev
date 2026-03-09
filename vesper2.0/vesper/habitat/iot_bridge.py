"""
IoT Bridge: Connects Habitat 3D navigation with VESPER IoT devices.

This module bridges the simulated 3D environment with real VESPER
device communication, enabling:
- Motion detection based on agent position
- Device state changes via MQTT-like pub/sub
- Automation rules (e.g., motion -> lights on)
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from enum import Enum

from vesper.core.event_bus import EventBus, Event, EventPriority
from vesper.network.broker import MessageBroker
from vesper.protocol.messages import Message, MessageType
from vesper.devices.motion_sensor import MotionSensor
from vesper.devices.base import IoTDevice

logger = logging.getLogger(__name__)


class DeviceType(Enum):
    """Supported device types."""
    MOTION_SENSOR = "motion_sensor"
    SMART_LIGHT = "smart_light"
    TEMPERATURE_SENSOR = "temperature_sensor"
    DOOR_SENSOR = "door_sensor"
    WATER_LEAK_SENSOR = "water_leak_sensor"
    HUMIDITY_SENSOR = "humidity_sensor"
    SMART_DOOR_LOCK = "smart_door_lock"
    THERMOSTAT = "thermostat"


@dataclass
class AutomationRule:
    """
    An automation rule that triggers actions based on events.
    
    Example:
        # Turn on lights when motion detected
        rule = AutomationRule(
            name="motion_lights",
            trigger_event="motion_detected",
            trigger_room="living room",
            action="set_state",
            target_device_type="smart_light",
            target_room="living room",
            action_params={"state": "on"},
        )
    """
    name: str
    trigger_event: str
    action: str  # "set_state", "toggle", "notify"
    trigger_room: Optional[str] = None
    trigger_device_id: Optional[str] = None
    target_device_type: Optional[str] = None
    target_device_id: Optional[str] = None
    target_room: Optional[str] = None
    action_params: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    cooldown: float = 5.0  # Seconds between triggers
    last_triggered: float = 0.0


@dataclass
class LiveDevice:
    """
    A live IoT device with real-time state and communication.
    """
    device_id: str
    device_type: str
    room: str
    position: Tuple[float, float, float]
    state: str = "off"
    properties: Dict[str, Any] = field(default_factory=dict)
    last_event_time: float = 0.0
    
    # For motion sensors
    detection_radius: float = 3.0
    is_triggered: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "device_type": self.device_type,
            "room": self.room,
            "position": self.position,
            "state": self.state,
            "properties": self.properties,
            "is_triggered": self.is_triggered,
        }


class IoTBridge:
    """
    Bridge between Habitat 3D navigation and VESPER IoT system.
    
    Features:
    - Creates real VESPER devices for each room
    - Detects motion based on agent position
    - Publishes events via MQTT-like broker
    - Executes automation rules
    """
    
    # Default devices per room type
    ROOM_DEVICES = {
        "living room": [
            (DeviceType.MOTION_SENSOR, 3.0),
            (DeviceType.SMART_LIGHT, None),
            (DeviceType.TEMPERATURE_SENSOR, None),
        ],
        "bedroom": [
            (DeviceType.MOTION_SENSOR, 2.5),
            (DeviceType.SMART_LIGHT, None),
            (DeviceType.TEMPERATURE_SENSOR, None),
        ],
        "kitchen": [
            (DeviceType.MOTION_SENSOR, 2.5),
            (DeviceType.SMART_LIGHT, None),
            (DeviceType.WATER_LEAK_SENSOR, None),
        ],
        "bathroom": [
            (DeviceType.MOTION_SENSOR, 2.0),
            (DeviceType.WATER_LEAK_SENSOR, None),
            (DeviceType.HUMIDITY_SENSOR, None),
        ],
        "office": [
            (DeviceType.MOTION_SENSOR, 3.0),
            (DeviceType.SMART_LIGHT, None),
        ],
        "hallway": [
            (DeviceType.MOTION_SENSOR, 4.0),
            (DeviceType.SMART_LIGHT, None),
        ],
        "entryway": [
            (DeviceType.MOTION_SENSOR, 3.0),
            (DeviceType.SMART_DOOR_LOCK, None),
            (DeviceType.DOOR_SENSOR, None),
        ],
        "closet": [
            (DeviceType.DOOR_SENSOR, None),
        ],
    }
    
    def __init__(self):
        # Core VESPER components
        self.event_bus = EventBus()
        self.broker = MessageBroker()
        
        # Live devices
        self.devices: Dict[str, LiveDevice] = {}
        self.rooms: Dict[str, List[str]] = {}  # room -> device_ids
        
        # Room positions for motion detection
        self.room_positions: Dict[str, Tuple[float, float, float]] = {}
        
        # Automation rules
        self.automation_rules: List[AutomationRule] = []
        
        # Event log for UI display
        self.event_log: List[Dict[str, Any]] = []
        self._max_log_size = 50
        
        # Subscribe to all device events
        self.broker.subscribe(
            "iot_bridge",
            "devices/#",
            self._on_device_event,
        )
        
        # Stats
        self._motion_events = 0
        self._automation_triggers = 0
        self._last_agent_room: Optional[str] = None
        
        logger.info("IoTBridge initialized with EventBus and MessageBroker")
    
    def setup_room(
        self,
        room_name: str,
        position: Tuple[float, float, float] = (0.0, 0.0, 0.0),
    ):
        """
        Setup IoT devices for a room.
        
        Args:
            room_name: Name of the room
            position: Room center position (x, y, z)
        """
        room_key = room_name.lower()
        self.room_positions[room_key] = position
        
        # Find matching device config
        device_config = None
        for pattern, config in self.ROOM_DEVICES.items():
            if pattern in room_key:
                device_config = config
                break
        
        if not device_config:
            # Default: just a motion sensor
            device_config = [(DeviceType.MOTION_SENSOR, 3.0)]
        
        # Create devices
        self.rooms[room_key] = []
        for device_type, param in device_config:
            device_id = f"{room_key}_{device_type.value}"
            
            device = LiveDevice(
                device_id=device_id,
                device_type=device_type.value,
                room=room_key,
                position=position,
                state="off",
                detection_radius=param if param else 3.0,
            )
            
            self.devices[device_id] = device
            self.rooms[room_key].append(device_id)
            
            # Subscribe device to its topic
            topic = f"devices/{room_key}/{device_type.value}"
            self.broker.subscribe(device_id, topic, lambda t, m: None)
        
        logger.debug(f"Setup {len(device_config)} devices for {room_name}")
    
    def setup_rooms(self, rooms: List[str]):
        """Setup devices for multiple rooms."""
        for room in rooms:
            self.setup_room(room)
        
        # Add default automation rules
        self._setup_default_automations()
        
        logger.info(f"Setup {len(self.devices)} devices across {len(rooms)} rooms")
    
    def _setup_default_automations(self):
        """Setup default automation rules."""
        # Motion -> Lights on (for all rooms with both)
        for room, device_ids in self.rooms.items():
            has_motion = any("motion_sensor" in d for d in device_ids)
            has_light = any("smart_light" in d for d in device_ids)
            
            if has_motion and has_light:
                rule = AutomationRule(
                    name=f"motion_lights_{room}",
                    trigger_event="motion_detected",
                    trigger_room=room,
                    action="set_state",
                    target_device_type="smart_light",
                    target_room=room,
                    action_params={"state": "on"},
                    cooldown=10.0,
                )
                self.automation_rules.append(rule)
                logger.debug(f"Added automation: motion -> lights for {room}")
    
    def add_device(
        self,
        device_type: str,
        room: str,
        position: Tuple[float, float, float],
    ) -> str:
        """
        Add a new device dynamically.
        
        Args:
            device_type: Type of device (e.g., "motion_sensor", "smart_light")
            room: Room name
            position: Device position (x, y, z)
            
        Returns:
            Device ID of the newly created device
        """
        # Generate unique device ID
        device_id = f"{room}_{device_type}_{len(self.devices)}"
        
        # Determine detection radius for motion sensors
        detection_radius = 3.0 if "motion" in device_type else None
        
        # Create device
        device = LiveDevice(
            device_id=device_id,
            device_type=device_type,
            room=room,
            position=position,
            state="off",
            detection_radius=detection_radius,
        )
        
        self.devices[device_id] = device
        
        # Add to room list
        if room not in self.rooms:
            self.rooms[room] = []
        self.rooms[room].append(device_id)
        
        # Subscribe to device topic
        topic = f"devices/{room}/{device_type}"
        self.broker.subscribe(device_id, topic, lambda t, m: None)
        
        logger.info(f"Added device: {device_id}")
        return device_id
    
    def add_automation_rule(
        self,
        name: str,
        trigger_event: str,
        action: str,
        trigger_room: Optional[str] = None,
        trigger_device_id: Optional[str] = None,
        target_device_type: Optional[str] = None,
        target_device_id: Optional[str] = None,
        target_room: Optional[str] = None,
        action_params: Optional[Dict[str, Any]] = None,
        cooldown: float = 5.0,
    ):
        """
        Add an automation rule dynamically.
        
        Args:
            name: Rule name
            trigger_event: Event type that triggers the rule
            action: Action to perform
            trigger_room: Room that triggers the rule
            trigger_device_id: Specific device that triggers
            target_device_type: Type of device to control
            target_device_id: Specific device to control
            target_room: Room containing target devices
            action_params: Parameters for the action
            cooldown: Cooldown between triggers
        """
        rule = AutomationRule(
            name=name,
            trigger_event=trigger_event,
            action=action,
            trigger_room=trigger_room,
            trigger_device_id=trigger_device_id,
            target_device_type=target_device_type,
            target_device_id=target_device_id,
            target_room=target_room,
            action_params=action_params or {},
            cooldown=cooldown,
        )
        self.automation_rules.append(rule)
        logger.info(f"Added automation rule: {name}")
    
    def update_agent_position(
        self,
        position: Tuple[float, float, float],
        agent_id: str = "player",
    ) -> List[Dict[str, Any]]:
        """
        Update agent position and trigger motion sensors.
        
        Args:
            position: Agent's current (x, y, z) position
            agent_id: Agent identifier
            
        Returns:
            List of triggered events
        """
        events = []
        current_time = time.time()
        
        # First, determine which room the agent is in
        current_room = self._get_agent_room(position)
        
        # Update room tracking and emit room enter event
        if current_room != self._last_agent_room:
            self._last_agent_room = current_room
            if current_room:
                self._log_event("room_enter", current_room, {"room": current_room})
                print(f"[IoT] > Entered {current_room}")
        
        # Now trigger/untrigger motion sensors based on current room
        for device_id, device in self.devices.items():
            if device.device_type != "motion_sensor":
                continue
            
            was_triggered = device.is_triggered
            
            # Trigger if agent is in the same room as the sensor
            if current_room and device.room == current_room:
                device.is_triggered = True
                device.state = "triggered"
                
                # Only emit event if newly triggered
                if not was_triggered:
                    # Calculate distance for logging
                    room_pos = self.room_positions.get(current_room, position)
                    dx = position[0] - room_pos[0]
                    dz = position[2] - room_pos[2]
                    distance = math.sqrt(dx*dx + dz*dz)
                    
                    event = self._emit_motion_event(device, agent_id, distance)
                    events.append(event)
                    self._motion_events += 1
                    
                    # Print to terminal for visibility
                    print(f"[IoT] ! Motion detected in {device.room}")
                    
                    # Check automation rules
                    self._check_automations("motion_detected", device.room, device_id)
            else:
                device.is_triggered = False
                device.state = "off"
        
        return events
    
    def _get_agent_room(self, position: Tuple[float, float, float]) -> Optional[str]:
        """Determine which room the agent is in based on closest room position."""
        closest_room = None
        closest_dist = float('inf')
        
        for room, room_pos in self.room_positions.items():
            dx = position[0] - room_pos[0]
            dz = position[2] - room_pos[2]
            dist = math.sqrt(dx*dx + dz*dz)
            
            if dist < closest_dist:
                closest_dist = dist
                closest_room = room
        
        return closest_room
    
    def _emit_motion_event(
        self,
        device: LiveDevice,
        agent_id: str,
        distance: float,
    ) -> Dict[str, Any]:
        """Emit a motion detected event."""
        event_data = {
            "event_type": "motion_detected",
            "device_id": device.device_id,
            "room": device.room,
            "agent_id": agent_id,
            "distance": round(distance, 2),
            "timestamp": time.time(),
        }
        
        # Publish to MQTT broker
        msg = Message(
            message_type=MessageType.EVENT,
            source_id=device.device_id,
            payload=event_data,
        )
        topic = f"devices/{device.room}/motion_sensor/events"
        self.broker.publish(topic, msg)
        
        # Emit to event bus
        event = Event.create(
            event_type="motion_detected",
            payload=event_data,
            source_id=device.device_id,
            priority=EventPriority.NORMAL,
        )
        self.event_bus.emit(event)
        
        # Log for UI
        self._log_event("motion_detected", device.room, event_data)
        
        device.last_event_time = time.time()
        device.state = "triggered"
        
        logger.debug(f"Motion detected in {device.room} (distance: {distance:.2f}m)")
        return event_data
    
    def _check_automations(
        self,
        event_type: str,
        room: str,
        device_id: str,
    ):
        """Check and execute matching automation rules."""
        current_time = time.time()
        
        for rule in self.automation_rules:
            if not rule.enabled:
                continue
            
            # Check cooldown
            if current_time - rule.last_triggered < rule.cooldown:
                continue
            
            # Check trigger match
            if rule.trigger_event != event_type:
                continue
            
            if rule.trigger_room and rule.trigger_room != room:
                continue
            
            if rule.trigger_device_id and rule.trigger_device_id != device_id:
                continue
            
            # Execute action
            self._execute_automation(rule)
            rule.last_triggered = current_time
            self._automation_triggers += 1
    
    def _execute_automation(self, rule: AutomationRule):
        """Execute an automation rule action."""
        target_devices = []
        
        # Find target devices
        for dev_id, device in self.devices.items():
            if rule.target_device_id and dev_id == rule.target_device_id:
                target_devices.append(device)
            elif rule.target_device_type and device.device_type == rule.target_device_type:
                if rule.target_room and device.room == rule.target_room:
                    target_devices.append(device)
                elif not rule.target_room:
                    target_devices.append(device)
        
        # Execute action
        for device in target_devices:
            if rule.action == "set_state":
                new_state = rule.action_params.get("state", "on")
                old_state = device.state
                device.state = new_state
                
                self._log_event(
                    "automation_triggered",
                    device.room,
                    {
                        "rule": rule.name,
                        "device": device.device_id,
                        "old_state": old_state,
                        "new_state": new_state,
                    }
                )
                
                logger.info(f"Automation '{rule.name}': {device.device_id} -> {new_state}")
                print(f"[IoT] ⚡ Automation: {device.device_id} turned {new_state} (rule: {rule.name})")
                
            elif rule.action == "toggle":
                device.state = "off" if device.state == "on" else "on"
                
        logger.debug(f"Executed automation: {rule.name} on {len(target_devices)} devices")
    
    def set_device_state(
        self,
        device_id: str,
        state: str,
        source: str = "user",
    ) -> bool:
        """
        Set a device's state.
        
        Args:
            device_id: Device to update
            state: New state value
            source: Who initiated the change
            
        Returns:
            True if successful
        """
        if device_id not in self.devices:
            return False
        
        device = self.devices[device_id]
        old_state = device.state
        device.state = state
        
        # Publish state change
        msg = Message(
            message_type=MessageType.COMMAND,
            source_id=source,
            payload={
                "device_id": device_id,
                "state": state,
                "old_state": old_state,
            },
        )
        topic = f"devices/{device.room}/{device.device_type}/state"
        self.broker.publish(topic, msg, retain=True)
        
        self._log_event("state_change", device.room, {
            "device_id": device_id,
            "old_state": old_state,
            "new_state": state,
            "source": source,
        })
        
        logger.info(f"Device {device_id}: {old_state} -> {state}")
        return True
    
    def toggle_device(self, device_id: str) -> Optional[str]:
        """Toggle a device on/off. Returns new state."""
        if device_id not in self.devices:
            return None
        
        device = self.devices[device_id]
        new_state = "off" if device.state == "on" else "on"
        self.set_device_state(device_id, new_state)
        return new_state
    
    def _log_event(self, event_type: str, room: str, data: Dict[str, Any]):
        """Log an event for UI display."""
        entry = {
            "time": time.time(),
            "type": event_type,
            "room": room,
            "data": data,
        }
        self.event_log.append(entry)
        
        # Trim log
        if len(self.event_log) > self._max_log_size:
            self.event_log = self.event_log[-self._max_log_size:]
    
    def _on_device_event(self, topic: str, message: Message):
        """Handle device events from broker."""
        logger.debug(f"Received on {topic}: {message.payload}")
    
    def get_device_states(self) -> Dict[str, Dict[str, Any]]:
        """Get all device states."""
        return {
            dev_id: dev.to_dict()
            for dev_id, dev in self.devices.items()
        }
    
    def get_room_summary(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get devices grouped by room."""
        summary = {}
        for room, device_ids in self.rooms.items():
            summary[room] = [
                self.devices[dev_id].to_dict()
                for dev_id in device_ids
                if dev_id in self.devices
            ]
        return summary
    
    def get_recent_events(self, count: int = 10) -> List[Dict[str, Any]]:
        """Get recent events."""
        return self.event_log[-count:]
    
    def print_event_log(self, count: int = 20):
        """Print recent events to terminal."""
        print("\n" + "="*60)
        print("📋 IoT Event Log (most recent events)")
        print("="*60)
        
        if not self.event_log:
            print("  No events recorded yet")
            print("="*60 + "\n")
            return
        
        events = self.event_log[-count:]
        for event in reversed(events):
            import time as time_module
            timestamp = event.get('time', 0)
            elapsed = time.time() - timestamp
            event_type = event.get('type', 'unknown')
            room = event.get('room', '')
            data = event.get('data', {})
            
            # Format time
            if elapsed < 60:
                time_str = f"{int(elapsed)}s ago"
            elif elapsed < 3600:
                time_str = f"{int(elapsed/60)}m ago"
            else:
                time_str = f"{int(elapsed/3600)}h ago"
            
            # Format event
            if event_type == "motion_detected":
                distance = data.get('distance', 0)
                print(f"  [{time_str:>8}] 🔴 Motion in {room} ({distance:.1f}m)")
            elif event_type == "automation_triggered":
                rule = data.get('rule', 'unknown')
                device = data.get('device', '')
                new_state = data.get('new_state', '')
                print(f"  [{time_str:>8}] ⚡ {rule}: {device} → {new_state}")
            elif event_type == "room_enter":
                print(f"  [{time_str:>8}] 🚪 Entered {room}")
            elif event_type == "state_change":
                device_id = data.get('device_id', '')
                old_state = data.get('old_state', '')
                new_state = data.get('new_state', '')
                source = data.get('source', 'unknown')
                print(f"  [{time_str:>8}] 🔧 {device_id}: {old_state} → {new_state} ({source})")
            else:
                print(f"  [{time_str:>8}] {event_type} in {room}")
        
        print("="*60)
        print(f"Total events: {len(self.event_log)} | Showing: {len(events)}")
        print("="*60 + "\n")
    
    @property
    def stats(self) -> Dict[str, Any]:
        """Get bridge statistics."""
        triggered_sensors = sum(
            1 for d in self.devices.values()
            if d.device_type == "motion_sensor" and d.is_triggered
        )
        
        lights_on = sum(
            1 for d in self.devices.values()
            if d.device_type == "smart_light" and d.state == "on"
        )
        
        return {
            "total_devices": len(self.devices),
            "total_rooms": len(self.rooms),
            "motion_events": self._motion_events,
            "automation_triggers": self._automation_triggers,
            "triggered_sensors": triggered_sensors,
            "lights_on": lights_on,
            "current_room": self._last_agent_room,
            "automation_rules": len(self.automation_rules),
        }
