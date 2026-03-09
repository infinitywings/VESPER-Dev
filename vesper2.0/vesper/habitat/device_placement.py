"""
Device placement utilities for positioning IoT devices in scenes.

Provides utilities for placing cameras, motion sensors, and other
IoT devices in 3D scenes based on room layouts.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from vesper.devices.base import IoTDevice
from vesper.core.environment import Environment

logger = logging.getLogger(__name__)


@dataclass
class PlacementConfig:
    """Configuration for device placement."""
    auto_place: bool = True
    motion_sensor_height: float = 2.5
    contact_sensor_height: float = 1.0
    light_sensor_height: float = 2.0
    camera_height: float = 2.5
    camera_wall_offset: float = 0.3  # Distance from wall


@dataclass
class PlacementRule:
    """Rule for placing a device type."""
    device_type: str
    room_types: List[str]
    height: float
    position_type: str  # "center", "corner", "door", "wall"


class DevicePlacer:
    """
    Utility for placing IoT devices in 3D scenes.
    
    Example:
        placer = DevicePlacer(scene_manager, environment)
        placer.auto_place_devices()
    """
    
    DEFAULT_RULES = [
        PlacementRule("motion_sensor", ["living_room", "kitchen", "hallway"], 2.5, "corner"),
        PlacementRule("security_camera", ["living_room", "kitchen", "entrance", "hallway"], 2.5, "corner"),
        PlacementRule("contact_sensor", ["bedroom", "bathroom"], 1.0, "door"),
        PlacementRule("smart_door", ["entrance", "hallway"], 1.0, "door"),
        PlacementRule("light_sensor", ["living_room", "bedroom"], 2.0, "wall"),
    ]
    
    def __init__(
        self,
        scene_manager: Any = None,
        environment: Optional[Environment] = None,
        config: Optional[PlacementConfig] = None,
    ):
        self.scene_manager = scene_manager
        self.environment = environment
        self.config = config or PlacementConfig()
        self._placements: List[Dict[str, Any]] = []
    
    @property
    def placements(self) -> List[Dict[str, Any]]:
        return self._placements.copy()
    
    def compute_placements(
        self,
        rules: Optional[List[PlacementRule]] = None,
    ) -> List[Dict[str, Any]]:
        """Compute device placements based on rules."""
        rules = rules or self.DEFAULT_RULES
        self._placements = []
        
        if not self.scene_manager or not self.scene_manager.is_loaded:
            logger.warning("Scene not loaded, using default placements")
            return self._get_default_placements()
        
        for rule in rules:
            rooms = []
            for room_type in rule.room_types:
                rooms.extend(self.scene_manager.get_room_by_type(room_type))
            
            for room in rooms:
                position = self._compute_position(room, rule)
                orientation = self._compute_orientation(position, room.center)
                self._placements.append({
                    "device_type": rule.device_type,
                    "room_id": room.room_id,
                    "room_type": room.room_type,
                    "position": position,
                    "orientation": orientation,
                })
        
        return self._placements
    
    def _compute_position(self, room: Any, rule: PlacementRule) -> Tuple[float, float, float]:
        """Compute position within a room with proper orientation support."""
        wall_offset = 0.3  # Keep devices away from walls
        
        if rule.position_type == "center":
            return (room.center[0], rule.height, room.center[2])
        elif rule.position_type == "corner":
            # Place in corner with offset for better viewing angle
            corner_x = room.bounds_max[0] - wall_offset
            corner_z = room.bounds_max[2] - wall_offset
            return (corner_x, rule.height, corner_z)
        elif rule.position_type == "door":
            return (room.bounds_min[0], rule.height, room.center[2])
        else:  # wall
            return (room.center[0], rule.height, room.bounds_max[2] - wall_offset)
    
    def _compute_orientation(
        self,
        position: Tuple[float, float, float],
        room_center: Tuple[float, float, float],
    ) -> Tuple[float, float]:
        """
        Compute device orientation (pan, tilt) to point toward room center.
        
        Args:
            position: Device position
            room_center: Room center position
            
        Returns:
            (pan, tilt) in radians
        """
        # Calculate direction to room center
        dx = room_center[0] - position[0]
        dz = room_center[2] - position[2]
        dy = room_center[1] - position[1]
        
        # Pan: horizontal angle (0 = +Z axis)
        pan = math.atan2(dx, dz)
        
        # Tilt: vertical angle to look at floor level
        horizontal_dist = math.sqrt(dx**2 + dz**2)
        if horizontal_dist > 0.1:
            tilt = -math.atan2(position[1] - room_center[1], horizontal_dist)
        else:
            tilt = -0.52  # Default ~30 degrees down
        
        return (pan, tilt)
    
    def _get_default_placements(self) -> List[Dict[str, Any]]:
        """Get default placements when no scene is loaded."""
        return [
            {"device_type": "motion_sensor", "position": (0, 2.5, 0)},
            {"device_type": "smart_door", "position": (3, 1.0, 0)},
            {"device_type": "contact_sensor", "position": (3, 1.0, 0)},
            {"device_type": "light_sensor", "position": (-2, 2.0, 2)},
        ]
    
    def apply_placements(self) -> int:
        """Apply computed placements to the environment."""
        if not self.environment:
            logger.warning("No environment configured")
            return 0
        
        count = 0
        for placement in self._placements:
            device = self._create_device(placement)
            if device:
                self.environment.register_device(device)
                count += 1
        
        logger.info(f"Placed {count} devices in environment")
        return count
    
    def _create_device(self, placement: Dict[str, Any]) -> Optional[IoTDevice]:
        """Create a device from placement info."""
        from vesper.devices import MotionSensor, ContactSensor, SmartDoor, LightSensor
        from vesper.devices.security_camera import SecurityCamera, SecurityCameraConfig
        
        device_type = placement["device_type"]
        position = placement["position"]
        orientation = placement.get("orientation", (0, -0.52))  # Default orientation
        room_id = placement.get("room_id", "unknown")
        
        if device_type == "security_camera":
            # Create camera with proper orientation
            config = SecurityCameraConfig(
                device_id=f"cam_{room_id}",
                name=f"Camera {room_id}",
                room=room_id,
                position=position,
                pan=orientation[0],
                tilt=orientation[1],
            )
            return SecurityCamera(config, event_bus=self.environment.event_bus if self.environment else None)
        
        device_classes = {
            "motion_sensor": MotionSensor,
            "contact_sensor": ContactSensor,
            "smart_door": SmartDoor,
            "light_sensor": LightSensor,
        }
        
        cls = device_classes.get(device_type)
        if cls:
            return cls(location=position, event_bus=self.environment.event_bus if self.environment else None)
        return None
