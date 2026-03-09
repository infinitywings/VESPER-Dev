"""
Habitat-Sim integration module for Vesper.

Provides 3D simulation capabilities using Facebook's Habitat-Sim.
"""

from vesper.habitat.simulator import HabitatSimulator, SimulatorConfig
from vesper.habitat.scene import SceneManager, SceneConfig
from vesper.habitat.embodiment import EmbodiedAgent, EmbodimentConfig
from vesper.habitat.device_placement import DevicePlacer, PlacementConfig
from vesper.habitat.integration import HabitatIntegration, create_integration

# New modular components for 3D simulation
from vesper.habitat.smart_home import SmartHomeIoT, SCENE_ROOMS
from vesper.habitat.task_manager import TaskManager, DAILY_TASKS
from vesper.habitat.hud import VesperHUD

# IoT overlay and humanoid avatar
from vesper.habitat.iot_overlay import IoTDeviceManager, IoTOverlayRenderer, IoTDeviceInfo
from vesper.habitat.humanoid import HumanoidController, HumanoidRenderer, HumanoidState
from vesper.habitat.vesper_integration import VesperIntegration, VesperConfig, create_vesper_integration

__all__ = [
    "HabitatSimulator",
    "SimulatorConfig",
    "SceneManager",
    "SceneConfig",
    "EmbodiedAgent",
    "EmbodimentConfig",
    "DevicePlacer",
    "PlacementConfig",
    "HabitatIntegration",
    "create_integration",
    # New components
    "SmartHomeIoT",
    "SCENE_ROOMS",
    "TaskManager",
    "DAILY_TASKS",
    "VesperHUD",
    # IoT and Humanoid
    "IoTDeviceManager",
    "IoTOverlayRenderer", 
    "IoTDeviceInfo",
    "HumanoidController",
    "HumanoidRenderer",
    "HumanoidState",
    "VesperIntegration",
    "VesperConfig",
    "create_vesper_integration",
]
