"""
VESPER Smart Home IoT System.

Manages IoT device placement and updates in the Habitat simulation.
Devices are placed based on actual room layout from the scene.
"""

import random
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from vesper.core.event_bus import EventBus, Event
from vesper.devices.manager import DeviceManager

# Try to import MQTT
try:
    from vesper.network.mqtt import MQTTTransport, MQTTConfig, MQTTEventBridge
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False


# ============================================================================
# Scene Room Definitions
# ============================================================================

# Default room layouts for ReplicaCAD apartments
# These are approximate centers and can be refined per-scene
SCENE_ROOMS = {
    "apt_0": {
        "living_room": {"center": (0.0, 0.0, 0.0), "size": (4.0, 4.0)},
        "kitchen": {"center": (3.0, 0.0, 3.0), "size": (3.0, 3.0)},
        "bedroom": {"center": (-3.0, 0.0, 0.0), "size": (3.0, 3.0)},
        "bathroom": {"center": (-3.0, 0.0, -3.0), "size": (2.0, 2.0)},
        "hallway": {"center": (0.0, 0.0, 2.0), "size": (2.0, 5.0)},
        "doors": [
            {"name": "Front Door", "pos": (0.0, 0.0, 5.0), "type": "exterior"},
            {"name": "Bedroom Door", "pos": (-2.0, 0.0, 0.0), "type": "interior"},
            {"name": "Bathroom Door", "pos": (-2.0, 0.0, -2.0), "type": "interior"},
        ],
    },
    "apt_1": {
        "living_room": {"center": (1.0, 0.0, 1.0), "size": (5.0, 5.0)},
        "kitchen": {"center": (4.0, 0.0, 4.0), "size": (3.0, 3.0)},
        "bedroom": {"center": (-4.0, 0.0, 1.0), "size": (4.0, 4.0)},
        "bathroom": {"center": (-3.0, 0.0, -4.0), "size": (2.5, 2.5)},
        "hallway": {"center": (0.0, 0.0, 4.0), "size": (2.0, 6.0)},
        "doors": [
            {"name": "Front Door", "pos": (0.0, 0.0, 7.0), "type": "exterior"},
            {"name": "Bedroom Door", "pos": (-2.5, 0.0, 1.0), "type": "interior"},
            {"name": "Bathroom Door", "pos": (-2.0, 0.0, -3.0), "type": "interior"},
        ],
    },
    "default": {
        "living_room": {"center": (0.0, 0.0, 0.0), "size": (5.0, 5.0)},
        "kitchen": {"center": (4.0, 0.0, 3.0), "size": (3.0, 3.0)},
        "bedroom": {"center": (-4.0, 0.0, 1.0), "size": (4.0, 4.0)},
        "bathroom": {"center": (-3.0, 0.0, -3.0), "size": (2.5, 2.5)},
        "hallway": {"center": (0.0, 0.0, 3.0), "size": (2.0, 6.0)},
        "doors": [
            {"name": "Front Door", "pos": (0.0, 0.0, 6.0), "type": "exterior"},
            {"name": "Bedroom Door", "pos": (-2.5, 0.0, 1.0), "type": "interior"},
            {"name": "Bathroom Door", "pos": (-2.0, 0.0, -2.0), "type": "interior"},
            {"name": "Kitchen Door", "pos": (2.5, 0.0, 2.0), "type": "interior"},
        ],
    },
}


class SmartHomeIoT:
    """
    Smart home IoT system with properly placed devices.
    
    Devices are placed based on room layout:
    - Motion sensors in each room (ceiling mounted)
    - Contact sensors on all doors (door + frame)
    - Light sensors in main rooms
    """
    
    def __init__(
        self,
        event_bus: EventBus,
        scene_name: str = "apt_0",
        use_mqtt: bool = False,
        mqtt_host: str = "localhost",
        mqtt_port: int = 1883,
    ):
        self.event_bus = event_bus
        self.scene_name = scene_name
        self.device_manager = DeviceManager(event_bus=event_bus)
        
        # Get room layout
        self.rooms = SCENE_ROOMS.get(scene_name, SCENE_ROOMS["default"])
        
        # MQTT bridge
        self.mqtt_bridge: Optional[MQTTEventBridge] = None
        if use_mqtt and MQTT_AVAILABLE:
            self._setup_mqtt(mqtt_host, mqtt_port)
        
        # Device tracking
        self.motion_sensors: Dict[str, Any] = {}
        self.door_sensors: Dict[str, Any] = {}
        self.contact_sensors: Dict[str, Any] = {}
        self.light_sensors: Dict[str, Any] = {}
        
        # Event log
        self.event_log: List[str] = []
        self.max_log_entries = 10
        
        self.event_bus.subscribe("*", self._on_event)
    
    def _setup_mqtt(self, host: str, port: int):
        """Setup MQTT connection."""
        try:
            mqtt_config = MQTTConfig(
                broker_host=host,
                broker_port=port,
                topic_prefix="vesper/smarthome",
            )
            self.mqtt_bridge = MQTTEventBridge(self.event_bus, mqtt_config)
            if self.mqtt_bridge.start():
                print(f"[IoT] MQTT connected to {host}:{port}")
            else:
                self.mqtt_bridge = None
        except Exception as e:
            print(f"[IoT] MQTT error: {e}")
    
    def setup_devices(self):
        """Create and place all IoT devices."""
        print(f"[IoT] Setting up devices for {self.scene_name}...")
        
        self._setup_motion_sensors()
        self._setup_door_sensors()
        self._setup_light_sensors()
        
        print(f"[IoT] Created {self.device_manager.device_count} devices")
        self._print_summary()
    
    def _setup_motion_sensors(self):
        """Place motion sensors in each room."""
        room_names = ["living_room", "kitchen", "bedroom", "bathroom", "hallway"]
        
        for room_name in room_names:
            if room_name not in self.rooms:
                continue
            
            room = self.rooms[room_name]
            center = room["center"]
            pos = (center[0], 2.4, center[2])
            
            sensor = self.device_manager.create_device(
                "motion_sensor",
                position=pos,
                name=f"{room_name.replace('_', ' ').title()} Motion",
                detection_radius=4.0,
                cooldown=3.0,
            )
            self.motion_sensors[room_name] = sensor
    
    def _setup_door_sensors(self):
        """Place contact sensors on all doors."""
        doors = self.rooms.get("doors", [])
        
        for door_info in doors:
            pos = door_info["pos"]
            name = door_info["name"]
            
            door = self.device_manager.create_device(
                "smart_door",
                position=pos,
                name=name,
            )
            self.door_sensors[name] = door
            
            contact_pos = (pos[0] + 0.1, pos[1] + 0.9, pos[2])
            contact = self.device_manager.create_device(
                "contact_sensor",
                position=contact_pos,
                name=f"{name} Contact",
            )
            self.contact_sensors[name] = contact
    
    def _setup_light_sensors(self):
        """Place light sensors in main rooms."""
        main_rooms = ["living_room", "kitchen", "bedroom"]
        
        for room_name in main_rooms:
            if room_name not in self.rooms:
                continue
            
            room = self.rooms[room_name]
            center = room["center"]
            pos = (center[0] + 1.0, 1.5, center[2])
            
            sensor = self.device_manager.create_device(
                "light_sensor",
                position=pos,
                name=f"{room_name.replace('_', ' ').title()} Light",
            )
            self.light_sensors[room_name] = sensor
    
    def _print_summary(self):
        """Print device summary."""
        print(f"  Motion Sensors: {len(self.motion_sensors)}")
        print(f"  Door Sensors: {len(self.door_sensors)}")
        print(f"  Contact Sensors: {len(self.contact_sensors)}")
    
    def update(self, agent_position: np.ndarray, dt: float = 0.033):
        """Update IoT devices based on agent position."""
        pos = tuple(agent_position)
        triggered = self.device_manager.check_motion("humanoid", pos)
        self.device_manager.update_all(dt)
        self.event_bus.process_events()
        return triggered
    
    def toggle_door(self, door_name: str = None):
        """Toggle a door."""
        doors = self.device_manager.get_devices_by_type("smart_door")
        if doors:
            if door_name:
                for door in doors:
                    if door_name.lower() in door.name.lower():
                        door.open() if not door.is_open else door.close()
                        return
            door = doors[0]
            door.open() if not door.is_open else door.close()
    
    def get_triggered_rooms(self) -> List[str]:
        """Get rooms with active motion detection."""
        triggered = []
        for room_name, sensor in self.motion_sensors.items():
            if sensor.motion_detected:
                triggered.append(room_name)
        return triggered
    
    def get_door_states(self) -> Dict[str, bool]:
        """Get door open/closed states."""
        return {name: door.is_open for name, door in self.door_sensors.items()}
    
    def get_room_info(self) -> str:
        """Get formatted room layout info for LLM context."""
        info_lines = ["Smart Home Layout:"]
        for room_name, room_data in self.rooms.items():
            if room_name == "doors":
                continue
            center = room_data["center"]
            info_lines.append(f"  - {room_name}: center at ({center[0]:.1f}, {center[2]:.1f})")
        
        info_lines.append("\nDoors:")
        for door in self.rooms.get("doors", []):
            pos = door["pos"]
            info_lines.append(f"  - {door['name']}: at ({pos[0]:.1f}, {pos[2]:.1f})")
        
        return "\n".join(info_lines)
    
    def get_room_location(self, room_name: str) -> Optional[Tuple[float, float, float]]:
        """Get the center coordinates of a room."""
        room_key = room_name.lower().replace(" ", "_")
        if room_key in self.rooms:
            center = self.rooms[room_key]["center"]
            return (center[0], center[1], center[2])
        return None
    
    def get_door_location(self, door_name: str) -> Optional[Tuple[float, float, float]]:
        """Get the coordinates of a door."""
        for door in self.rooms.get("doors", []):
            if door_name.lower() in door["name"].lower():
                pos = door["pos"]
                return (pos[0], pos[1], pos[2])
        return None
    
    def _on_event(self, event: Event):
        """Handle IoT events for logging."""
        entry = f"{event.event_type}: {event.source_id[:12]}"
        self.event_log.append(entry)
        if len(self.event_log) > self.max_log_entries:
            self.event_log.pop(0)
    
    def get_device_states(self) -> List[Dict[str, Any]]:
        """Get all device states for UI display."""
        return [device.get_state() for device in self.device_manager._devices.values()]
    
    def close(self):
        """Cleanup resources."""
        if self.mqtt_bridge:
            self.mqtt_bridge.stop()
