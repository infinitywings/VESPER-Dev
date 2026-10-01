"""
Geometric sensor models; visualization is an optional dependency.
"""

from vesper.habitat.sensors.motion_sensor import (
    PIRMotionSensor,
    MotionSensorConfig,
    DetectionEvent,
    SensitivityLevel,
)
from vesper.habitat.sensors.camera import (
    SecurityCamera,
    CameraConfig,
)
def __getattr__(name):
    if name in {"SensorVisualizer", "AvatarRenderer"}:
        from vesper.habitat.sensors import visualizer
        return getattr(visualizer, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "PIRMotionSensor",
    "MotionSensorConfig",
    "DetectionEvent",
    "SensitivityLevel",
    "SecurityCamera",
    "CameraConfig",
    "SensorVisualizer",
    "AvatarRenderer",
]
