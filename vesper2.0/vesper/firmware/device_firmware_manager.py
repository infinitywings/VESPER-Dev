"""
VESPER Per-Device Firmware Manager

Manages firmware compilation, assignment, and deployment for individual
IoT devices. Each device in a scene gets its own firmware instance
matching its device type.

Supported device types:
    - motion_sensor: PIR motion detection
    - temperature_sensor: Thermal monitoring  
    - smart_light: Dimmable light with color temp
    - humidity_sensor: RH% + temperature combo
    - door_sensor: Magnetic contact sensor
    - smart_plug: Relay with power metering
"""

import os
import subprocess
import logging
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class DeviceType(Enum):
    """Supported firmware device types."""
    MOTION_SENSOR = "motion_sensor"
    TEMPERATURE_SENSOR = "temperature_sensor"
    SMART_LIGHT = "smart_light"
    HUMIDITY_SENSOR = "humidity_sensor"
    DOOR_SENSOR = "door_sensor"
    SMART_PLUG = "smart_plug"
    GENERIC = "sensor_firmware"  # Legacy fallback


# Mapping from common device names (as used in eval) to firmware types
DEVICE_NAME_MAP = {
    # Motion sensors
    "motion sensor": DeviceType.MOTION_SENSOR,
    "motion_sensor": DeviceType.MOTION_SENSOR,
    "pir sensor": DeviceType.MOTION_SENSOR,
    "occupancy sensor": DeviceType.MOTION_SENSOR,
    # Temperature sensors
    "temperature sensor": DeviceType.TEMPERATURE_SENSOR,
    "temperature_sensor": DeviceType.TEMPERATURE_SENSOR,
    "temp sensor": DeviceType.TEMPERATURE_SENSOR,
    "thermostat": DeviceType.TEMPERATURE_SENSOR,
    "thermometer": DeviceType.TEMPERATURE_SENSOR,
    # Smart lights
    "smart light": DeviceType.SMART_LIGHT,
    "smart_light": DeviceType.SMART_LIGHT,
    "light": DeviceType.SMART_LIGHT,
    "lamp": DeviceType.SMART_LIGHT,
    "ceiling light": DeviceType.SMART_LIGHT,
    "led light": DeviceType.SMART_LIGHT,
    "bulb": DeviceType.SMART_LIGHT,
    # Humidity sensors
    "humidity sensor": DeviceType.HUMIDITY_SENSOR,
    "humidity_sensor": DeviceType.HUMIDITY_SENSOR,
    "hygrometer": DeviceType.HUMIDITY_SENSOR,
    # Door sensors
    "door sensor": DeviceType.DOOR_SENSOR,
    "door_sensor": DeviceType.DOOR_SENSOR,
    "contact sensor": DeviceType.DOOR_SENSOR,
    "window sensor": DeviceType.DOOR_SENSOR,
    "entry sensor": DeviceType.DOOR_SENSOR,
    # Smart plugs
    "smart plug": DeviceType.SMART_PLUG,
    "smart_plug": DeviceType.SMART_PLUG,
    "outlet": DeviceType.SMART_PLUG,
    "plug": DeviceType.SMART_PLUG,
    "power strip": DeviceType.SMART_PLUG,
    "switch": DeviceType.SMART_PLUG,
    "smart switch": DeviceType.SMART_PLUG,
}


@dataclass
class DeviceFirmwareConfig:
    """Configuration for a single device's firmware instance."""
    device_id: str
    device_type: DeviceType
    firmware_path: str = ""
    tcp_port: int = 0
    device_name: str = ""
    room: str = ""
    # Runtime state
    container_id: Optional[str] = None
    qemu_pid: Optional[int] = None


@dataclass
class SceneFirmwareConfig:
    """All firmware instances for a scene."""
    scene_id: str
    devices: List[DeviceFirmwareConfig] = field(default_factory=list)
    base_port: int = 15011


class DeviceFirmwareManager:
    """
    Manages per-device firmware compilation, deployment, and lifecycle.
    
    Each device in a scene gets:
    1. Its own firmware binary (compiled from device-specific C source)
    2. Its own QEMU instance (via Docker container)
    3. Its own TCP port for communication
    """

    def __init__(self, workspace_root: Optional[str] = None):
        if workspace_root:
            self.workspace = Path(workspace_root)
        else:
            self.workspace = Path(__file__).parent.parent.parent
        
        self.firmware_src = self.workspace / "vesper" / "firmware" / "samples"
        self.device_types_src = self.firmware_src / "device_types"
        self.build_dir = self.workspace / "build" / "firmware"
        self.build_dir.mkdir(parents=True, exist_ok=True)
        
        # Track compiled firmware
        self._compiled: Dict[DeviceType, Path] = {}
        
    def resolve_device_type(self, device_name: str) -> DeviceType:
        """Map a device name string to a firmware DeviceType."""
        name_lower = device_name.lower().strip()
        
        # Direct match
        if name_lower in DEVICE_NAME_MAP:
            return DEVICE_NAME_MAP[name_lower]
        
        # Substring match
        for key, dtype in DEVICE_NAME_MAP.items():
            if key in name_lower or name_lower in key:
                return dtype
        
        # Keyword heuristics
        if any(w in name_lower for w in ["motion", "pir", "occupancy", "movement"]):
            return DeviceType.MOTION_SENSOR
        if any(w in name_lower for w in ["temp", "therm", "heat"]):
            return DeviceType.TEMPERATURE_SENSOR
        if any(w in name_lower for w in ["light", "lamp", "bulb", "led", "dimmer"]):
            return DeviceType.SMART_LIGHT
        if any(w in name_lower for w in ["humid", "moisture", "hygro"]):
            return DeviceType.HUMIDITY_SENSOR
        if any(w in name_lower for w in ["door", "window", "contact", "entry", "gate"]):
            return DeviceType.DOOR_SENSOR
        if any(w in name_lower for w in ["plug", "outlet", "relay", "switch", "power"]):
            return DeviceType.SMART_PLUG
        
        logger.warning(f"Unknown device type '{device_name}', using GENERIC firmware")
        return DeviceType.GENERIC

    def compile_firmware(self, device_type: DeviceType, force: bool = False) -> Path:
        """
        Compile firmware for a specific device type.
        Returns path to the compiled .elf file.
        """
        if device_type in self._compiled and not force:
            return self._compiled[device_type]
        
        type_name = device_type.value
        
        # Find source file
        if device_type == DeviceType.GENERIC:
            src_file = self.firmware_src / "sensor_firmware.c"
        else:
            src_file = self.device_types_src / f"{type_name}.c"
        
        if not src_file.exists():
            logger.warning(f"Source not found: {src_file}, falling back to generic")
            src_file = self.firmware_src / "sensor_firmware.c"
            type_name = "sensor_firmware"
        
        # Output paths
        out_dir = self.build_dir / type_name
        out_dir.mkdir(parents=True, exist_ok=True)
        elf_path = out_dir / f"{type_name}.elf"
        bin_path = out_dir / f"{type_name}.bin"
        
        # Check if already built
        if elf_path.exists() and not force:
            if elf_path.stat().st_mtime > src_file.stat().st_mtime:
                logger.info(f"Using cached firmware: {elf_path}")
                self._compiled[device_type] = elf_path
                return elf_path
        
        linker_script = self.firmware_src / "linker.ld"
        
        logger.info(f"Compiling firmware: {type_name} from {src_file}")
        
        # Compile
        cc = "arm-none-eabi-gcc"
        cflags = [
            "-mcpu=cortex-m3", "-mthumb",
            "-nostartfiles", "-nostdlib",
            "-Wall", "-Os",
            "-ffunction-sections", "-fdata-sections",
        ]
        ldflags = [f"-T{linker_script}", "-Wl,--gc-sections"]
        
        compile_cmd = [cc] + cflags + ldflags + ["-o", str(elf_path), str(src_file)]
        
        try:
            result = subprocess.run(
                compile_cmd, capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                logger.error(f"Compile failed for {type_name}: {result.stderr}")
                raise RuntimeError(f"Firmware compile failed: {result.stderr}")
            
            # Generate .bin
            subprocess.run(
                ["arm-none-eabi-objcopy", "-O", "binary", str(elf_path), str(bin_path)],
                capture_output=True, text=True, timeout=10
            )
            
            # Report size
            size_result = subprocess.run(
                ["arm-none-eabi-size", str(elf_path)],
                capture_output=True, text=True, timeout=5
            )
            if size_result.returncode == 0:
                logger.info(f"Firmware size ({type_name}):\n{size_result.stdout}")
            
            self._compiled[device_type] = elf_path
            logger.info(f"✓ Compiled {type_name}: {elf_path}")
            return elf_path
            
        except FileNotFoundError:
            logger.error("arm-none-eabi-gcc not found. Install: brew install arm-none-eabi-gcc")
            raise
    
    def compile_all(self, force: bool = False) -> Dict[DeviceType, Path]:
        """Compile all device firmware types."""
        results = {}
        for dtype in DeviceType:
            try:
                path = self.compile_firmware(dtype, force=force)
                results[dtype] = path
                logger.info(f"✓ {dtype.value}: {path}")
            except Exception as e:
                logger.error(f"✗ {dtype.value}: {e}")
        return results
    
    def create_scene_config(
        self,
        scene_id: str,
        room_devices: Dict[str, List[str]],
        base_port: int = 15011
    ) -> SceneFirmwareConfig:
        """
        Create firmware configuration for all devices in a scene.
        
        Args:
            scene_id: Unique scene identifier
            room_devices: Mapping of room name -> list of device names
            base_port: Starting TCP port for firmware instances
            
        Returns:
            SceneFirmwareConfig with per-device firmware assignments
        """
        config = SceneFirmwareConfig(scene_id=scene_id, base_port=base_port)
        port = base_port
        device_idx = 0
        
        for room, devices in room_devices.items():
            for dev_name in devices:
                device_type = self.resolve_device_type(dev_name)
                firmware_path = self.compile_firmware(device_type)
                
                dev_config = DeviceFirmwareConfig(
                    device_id=f"{scene_id}-{room}-dev{device_idx}",
                    device_type=device_type,
                    firmware_path=str(firmware_path),
                    tcp_port=port,
                    device_name=dev_name,
                    room=room,
                )
                config.devices.append(dev_config)
                port += 1
                device_idx += 1
        
        logger.info(
            f"Scene {scene_id}: {len(config.devices)} devices, "
            f"ports {base_port}-{port - 1}"
        )
        return config

    def get_docker_run_args(
        self,
        dev_config: DeviceFirmwareConfig,
        docker_image: str = "vesper-qemu-arm:latest",
    ) -> List[str]:
        """
        Generate docker run arguments for a specific device firmware.
        
        Returns list of args for subprocess.run(["docker", "run"] + args).
        """
        safe_name = dev_config.device_id.replace("/", "-").replace(" ", "_")
        container_name = f"vesper-fw-{safe_name}"
        
        return [
            "--rm", "-d",
            "--name", container_name,
            "-p", f"{dev_config.tcp_port}:15000",
            "-e", f"DEVICE_TYPE={dev_config.device_type.value}",
            "-e", f"DEVICE_ID={dev_config.device_id}",
            # Mount the specific firmware binary
            "-v", f"{dev_config.firmware_path}:/firmware/device.elf:ro",
            docker_image,
            "qemu-system-arm",
            "-machine", "lm3s6965evb",
            "-cpu", "cortex-m3",
            "-nographic",
            "-kernel", "/firmware/device.elf",
            "-serial", "tcp::15000,server,nowait",
        ]

    def deploy_scene(
        self,
        config: SceneFirmwareConfig,
        docker_image: str = "vesper-qemu-arm:latest",
    ) -> List[str]:
        """
        Deploy all device firmware for a scene using Docker.
        
        Returns list of container IDs.
        """
        container_ids = []
        
        for dev in config.devices:
            args = self.get_docker_run_args(dev, docker_image)
            cmd = ["docker", "run"] + args
            
            logger.info(f"Deploying {dev.device_id} ({dev.device_type.value}) on port {dev.tcp_port}")
            
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    cid = result.stdout.strip()[:12]
                    dev.container_id = cid
                    container_ids.append(cid)
                    logger.info(f"  ✓ Container {cid} for {dev.device_name}")
                else:
                    logger.error(f"  ✗ Failed: {result.stderr}")
            except Exception as e:
                logger.error(f"  ✗ Deploy error: {e}")
        
        return container_ids

    def teardown_scene(self, config: SceneFirmwareConfig):
        """Stop and remove all containers for a scene."""
        for dev in config.devices:
            if dev.container_id:
                try:
                    subprocess.run(
                        ["docker", "rm", "-f", dev.container_id],
                        capture_output=True, timeout=10
                    )
                    logger.info(f"Removed container {dev.container_id}")
                except Exception:
                    pass
            dev.container_id = None


def get_firmware_for_device(device_name: str, workspace: Optional[str] = None) -> Tuple[DeviceType, Path]:
    """
    Convenience: resolve device name to type and compile its firmware.
    Returns (DeviceType, path_to_elf).
    """
    mgr = DeviceFirmwareManager(workspace)
    dtype = mgr.resolve_device_type(device_name)
    path = mgr.compile_firmware(dtype)
    return dtype, path
