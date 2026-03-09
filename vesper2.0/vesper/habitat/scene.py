"""
Scene management for Habitat-Sim.

Handles loading, configuring, and managing 3D scenes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

try:
    import habitat_sim
    HABITAT_AVAILABLE = True
except ImportError:
    HABITAT_AVAILABLE = False


logger = logging.getLogger(__name__)


class SceneDataset(str, Enum):
    """Available scene datasets."""
    HSSD = "hssd-hab"
    REPLICA_CAD = "replica_cad"
    MP3D = "mp3d"
    GIBSON = "gibson"
    HM3D = "hm3d"


@dataclass
class RoomInfo:
    """Information about a room in the scene."""
    room_id: str
    room_type: str  # "living_room", "kitchen", "bedroom", etc.
    center: Tuple[float, float, float]
    bounds_min: Tuple[float, float, float]
    bounds_max: Tuple[float, float, float]
    navigable_area: float = 0.0
    
    @property
    def size(self) -> Tuple[float, float, float]:
        """Room dimensions (width, height, depth)."""
        return (
            self.bounds_max[0] - self.bounds_min[0],
            self.bounds_max[1] - self.bounds_min[1],
            self.bounds_max[2] - self.bounds_min[2],
        )


@dataclass
class ObjectInfo:
    """Information about an object in the scene."""
    object_id: int
    semantic_id: int
    category: str
    position: Tuple[float, float, float]
    rotation: Tuple[float, float, float, float]  # Quaternion
    bounding_box: Optional[Tuple[Tuple[float, ...], Tuple[float, ...]]] = None


@dataclass
class SceneConfig:
    """Configuration for scene loading."""
    scene_path: str = ""
    dataset: SceneDataset = SceneDataset.HSSD
    data_dir: str = "./data"
    
    # Scene options
    load_semantic_mesh: bool = True
    load_navmesh: bool = True
    
    # Generation options
    navmesh_settings: Dict[str, Any] = field(default_factory=lambda: {
        "agent_radius": 0.1,
        "agent_height": 1.5,
        "agent_max_climb": 0.2,
        "agent_max_slope": 45.0,
    })


class SceneManager:
    """
    Manages 3D scenes for the simulation.
    
    Responsibilities:
    - Load and switch scenes
    - Query scene structure (rooms, objects)
    - Manage semantic annotations
    - Handle navigation mesh
    
    Example:
        config = SceneConfig(
            scene_path="102816036.glb",
            dataset=SceneDataset.HSSD,
        )
        
        manager = SceneManager(config)
        manager.load()
        
        # Get rooms
        for room in manager.rooms:
            print(f"{room.room_type}: {room.size}")
        
        # Find objects
        chairs = manager.find_objects_by_category("chair")
    """
    
    def __init__(self, config: Optional[SceneConfig] = None):
        """
        Initialize scene manager.
        
        Args:
            config: Scene configuration
        """
        self.config = config or SceneConfig()
        
        self._scene_path: Optional[Path] = None
        self._rooms: List[RoomInfo] = []
        self._objects: List[ObjectInfo] = []
        self._semantic_map: Dict[int, str] = {}
        self._is_loaded = False
        
        self._stats = {
            "rooms_count": 0,
            "objects_count": 0,
            "navigable_area": 0.0,
        }
    
    @property
    def is_loaded(self) -> bool:
        """Check if scene is loaded."""
        return self._is_loaded
    
    @property
    def rooms(self) -> List[RoomInfo]:
        """Get list of rooms in the scene."""
        return self._rooms.copy()
    
    @property
    def objects(self) -> List[ObjectInfo]:
        """Get list of objects in the scene."""
        return self._objects.copy()
    
    @property
    def stats(self) -> Dict[str, Any]:
        """Scene statistics."""
        return self._stats.copy()
    
    def load(self, simulator: Any = None) -> bool:
        """
        Load the scene.
        
        Args:
            simulator: Optional Habitat simulator instance
            
        Returns:
            True if successful
        """
        scene_path = self._resolve_scene_path()
        
        if not scene_path or not scene_path.exists():
            logger.error(f"Scene not found: {scene_path}")
            return False
        
        self._scene_path = scene_path
        
        # Load scene metadata
        self._load_metadata()
        
        # Load semantic annotations if available
        if self.config.load_semantic_mesh:
            self._load_semantic_annotations()
        
        self._is_loaded = True
        logger.info(f"Scene loaded: {scene_path}")
        
        return True
    
    def _resolve_scene_path(self) -> Optional[Path]:
        """Resolve the full scene path."""
        if self.config.scene_path:
            path = Path(self.config.scene_path)
            if path.is_absolute() and path.exists():
                return path
        
        # Try in data directory
        data_path = Path(self.config.data_dir)
        
        # Check dataset-specific paths
        dataset_paths = {
            SceneDataset.HSSD: data_path / "hssd-hab" / "scenes",
            SceneDataset.REPLICA_CAD: data_path / "replica_cad" / "stages",
            SceneDataset.MP3D: data_path / "mp3d",
            SceneDataset.HM3D: data_path / "hm3d",
        }
        
        base_path = dataset_paths.get(self.config.dataset, data_path)
        
        if self.config.scene_path:
            full_path = base_path / self.config.scene_path
            if full_path.exists():
                return full_path
        
        # Try to find any scene in the dataset
        for ext in [".glb", ".gltf", ".ply"]:
            scenes = list(base_path.glob(f"**/*{ext}"))
            if scenes:
                return scenes[0]
        
        return None
    
    def _load_metadata(self) -> None:
        """Load scene metadata."""
        if not self._scene_path:
            return
        
        # Look for JSON metadata file
        meta_path = self._scene_path.with_suffix(".json")
        if not meta_path.exists():
            meta_path = self._scene_path.parent / f"{self._scene_path.stem}_semantic.json"
        
        if meta_path.exists():
            try:
                with open(meta_path) as f:
                    data = json.load(f)
                    self._parse_metadata(data)
            except Exception as e:
                logger.warning(f"Failed to load metadata: {e}")
    
    def _parse_metadata(self, data: Dict[str, Any]) -> None:
        """Parse scene metadata JSON."""
        # Parse rooms
        if "rooms" in data:
            for room_data in data["rooms"]:
                room = RoomInfo(
                    room_id=room_data.get("id", "unknown"),
                    room_type=room_data.get("type", "unknown"),
                    center=tuple(room_data.get("center", [0, 0, 0])),
                    bounds_min=tuple(room_data.get("bounds_min", [0, 0, 0])),
                    bounds_max=tuple(room_data.get("bounds_max", [0, 0, 0])),
                )
                self._rooms.append(room)
        
        # Parse objects
        if "objects" in data:
            for obj_data in data["objects"]:
                obj = ObjectInfo(
                    object_id=obj_data.get("id", 0),
                    semantic_id=obj_data.get("semantic_id", 0),
                    category=obj_data.get("category", "unknown"),
                    position=tuple(obj_data.get("position", [0, 0, 0])),
                    rotation=tuple(obj_data.get("rotation", [0, 0, 0, 1])),
                )
                self._objects.append(obj)
        
        self._stats["rooms_count"] = len(self._rooms)
        self._stats["objects_count"] = len(self._objects)
    
    def _load_semantic_annotations(self) -> None:
        """Load semantic annotations (category mappings)."""
        # Check for semantic file
        sem_path = self._scene_path.parent / "semantic_class_map.csv"
        
        if sem_path.exists():
            try:
                with open(sem_path) as f:
                    for line in f:
                        parts = line.strip().split(",")
                        if len(parts) >= 2:
                            self._semantic_map[int(parts[0])] = parts[1]
            except Exception as e:
                logger.warning(f"Failed to load semantic annotations: {e}")
    
    def get_room_by_id(self, room_id: str) -> Optional[RoomInfo]:
        """Get a room by its ID."""
        for room in self._rooms:
            if room.room_id == room_id:
                return room
        return None
    
    def get_room_by_type(self, room_type: str) -> List[RoomInfo]:
        """Get all rooms of a specific type."""
        return [r for r in self._rooms if r.room_type == room_type]
    
    def get_room_at_position(
        self,
        position: Tuple[float, float, float],
    ) -> Optional[RoomInfo]:
        """Get the room containing a position."""
        for room in self._rooms:
            if (room.bounds_min[0] <= position[0] <= room.bounds_max[0] and
                room.bounds_min[1] <= position[1] <= room.bounds_max[1] and
                room.bounds_min[2] <= position[2] <= room.bounds_max[2]):
                return room
        return None
    
    def find_objects_by_category(self, category: str) -> List[ObjectInfo]:
        """Find all objects of a category."""
        return [o for o in self._objects if o.category.lower() == category.lower()]
    
    def find_objects_near(
        self,
        position: Tuple[float, float, float],
        radius: float,
    ) -> List[ObjectInfo]:
        """Find objects within a radius of a position."""
        result = []
        for obj in self._objects:
            dx = obj.position[0] - position[0]
            dy = obj.position[1] - position[1]
            dz = obj.position[2] - position[2]
            distance = (dx*dx + dy*dy + dz*dz) ** 0.5
            if distance <= radius:
                result.append(obj)
        return result
    
    def get_semantic_category(self, semantic_id: int) -> str:
        """Get category name for a semantic ID."""
        return self._semantic_map.get(semantic_id, "unknown")
    
    def get_spawn_points(
        self,
        room_types: Optional[List[str]] = None,
        count: int = 1,
    ) -> List[Tuple[float, float, float]]:
        """
        Get valid spawn points for agents.
        
        Args:
            room_types: Limit to specific room types
            count: Number of points to return
            
        Returns:
            List of spawn positions
        """
        points = []
        
        # Filter rooms
        rooms = self._rooms
        if room_types:
            rooms = [r for r in rooms if r.room_type in room_types]
        
        for room in rooms:
            if len(points) >= count:
                break
            # Use room center as spawn point
            points.append(room.center)
        
        # If not enough rooms, use origin
        while len(points) < count:
            points.append((0.0, 0.0, 0.0))
        
        return points[:count]
    
    def get_device_placement_points(
        self,
        device_type: str,
        room_type: Optional[str] = None,
    ) -> List[Tuple[float, float, float]]:
        """
        Get suggested placement points for IoT devices.
        
        Args:
            device_type: Type of device ("motion_sensor", "door", etc.)
            room_type: Limit to specific room type
            
        Returns:
            List of suggested positions
        """
        points = []
        rooms = self._rooms
        
        if room_type:
            rooms = [r for r in rooms if r.room_type == room_type]
        
        for room in rooms:
            if device_type == "motion_sensor":
                # Place in room center, near ceiling
                points.append((
                    room.center[0],
                    room.bounds_max[1] - 0.3,  # Near ceiling
                    room.center[2],
                ))
            
            elif device_type == "contact_sensor" or device_type == "smart_door":
                # Look for door-like openings (edges of rooms)
                points.append((
                    room.bounds_min[0],  # Edge of room
                    room.center[1],
                    room.center[2],
                ))
            
            elif device_type == "light_sensor":
                # Place near windows (assume they're at max z)
                points.append((
                    room.center[0],
                    room.center[1] + 1.0,  # Upper wall
                    room.bounds_max[2] - 0.1,
                ))
        
        return points
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize scene info to dictionary."""
        return {
            "scene_path": str(self._scene_path) if self._scene_path else None,
            "dataset": self.config.dataset.value,
            "rooms": [
                {
                    "id": r.room_id,
                    "type": r.room_type,
                    "center": r.center,
                    "size": r.size,
                }
                for r in self._rooms
            ],
            "objects_count": len(self._objects),
            "stats": self._stats,
        }
