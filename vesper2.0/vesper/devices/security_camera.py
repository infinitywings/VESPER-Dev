"""
Security Camera device for VESPER IoT simulation.

A camera device that integrates with the base IoT device system,
with proper angle/orientation calculation for different room layouts.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from vesper.core.event_bus import EventBus, EventPriority
from vesper.devices.base import IoTDevice


logger = logging.getLogger(__name__)


class CameraMode(Enum):
    """Camera operating modes."""
    FIXED = "fixed"         # Static position and orientation
    TRACKING = "tracking"   # Follows a target
    PATROL = "patrol"       # Pans between preset positions


class MountPosition(Enum):
    """Standard mounting positions for cameras in a room."""
    CORNER_NE = "corner_ne"   # Northeast corner (positive X, positive Z)
    CORNER_NW = "corner_nw"   # Northwest corner (negative X, positive Z)
    CORNER_SE = "corner_se"   # Southeast corner (positive X, negative Z)
    CORNER_SW = "corner_sw"   # Southwest corner (negative X, negative Z)
    WALL_N = "wall_north"     # North wall center
    WALL_S = "wall_south"     # South wall center
    WALL_E = "wall_east"      # East wall center  
    WALL_W = "wall_west"      # West wall center
    CENTER = "center"         # Ceiling center (looking down)
    ENTRANCE = "entrance"     # Near door, looking into room


@dataclass
class CameraPlacement:
    """Computed camera placement with position and orientation."""
    position: Tuple[float, float, float]
    pan: float  # Radians - rotation around Y axis (horizontal aim)
    tilt: float  # Radians - rotation around X axis (vertical aim)
    mount_position: MountPosition


@dataclass
class SecurityCameraConfig:
    """Configuration for a security camera device."""
    
    # Identification
    device_id: str = ""
    name: str = ""
    room: str = "unknown"
    
    # Position and orientation (can be auto-computed from room)
    position: Tuple[float, float, float] = (0.0, 2.5, 0.0)
    pan: float = 0.0    # Radians, 0 = facing +Z axis
    tilt: float = -0.52  # Radians, ~-30 degrees (looking down)
    
    # Mounting preference (for auto-placement)
    preferred_mount: MountPosition = MountPosition.CORNER_NE
    mount_height: float = 2.5  # Meters above floor
    wall_offset: float = 0.2   # Distance from wall
    
    # Optics
    horizontal_fov: float = 90.0  # Degrees
    vertical_fov: float = 60.0    # Degrees
    max_range: float = 15.0       # Meters
    min_range: float = 0.5        # Meters
    resolution: Tuple[int, int] = (640, 480)
    
    # PTZ limits
    pan_min: float = -1.57   # -90 degrees in radians
    pan_max: float = 1.57    # +90 degrees in radians
    tilt_min: float = -0.79  # -45 degrees
    tilt_max: float = 0.26   # +15 degrees
    pan_speed: float = 0.79  # Radians per second (~45 deg/s)
    tilt_speed: float = 0.52 # Radians per second (~30 deg/s)
    
    # Behavior
    mode: CameraMode = CameraMode.FIXED
    motion_threshold: float = 0.05  # Meters - movement threshold for motion detection


class SecurityCamera(IoTDevice):
    """
    Security camera device with proper angle calculations.
    
    Features:
    - Auto-placement based on room bounds
    - Correct orientation to point toward room center
    - Motion detection within field of view
    - PTZ (Pan-Tilt-Zoom) control
    - Target tracking mode
    
    Events:
        - camera_motion_detected: Motion detected in view
        - camera_target_acquired: Target entered view
        - camera_target_lost: Target left view
    """
    
    DEVICE_TYPE = "security_camera"
    EVENT_MOTION_DETECTED = "camera_motion_detected"
    EVENT_TARGET_ACQUIRED = "camera_target_acquired"
    EVENT_TARGET_LOST = "camera_target_lost"
    
    def __init__(
        self,
        config: SecurityCameraConfig,
        event_bus: Optional[EventBus] = None,
    ):
        """
        Initialize the security camera.
        
        Args:
            config: Camera configuration
            event_bus: Event bus for emitting events
        """
        super().__init__(
            device_type=self.DEVICE_TYPE,
            location=config.position,
            device_id=config.device_id or f"cam_{id(self)}",
            event_bus=event_bus,
            name=config.name or f"Camera {config.room}",
        )
        
        self.config = config
        
        # Current orientation (may differ from config in tracking mode)
        self.current_pan = config.pan
        self.current_tilt = config.tilt
        
        # State
        self.targets_in_view: List[str] = []
        self.motion_detected = False
        self._previous_targets_in_view: List[str] = []
        
        # Tracking state
        self._tracking_target_id: Optional[str] = None
        self._last_target_positions: Dict[str, Tuple[float, float, float]] = {}
        self._last_update_time: float = 0.0
        
        logger.debug(f"Camera {self._device_id} initialized at {config.position}")
    
    @classmethod
    def compute_placement_for_room(
        cls,
        room_center: Tuple[float, float, float],
        room_bounds: Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = None,
        mount_position: MountPosition = MountPosition.CORNER_NE,
        mount_height: float = 2.5,
        room_size_estimate: float = 4.0,
    ) -> CameraPlacement:
        """
        Compute optimal camera placement and orientation for a room.
        
        The camera is placed at a corner or wall position, pointing toward
        the room center with an appropriate downward tilt to see the floor.
        
        Args:
            room_center: (x, y, z) center position of the room
            room_bounds: Optional ((min_x, min_y, min_z), (max_x, max_y, max_z))
            mount_position: Where to mount the camera
            mount_height: Height above floor
            room_size_estimate: Estimated room size if bounds not provided
            
        Returns:
            CameraPlacement with position and orientation
        """
        cx, cy, cz = room_center
        
        # Determine room extents
        if room_bounds:
            min_b, max_b = room_bounds
            half_x = (max_b[0] - min_b[0]) / 2
            half_z = (max_b[2] - min_b[2]) / 2
        else:
            half_x = room_size_estimate / 2
            half_z = room_size_estimate / 2
        
        # Wall offset to keep camera slightly in front of wall
        wall_offset = 0.3
        
        # Compute position based on mount position
        mount_offsets = {
            MountPosition.CORNER_NE: (half_x - wall_offset, half_z - wall_offset),
            MountPosition.CORNER_NW: (-half_x + wall_offset, half_z - wall_offset),
            MountPosition.CORNER_SE: (half_x - wall_offset, -half_z + wall_offset),
            MountPosition.CORNER_SW: (-half_x + wall_offset, -half_z + wall_offset),
            MountPosition.WALL_N: (0, half_z - wall_offset),
            MountPosition.WALL_S: (0, -half_z + wall_offset),
            MountPosition.WALL_E: (half_x - wall_offset, 0),
            MountPosition.WALL_W: (-half_x + wall_offset, 0),
            MountPosition.CENTER: (0, 0),
            MountPosition.ENTRANCE: (half_x - wall_offset, -half_z + 0.5),
        }
        
        offset_x, offset_z = mount_offsets.get(mount_position, (half_x - wall_offset, half_z - wall_offset))
        
        position = (cx + offset_x, mount_height, cz + offset_z)
        
        # Calculate pan angle to point toward room center
        # Vector from camera to center
        to_center_x = cx - position[0]
        to_center_z = cz - position[2]
        
        # Pan angle: rotation around Y axis
        # atan2(x, z) gives angle where 0 = +Z axis
        pan = math.atan2(to_center_x, to_center_z)
        
        # Calculate tilt angle to look at floor center
        # Distance to center (horizontal)
        horizontal_distance = math.sqrt(to_center_x**2 + to_center_z**2)
        
        # Height difference (camera height - floor level at center)
        height_diff = mount_height - cy
        
        # Tilt: angle to look down at floor
        # Negative tilt = looking down
        if horizontal_distance > 0.1:
            tilt = -math.atan2(height_diff, horizontal_distance)
        else:
            # Directly above center - look straight down
            tilt = -math.pi / 2 if mount_position == MountPosition.CENTER else -0.52
        
        # Clamp tilt to reasonable range
        tilt = max(-math.pi/2, min(0.26, tilt))
        
        return CameraPlacement(
            position=position,
            pan=pan,
            tilt=tilt,
            mount_position=mount_position,
        )
    
    @classmethod
    def from_room_layout(
        cls,
        room_name: str,
        room_center: Tuple[float, float, float],
        room_bounds: Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = None,
        mount_position: MountPosition = MountPosition.CORNER_NE,
        mount_height: float = 2.5,
        event_bus: Optional[EventBus] = None,
        **kwargs,
    ) -> "SecurityCamera":
        """
        Create a camera automatically placed and oriented for a room.
        
        Args:
            room_name: Name of the room
            room_center: (x, y, z) center of the room
            room_bounds: Optional room bounding box
            mount_position: Where to mount the camera
            mount_height: Height above floor
            event_bus: Event bus for events
            **kwargs: Additional config parameters
            
        Returns:
            Configured SecurityCamera instance
        """
        placement = cls.compute_placement_for_room(
            room_center=room_center,
            room_bounds=room_bounds,
            mount_position=mount_position,
            mount_height=mount_height,
        )
        
        room_id = room_name.replace(' ', '_').replace('.', '_')
        
        config = SecurityCameraConfig(
            device_id=f"cam_{room_id}",
            name=f"Camera {room_name}",
            room=room_name,
            position=placement.position,
            pan=placement.pan,
            tilt=placement.tilt,
            preferred_mount=placement.mount_position,
            mount_height=mount_height,
            **kwargs,
        )
        
        return cls(config, event_bus=event_bus)
    
    def update(self, dt: float) -> None:
        """
        Update device state for a simulation tick.
        
        Args:
            dt: Time delta in seconds since last update.
        """
        self._state.last_update_time += dt
        
        # Handle tracking mode - smooth pan/tilt toward target
        if self.config.mode == CameraMode.TRACKING and self._tracking_target_id:
            if self._tracking_target_id in self._last_target_positions:
                self._update_tracking(
                    self._last_target_positions[self._tracking_target_id],
                    dt
                )
    
    def update_with_targets(
        self,
        targets: Dict[str, Tuple[float, float, float]],
        current_time: Optional[float] = None,
        dt: float = 0.016,
    ) -> Dict[str, Any]:
        """
        Update camera with target positions and check for visibility.
        
        Args:
            targets: Dict of target_id -> (x, y, z) positions
            current_time: Current simulation time
            dt: Delta time since last update
            
        Returns:
            Dict with update results including visible targets
        """
        if current_time is None:
            current_time = time.time()
        
        self._state.last_update_time = current_time
        
        # Store previous state
        self._previous_targets_in_view = self.targets_in_view.copy()
        
        # Handle tracking mode
        if self.config.mode == CameraMode.TRACKING and self._tracking_target_id:
            self._update_tracking_target(targets, dt)
        
        # Check which targets are in view
        self.targets_in_view = []
        targets_info = []
        
        for target_id, position in targets.items():
            info = self._check_target_visibility(target_id, position)
            if info:
                self.targets_in_view.append(target_id)
                targets_info.append(info)
        
        # Detect motion
        self.motion_detected = self._detect_motion(targets)
        
        # Emit events for target changes
        self._emit_target_events()
        
        # Store positions for motion detection
        self._last_target_positions = dict(targets)
        
        return {
            "device_id": self._device_id,
            "timestamp": current_time,
            "targets_in_view": targets_info,
            "motion_detected": self.motion_detected,
            "pan": self.current_pan,
            "tilt": self.current_tilt,
        }
    
    def _update_tracking_target(
        self,
        targets: Dict[str, Tuple[float, float, float]],
        dt: float,
    ):
        """Update camera orientation to track target."""
        if self._tracking_target_id not in targets:
            return
        
        target_pos = np.array(targets[self._tracking_target_id])
        camera_pos = np.array(self.config.position)
        
        # Vector to target
        to_target = target_pos - camera_pos
        distance = np.linalg.norm(to_target)
        
        if distance < 0.1:
            return
        
        # Calculate desired pan and tilt
        horizontal_dist = math.sqrt(to_target[0]**2 + to_target[2]**2)
        
        desired_pan = math.atan2(to_target[0], to_target[2])
        desired_tilt = -math.atan2(to_target[1], horizontal_dist)
        
        # Clamp to limits
        desired_pan = np.clip(desired_pan, self.config.pan_min, self.config.pan_max)
        desired_tilt = np.clip(desired_tilt, self.config.tilt_min, self.config.tilt_max)
        
        # Smooth movement towards target
        pan_diff = desired_pan - self.current_pan
        tilt_diff = desired_tilt - self.current_tilt
        
        max_pan_delta = self.config.pan_speed * dt
        max_tilt_delta = self.config.tilt_speed * dt
        
        self.current_pan += np.clip(pan_diff, -max_pan_delta, max_pan_delta)
        self.current_tilt += np.clip(tilt_diff, -max_tilt_delta, max_tilt_delta)
    
    def _check_target_visibility(
        self,
        target_id: str,
        position: Tuple[float, float, float],
    ) -> Optional[Dict[str, Any]]:
        """
        Check if a target is within the camera's field of view.
        
        Uses proper 3D geometry with camera orientation.
        
        Returns:
            Target info dict if visible, None otherwise
        """
        camera_pos = np.array(self.config.position)
        target_pos = np.array(position)
        
        # Vector to target
        to_target = target_pos - camera_pos
        distance = np.linalg.norm(to_target)
        
        # Check range
        if distance < self.config.min_range or distance > self.config.max_range:
            return None
        
        # Calculate camera forward direction based on pan and tilt
        forward = np.array([
            math.sin(self.current_pan) * math.cos(self.current_tilt),
            -math.sin(self.current_tilt),
            math.cos(self.current_pan) * math.cos(self.current_tilt),
        ])
        
        # Camera right vector (for horizontal angle)
        right = np.array([
            math.cos(self.current_pan),
            0,
            -math.sin(self.current_pan),
        ])
        
        # Camera up vector
        up = np.cross(right, forward)
        
        # Normalize target direction
        to_target_norm = to_target / distance
        
        # Calculate angles using dot products
        forward_dot = np.dot(forward, to_target_norm)
        
        # Avoid divide by zero
        if forward_dot <= 0:
            return None  # Target is behind camera
        
        right_dot = np.dot(right, to_target_norm)
        up_dot = np.dot(up, to_target_norm)
        
        # Horizontal angle from center
        horizontal_angle = math.degrees(math.atan2(right_dot, forward_dot))
        # Vertical angle from center  
        vertical_angle = math.degrees(math.atan2(up_dot, forward_dot))
        
        # Check if within FOV
        half_h_fov = self.config.horizontal_fov / 2
        half_v_fov = self.config.vertical_fov / 2
        
        if abs(horizontal_angle) > half_h_fov or abs(vertical_angle) > half_v_fov:
            return None
        
        # Calculate normalized position in frame (0-1)
        frame_x = 0.5 + (horizontal_angle / self.config.horizontal_fov)
        frame_y = 0.5 - (vertical_angle / self.config.vertical_fov)
        
        return {
            "target_id": target_id,
            "distance": round(distance, 2),
            "horizontal_angle": round(horizontal_angle, 1),
            "vertical_angle": round(vertical_angle, 1),
            "frame_position": (round(frame_x, 3), round(frame_y, 3)),
        }
    
    def _detect_motion(
        self,
        targets: Dict[str, Tuple[float, float, float]],
    ) -> bool:
        """Detect if there's motion within the camera view."""
        for target_id in self.targets_in_view:
            if target_id in self._last_target_positions:
                old_pos = np.array(self._last_target_positions[target_id])
                new_pos = np.array(targets[target_id])
                movement = np.linalg.norm(new_pos - old_pos)
                if movement > self.config.motion_threshold:
                    return True
        return False
    
    def _emit_target_events(self):
        """Emit events for targets entering/leaving view."""
        # Targets that just entered view
        for target_id in self.targets_in_view:
            if target_id not in self._previous_targets_in_view:
                self.emit_event(
                    self.EVENT_TARGET_ACQUIRED,
                    {"target_id": target_id, "room": self.config.room},
                )
        
        # Targets that just left view
        for target_id in self._previous_targets_in_view:
            if target_id not in self.targets_in_view:
                self.emit_event(
                    self.EVENT_TARGET_LOST,
                    {"target_id": target_id, "room": self.config.room},
                )
    
    def set_tracking_target(self, target_id: Optional[str]):
        """Set the target to track."""
        self._tracking_target_id = target_id
        if target_id:
            self.config.mode = CameraMode.TRACKING
        else:
            self.config.mode = CameraMode.FIXED
    
    def set_orientation(self, pan: float, tilt: float):
        """Manually set camera orientation in radians."""
        self.current_pan = np.clip(pan, self.config.pan_min, self.config.pan_max)
        self.current_tilt = np.clip(tilt, self.config.tilt_min, self.config.tilt_max)
    
    def get_fov_corners(self) -> List[Tuple[float, float, float]]:
        """
        Get the four corners of the camera's field of view at max range.
        
        Returns:
            List of 4 corner points plus the camera position (5 total)
        """
        camera_pos = np.array(self.config.position)
        
        half_h = math.radians(self.config.horizontal_fov / 2)
        half_v = math.radians(self.config.vertical_fov / 2)
        
        corners = []
        
        # Calculate four corners
        for h_sign in [-1, 1]:
            for v_sign in [-1, 1]:
                h_angle = self.current_pan + h_sign * half_h
                v_angle = self.current_tilt + v_sign * half_v
                
                direction = np.array([
                    math.sin(h_angle) * math.cos(v_angle),
                    -math.sin(v_angle),
                    math.cos(h_angle) * math.cos(v_angle),
                ])
                
                corner = camera_pos + direction * self.config.max_range
                corners.append(tuple(corner))
        
        # Add camera position as first point
        return [tuple(camera_pos)] + corners
    
    def get_state(self) -> Dict[str, Any]:
        """Get the current state of the camera."""
        base_state = self.get_base_state()
        base_state.update({
            "room": self.config.room,
            "pan": round(self.current_pan, 3),
            "tilt": round(self.current_tilt, 3),
            "pan_degrees": round(math.degrees(self.current_pan), 1),
            "tilt_degrees": round(math.degrees(self.current_tilt), 1),
            "horizontal_fov": self.config.horizontal_fov,
            "vertical_fov": self.config.vertical_fov,
            "max_range": self.config.max_range,
            "mode": self.config.mode.value,
            "targets_in_view": self.targets_in_view,
            "motion_detected": self.motion_detected,
            "tracking_target": self._tracking_target_id,
        })
        return base_state
    
    @property
    def state_summary(self) -> str:
        """Get current state as a simple string."""
        if not self._state.is_active:
            return "inactive"
        if self.motion_detected:
            return "motion"
        if self.targets_in_view:
            return "tracking"
        return "idle"
    
    def __repr__(self) -> str:
        return (
            f"SecurityCamera(id={self._device_id}, room={self.config.room}, "
            f"pan={math.degrees(self.current_pan):.1f}°, tilt={math.degrees(self.current_tilt):.1f}°)"
        )
