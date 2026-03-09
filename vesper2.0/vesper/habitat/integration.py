"""
Habitat 3.0 Integration Bridge.

Connects the Habitat 3.0 simulation (via habitat-lab) to VESPER IoT devices.
This bridge enables:
- IoT sensors to detect humanoid agent positions
- Smart devices to interact with articulated objects (doors, drawers)
- LLM agents to control humanoid avatars through the simulation
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable
from enum import Enum

import numpy as np

from vesper.core.event_bus import EventBus, Event, EventPriority
from vesper.core.environment import Environment
from vesper.devices.base import IoTDevice
from vesper.devices.motion_sensor import MotionSensor
from vesper.devices.contact_sensor import ContactSensor
from vesper.devices.smart_door import SmartDoor
from vesper.devices.light_sensor import LightSensor

logger = logging.getLogger(__name__)


# Try to import habitat components
try:
    import habitat
    from habitat.config.default_structured_configs import (
        HabitatConfigPlugin,
        register_hydra_plugin,
    )
    from omegaconf import DictConfig, OmegaConf
    HABITAT_LAB_AVAILABLE = True
except ImportError:
    HABITAT_LAB_AVAILABLE = False
    logger.warning("habitat-lab not installed. Install from habitat-lab-official/")


class ArticulatedObjectType(str, Enum):
    """Types of articulated objects in the scene."""
    DOOR = "door"
    DRAWER = "drawer"
    FRIDGE = "fridge"
    CABINET = "cabinet"


@dataclass
class VesperAgent:
    """
    A VESPER agent that controls a humanoid in Habitat 3.0.
    
    Bridges the SmartAgent (LLM-based) with Habitat humanoid control.
    """
    agent_id: str
    name: str = "Agent"
    position: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: float = 0.0  # Degrees
    
    # Humanoid state
    is_humanoid: bool = True
    humanoid_type: str = "female_0"  # From hab3_bench_assets
    
    # Control state
    current_action: Optional[str] = None
    target_position: Optional[Tuple[float, float, float]] = None
    is_moving: bool = False
    
    # Statistics
    total_distance: float = 0.0
    actions_taken: int = 0
    
    def update_position(self, new_position: Tuple[float, float, float]) -> float:
        """Update position and return distance traveled."""
        distance = np.linalg.norm(np.array(new_position) - np.array(self.position))
        self.total_distance += distance
        self.position = new_position
        return distance


@dataclass
class IoTDevicePlacement:
    """
    Placement information for an IoT device in the Habitat scene.
    """
    device_id: str
    device_type: str
    position: Tuple[float, float, float]
    rotation: float = 0.0
    
    # Link to articulated objects
    linked_object_id: Optional[str] = None  # e.g., link door sensor to door object
    
    # Room assignment
    room: Optional[str] = None


class HabitatIntegration:
    """
    Main integration bridge between VESPER and Habitat 3.0.
    
    Features:
    - Automatic IoT device placement based on scene structure
    - Real-time sensor updates from agent positions
    - Articulated object control (doors, drawers)
    - LLM agent integration with humanoid control
    
    Example:
        from vesper.habitat.integration import HabitatIntegration
        from vesper.core.event_bus import EventBus
        
        event_bus = EventBus()
        integration = HabitatIntegration(event_bus=event_bus)
        
        # Initialize with a scene
        integration.initialize_scene("data/replica_cad/scenes/v3_sc0_staging_00.scene_instance.json")
        
        # Add agents
        agent = integration.add_agent("agent_1", position=(0, 0, 0))
        
        # Run simulation loop
        while True:
            integration.step()
            event_bus.process_events()
    """
    
    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        environment: Optional[Environment] = None,
    ):
        """
        Initialize the Habitat integration.
        
        Args:
            event_bus: VESPER event bus for IoT communication
            environment: VESPER environment for device management
        """
        self._event_bus = event_bus or EventBus()
        self._environment = environment
        
        # Agents
        self._agents: Dict[str, VesperAgent] = {}
        
        # IoT devices
        self._devices: Dict[str, IoTDevice] = {}
        self._device_placements: Dict[str, IoTDevicePlacement] = {}
        
        # Scene state
        self._scene_path: Optional[str] = None
        self._is_initialized: bool = False
        
        # Articulated objects from scene
        self._articulated_objects: Dict[str, Dict[str, Any]] = {}
        
        # Simulation state
        self._tick: int = 0
        self._sim_time: float = 0.0
        self._timestep: float = 1.0 / 30.0  # 30 Hz
        
        # Callbacks
        self._on_motion_callbacks: List[Callable] = []
        self._on_door_callbacks: List[Callable] = []
        
        # Statistics
        self._stats = {
            "ticks": 0,
            "motion_events": 0,
            "door_events": 0,
            "llm_calls": 0,
        }
    
    @property
    def is_initialized(self) -> bool:
        return self._is_initialized
    
    @property
    def event_bus(self) -> EventBus:
        return self._event_bus
    
    @property
    def agents(self) -> Dict[str, VesperAgent]:
        return self._agents.copy()
    
    @property
    def devices(self) -> Dict[str, IoTDevice]:
        return self._devices.copy()
    
    @property
    def stats(self) -> Dict[str, Any]:
        return self._stats.copy()
    
    def initialize_scene(
        self,
        scene_path: str,
        auto_place_devices: bool = True,
    ) -> bool:
        """
        Initialize with a Habitat 3.0 scene.
        
        Args:
            scene_path: Path to scene file (.glb or .scene_instance.json)
            auto_place_devices: Automatically place IoT devices based on scene
            
        Returns:
            True if initialization successful
        """
        self._scene_path = scene_path
        
        # Parse scene and find articulated objects
        if Path(scene_path).suffix == ".json":
            self._parse_scene_instance(scene_path)
        
        # Auto-place IoT devices if requested
        if auto_place_devices:
            self._auto_place_devices()
        
        self._is_initialized = True
        logger.info(f"Initialized integration with scene: {scene_path}")
        logger.info(f"  Devices: {len(self._devices)}")
        logger.info(f"  Articulated objects: {len(self._articulated_objects)}")
        
        return True
    
    def _parse_scene_instance(self, scene_path: str) -> None:
        """Parse a scene instance JSON to find articulated objects."""
        import json
        
        try:
            with open(scene_path, 'r') as f:
                scene_data = json.load(f)
            
            # Look for articulated object instances
            for obj in scene_data.get("object_instances", []):
                obj_template = obj.get("template_name", "")
                motion_type = obj.get("motion_type", "")
                
                # Check if it's an articulated object (door, drawer, etc.)
                if motion_type == "DYNAMIC" or "door" in obj_template.lower():
                    obj_id = obj.get("name", obj_template)
                    self._articulated_objects[obj_id] = {
                        "template": obj_template,
                        "position": obj.get("translation", [0, 0, 0]),
                        "rotation": obj.get("rotation", [1, 0, 0, 0]),
                        "type": self._infer_object_type(obj_template),
                    }
                    
        except Exception as e:
            logger.warning(f"Failed to parse scene instance: {e}")
    
    def _infer_object_type(self, template_name: str) -> ArticulatedObjectType:
        """Infer the type of articulated object from its template name."""
        name_lower = template_name.lower()
        if "door" in name_lower:
            return ArticulatedObjectType.DOOR
        elif "drawer" in name_lower:
            return ArticulatedObjectType.DRAWER
        elif "fridge" in name_lower:
            return ArticulatedObjectType.FRIDGE
        elif "cabinet" in name_lower:
            return ArticulatedObjectType.CABINET
        return ArticulatedObjectType.DOOR  # Default
    
    def _auto_place_devices(self) -> None:
        """
        Automatically place IoT devices based on scene structure.
        
        Places:
        - Motion sensors in key locations
        - Contact sensors on doors
        - Light sensors in rooms
        """
        # Place contact sensors on doors
        for obj_id, obj_info in self._articulated_objects.items():
            if obj_info["type"] == ArticulatedObjectType.DOOR:
                position = tuple(obj_info["position"])
                
                # Add contact sensor
                sensor = ContactSensor(
                    location=position,
                    device_id=f"contact_{obj_id}",
                    event_bus=self._event_bus,
                    name=f"Contact Sensor ({obj_id})",
                )
                self._devices[sensor.device_id] = sensor
                self._device_placements[sensor.device_id] = IoTDevicePlacement(
                    device_id=sensor.device_id,
                    device_type="contact_sensor",
                    position=position,
                    linked_object_id=obj_id,
                )
                
                # Add smart door
                door = SmartDoor(
                    location=position,
                    device_id=f"door_{obj_id}",
                    event_bus=self._event_bus,
                    name=f"Smart Door ({obj_id})",
                )
                self._devices[door.device_id] = door
                self._device_placements[door.device_id] = IoTDevicePlacement(
                    device_id=door.device_id,
                    device_type="smart_door",
                    position=position,
                    linked_object_id=obj_id,
                )
        
        # Place motion sensors at strategic locations
        motion_positions = [
            (0.0, 0.5, 0.0),    # Center entrance
            (3.0, 0.5, 0.0),    # Room 1
            (-3.0, 0.5, 0.0),   # Room 2
            (0.0, 0.5, 3.0),    # Kitchen
            (0.0, 0.5, -3.0),   # Living room
        ]
        
        for i, pos in enumerate(motion_positions):
            sensor = MotionSensor(
                location=pos,
                detection_radius=4.0,
                cooldown=1.0,
                device_id=f"motion_{i}",
                event_bus=self._event_bus,
                name=f"Motion Sensor {i}",
            )
            self._devices[sensor.device_id] = sensor
            self._device_placements[sensor.device_id] = IoTDevicePlacement(
                device_id=sensor.device_id,
                device_type="motion_sensor",
                position=pos,
            )
        
        # Place light sensors
        light_positions = [
            (0.0, 2.0, 0.0),  # Center ceiling
            (5.0, 2.0, 0.0),  # Room 1 ceiling
        ]
        
        for i, pos in enumerate(light_positions):
            sensor = LightSensor(
                location=pos,
                device_id=f"light_{i}",
                event_bus=self._event_bus,
                name=f"Light Sensor {i}",
            )
            self._devices[sensor.device_id] = sensor
            self._device_placements[sensor.device_id] = IoTDevicePlacement(
                device_id=sensor.device_id,
                device_type="light_sensor",
                position=pos,
            )
        
        logger.info(f"Auto-placed {len(self._devices)} IoT devices")
    
    def add_device(
        self,
        device: IoTDevice,
        linked_object_id: Optional[str] = None,
        room: Optional[str] = None,
    ) -> str:
        """
        Add a custom IoT device to the integration.
        
        Args:
            device: The IoT device to add
            linked_object_id: Link to an articulated object
            room: Room name for the device
            
        Returns:
            Device ID
        """
        self._devices[device.device_id] = device
        self._device_placements[device.device_id] = IoTDevicePlacement(
            device_id=device.device_id,
            device_type=device.device_type,
            position=device.location,
            linked_object_id=linked_object_id,
            room=room,
        )
        return device.device_id
    
    def add_agent(
        self,
        agent_id: str,
        name: str = "Agent",
        position: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        is_humanoid: bool = True,
    ) -> VesperAgent:
        """
        Add a VESPER agent (humanoid) to the simulation.
        
        Args:
            agent_id: Unique identifier
            name: Human-readable name
            position: Initial position
            is_humanoid: Whether to use humanoid model
            
        Returns:
            The created VesperAgent
        """
        agent = VesperAgent(
            agent_id=agent_id,
            name=name,
            position=position,
            is_humanoid=is_humanoid,
        )
        self._agents[agent_id] = agent
        
        logger.info(f"Added agent '{name}' ({agent_id}) at {position}")
        
        return agent
    
    def update_agent_position(
        self,
        agent_id: str,
        position: Tuple[float, float, float],
    ) -> bool:
        """
        Update an agent's position (called from Habitat simulation).
        
        This triggers IoT sensor updates (motion detection, etc.)
        """
        if agent_id not in self._agents:
            return False
        
        agent = self._agents[agent_id]
        old_position = agent.position
        distance = agent.update_position(position)
        
        # Check motion sensors
        for device_id, device in self._devices.items():
            if isinstance(device, MotionSensor):
                if device.detect_agent(agent_id, position):
                    self._stats["motion_events"] += 1
                    for callback in self._on_motion_callbacks:
                        callback(device, agent, position)
        
        return True
    
    def operate_door(
        self,
        door_id: str,
        operation: str,  # "open" or "close"
        agent_id: Optional[str] = None,
    ) -> bool:
        """
        Operate a smart door.
        
        Args:
            door_id: ID of the door device
            operation: "open" or "close"
            agent_id: Agent performing the action
            
        Returns:
            True if operation successful
        """
        device = self._devices.get(door_id)
        if not isinstance(device, SmartDoor):
            # Try with prefix
            device = self._devices.get(f"door_{door_id}")
        
        if not isinstance(device, SmartDoor):
            logger.warning(f"Door not found: {door_id}")
            return False
        
        if operation == "open":
            success = device.open()
        elif operation == "close":
            success = device.close()
        else:
            logger.warning(f"Unknown door operation: {operation}")
            return False
        
        if success:
            self._stats["door_events"] += 1
            for callback in self._on_door_callbacks:
                callback(device, operation, agent_id)
            
            # Update linked contact sensor
            placement = self._device_placements.get(device.device_id)
            if placement and placement.linked_object_id:
                contact_id = f"contact_{placement.linked_object_id}"
                contact = self._devices.get(contact_id)
                if isinstance(contact, ContactSensor):
                    if operation == "open":
                        contact.set_state(is_open=True)
                    else:
                        contact.set_state(is_open=False)
        
        return success
    
    def step(self, dt: Optional[float] = None) -> Dict[str, Any]:
        """
        Step the integration forward.
        
        Args:
            dt: Time delta (uses default timestep if None)
            
        Returns:
            Dictionary of events and state changes
        """
        dt = dt or self._timestep
        self._tick += 1
        self._sim_time += dt
        self._stats["ticks"] += 1
        
        # Update all devices
        for device in self._devices.values():
            device.update(dt)
        
        # Process events
        events_processed = self._event_bus.process_events()
        
        return {
            "tick": self._tick,
            "sim_time": self._sim_time,
            "events_processed": events_processed,
        }
    
    def get_sensor_readings(self) -> Dict[str, Dict[str, Any]]:
        """Get current readings from all sensors."""
        readings = {}
        for device_id, device in self._devices.items():
            readings[device_id] = device.get_state()
        return readings
    
    def get_scene_state(self) -> Dict[str, Any]:
        """Get complete scene state for LLM context."""
        return {
            "tick": self._tick,
            "sim_time": self._sim_time,
            "agents": {
                agent_id: {
                    "name": agent.name,
                    "position": agent.position,
                    "is_moving": agent.is_moving,
                }
                for agent_id, agent in self._agents.items()
            },
            "devices": {
                device_id: device.get_state()
                for device_id, device in self._devices.items()
            },
            "pending_events": self._event_bus.pending_count,
        }
    
    def on_motion_detected(self, callback: Callable) -> None:
        """Register callback for motion detection events."""
        self._on_motion_callbacks.append(callback)
    
    def on_door_operated(self, callback: Callable) -> None:
        """Register callback for door operation events."""
        self._on_door_callbacks.append(callback)
    
    def reset(self) -> None:
        """Reset the integration state."""
        self._tick = 0
        self._sim_time = 0.0
        self._stats = {
            "ticks": 0,
            "motion_events": 0,
            "door_events": 0,
            "llm_calls": 0,
        }
        
        for agent in self._agents.values():
            agent.position = (0.0, 0.0, 0.0)
            agent.is_moving = False
            agent.total_distance = 0.0
            agent.actions_taken = 0
        
        for device in self._devices.values():
            device.reset()
        
        self._event_bus.clear()
    
    def close(self) -> None:
        """Clean up resources."""
        self._devices.clear()
        self._agents.clear()
        self._articulated_objects.clear()
        self._is_initialized = False


# Convenience function for quick setup
def create_integration(
    scene_path: Optional[str] = None,
    auto_place_devices: bool = True,
) -> HabitatIntegration:
    """
    Create and initialize a HabitatIntegration instance.
    
    Args:
        scene_path: Optional path to scene file
        auto_place_devices: Auto-place IoT devices
        
    Returns:
        Initialized HabitatIntegration
    """
    event_bus = EventBus()
    integration = HabitatIntegration(event_bus=event_bus)
    
    if scene_path:
        integration.initialize_scene(
            scene_path=scene_path,
            auto_place_devices=auto_place_devices,
        )
    
    return integration
