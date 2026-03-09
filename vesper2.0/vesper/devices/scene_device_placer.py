"""
Scene-aware device placement for IoT devices in 3D environments.

Handles automatic placement of cameras and motion sensors based on:
- Room layout and bounds
- Room type (kitchen, bedroom, etc.)
- Scene type (HSSD, HM3D, ReplicaCAD)
- Optimal viewing angles for security coverage
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from vesper.devices.security_camera import SecurityCamera
    from vesper.devices.motion_sensor import MotionSensor

logger = logging.getLogger(__name__)


class SceneType(Enum):
    """Known scene dataset types with different characteristics."""
    HSSD = "hssd"           # HSSD-hab scenes
    HM3D = "hm3d"           # HM3D scenes  
    REPLICA_CAD = "replica_cad"  # ReplicaCAD scenes
    HABITAT_TEST = "habitat_test"  # Test scenes
    UNKNOWN = "unknown"


class RoomType(Enum):
    """Standard room types for device placement."""
    LIVING_ROOM = "living_room"
    BEDROOM = "bedroom"
    KITCHEN = "kitchen"
    BATHROOM = "bathroom"
    HALLWAY = "hallway"
    DINING_ROOM = "dining_room"
    OFFICE = "office"
    GARAGE = "garage"
    ENTRANCE = "entrance"
    CLOSET = "closet"
    LAUNDRY = "laundry"
    OTHER = "other"


@dataclass
class RoomInfo:
    """Information about a room for device placement."""
    name: str
    room_type: RoomType
    center: Tuple[float, float, float]
    bounds: Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = None
    floor_height: float = 0.0
    ceiling_height: float = 2.8
    area: float = 0.0  # Square meters
    
    @property
    def size(self) -> Tuple[float, float, float]:
        """Get room size (width, height, depth)."""
        if self.bounds:
            min_b, max_b = self.bounds
            return (
                max_b[0] - min_b[0],
                max_b[1] - min_b[1],
                max_b[2] - min_b[2],
            )
        return (4.0, 2.8, 4.0)  # Default estimate


@dataclass
class DevicePlacementConfig:
    """Configuration for device placement in scenes."""
    
    # Camera settings
    camera_mount_height: float = 2.5  # Meters above floor
    camera_wall_offset: float = 0.3   # Distance from wall
    camera_prefer_corners: bool = True
    camera_coverage_overlap: float = 0.2  # Allow 20% FOV overlap
    
    # Motion sensor settings
    motion_sensor_height: float = 2.2  # PIR sensor height
    motion_sensor_detection_range: float = 8.0
    motion_sensor_fov: float = 110.0  # Degrees
    
    # Room-specific settings
    rooms_requiring_camera: List[str] = field(default_factory=lambda: [
        "living_room", "kitchen", "entrance", "hallway", "dining_room"
    ])
    rooms_requiring_motion_sensor: List[str] = field(default_factory=lambda: [
        "living_room", "bedroom", "kitchen", "bathroom", 
        "hallway", "entrance", "office"
    ])
    
    # Skip small rooms
    min_room_area_for_camera: float = 4.0  # sq meters
    min_room_area_for_motion: float = 2.0  # sq meters


@dataclass
class PlacedDevice:
    """A device that has been placed in the scene."""
    device_type: str
    device_id: str
    room_name: str
    position: Tuple[float, float, float]
    orientation: Tuple[float, float]  # (pan, tilt) in radians
    config: Dict[str, Any] = field(default_factory=dict)


class SceneDevicePlacer:
    """
    Handles placement of IoT devices based on scene layout.
    
    This class analyzes room layouts and computes optimal positions
    and orientations for cameras and motion sensors to achieve
    good coverage of each room.
    
    Example:
        placer = SceneDevicePlacer()
        
        # Add rooms from scene
        placer.add_room("Living Room", (0, 0, 0), RoomType.LIVING_ROOM, bounds=...)
        placer.add_room("Kitchen", (5, 0, 0), RoomType.KITCHEN, bounds=...)
        
        # Compute placements
        placements = placer.compute_device_placements()
        
        # Create actual devices
        cameras = placer.create_cameras()
        motion_sensors = placer.create_motion_sensors()
    """
    
    # Mapping from common room name patterns to RoomType
    ROOM_TYPE_PATTERNS = {
        RoomType.LIVING_ROOM: ["living", "lounge", "family", "sitting"],
        RoomType.BEDROOM: ["bedroom", "bed room", "master"],
        RoomType.KITCHEN: ["kitchen", "pantry"],
        RoomType.BATHROOM: ["bathroom", "bath", "toilet", "wc", "restroom"],
        RoomType.HALLWAY: ["hall", "corridor", "passage"],
        RoomType.DINING_ROOM: ["dining", "eating"],
        RoomType.OFFICE: ["office", "study", "work"],
        RoomType.GARAGE: ["garage", "carport"],
        RoomType.ENTRANCE: ["entrance", "entry", "foyer", "vestibule", "porch"],
        RoomType.CLOSET: ["closet", "wardrobe", "storage"],
        RoomType.LAUNDRY: ["laundry", "utility"],
    }
    
    # Optimal camera mount positions for each room type
    CAMERA_MOUNT_PREFERENCES = {
        RoomType.LIVING_ROOM: ["corner_ne", "corner_nw"],
        RoomType.BEDROOM: ["corner_ne"],
        RoomType.KITCHEN: ["corner_nw", "corner_sw"],
        RoomType.BATHROOM: [],  # Usually no camera
        RoomType.HALLWAY: ["wall_n", "wall_s"],
        RoomType.DINING_ROOM: ["corner_ne", "corner_se"],
        RoomType.OFFICE: ["corner_ne"],
        RoomType.ENTRANCE: ["wall_n", "corner_nw"],
        RoomType.OTHER: ["corner_ne"],
    }
    
    def __init__(
        self,
        config: Optional[DevicePlacementConfig] = None,
        scene_type: SceneType = SceneType.UNKNOWN,
    ):
        """
        Initialize the scene device placer.
        
        Args:
            config: Placement configuration
            scene_type: Type of scene dataset
        """
        self.config = config or DevicePlacementConfig()
        self.scene_type = scene_type
        
        self.rooms: Dict[str, RoomInfo] = {}
        self.placements: List[PlacedDevice] = []
        
        # Track placed device positions for collision avoidance
        self._camera_positions: List[Tuple[float, float, float]] = []
        self._motion_sensor_positions: List[Tuple[float, float, float]] = []
        self._room_corners: Dict[str, Dict[str, Any]] = {}  # Track corner used per room
    
    def detect_scene_type(self, scene_path: str) -> SceneType:
        """Detect the scene type from the path."""
        path_lower = scene_path.lower()
        
        if "hssd" in path_lower:
            return SceneType.HSSD
        elif "hm3d" in path_lower:
            return SceneType.HM3D
        elif "replica" in path_lower:
            return SceneType.REPLICA_CAD
        elif "habitat-test" in path_lower or "test-scenes" in path_lower:
            return SceneType.HABITAT_TEST
        
        return SceneType.UNKNOWN
    
    def classify_room_type(self, room_name: str) -> RoomType:
        """Classify a room by its name."""
        name_lower = room_name.lower()
        
        for room_type, patterns in self.ROOM_TYPE_PATTERNS.items():
            for pattern in patterns:
                if pattern in name_lower:
                    return room_type
        
        return RoomType.OTHER
    
    def add_room(
        self,
        name: str,
        center: Tuple[float, float, float],
        room_type: Optional[RoomType] = None,
        bounds: Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = None,
        floor_height: float = 0.0,
        ceiling_height: float = 2.8,
    ):
        """
        Add a room to be considered for device placement.
        
        Args:
            name: Room name (e.g., "Living Room")
            center: (x, y, z) center position
            room_type: Type of room (auto-detected if None)
            bounds: Optional bounding box
            floor_height: Floor level
            ceiling_height: Ceiling height
        """
        if room_type is None:
            room_type = self.classify_room_type(name)
        
        # Calculate area if bounds provided
        area = 0.0
        if bounds:
            min_b, max_b = bounds
            area = (max_b[0] - min_b[0]) * (max_b[2] - min_b[2])
        
        room_info = RoomInfo(
            name=name,
            room_type=room_type,
            center=center,
            bounds=bounds,
            floor_height=floor_height,
            ceiling_height=ceiling_height,
            area=area,
        )
        
        self.rooms[name] = room_info
        logger.debug(f"Added room: {name} ({room_type.value}) at {center}")
    
    def add_rooms_from_positions(
        self,
        room_positions: Dict[str, Tuple[float, float, float]],
        room_size_estimate: float = 4.0,
    ):
        """
        Add multiple rooms from a position dictionary.
        
        Args:
            room_positions: Dict of room_name -> (x, y, z) position
            room_size_estimate: Estimated room size for bounds
        """
        for name, pos in room_positions.items():
            # Create estimated bounds
            half_size = room_size_estimate / 2
            bounds = (
                (pos[0] - half_size, pos[1], pos[2] - half_size),
                (pos[0] + half_size, pos[1] + 2.8, pos[2] + half_size),
            )
            
            self.add_room(
                name=name,
                center=pos,
                bounds=bounds,
            )
    
    def compute_device_placements(self) -> List[PlacedDevice]:
        """
        Compute optimal device placements for all rooms.
        
        Camera and motion sensor are co-located at the same corner,
        both covering a 90-degree area into the room.
        
        Returns:
            List of placed devices
        """
        self.placements = []
        self._camera_positions = []
        self._motion_sensor_positions = []
        self._room_corners = {}  # Track which corner is used for each room
        
        for room_name, room_info in self.rooms.items():
            # First, select a corner for this room
            corner_data = self._select_corner_for_room(room_info)
            self._room_corners[room_name] = corner_data
            
            # Compute camera placement at this corner
            if self._should_place_camera(room_info):
                camera_placement = self._compute_camera_placement(room_info, corner_data)
                if camera_placement:
                    self.placements.append(camera_placement)
                    self._camera_positions.append(camera_placement.position)
            
            # Compute motion sensor placement at the SAME corner
            if self._should_place_motion_sensor(room_info):
                motion_placement = self._compute_motion_sensor_placement(room_info, corner_data)
                if motion_placement:
                    self.placements.append(motion_placement)
                    self._motion_sensor_positions.append(motion_placement.position)
        
        logger.info(f"Computed {len(self.placements)} device placements")
        return self.placements
    
    def _select_corner_for_room(self, room_info: RoomInfo) -> Dict[str, Any]:
        """
        Select a corner for device placement in a room.
        
        Returns a dict with corner info:
        - corner_name: "ne", "nw", "se", "sw"
        - position: (x, y, z) world position
        - pan_angle: angle (radians) to point toward room center
        - coverage_angle: 90 degrees for corner coverage
        """
        # Get preferred mount positions for this room type
        preferences = self.CAMERA_MOUNT_PREFERENCES.get(
            room_info.room_type, ["corner_ne", "corner_nw", "corner_se", "corner_sw"]
        )
        
        if not preferences:
            preferences = ["corner_ne", "corner_nw", "corner_se", "corner_sw"]
        
        # Cycle through corners for variety across rooms
        room_index = len(self._room_corners)
        mount_name = preferences[room_index % len(preferences)]
        
        # Calculate corner position
        cx, cy, cz = room_info.center
        
        if room_info.bounds:
            min_b, max_b = room_info.bounds
            half_x = (max_b[0] - min_b[0]) / 2
            half_z = (max_b[2] - min_b[2]) / 2
        else:
            half_x = 2.0
            half_z = 2.0
        
        wall_offset = 0.2  # Distance from wall
        
        # Corner positions and their pan angles to face room center
        # Pan angle is calculated to bisect the 90-degree corner (45 degrees into room)
        corner_data = {
            "corner_ne": {
                "offset": (half_x - wall_offset, half_z - wall_offset),
                "pan": math.atan2(-1, -1),  # Point toward SW (into room from NE corner) = -135°
            },
            "corner_nw": {
                "offset": (-half_x + wall_offset, half_z - wall_offset),
                "pan": math.atan2(1, -1),  # Point toward SE (into room from NW corner) = 135°
            },
            "corner_se": {
                "offset": (half_x - wall_offset, -half_z + wall_offset),
                "pan": math.atan2(-1, 1),  # Point toward NW (into room from SE corner) = -45°
            },
            "corner_sw": {
                "offset": (-half_x + wall_offset, -half_z + wall_offset),
                "pan": math.atan2(1, 1),  # Point toward NE (into room from SW corner) = 45°
            },
        }
        
        corner_info = corner_data.get(mount_name, corner_data["corner_ne"])
        offset_x, offset_z = corner_info["offset"]
        
        return {
            "corner_name": mount_name,
            "position": (cx + offset_x, cy, cz + offset_z),
            "pan_angle": corner_info["pan"],
            "coverage_angle": math.radians(90),  # 90 degree FOV coverage
        }
    
    def _should_place_camera(self, room_info: RoomInfo) -> bool:
        """Determine if a camera should be placed in this room."""
        # Check room type
        if room_info.room_type == RoomType.BATHROOM:
            return False  # Privacy - no cameras in bathrooms
        
        if room_info.room_type == RoomType.CLOSET:
            return False  # Too small
        
        # Check area
        if room_info.area > 0 and room_info.area < self.config.min_room_area_for_camera:
            return False
        
        return True
    
    def _should_place_motion_sensor(self, room_info: RoomInfo) -> bool:
        """Determine if a motion sensor should be placed in this room."""
        # Check area
        if room_info.area > 0 and room_info.area < self.config.min_room_area_for_motion:
            return False
        
        return True
    
    def _compute_camera_placement(self, room_info: RoomInfo, corner_data: Dict[str, Any]) -> Optional[PlacedDevice]:
        """
        Compute camera placement at the designated corner.
        
        Camera is placed at the corner position, looking diagonally into the room
        with 90-degree horizontal FOV to cover the corner's area.
        """
        corner_x, corner_y, corner_z = corner_data["position"]
        pan = corner_data["pan_angle"]  # radians, pointing into room
        
        # Camera position at corner, at mount height
        camera_pos = (corner_x, self.config.camera_mount_height, corner_z)
        
        # Calculate tilt to look at floor center of room
        cx, cy, cz = room_info.center
        horizontal_distance = math.sqrt((cx - corner_x)**2 + (cz - corner_z)**2)
        height_diff = self.config.camera_mount_height - cy
        
        if horizontal_distance > 0.1:
            tilt = -math.atan2(height_diff, horizontal_distance)
        else:
            tilt = -0.52  # ~-30 degrees default
        
        # Clamp tilt to reasonable range
        tilt = max(-math.pi/2, min(0.0, tilt))
        
        room_id = room_info.name.replace(' ', '_').replace('.', '_')
        
        return PlacedDevice(
            device_type="security_camera",
            device_id=f"cam_{room_id}",
            room_name=room_info.name,
            position=camera_pos,
            orientation=(pan, tilt),
            config={
                "corner": corner_data["corner_name"],
                "mount_height": self.config.camera_mount_height,
                "horizontal_fov": 90.0,  # 90 degree coverage for corner
                "vertical_fov": 60.0,
                "max_range": 15.0,
            },
        )
    
    def _compute_motion_sensor_placement(self, room_info: RoomInfo, corner_data: Dict[str, Any]) -> Optional[PlacedDevice]:
        """
        Compute motion sensor placement at the SAME corner as camera.
        
        Motion sensor is co-located with camera, both covering the 90-degree
        area from the corner into the room.
        """
        corner_x, corner_y, corner_z = corner_data["position"]
        pan = corner_data["pan_angle"]  # Same pan angle as camera
        
        # Motion sensor slightly below camera at same corner
        sensor_pos = (
            corner_x,
            self.config.motion_sensor_height,  # Usually 2.3m
            corner_z,
        )
        
        # Slight downward tilt for motion sensor
        tilt = math.radians(-15)
        
        room_id = room_info.name.replace(' ', '_').replace('.', '_')
        
        return PlacedDevice(
            device_type="motion_sensor",
            device_id=f"pir_{room_id}",
            room_name=room_info.name,
            position=sensor_pos,
            orientation=(pan, tilt),
            config={
                "corner": corner_data["corner_name"],
                "detection_range": self.config.motion_sensor_detection_range,
                "detection_angle": 90.0,  # 90 degree FOV to match camera
                "sensitivity": "medium",
            },
        )
    
    def create_cameras(self, event_bus=None) -> List["SecurityCamera"]:
        """
        Create SecurityCamera instances from computed placements.
        
        Args:
            event_bus: Optional event bus for camera events
            
        Returns:
            List of SecurityCamera instances
        """
        from vesper.devices.security_camera import SecurityCamera, SecurityCameraConfig, MountPosition
        
        cameras = []
        
        for placement in self.placements:
            if placement.device_type != "security_camera":
                continue
            
            # Get room info
            room_info = self.rooms.get(placement.room_name)
            
            config = SecurityCameraConfig(
                device_id=placement.device_id,
                name=f"Camera {placement.room_name}",
                room=placement.room_name,
                position=placement.position,
                pan=placement.orientation[0],
                tilt=placement.orientation[1],
                mount_height=placement.config.get("mount_height", 2.5),
                horizontal_fov=placement.config.get("horizontal_fov", 90.0),
                vertical_fov=placement.config.get("vertical_fov", 60.0),
                max_range=placement.config.get("max_range", 15.0),
            )
            
            camera = SecurityCamera(config, event_bus=event_bus)
            cameras.append(camera)
            
            logger.debug(
                f"Created camera {placement.device_id} at {placement.position}, "
                f"pan={math.degrees(placement.orientation[0]):.1f}°, "
                f"tilt={math.degrees(placement.orientation[1]):.1f}°"
            )
        
        return cameras
    
    def create_motion_sensors(self, event_bus=None) -> List["MotionSensor"]:
        """
        Create MotionSensor instances from computed placements.
        
        Args:
            event_bus: Optional event bus for sensor events
            
        Returns:
            List of MotionSensor instances
        """
        from vesper.devices.motion_sensor import MotionSensor
        
        sensors = []
        
        for placement in self.placements:
            if placement.device_type != "motion_sensor":
                continue
            
            sensor = MotionSensor(
                location=placement.position,
                detection_radius=placement.config.get("detection_range", 8.0),
                cooldown=placement.config.get("cooldown", 2.0),
                device_id=placement.device_id,
                event_bus=event_bus,
                name=f"Motion Sensor {placement.room_name}",
            )
            sensors.append(sensor)
            
            logger.debug(
                f"Created motion sensor {placement.device_id} at {placement.position}"
            )
        
        return sensors
    
    def get_placement_summary(self) -> Dict[str, Any]:
        """Get a summary of all placements."""
        cameras = [p for p in self.placements if p.device_type == "security_camera"]
        motion_sensors = [p for p in self.placements if p.device_type == "motion_sensor"]
        
        return {
            "total_devices": len(self.placements),
            "cameras": len(cameras),
            "motion_sensors": len(motion_sensors),
            "rooms_covered": list(set(p.room_name for p in self.placements)),
            "scene_type": self.scene_type.value,
        }
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert all placements to a dictionary for serialization."""
        return {
            "scene_type": self.scene_type.value,
            "placements": [
                {
                    "device_type": p.device_type,
                    "device_id": p.device_id,
                    "room_name": p.room_name,
                    "position": p.position,
                    "orientation": p.orientation,
                    "config": p.config,
                }
                for p in self.placements
            ],
            "rooms": {
                name: {
                    "name": info.name,
                    "room_type": info.room_type.value,
                    "center": info.center,
                    "area": info.area,
                }
                for name, info in self.rooms.items()
            },
        }


def create_devices_for_scene(
    room_positions: Dict[str, Tuple[float, float, float]],
    scene_path: str = "",
    event_bus=None,
) -> Tuple[List["SecurityCamera"], List["MotionSensor"]]:
    """
    Convenience function to create devices for a scene.
    
    Args:
        room_positions: Dict of room_name -> (x, y, z)
        scene_path: Path to scene file (for auto-detecting scene type)
        event_bus: Optional event bus
        
    Returns:
        Tuple of (cameras, motion_sensors)
    """
    placer = SceneDevicePlacer()
    
    if scene_path:
        placer.scene_type = placer.detect_scene_type(scene_path)
    
    placer.add_rooms_from_positions(room_positions)
    placer.compute_device_placements()
    
    cameras = placer.create_cameras(event_bus)
    motion_sensors = placer.create_motion_sensors(event_bus)
    
    summary = placer.get_placement_summary()
    logger.info(
        f"Created {summary['cameras']} cameras and {summary['motion_sensors']} motion sensors "
        f"for {len(summary['rooms_covered'])} rooms"
    )
    
    return cameras, motion_sensors
