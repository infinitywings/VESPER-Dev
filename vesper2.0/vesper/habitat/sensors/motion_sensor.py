"""
PIR Motion Sensor simulation with realistic detection characteristics.

Features:
- Conical detection zone (not just radius)
- Configurable detection angle (typical PIR: 90-120°)
- Configurable detection range (typical: 5-12 meters)
- Mounting position and orientation
- Cooldown period between detections
- Sensitivity levels
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


class SensitivityLevel(Enum):
    """Motion sensor sensitivity levels."""
    LOW = "low"       # Less sensitive, fewer false positives
    MEDIUM = "medium" # Balanced
    HIGH = "high"     # More sensitive, may detect small movements


@dataclass
class MotionSensorConfig:
    """Configuration for a PIR motion sensor."""
    
    # Detection characteristics
    detection_range: float = 8.0  # meters (typical PIR: 5-12m)
    detection_angle: float = 110.0  # degrees (typical PIR: 90-120°)
    vertical_angle: float = 80.0  # degrees vertical coverage
    
    # Mounting
    mount_height: float = 2.2  # meters above floor
    orientation: float = 0.0  # degrees, 0 = facing +Z axis
    tilt: float = -15.0  # degrees, negative = looking down
    
    # Behavior
    sensitivity: SensitivityLevel = SensitivityLevel.MEDIUM
    cooldown: float = 3.0  # seconds between detections
    min_motion_speed: float = 0.1  # m/s minimum to trigger
    
    # Physical properties
    position: Tuple[float, float, float] = (0.0, 2.2, 0.0)
    room: str = "unknown"
    device_id: str = ""


@dataclass
class DetectionEvent:
    """A motion detection event."""
    device_id: str
    timestamp: float
    target_position: Tuple[float, float, float]
    target_id: str
    distance: float
    angle_from_center: float
    confidence: float  # 0.0 to 1.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "timestamp": self.timestamp,
            "target_position": self.target_position,
            "target_id": self.target_id,
            "distance": round(self.distance, 2),
            "angle_from_center": round(self.angle_from_center, 1),
            "confidence": round(self.confidence, 2),
        }


class PIRMotionSensor:
    """
    Realistic PIR (Passive Infrared) motion sensor simulation.
    
    Simulates a real PIR sensor with:
    - Conical detection zone
    - Range and angle limits
    - Cooldown between triggers
    - Sensitivity-based confidence
    """
    
    # Sensitivity multipliers for detection
    SENSITIVITY_PARAMS = {
        SensitivityLevel.LOW: {
            "range_mult": 0.7,
            "angle_mult": 0.8,
            "min_confidence": 0.6,
        },
        SensitivityLevel.MEDIUM: {
            "range_mult": 1.0,
            "angle_mult": 1.0,
            "min_confidence": 0.4,
        },
        SensitivityLevel.HIGH: {
            "range_mult": 1.2,
            "angle_mult": 1.1,
            "min_confidence": 0.2,
        },
    }
    
    def __init__(self, config: MotionSensorConfig):
        """
        Initialize the motion sensor.
        
        Args:
            config: Sensor configuration
        """
        self.config = config
        self.device_id = config.device_id or f"pir_{id(self)}"
        
        # State
        self.is_triggered = False
        self.last_trigger_time = 0.0
        self.last_detection: Optional[DetectionEvent] = None
        
        # Tracking for motion detection
        self._last_positions: Dict[str, Tuple[float, float, float]] = {}
        self._last_position_times: Dict[str, float] = {}
        
        # Precompute detection parameters
        self._update_detection_params()
        
        logger.debug(f"PIR sensor {self.device_id} initialized at {config.position}")
    
    def _update_detection_params(self):
        """Precompute detection parameters based on config."""
        sens_params = self.SENSITIVITY_PARAMS[self.config.sensitivity]
        
        self._effective_range = self.config.detection_range * sens_params["range_mult"]
        self._effective_angle = self.config.detection_angle * sens_params["angle_mult"]
        self._min_confidence = sens_params["min_confidence"]
        
        # Convert orientation to radians
        self._orientation_rad = math.radians(self.config.orientation)
        self._tilt_rad = math.radians(self.config.tilt)
        
        # Compute forward direction vector
        self._forward = np.array([
            math.sin(self._orientation_rad) * math.cos(self._tilt_rad),
            math.sin(self._tilt_rad),
            math.cos(self._orientation_rad) * math.cos(self._tilt_rad),
        ])
    
    @property
    def position(self) -> Tuple[float, float, float]:
        """Get sensor position."""
        return self.config.position
    
    @property
    def room(self) -> str:
        """Get sensor room."""
        return self.config.room
    
    def update(
        self,
        targets: Dict[str, Tuple[float, float, float]],
        current_time: Optional[float] = None,
    ) -> List[DetectionEvent]:
        """
        Update sensor state and check for motion.
        
        Args:
            targets: Dict of target_id -> (x, y, z) positions
            current_time: Current simulation time (uses real time if None)
            
        Returns:
            List of detection events (empty if no motion detected)
        """
        if current_time is None:
            current_time = time.time()
        
        events = []
        
        # Check cooldown
        if current_time - self.last_trigger_time < self.config.cooldown:
            return events
        
        for target_id, position in targets.items():
            detection = self._check_target(target_id, position, current_time)
            if detection:
                events.append(detection)
                self.is_triggered = True
                self.last_trigger_time = current_time
                self.last_detection = detection
                
                logger.debug(
                    f"[{self.device_id}] Motion detected: {target_id} "
                    f"at {detection.distance:.1f}m, {detection.angle_from_center:.0f}°"
                )
                break  # Only one detection per update
        
        # Update position history
        for target_id, position in targets.items():
            self._last_positions[target_id] = position
            self._last_position_times[target_id] = current_time
        
        # Reset triggered state if cooldown expired and no new detection
        if not events and current_time - self.last_trigger_time >= self.config.cooldown:
            self.is_triggered = False
        
        return events
    
    def _check_target(
        self,
        target_id: str,
        position: Tuple[float, float, float],
        current_time: float,
    ) -> Optional[DetectionEvent]:
        """
        Check if a target is detected.
        
        Args:
            target_id: Target identifier
            position: Target position (x, y, z)
            current_time: Current time
            
        Returns:
            DetectionEvent if detected, None otherwise
        """
        sensor_pos = np.array(self.config.position)
        target_pos = np.array(position)
        
        # Vector from sensor to target
        to_target = target_pos - sensor_pos
        distance = np.linalg.norm(to_target)
        
        # Check range
        if distance > self._effective_range or distance < 0.3:
            return None
        
        # Normalize direction
        to_target_norm = to_target / distance
        
        # Calculate angle from sensor forward direction
        dot_product = np.dot(self._forward, to_target_norm)
        dot_product = np.clip(dot_product, -1.0, 1.0)
        angle = math.degrees(math.acos(dot_product))
        
        # Check if within detection cone
        half_angle = self._effective_angle / 2
        if angle > half_angle:
            return None
        
        # Check for motion (velocity)
        if target_id in self._last_positions:
            last_pos = np.array(self._last_positions[target_id])
            last_time = self._last_position_times[target_id]
            dt = current_time - last_time
            
            if dt > 0:
                velocity = np.linalg.norm(target_pos - last_pos) / dt
                if velocity < self.config.min_motion_speed:
                    return None
        
        # Calculate detection confidence
        # Higher confidence when target is closer and more centered
        range_factor = 1.0 - (distance / self._effective_range)
        angle_factor = 1.0 - (angle / half_angle)
        confidence = (range_factor * 0.6 + angle_factor * 0.4)
        
        if confidence < self._min_confidence:
            return None
        
        return DetectionEvent(
            device_id=self.device_id,
            timestamp=current_time,
            target_position=position,
            target_id=target_id,
            distance=distance,
            angle_from_center=angle,
            confidence=confidence,
        )
    
    def get_detection_cone_points(
        self,
        num_points: int = 32,
    ) -> List[Tuple[float, float, float]]:
        """
        Get points defining the detection cone for visualization.
        
        Args:
            num_points: Number of points around the cone base
            
        Returns:
            List of (x, y, z) points forming the cone
        """
        points = []
        sensor_pos = np.array(self.config.position)
        
        # Apex of cone (sensor position)
        points.append(tuple(sensor_pos))
        
        half_angle = math.radians(self._effective_angle / 2)
        
        # Generate points around the cone base
        for i in range(num_points):
            theta = 2 * math.pi * i / num_points
            
            # Direction in local space
            local_dir = np.array([
                math.sin(half_angle) * math.cos(theta),
                math.sin(half_angle) * math.sin(theta),
                math.cos(half_angle),
            ])
            
            # Rotate by sensor orientation
            cos_o = math.cos(self._orientation_rad)
            sin_o = math.sin(self._orientation_rad)
            cos_t = math.cos(self._tilt_rad)
            sin_t = math.sin(self._tilt_rad)
            
            # Apply rotation (yaw then pitch)
            rotated = np.array([
                cos_o * local_dir[0] - sin_o * local_dir[2],
                cos_t * local_dir[1] - sin_t * (sin_o * local_dir[0] + cos_o * local_dir[2]),
                sin_t * local_dir[1] + cos_t * (sin_o * local_dir[0] + cos_o * local_dir[2]),
            ])
            
            # Scale by range and add sensor position
            point = sensor_pos + rotated * self._effective_range
            points.append(tuple(point))
        
        return points
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert sensor state to dictionary."""
        return {
            "device_id": self.device_id,
            "type": "pir_motion_sensor",
            "position": self.config.position,
            "room": self.config.room,
            "is_triggered": self.is_triggered,
            "detection_range": self.config.detection_range,
            "detection_angle": self.config.detection_angle,
            "sensitivity": self.config.sensitivity.value,
            "last_detection": self.last_detection.to_dict() if self.last_detection else None,
        }
    
    @property
    def state(self) -> str:
        """Get current state as string."""
        return "triggered" if self.is_triggered else "idle"


class MotionSensorManager:
    """
    Manages multiple PIR motion sensors.
    """
    
    def __init__(self):
        self.sensors: Dict[str, PIRMotionSensor] = {}
    
    def add_sensor(
        self,
        device_id: str,
        position: Tuple[float, float, float],
        room: str,
        orientation: float = 0.0,
        **kwargs,
    ) -> PIRMotionSensor:
        """
        Add a new motion sensor.
        
        Args:
            device_id: Unique sensor ID
            position: (x, y, z) position
            room: Room name
            orientation: Facing direction in degrees
            **kwargs: Additional MotionSensorConfig parameters
            
        Returns:
            The created sensor
        """
        config = MotionSensorConfig(
            device_id=device_id,
            position=position,
            room=room,
            orientation=orientation,
            **kwargs,
        )
        sensor = PIRMotionSensor(config)
        self.sensors[device_id] = sensor
        return sensor
    
    def update_all(
        self,
        targets: Dict[str, Tuple[float, float, float]],
        current_time: Optional[float] = None,
    ) -> List[DetectionEvent]:
        """
        Update all sensors and return detection events.
        
        Args:
            targets: Dict of target_id -> position
            current_time: Current simulation time
            
        Returns:
            List of all detection events
        """
        all_events = []
        for sensor in self.sensors.values():
            events = sensor.update(targets, current_time)
            all_events.extend(events)
        return all_events
    
    def get_triggered_sensors(self) -> List[str]:
        """Get list of currently triggered sensor IDs."""
        return [
            sensor_id for sensor_id, sensor in self.sensors.items()
            if sensor.is_triggered
        ]
    
    def get_sensor(self, device_id: str) -> Optional[PIRMotionSensor]:
        """Get a sensor by ID."""
        return self.sensors.get(device_id)
