"""
Security Camera simulation with humanoid tracking and field of view.

Features:
- Configurable field of view (FOV)
- Pan/tilt orientation
- Humanoid tracking mode
- Motion detection within view
- Visual FOV representation
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class CameraMode(Enum):
    """Camera operating modes."""
    FIXED = "fixed"         # Static position and orientation
    TRACKING = "tracking"   # Follows a target
    PATROL = "patrol"       # Pans between preset positions


@dataclass
class CameraConfig:
    """Configuration for a security camera."""
    
    # Optics
    horizontal_fov: float = 90.0  # degrees
    vertical_fov: float = 60.0    # degrees
    max_range: float = 15.0       # meters
    min_range: float = 0.5        # meters
    
    # Mounting
    position: Tuple[float, float, float] = (0.0, 2.5, 0.0)
    pan: float = 0.0    # degrees, 0 = facing +Z
    tilt: float = -10.0 # degrees, negative = looking down
    
    # PTZ limits (for tracking mode)
    pan_min: float = -90.0
    pan_max: float = 90.0
    tilt_min: float = -45.0
    tilt_max: float = 15.0
    pan_speed: float = 45.0   # degrees per second
    tilt_speed: float = 30.0  # degrees per second
    
    # Behavior
    mode: CameraMode = CameraMode.FIXED
    tracking_target: Optional[str] = None  # Target ID to track
    
    # Properties
    room: str = "unknown"
    device_id: str = ""
    resolution: Tuple[int, int] = (640, 480)


@dataclass
class CameraFrame:
    """A captured camera frame."""
    device_id: str
    timestamp: float
    targets_in_view: List[Dict[str, Any]]
    pan: float
    tilt: float
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "timestamp": self.timestamp,
            "targets_in_view": self.targets_in_view,
            "pan": round(self.pan, 1),
            "tilt": round(self.tilt, 1),
        }


class SecurityCamera:
    """
    Security camera simulation with tracking capabilities.
    
    Simulates a PTZ (Pan-Tilt-Zoom) security camera with:
    - Field of view detection
    - Target tracking
    - Motion detection
    """
    
    def __init__(self, config: CameraConfig):
        """
        Initialize the security camera.
        
        Args:
            config: Camera configuration
        """
        self.config = config
        self.device_id = config.device_id or f"cam_{id(self)}"
        
        # Current orientation (may differ from config in tracking mode)
        self.current_pan = config.pan
        self.current_tilt = config.tilt
        
        # State
        self.is_active = True
        self.targets_in_view: List[str] = []
        self.last_frame: Optional[CameraFrame] = None
        self.motion_detected = False
        
        # Tracking state
        self._tracking_target_id: Optional[str] = config.tracking_target
        self._last_target_positions: Dict[str, Tuple[float, float, float]] = {}
        
        logger.debug(f"Camera {self.device_id} initialized at {config.position}")
    
    @property
    def position(self) -> Tuple[float, float, float]:
        """Get camera position."""
        return self.config.position
    
    @property
    def room(self) -> str:
        """Get camera room."""
        return self.config.room
    
    def update(
        self,
        targets: Dict[str, Tuple[float, float, float]],
        current_time: Optional[float] = None,
        dt: float = 0.016,  # ~60 FPS
    ) -> CameraFrame:
        """
        Update camera state and check for targets in view.
        
        Args:
            targets: Dict of target_id -> (x, y, z) positions
            current_time: Current simulation time
            dt: Delta time since last update
            
        Returns:
            CameraFrame with current view state
        """
        if current_time is None:
            current_time = time.time()
        
        # Handle tracking mode
        if self.config.mode == CameraMode.TRACKING and self._tracking_target_id:
            self._update_tracking(targets, dt)
        
        # Check which targets are in view
        targets_in_view = []
        self.targets_in_view = []
        
        for target_id, position in targets.items():
            target_info = self._check_target_in_view(target_id, position)
            if target_info:
                targets_in_view.append(target_info)
                self.targets_in_view.append(target_id)
        
        # Check for motion
        self.motion_detected = self._detect_motion(targets, current_time)
        
        # Create frame
        frame = CameraFrame(
            device_id=self.device_id,
            timestamp=current_time,
            targets_in_view=targets_in_view,
            pan=self.current_pan,
            tilt=self.current_tilt,
        )
        self.last_frame = frame
        
        # Update position history
        self._last_target_positions = dict(targets)
        
        return frame
    
    def _update_tracking(
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
        
        desired_pan = math.degrees(math.atan2(to_target[0], to_target[2]))
        desired_tilt = math.degrees(math.atan2(-to_target[1], horizontal_dist))
        
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
    
    def _check_target_in_view(
        self,
        target_id: str,
        position: Tuple[float, float, float],
    ) -> Optional[Dict[str, Any]]:
        """
        Check if a target is within the camera's field of view.
        
        Returns:
            Target info dict if in view, None otherwise
        """
        camera_pos = np.array(self.config.position)
        target_pos = np.array(position)
        
        # Vector to target
        to_target = target_pos - camera_pos
        distance = np.linalg.norm(to_target)
        
        # Check range
        if distance < self.config.min_range or distance > self.config.max_range:
            return None
        
        # Calculate camera forward direction
        pan_rad = math.radians(self.current_pan)
        tilt_rad = math.radians(self.current_tilt)
        
        forward = np.array([
            math.sin(pan_rad) * math.cos(tilt_rad),
            -math.sin(tilt_rad),
            math.cos(pan_rad) * math.cos(tilt_rad),
        ])
        
        # Camera right vector (for horizontal angle)
        right = np.array([
            math.cos(pan_rad),
            0,
            -math.sin(pan_rad),
        ])
        
        # Camera up vector
        up = np.cross(right, forward)
        
        # Normalize target direction
        to_target_norm = to_target / distance
        
        # Calculate angles
        forward_dot = np.dot(forward, to_target_norm)
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
        current_time: float,
    ) -> bool:
        """Detect if there's motion within the camera view."""
        for target_id in self.targets_in_view:
            if target_id in self._last_target_positions:
                old_pos = np.array(self._last_target_positions[target_id])
                new_pos = np.array(targets[target_id])
                movement = np.linalg.norm(new_pos - old_pos)
                if movement > 0.05:  # Threshold for motion
                    return True
        return False
    
    def set_tracking_target(self, target_id: Optional[str]):
        """Set the target to track."""
        self._tracking_target_id = target_id
        if target_id:
            self.config.mode = CameraMode.TRACKING
        else:
            self.config.mode = CameraMode.FIXED
    
    def set_orientation(self, pan: float, tilt: float):
        """Manually set camera orientation."""
        self.current_pan = np.clip(pan, self.config.pan_min, self.config.pan_max)
        self.current_tilt = np.clip(tilt, self.config.tilt_min, self.config.tilt_max)
    
    def get_fov_corners(self) -> List[Tuple[float, float, float]]:
        """
        Get the four corners of the camera's field of view at max range.
        
        Returns:
            List of 4 corner points plus the camera position (5 total)
        """
        camera_pos = np.array(self.config.position)
        
        pan_rad = math.radians(self.current_pan)
        tilt_rad = math.radians(self.current_tilt)
        
        half_h = math.radians(self.config.horizontal_fov / 2)
        half_v = math.radians(self.config.vertical_fov / 2)
        
        corners = []
        
        # Calculate four corners
        for h_sign in [-1, 1]:
            for v_sign in [-1, 1]:
                h_angle = pan_rad + h_sign * half_h
                v_angle = tilt_rad + v_sign * half_v
                
                direction = np.array([
                    math.sin(h_angle) * math.cos(v_angle),
                    -math.sin(v_angle),
                    math.cos(h_angle) * math.cos(v_angle),
                ])
                
                corner = camera_pos + direction * self.config.max_range
                corners.append(tuple(corner))
        
        # Add camera position as first point
        return [tuple(camera_pos)] + corners
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert camera state to dictionary."""
        return {
            "device_id": self.device_id,
            "type": "security_camera",
            "position": self.config.position,
            "room": self.config.room,
            "is_active": self.is_active,
            "pan": round(self.current_pan, 1),
            "tilt": round(self.current_tilt, 1),
            "horizontal_fov": self.config.horizontal_fov,
            "vertical_fov": self.config.vertical_fov,
            "mode": self.config.mode.value,
            "targets_in_view": self.targets_in_view,
            "motion_detected": self.motion_detected,
        }
    
    @property
    def state(self) -> str:
        """Get current state as string."""
        if not self.is_active:
            return "inactive"
        if self.motion_detected:
            return "motion"
        if self.targets_in_view:
            return "tracking"
        return "idle"


class CameraManager:
    """
    Manages multiple security cameras.
    """
    
    def __init__(self):
        self.cameras: Dict[str, SecurityCamera] = {}
    
    def add_camera(
        self,
        device_id: str,
        position: Tuple[float, float, float],
        room: str,
        pan: float = 0.0,
        tilt: float = -10.0,
        **kwargs,
    ) -> SecurityCamera:
        """
        Add a new security camera.
        
        Args:
            device_id: Unique camera ID
            position: (x, y, z) position
            room: Room name
            pan: Initial pan angle
            tilt: Initial tilt angle
            **kwargs: Additional CameraConfig parameters
            
        Returns:
            The created camera
        """
        config = CameraConfig(
            device_id=device_id,
            position=position,
            room=room,
            pan=pan,
            tilt=tilt,
            **kwargs,
        )
        camera = SecurityCamera(config)
        self.cameras[device_id] = camera
        return camera
    
    def update_all(
        self,
        targets: Dict[str, Tuple[float, float, float]],
        current_time: Optional[float] = None,
        dt: float = 0.016,
    ) -> List[CameraFrame]:
        """
        Update all cameras and return frames.
        
        Args:
            targets: Dict of target_id -> position
            current_time: Current simulation time
            dt: Delta time
            
        Returns:
            List of camera frames
        """
        frames = []
        for camera in self.cameras.values():
            if camera.is_active:
                frame = camera.update(targets, current_time, dt)
                frames.append(frame)
        return frames
    
    def get_cameras_detecting_target(self, target_id: str) -> List[str]:
        """Get list of camera IDs that can see the target."""
        return [
            cam_id for cam_id, cam in self.cameras.items()
            if target_id in cam.targets_in_view
        ]
    
    def get_camera(self, device_id: str) -> Optional[SecurityCamera]:
        """Get a camera by ID."""
        return self.cameras.get(device_id)
