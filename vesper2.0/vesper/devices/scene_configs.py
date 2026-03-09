"""
Scene-specific device placement configurations.

Different scene datasets (HSSD, HM3D, ReplicaCAD) have different
characteristics that affect optimal device placement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum

from vesper.devices.scene_device_placer import (
    SceneType,
    RoomType,
    DevicePlacementConfig,
)
from vesper.devices.security_camera import MountPosition


@dataclass
class SceneLayoutConfig:
    """Configuration for a specific scene layout type."""
    
    # Scene characteristics
    scene_type: SceneType
    typical_ceiling_height: float = 2.8
    typical_room_size: float = 4.0  # meters
    has_semantic_labels: bool = True
    
    # Camera placement preferences
    camera_mount_height: float = 2.5
    camera_prefer_corners: bool = True
    camera_horizontal_fov: float = 90.0
    camera_vertical_fov: float = 60.0
    
    # Room-specific camera mounts
    room_camera_mounts: Dict[RoomType, List[MountPosition]] = field(default_factory=dict)
    
    # Motion sensor preferences
    motion_sensor_height: float = 2.2
    motion_sensor_range: float = 8.0
    motion_sensor_fov: float = 110.0
    
    # Rooms to skip for cameras (privacy)
    skip_camera_rooms: List[RoomType] = field(default_factory=lambda: [
        RoomType.BATHROOM,
        RoomType.CLOSET,
    ])
    
    # Rooms requiring extra coverage
    high_priority_rooms: List[RoomType] = field(default_factory=lambda: [
        RoomType.LIVING_ROOM,
        RoomType.ENTRANCE,
        RoomType.KITCHEN,
    ])


# Pre-configured layouts for known scene types

HSSD_LAYOUT = SceneLayoutConfig(
    scene_type=SceneType.HSSD,
    typical_ceiling_height=2.8,
    typical_room_size=4.5,  # HSSD rooms tend to be larger
    has_semantic_labels=True,
    camera_mount_height=2.5,
    camera_prefer_corners=True,
    room_camera_mounts={
        RoomType.LIVING_ROOM: [MountPosition.CORNER_NE, MountPosition.CORNER_SW],
        RoomType.KITCHEN: [MountPosition.CORNER_NW],
        RoomType.BEDROOM: [MountPosition.CORNER_NE],
        RoomType.HALLWAY: [MountPosition.WALL_N, MountPosition.WALL_S],
        RoomType.ENTRANCE: [MountPosition.WALL_N],
        RoomType.DINING_ROOM: [MountPosition.CORNER_SE],
        RoomType.OFFICE: [MountPosition.CORNER_NE],
    },
    motion_sensor_height=2.2,
    motion_sensor_range=8.0,
    motion_sensor_fov=110.0,
)


HM3D_LAYOUT = SceneLayoutConfig(
    scene_type=SceneType.HM3D,
    typical_ceiling_height=2.6,
    typical_room_size=4.0,
    has_semantic_labels=True,
    camera_mount_height=2.4,
    camera_prefer_corners=True,
    room_camera_mounts={
        RoomType.LIVING_ROOM: [MountPosition.CORNER_NE],
        RoomType.KITCHEN: [MountPosition.CORNER_NW],
        RoomType.BEDROOM: [MountPosition.CORNER_NE],
        RoomType.HALLWAY: [MountPosition.WALL_N],
        RoomType.ENTRANCE: [MountPosition.CORNER_NW],
    },
    motion_sensor_height=2.2,
    motion_sensor_range=7.0,  # Slightly shorter range for smaller rooms
    motion_sensor_fov=110.0,
)


REPLICA_CAD_LAYOUT = SceneLayoutConfig(
    scene_type=SceneType.REPLICA_CAD,
    typical_ceiling_height=2.7,
    typical_room_size=3.5,  # ReplicaCAD rooms are compact
    has_semantic_labels=True,
    camera_mount_height=2.3,
    camera_prefer_corners=True,
    camera_horizontal_fov=100.0,  # Wider FOV for smaller spaces
    room_camera_mounts={
        RoomType.LIVING_ROOM: [MountPosition.CORNER_NE],
        RoomType.KITCHEN: [MountPosition.CORNER_SE],
        RoomType.BEDROOM: [MountPosition.CORNER_NW],
        RoomType.HALLWAY: [MountPosition.WALL_E],
    },
    motion_sensor_height=2.1,
    motion_sensor_range=6.0,
    motion_sensor_fov=120.0,  # Wider FOV for small rooms
)


HABITAT_TEST_LAYOUT = SceneLayoutConfig(
    scene_type=SceneType.HABITAT_TEST,
    typical_ceiling_height=2.5,
    typical_room_size=5.0,
    has_semantic_labels=False,
    camera_mount_height=2.3,
    camera_prefer_corners=True,
    room_camera_mounts={},  # Use defaults
    motion_sensor_height=2.0,
    motion_sensor_range=10.0,  # Longer range for test scenes
)


# Registry of all layouts
SCENE_LAYOUTS: Dict[SceneType, SceneLayoutConfig] = {
    SceneType.HSSD: HSSD_LAYOUT,
    SceneType.HM3D: HM3D_LAYOUT,
    SceneType.REPLICA_CAD: REPLICA_CAD_LAYOUT,
    SceneType.HABITAT_TEST: HABITAT_TEST_LAYOUT,
}


def get_layout_for_scene(scene_path: str) -> SceneLayoutConfig:
    """
    Get the appropriate layout configuration for a scene.
    
    Args:
        scene_path: Path to the scene file
        
    Returns:
        SceneLayoutConfig for the detected scene type
    """
    path_lower = scene_path.lower()
    
    if "hssd" in path_lower:
        return HSSD_LAYOUT
    elif "hm3d" in path_lower:
        return HM3D_LAYOUT
    elif "replica" in path_lower:
        return REPLICA_CAD_LAYOUT
    elif "habitat-test" in path_lower or "test-scenes" in path_lower:
        return HABITAT_TEST_LAYOUT
    
    # Default: use HSSD layout as it's most complete
    return HSSD_LAYOUT


def get_camera_mount_for_room(
    room_type: RoomType,
    layout: SceneLayoutConfig,
    index: int = 0,
) -> MountPosition:
    """
    Get the preferred camera mount position for a room type.
    
    Args:
        room_type: Type of room
        layout: Scene layout configuration
        index: Which mount to use if multiple are available
        
    Returns:
        MountPosition for camera placement
    """
    mounts = layout.room_camera_mounts.get(room_type, [MountPosition.CORNER_NE])
    
    if not mounts:
        mounts = [MountPosition.CORNER_NE]
    
    return mounts[index % len(mounts)]


@dataclass
class RoomCoverageRequirement:
    """Coverage requirements for a specific room."""
    room_type: RoomType
    needs_camera: bool = True
    needs_motion_sensor: bool = True
    camera_count: int = 1
    motion_sensor_count: int = 1
    priority: int = 1  # Higher = more important


# Default coverage requirements per room type
DEFAULT_COVERAGE = {
    RoomType.LIVING_ROOM: RoomCoverageRequirement(
        RoomType.LIVING_ROOM, needs_camera=True, needs_motion_sensor=True, priority=3
    ),
    RoomType.KITCHEN: RoomCoverageRequirement(
        RoomType.KITCHEN, needs_camera=True, needs_motion_sensor=True, priority=2
    ),
    RoomType.ENTRANCE: RoomCoverageRequirement(
        RoomType.ENTRANCE, needs_camera=True, needs_motion_sensor=True, priority=3
    ),
    RoomType.HALLWAY: RoomCoverageRequirement(
        RoomType.HALLWAY, needs_camera=True, needs_motion_sensor=True, priority=2
    ),
    RoomType.BEDROOM: RoomCoverageRequirement(
        RoomType.BEDROOM, needs_camera=False, needs_motion_sensor=True, priority=1
    ),
    RoomType.BATHROOM: RoomCoverageRequirement(
        RoomType.BATHROOM, needs_camera=False, needs_motion_sensor=True, priority=1
    ),
    RoomType.OFFICE: RoomCoverageRequirement(
        RoomType.OFFICE, needs_camera=True, needs_motion_sensor=True, priority=2
    ),
    RoomType.DINING_ROOM: RoomCoverageRequirement(
        RoomType.DINING_ROOM, needs_camera=True, needs_motion_sensor=True, priority=2
    ),
    RoomType.GARAGE: RoomCoverageRequirement(
        RoomType.GARAGE, needs_camera=True, needs_motion_sensor=True, priority=2
    ),
    RoomType.CLOSET: RoomCoverageRequirement(
        RoomType.CLOSET, needs_camera=False, needs_motion_sensor=False, priority=0
    ),
    RoomType.LAUNDRY: RoomCoverageRequirement(
        RoomType.LAUNDRY, needs_camera=False, needs_motion_sensor=True, priority=1
    ),
    RoomType.OTHER: RoomCoverageRequirement(
        RoomType.OTHER, needs_camera=True, needs_motion_sensor=True, priority=1
    ),
}


def get_coverage_requirement(room_type: RoomType) -> RoomCoverageRequirement:
    """Get coverage requirements for a room type."""
    return DEFAULT_COVERAGE.get(room_type, DEFAULT_COVERAGE[RoomType.OTHER])
