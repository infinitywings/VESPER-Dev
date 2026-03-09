"""
IoT Device models for Vesper.
"""

from vesper.devices.base import IoTDevice
from vesper.devices.motion_sensor import MotionSensor
from vesper.devices.contact_sensor import ContactSensor
from vesper.devices.smart_door import SmartDoor
from vesper.devices.light_sensor import LightSensor
from vesper.devices.security_camera import (
    SecurityCamera,
    SecurityCameraConfig,
    CameraMode,
    MountPosition,
)
from vesper.devices.scene_device_placer import (
    SceneDevicePlacer,
    SceneType,
    RoomType,
    RoomInfo,
    DevicePlacementConfig,
    PlacedDevice,
    create_devices_for_scene,
)
from vesper.devices.scene_configs import (
    SceneLayoutConfig,
    SCENE_LAYOUTS,
    HSSD_LAYOUT,
    HM3D_LAYOUT,
    REPLICA_CAD_LAYOUT,
    get_layout_for_scene,
    get_camera_mount_for_room,
    get_coverage_requirement,
)
from vesper.devices.manager import DeviceManager, DeviceManagerConfig, create_smart_home_setup

__all__ = [
    # Base
    "IoTDevice",
    
    # Sensors
    "MotionSensor",
    "ContactSensor",
    "SmartDoor",
    "LightSensor",
    
    # Camera
    "SecurityCamera",
    "SecurityCameraConfig",
    "CameraMode",
    "MountPosition",
    
    # Scene Placement
    "SceneDevicePlacer",
    "SceneType",
    "RoomType",
    "RoomInfo",
    "DevicePlacementConfig",
    "PlacedDevice",
    "create_devices_for_scene",
    
    # Scene Configs
    "SceneLayoutConfig",
    "SCENE_LAYOUTS",
    "HSSD_LAYOUT",
    "HM3D_LAYOUT",
    "REPLICA_CAD_LAYOUT",
    "get_layout_for_scene",
    "get_camera_mount_for_room",
    "get_coverage_requirement",
    
    # Manager
    "DeviceManager",
    "DeviceManagerConfig",
    "create_smart_home_setup",
]
