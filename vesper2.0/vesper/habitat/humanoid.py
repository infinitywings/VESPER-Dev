"""
Humanoid Avatar Controller for Habitat 3D visualization.

Provides a humanoid character that can navigate in the scene,
following navigation commands and animating appropriately.
Supports first-person and third-person view switching.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import habitat_sim

logger = logging.getLogger(__name__)


class HumanoidState(Enum):
    """States for humanoid animation."""
    IDLE = "idle"
    WALKING = "walking"
    TURNING = "turning"
    ARRIVED = "arrived"


class ViewMode(Enum):
    """Camera view modes."""
    FIRST_PERSON = "first_person"
    THIRD_PERSON = "third_person"


@dataclass
class HumanoidConfig:
    """Configuration for humanoid avatar."""
    height: float = 1.7
    eye_height: float = 1.6  # Eye level for first-person view
    walk_speed: float = 1.0  # m/s
    turn_speed: float = 90.0  # deg/s
    model_path: Optional[str] = None
    use_animation: bool = True
    
    # Third-person camera settings
    third_person_distance: float = 3.0  # Distance behind humanoid
    third_person_height: float = 2.0  # Height above humanoid
    third_person_pitch: float = -15.0  # Camera pitch (degrees)


@dataclass
class HumanoidPose:
    """Current pose of the humanoid."""
    position: Tuple[float, float, float]
    rotation: float  # Yaw in radians
    state: HumanoidState = HumanoidState.IDLE
    head_pitch: float = 0.0  # Head tilt (looking up/down)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "position": self.position,
            "rotation": self.rotation,
            "head_pitch": self.head_pitch,
            "state": self.state.value,
        }


class HumanoidController:
    """
    Controls a humanoid avatar in the Habitat scene.
    
    The humanoid follows navigation paths and can be controlled
    by the ObjectNav demo or autonomous agents.
    
    Supports first-person and third-person view modes (toggle with V key).
    """
    
    def __init__(
        self,
        config: Optional[HumanoidConfig] = None,
    ):
        self.config = config or HumanoidConfig()
        self.pose = HumanoidPose(
            position=(0.0, 0.0, 0.0),
            rotation=0.0,
        )
        
        # View mode
        self.view_mode = ViewMode.FIRST_PERSON
        self._view_transition = 0.0  # 0 = first person, 1 = third person
        self._transition_speed = 5.0  # Transition speed
        
        # Navigation state
        self.target_position: Optional[Tuple[float, float, float]] = None
        self.path: List[Tuple[float, float, float]] = []
        self.current_path_index = 0
        
        # Habitat objects (set by attach_to_sim)
        self._sim: Optional["habitat_sim.Simulator"] = None
        self._rigid_object = None
        self._articulated_object = None
        
        # Stats
        self._distance_traveled = 0.0
        self._actions_taken = 0
    
    def attach_to_sim(
        self,
        sim: "habitat_sim.Simulator",
        position: Optional[Tuple[float, float, float]] = None,
    ) -> bool:
        """
        Attach humanoid to a Habitat simulator.
        
        Note: Full articulated humanoid requires proper URDF and animations.
        For now, we track position/rotation and can render a simple marker.
        
        Args:
            sim: Habitat simulator instance
            position: Initial position (uses current pose if None)
            
        Returns:
            True if attached successfully
        """
        self._sim = sim
        
        if position:
            self.pose.position = position
        
        # Try to load humanoid model if available
        if self.config.model_path:
            success = self._load_humanoid_model()
            if success:
                logger.info("Loaded humanoid model")
                return True
            else:
                logger.warning("Failed to load humanoid model, using virtual tracking")
        
        logger.info(f"Humanoid controller attached at {self.pose.position}")
        return True
    
    def _load_humanoid_model(self) -> bool:
        """Load a humanoid model from URDF or GLB."""
        # TODO: Implement proper humanoid loading with animations
        # For now, return False to use virtual tracking
        return False
    
    def sync_with_agent(
        self,
        agent_position: Tuple[float, float, float],
        agent_rotation: Tuple[float, float, float, float],  # Quaternion
    ):
        """
        Sync humanoid position with the agent's position.
        
        Args:
            agent_position: Agent's current position
            agent_rotation: Agent's rotation as quaternion (x, y, z, w)
        """
        self.pose.position = tuple(agent_position)
        
        # Convert quaternion to yaw
        qx, qy, qz, qw = agent_rotation
        yaw = math.atan2(2 * (qw * qy + qx * qz), 1 - 2 * (qy * qy + qz * qz))
        self.pose.rotation = yaw
    
    def set_target(
        self,
        target: Tuple[float, float, float],
        path: Optional[List[Tuple[float, float, float]]] = None,
    ):
        """
        Set navigation target.
        
        Args:
            target: Target position
            path: Optional path to follow (computed if None)
        """
        self.target_position = target
        self.path = list(path) if path else [target]
        self.current_path_index = 0
        self.pose.state = HumanoidState.WALKING
    
    def update(self, action: Optional[str] = None) -> str:
        """
        Update humanoid based on action.
        
        Args:
            action: Action to execute ("move_forward", "turn_left", "turn_right")
            
        Returns:
            Current state as string
        """
        if action is None:
            self.pose.state = HumanoidState.IDLE
            return self.pose.state.value
        
        self._actions_taken += 1
        
        if action == "move_forward":
            # Move in the current facing direction
            dx = -math.sin(self.pose.rotation) * self.config.walk_speed * 0.15
            dz = -math.cos(self.pose.rotation) * self.config.walk_speed * 0.15
            
            new_pos = (
                self.pose.position[0] + dx,
                self.pose.position[1],
                self.pose.position[2] + dz,
            )
            
            self._distance_traveled += 0.15
            self.pose.position = new_pos
            self.pose.state = HumanoidState.WALKING
            
        elif action == "turn_left":
            self.pose.rotation += math.radians(5.0)  # 5 degrees
            self.pose.state = HumanoidState.TURNING
            
        elif action == "turn_right":
            self.pose.rotation -= math.radians(5.0)
            self.pose.state = HumanoidState.TURNING
        
        # Check if reached target
        if self.target_position:
            dist = math.sqrt(
                (self.pose.position[0] - self.target_position[0])**2 +
                (self.pose.position[2] - self.target_position[2])**2
            )
            if dist < 0.5:
                self.pose.state = HumanoidState.ARRIVED
                self.target_position = None
        
        return self.pose.state.value
    
    def get_position(self) -> Tuple[float, float, float]:
        """Get current position."""
        return self.pose.position
    
    def get_rotation(self) -> float:
        """Get current yaw rotation in radians."""
        return self.pose.rotation
    
    def toggle_view_mode(self) -> ViewMode:
        """
        Toggle between first-person and third-person view.
        
        Returns:
            New view mode
        """
        if self.view_mode == ViewMode.FIRST_PERSON:
            self.view_mode = ViewMode.THIRD_PERSON
        else:
            self.view_mode = ViewMode.FIRST_PERSON
        
        logger.info(f"View mode: {self.view_mode.value}")
        return self.view_mode
    
    def set_view_mode(self, mode: ViewMode):
        """Set the view mode."""
        self.view_mode = mode
    
    def update_view_transition(self, dt: float):
        """
        Update smooth transition between view modes.
        
        Args:
            dt: Delta time in seconds
        """
        target = 1.0 if self.view_mode == ViewMode.THIRD_PERSON else 0.0
        
        if self._view_transition < target:
            self._view_transition = min(target, self._view_transition + dt * self._transition_speed)
        elif self._view_transition > target:
            self._view_transition = max(target, self._view_transition - dt * self._transition_speed)
    
    def get_first_person_camera(self) -> Tuple[Tuple[float, float, float], Tuple[float, float, float, float]]:
        """
        Get camera position and rotation for first-person view.
        
        Returns:
            Tuple of (position, quaternion rotation)
        """
        # Position at eye height
        pos = (
            self.pose.position[0],
            self.pose.position[1] + self.config.eye_height,
            self.pose.position[2],
        )
        
        # Create quaternion from yaw and pitch
        yaw = self.pose.rotation
        pitch = self.pose.head_pitch
        
        # Convert euler to quaternion (yaw, pitch, roll=0)
        cy = math.cos(yaw / 2)
        sy = math.sin(yaw / 2)
        cp = math.cos(pitch / 2)
        sp = math.sin(pitch / 2)
        
        qw = cy * cp
        qx = cy * sp
        qy = sy * cp
        qz = sy * sp
        
        return pos, (qx, qy, qz, qw)
    
    def get_third_person_camera(self) -> Tuple[Tuple[float, float, float], Tuple[float, float, float, float]]:
        """
        Get camera position and rotation for third-person view.
        
        Returns:
            Tuple of (position, quaternion rotation)
        """
        # Camera position behind and above the humanoid
        dist = self.config.third_person_distance
        height = self.config.third_person_height
        
        # Calculate camera offset based on humanoid rotation
        yaw = self.pose.rotation
        cam_x = self.pose.position[0] + dist * math.sin(yaw)
        cam_z = self.pose.position[2] + dist * math.cos(yaw)
        cam_y = self.pose.position[1] + height
        
        pos = (cam_x, cam_y, cam_z)
        
        # Camera looks at humanoid (pitch down)
        pitch = math.radians(self.config.third_person_pitch)
        
        # Quaternion looking at humanoid
        cy = math.cos((yaw + math.pi) / 2)
        sy = math.sin((yaw + math.pi) / 2)
        cp = math.cos(pitch / 2)
        sp = math.sin(pitch / 2)
        
        qw = cy * cp
        qx = cy * sp
        qy = sy * cp
        qz = sy * sp
        
        return pos, (qx, qy, qz, qw)
    
    def get_current_camera(self) -> Tuple[Tuple[float, float, float], Tuple[float, float, float, float]]:
        """
        Get current camera position and rotation based on view mode.
        
        Returns:
            Tuple of (position, quaternion rotation)
        """
        if self._view_transition <= 0.0:
            return self.get_first_person_camera()
        elif self._view_transition >= 1.0:
            return self.get_third_person_camera()
        else:
            # Interpolate between views
            fp_pos, fp_rot = self.get_first_person_camera()
            tp_pos, tp_rot = self.get_third_person_camera()
            
            t = self._view_transition
            
            # Lerp position
            pos = (
                fp_pos[0] * (1 - t) + tp_pos[0] * t,
                fp_pos[1] * (1 - t) + tp_pos[1] * t,
                fp_pos[2] * (1 - t) + tp_pos[2] * t,
            )
            
            # Slerp rotation (simplified lerp for now)
            rot = (
                fp_rot[0] * (1 - t) + tp_rot[0] * t,
                fp_rot[1] * (1 - t) + tp_rot[1] * t,
                fp_rot[2] * (1 - t) + tp_rot[2] * t,
                fp_rot[3] * (1 - t) + tp_rot[3] * t,
            )
            
            # Normalize quaternion
            mag = math.sqrt(sum(c * c for c in rot))
            rot = tuple(c / mag for c in rot)
            
            return pos, rot
    
    def look_at(self, target: Tuple[float, float, float]):
        """
        Rotate humanoid to look at a target.
        
        Args:
            target: Target position to look at
        """
        dx = target[0] - self.pose.position[0]
        dz = target[2] - self.pose.position[2]
        
        self.pose.rotation = math.atan2(dx, dz)
    
    def set_head_pitch(self, pitch: float):
        """
        Set head pitch (looking up/down).
        
        Args:
            pitch: Pitch angle in radians (positive = look up)
        """
        # Clamp to reasonable range
        self.pose.head_pitch = max(-math.pi / 4, min(math.pi / 4, pitch))
    
    @property
    def stats(self) -> Dict[str, Any]:
        """Get humanoid stats."""
        return {
            "distance_traveled": self._distance_traveled,
            "actions_taken": self._actions_taken,
            "current_state": self.pose.state.value,
            "view_mode": self.view_mode.value,
            "position": self.pose.position,
            "rotation_deg": math.degrees(self.pose.rotation),
        }


class HumanoidRenderer:
    """
    Renders humanoid avatar overlay when third-person view isn't available.
    
    Draws a simple avatar representation at the humanoid's position.
    """
    
    def __init__(self, controller: HumanoidController):
        self.controller = controller
        self._font = None
        self._font_small = None
        self._initialized = False
    
    def init_fonts(self):
        """Initialize fonts."""
        try:
            import pygame
            self._font = pygame.font.SysFont("Arial", 12)
            self._font_small = pygame.font.SysFont("Arial", 10)
            self._initialized = True
        except Exception:
            pass
    
    def render_minimap_avatar(
        self,
        surface,
        map_center: Tuple[int, int],
        map_scale: float,
        world_center: Tuple[float, float],
    ):
        """
        Render humanoid on a minimap.
        
        Args:
            surface: Pygame surface
            map_center: Center of minimap on screen
            map_scale: Pixels per meter
            world_center: World position at map center
        """
        try:
            import pygame
        except ImportError:
            return
        
        pos = self.controller.pose.position
        
        # Convert world to map coordinates
        dx = (pos[0] - world_center[0]) * map_scale
        dz = (pos[2] - world_center[1]) * map_scale
        
        map_x = int(map_center[0] + dx)
        map_y = int(map_center[1] + dz)
        
        # Draw avatar
        state = self.controller.pose.state
        color = {
            HumanoidState.IDLE: (0, 255, 0),
            HumanoidState.WALKING: (0, 200, 255),
            HumanoidState.TURNING: (255, 200, 0),
            HumanoidState.ARRIVED: (0, 255, 100),
        }.get(state, (255, 255, 255))
        
        # Body circle
        pygame.draw.circle(surface, color, (map_x, map_y), 8)
        pygame.draw.circle(surface, (255, 255, 255), (map_x, map_y), 8, 1)
        
        # Direction indicator
        rot = self.controller.pose.rotation
        end_x = map_x + int(12 * math.sin(rot))
        end_y = map_y + int(12 * math.cos(rot))
        pygame.draw.line(surface, (255, 255, 255), (map_x, map_y), (end_x, end_y), 2)
    
    def render_status(
        self,
        surface,
        position: Tuple[int, int] = (10, 100),
    ):
        """
        Render humanoid status panel.
        
        Args:
            surface: Pygame surface
            position: Top-left corner of panel
        """
        try:
            import pygame
        except ImportError:
            return
        
        if not self._initialized:
            self.init_fonts()
        
        if not self._font:
            return
        
        stats = self.controller.stats
        pose = self.controller.pose
        view_mode = self.controller.view_mode
        
        # Panel
        panel_width = 180
        panel_height = 100
        panel = pygame.Surface((panel_width, panel_height), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 150))
        
        # Title
        title = self._font.render("Humanoid Avatar", True, (0, 255, 255))
        panel.blit(title, (10, 5))
        
        # View mode with toggle hint
        view_color = (100, 200, 255) if view_mode == ViewMode.FIRST_PERSON else (255, 200, 100)
        view_text = self._font.render(f"View: {view_mode.value}", True, view_color)
        panel.blit(view_text, (10, 25))
        
        # State
        state_colors = {
            "idle": (100, 100, 100),
            "walking": (0, 255, 0),
            "turning": (255, 200, 0),
            "arrived": (0, 255, 100),
        }
        state_color = state_colors.get(pose.state.value, (255, 255, 255))
        state_text = self._font.render(f"State: {pose.state.value}", True, state_color)
        panel.blit(state_text, (10, 45))
        
        # Distance
        dist_text = self._font.render(
            f"Distance: {stats['distance_traveled']:.1f}m", True, (255, 255, 255)
        )
        panel.blit(dist_text, (10, 65))
        
        # Toggle hint
        hint_text = self._font_small.render("Press V to toggle view", True, (150, 150, 150))
        panel.blit(hint_text, (10, 85))
        
        surface.blit(panel, position)
