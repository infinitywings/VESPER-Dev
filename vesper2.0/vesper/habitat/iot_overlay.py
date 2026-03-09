"""
IoT Device Overlay for Habitat 3D visualization.

Renders smart home devices (sensors, lights, doors) as visual overlays
in the 3D scene. Works with both first-person and third-person views.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING, Union

import numpy as np

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class IoTDeviceInfo:
    """Information about an IoT device for visualization."""
    device_id: str
    device_type: str  # "motion_sensor", "contact_sensor", "light_sensor", "smart_light", "smart_door"
    room: str
    position: Tuple[float, float, float]  # 3D world position
    state: Dict[str, Any] = field(default_factory=dict)
    
    # Visual properties
    icon: str = "●"  # Default icon
    color: Tuple[int, int, int] = (0, 255, 0)  # Default green
    
    def __post_init__(self):
        """Set icon and color based on device type."""
        device_visuals = {
            "motion_sensor": ("◉", (255, 200, 0)),    # Yellow
            "contact_sensor": ("⊡", (0, 200, 255)),   # Cyan
            "light_sensor": ("☀", (255, 255, 100)),   # Light yellow
            "smart_light": ("💡", (255, 255, 200)),   # White/yellow
            "smart_door": ("🚪", (150, 100, 50)),     # Brown
            "leak_sensor": ("💧", (0, 100, 255)),     # Blue
            "temperature": ("🌡", (255, 100, 100)),   # Red
            "humidity": ("💨", (100, 200, 255)),      # Light blue
        }
        if self.device_type in device_visuals:
            self.icon, self.color = device_visuals[self.device_type]


class IoTDeviceManager:
    """
    Manages IoT devices in the Habitat scene.
    
    Handles:
    - Device placement based on room positions
    - Device state updates
    - Provides device info for rendering
    """
    
    # Default device types for each room category
    ROOM_DEVICES = {
        "kitchen": ["motion_sensor", "smart_light", "leak_sensor"],
        "bathroom": ["motion_sensor", "leak_sensor", "humidity"],
        "bedroom": ["motion_sensor", "smart_light", "temperature"],
        "living room": ["motion_sensor", "smart_light", "temperature"],
        "hallway": ["motion_sensor", "smart_light"],
        "toilet": ["motion_sensor", "contact_sensor"],
        "closet": ["contact_sensor"],
        "office": ["motion_sensor", "smart_light"],
        "dining room": ["motion_sensor", "smart_light"],
        "laundryroom": ["leak_sensor", "humidity"],
        "entryway": ["motion_sensor", "smart_door", "contact_sensor"],
        "outdoor": [],  # No devices outdoors
    }
    
    def __init__(self):
        self.devices: Dict[str, IoTDeviceInfo] = {}
        self._device_counter = 0
    
    def setup_devices_for_rooms(
        self,
        room_positions: Union[Dict[str, List[Tuple[float, float, float]]], List[str]],
    ) -> Dict[str, List[str]]:
        """
        Set up IoT devices based on room positions or room names.
        
        Args:
            room_positions: Either:
                - Dict of room_name -> list of navigable positions
                - List of room names (will use dummy positions)
            
        Returns:
            Dict of room_name -> list of device_types placed
        """
        self.devices.clear()
        room_devices_map = {}
        
        # Handle list of room names (convert to dict with dummy positions)
        if isinstance(room_positions, list):
            room_positions = {room: [(0.0, 0.0, 0.0)] for room in room_positions}
        
        for room_name, positions in room_positions.items():
            if not positions:
                continue
            
            # Determine room category (handle numbered rooms like "bedroom.001")
            room_category = room_name.split('.')[0].lower()
            
            # Get device types for this room category
            device_types = self.ROOM_DEVICES.get(room_category, ["motion_sensor"])
            
            # Place devices
            placed_devices = []
            for device_type in device_types:
                device = self._create_device(device_type, room_name, positions)
                if device:
                    self.devices[device.device_id] = device
                    placed_devices.append(device_type)
            
            room_devices_map[room_name] = placed_devices
        
        logger.info(f"Placed {len(self.devices)} IoT devices in {len(room_devices_map)} rooms")
        return room_devices_map
    
    def _create_device(
        self,
        device_type: str,
        room: str,
        positions: List[Tuple[float, float, float]],
    ) -> Optional[IoTDeviceInfo]:
        """Create a device at a suitable position in the room."""
        if not positions:
            return None
        
        # Pick a position (for sensors, offset slightly from center)
        base_pos = random.choice(positions)
        
        # Add height offset based on device type
        height_offsets = {
            "motion_sensor": 2.2,      # Ceiling mounted
            "contact_sensor": 1.5,     # Door frame height
            "light_sensor": 2.0,       # Near ceiling
            "smart_light": 2.4,        # Ceiling
            "smart_door": 1.0,         # Door handle height
            "leak_sensor": 0.1,        # Floor level
            "temperature": 1.5,        # Wall mounted
            "humidity": 1.5,           # Wall mounted
        }
        
        height = height_offsets.get(device_type, 1.5)
        
        # Small random offset to avoid overlap
        offset_x = random.uniform(-0.5, 0.5)
        offset_z = random.uniform(-0.5, 0.5)
        
        position = (
            base_pos[0] + offset_x,
            base_pos[1] + height,  # Y is up in Habitat
            base_pos[2] + offset_z,
        )
        
        self._device_counter += 1
        device_id = f"{device_type}_{room}_{self._device_counter}"
        
        # Initial state
        state = self._get_initial_state(device_type)
        
        return IoTDeviceInfo(
            device_id=device_id,
            device_type=device_type,
            room=room,
            position=position,
            state=state,
        )
    
    def _get_initial_state(self, device_type: str) -> Dict[str, Any]:
        """Get initial state for a device type."""
        states = {
            "motion_sensor": {"detected": False, "last_detection": None},
            "contact_sensor": {"open": False},
            "light_sensor": {"lux": 300},
            "smart_light": {"on": True, "brightness": 100},
            "smart_door": {"locked": True, "open": False},
            "leak_sensor": {"leak_detected": False},
            "temperature": {"value": 22.0, "unit": "C"},
            "humidity": {"value": 45.0, "unit": "%"},
        }
        return states.get(device_type, {})
    
    def update_device_state(self, device_id: str, state: Dict[str, Any]) -> bool:
        """Update a device's state."""
        if device_id not in self.devices:
            return False
        self.devices[device_id].state.update(state)
        return True
    
    def trigger_motion(self, room: str) -> List[str]:
        """Trigger motion detection in a room."""
        triggered = []
        for device_id, device in self.devices.items():
            if device.room == room and device.device_type == "motion_sensor":
                device.state["detected"] = True
                triggered.append(device_id)
        return triggered
    
    def get_devices_in_room(self, room: str) -> List[IoTDeviceInfo]:
        """Get all devices in a room."""
        return [d for d in self.devices.values() if d.room == room]
    
    def get_room_devices_summary(self) -> Dict[str, List[str]]:
        """Get summary of devices per room for LLM prompts."""
        summary = {}
        for device in self.devices.values():
            if device.room not in summary:
                summary[device.room] = []
            # Use friendly names
            friendly_names = {
                "motion_sensor": "motion sensor",
                "contact_sensor": "door sensor",
                "light_sensor": "light sensor",
                "smart_light": "smart light",
                "smart_door": "smart door lock",
                "leak_sensor": "water leak sensor",
                "temperature": "temperature sensor",
                "humidity": "humidity sensor",
            }
            name = friendly_names.get(device.device_type, device.device_type)
            if name not in summary[device.room]:
                summary[device.room].append(name)
        return summary


class IoTOverlayRenderer:
    """
    Renders IoT device overlays on the Habitat view.
    
    Uses pygame to draw device icons and status indicators
    at projected 2D positions based on 3D world coordinates.
    """
    
    def __init__(
        self,
        device_manager: IoTDeviceManager,
        screen_size: Tuple[int, int] = (1280, 720),
    ):
        self.device_manager = device_manager
        self.screen_size = screen_size
        self.font = None
        self.small_font = None
        self._initialized = False
    
    def init_fonts(self):
        """Initialize fonts (call after pygame.init())."""
        if not PYGAME_AVAILABLE:
            return
        if not self._initialized:
            try:
                self.font = pygame.font.SysFont("Arial", 16)
                self.small_font = pygame.font.SysFont("Arial", 12)
                self._initialized = True
            except Exception as e:
                logger.warning(f"Failed to init fonts: {e}")
    
    def project_to_screen(
        self,
        world_pos: Tuple[float, float, float],
        agent_pos: Tuple[float, float, float],
        agent_rotation: float,  # Yaw in radians
        fov: float = 90.0,
    ) -> Optional[Tuple[int, int, float]]:
        """
        Project 3D world position to 2D screen coordinates.
        
        Returns:
            (x, y, distance) or None if behind camera
        """
        # Vector from agent to device
        dx = world_pos[0] - agent_pos[0]
        dy = world_pos[1] - agent_pos[1]
        dz = world_pos[2] - agent_pos[2]
        
        # Distance
        distance = math.sqrt(dx*dx + dy*dy + dz*dz)
        if distance < 0.1:
            return None
        
        # Rotate to camera space (agent looks along -Z in world, forward is -Z)
        cos_r = math.cos(-agent_rotation)
        sin_r = math.sin(-agent_rotation)
        
        # Camera space: X right, Y up, Z forward (into screen)
        cam_x = dx * cos_r - dz * sin_r
        cam_z = dx * sin_r + dz * cos_r
        cam_y = dy
        
        # Behind camera check
        if cam_z >= 0:
            return None
        
        # Project to screen
        fov_rad = math.radians(fov)
        aspect = self.screen_size[0] / self.screen_size[1]
        
        # Perspective projection
        proj_x = cam_x / (-cam_z) / math.tan(fov_rad / 2)
        proj_y = cam_y / (-cam_z) / math.tan(fov_rad / 2) * aspect
        
        # Convert to screen coordinates
        screen_x = int((proj_x + 1) * self.screen_size[0] / 2)
        screen_y = int((1 - proj_y) * self.screen_size[1] / 2)
        
        # Clamp to screen bounds
        if screen_x < 0 or screen_x >= self.screen_size[0]:
            return None
        if screen_y < 0 or screen_y >= self.screen_size[1]:
            return None
        
        return (screen_x, screen_y, distance)
    
    def render(
        self,
        surface: "pygame.Surface",
        agent_pos: Tuple[float, float, float],
        agent_rotation: float,
        show_labels: bool = True,
        max_distance: float = 15.0,
    ):
        """
        Render IoT device overlays on the given surface.
        
        Args:
            surface: Pygame surface to draw on
            agent_pos: Agent's 3D position
            agent_rotation: Agent's yaw rotation (radians)
            show_labels: Whether to show device labels
            max_distance: Maximum distance to show devices
        """
        if not PYGAME_AVAILABLE or not self._initialized:
            self.init_fonts()
            if not self._initialized:
                return
        
        visible_devices = []
        
        for device in self.device_manager.devices.values():
            result = self.project_to_screen(
                device.position,
                agent_pos,
                agent_rotation,
            )
            if result and result[2] <= max_distance:
                visible_devices.append((device, result))
        
        # Sort by distance (far to near) for proper overlap
        visible_devices.sort(key=lambda x: -x[1][2])
        
        for device, (screen_x, screen_y, distance) in visible_devices:
            self._render_device(
                surface, device, screen_x, screen_y, distance, show_labels
            )
    
    def _render_device(
        self,
        surface: "pygame.Surface",
        device: IoTDeviceInfo,
        x: int,
        y: int,
        distance: float,
        show_labels: bool,
    ):
        """Render a single device."""
        # Size based on distance
        base_size = 24
        size = max(8, int(base_size * (5.0 / (distance + 1))))
        
        # Color based on state
        color = device.color
        if device.device_type == "motion_sensor" and device.state.get("detected"):
            color = (255, 0, 0)  # Red when motion detected
        elif device.device_type == "smart_light" and not device.state.get("on"):
            color = (100, 100, 100)  # Gray when off
        elif device.device_type == "leak_sensor" and device.state.get("leak_detected"):
            color = (255, 0, 0)  # Red when leak
        
        # Draw device marker
        pygame.draw.circle(surface, color, (x, y), size // 2)
        pygame.draw.circle(surface, (255, 255, 255), (x, y), size // 2, 1)
        
        # Draw icon/label
        if show_labels and self.font:
            # Device type abbreviation
            abbrevs = {
                "motion_sensor": "M",
                "contact_sensor": "C",
                "light_sensor": "L",
                "smart_light": "💡",
                "smart_door": "🚪",
                "leak_sensor": "💧",
                "temperature": "T",
                "humidity": "H",
            }
            text = abbrevs.get(device.device_type, "?")
            text_surface = self.small_font.render(text, True, (255, 255, 255))
            text_rect = text_surface.get_rect(center=(x, y))
            surface.blit(text_surface, text_rect)
            
            # Room label below
            room_text = self.small_font.render(
                device.room.split('.')[0], True, (200, 200, 200)
            )
            surface.blit(room_text, (x - room_text.get_width() // 2, y + size // 2 + 2))
    
    def render_device_panel(
        self,
        surface: "pygame.Surface",
        current_room: Optional[str] = None,
        position: Tuple[int, int] = (10, 80),
    ):
        """
        Render a panel showing devices in the current room.
        
        Args:
            surface: Pygame surface to draw on
            current_room: Name of the room agent is in
            position: Top-left corner of the panel
        """
        if not PYGAME_AVAILABLE or not self._initialized:
            return
        
        devices_in_room = []
        if current_room:
            devices_in_room = self.device_manager.get_devices_in_room(current_room)
        
        if not devices_in_room:
            return
        
        # Panel dimensions
        panel_width = 200
        panel_height = 30 + len(devices_in_room) * 22
        
        # Draw panel background
        panel = pygame.Surface((panel_width, panel_height), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 150))
        
        # Title
        if self.font:
            title = self.font.render(f"Devices in {current_room}:", True, (0, 255, 255))
            panel.blit(title, (10, 5))
        
        # List devices
        y = 30
        for device in devices_in_room:
            # Status indicator
            status_color = device.color
            if device.device_type == "motion_sensor":
                status_color = (255, 0, 0) if device.state.get("detected") else (0, 255, 0)
            elif device.device_type == "smart_light":
                status_color = (255, 255, 0) if device.state.get("on") else (100, 100, 100)
            
            pygame.draw.circle(panel, status_color, (15, y + 8), 5)
            
            # Device name
            if self.small_font:
                friendly_name = device.device_type.replace("_", " ").title()
                text = self.small_font.render(friendly_name, True, (255, 255, 255))
                panel.blit(text, (25, y))
            
            y += 22
        
        surface.blit(panel, position)
