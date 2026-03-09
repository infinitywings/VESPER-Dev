"""
3D Sensor Bridge - Connect Habitat 3D sensors to simulated firmware sensors.

This module bridges the gap between:
- 3D spatial sensors (PIR motion, cameras) in Habitat-sim
- Simulated firmware sensors (pure Python)
- VESPER event bus

When the humanoid moves in 3D space:
1. Habitat sensors detect motion spatially
2. Bridge triggers corresponding firmware sensor events
3. Firmware sensors emit realistic sensor data
4. Events flow to VESPER event bus
"""

import asyncio
import time
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
import numpy as np

from vesper.habitat.sensors import PIRMotionSensor, SecurityCamera
from vesper.firmware.sensor_templates import (
    SensorNetwork,
    SensorConfig,
    SensorType,
    SimulatedSensor,
)
from vesper.core.event_bus import EventBus, Event, EventPriority


@dataclass
class SensorBridgeConfig:
    """Configuration for 3D-to-firmware sensor bridge."""
    enable_motion_sensors: bool = True
    enable_cameras: bool = True
    enable_environmental: bool = True  # Temperature, humidity, etc.
    
    # Environmental sensor update intervals
    temp_update_interval: float = 30.0
    humidity_update_interval: float = 60.0
    
    # Base temperature per room (will vary)
    base_temperature: float = 22.0
    base_humidity: float = 45.0
    
    # When humanoid enters a room, temperature/humidity change
    occupancy_temp_increase: float = 1.5  # Degrees C from body heat
    occupancy_humidity_increase: float = 5.0  # % from breathing


class Sensor3DBridge:
    """
    Bridge between 3D spatial sensors and simulated firmware sensors.
    
    This creates a realistic IoT simulation where:
    - 3D motion detection triggers firmware PIR sensor events
    - Room occupancy affects environmental sensors
    - Cameras trigger light level sensors
    - All events flow through firmware simulation to event bus
    """
    
    def __init__(
        self,
        config: SensorBridgeConfig,
        event_bus: Optional[EventBus] = None,
    ):
        self.config = config
        self.event_bus = event_bus
        
        # Create firmware sensor network
        self.sensor_network = SensorNetwork()
        
        # Mapping from 3D sensors to firmware sensors
        self._motion_sensor_map: Dict[str, str] = {}  # 3D device_id -> firmware device_id
        self._camera_map: Dict[str, str] = {}
        self._room_sensors: Dict[str, Dict[str, SimulatedSensor]] = {}
        
        # Room occupancy tracking
        self._room_occupancy: Dict[str, bool] = {}
        self._last_occupancy_change: Dict[str, float] = {}
        
        # Background task
        self._sensor_task: Optional[asyncio.Task] = None
        
        # Connect sensor network to event bus if provided
        if self.event_bus:
            self.sensor_network.on_sensor_data(self._on_firmware_sensor_data)
    
    def add_motion_sensor_3d(
        self,
        motion_sensor_3d: PIRMotionSensor,
        room_name: str,
    ) -> str:
        """
        Add a 3D motion sensor and create corresponding firmware sensor.
        Returns the firmware device ID.
        """
        if not self.config.enable_motion_sensors:
            return None
            
        # Create firmware motion sensor
        fw_device_id = f"{motion_sensor_3d.config.device_id}_fw"
        
        fw_config = SensorConfig(
            sensor_type=SensorType.MOTION,
            device_id=fw_device_id,
            location=room_name,
            update_interval=0.5,  # Fast updates for motion
            motion_probability=0.0,  # Controlled by 3D detection
            motion_cooldown=motion_sensor_3d.config.cooldown,
        )
        
        fw_sensor = self.sensor_network.add_sensor(fw_config)
        
        # Map 3D sensor to firmware sensor
        self._motion_sensor_map[motion_sensor_3d.config.device_id] = fw_device_id
        
        # Add to room sensors
        if room_name not in self._room_sensors:
            self._room_sensors[room_name] = {}
        self._room_sensors[room_name]["motion"] = fw_sensor
        
        return fw_device_id
    
    def add_camera_3d(
        self,
        camera_3d: SecurityCamera,
        room_name: str,
    ) -> str:
        """
        Add a 3D camera and create corresponding firmware light sensor.
        (Cameras can detect light levels)
        """
        if not self.config.enable_cameras:
            return None
            
        # Create firmware light sensor (cameras have light sensors)
        fw_device_id = f"{camera_3d.config.device_id}_light_fw"
        
        fw_config = SensorConfig(
            sensor_type=SensorType.LIGHT,
            device_id=fw_device_id,
            location=room_name,
            update_interval=5.0,
            initial_value=300.0,  # Normal indoor lighting
        )
        
        fw_sensor = self.sensor_network.add_sensor(fw_config)
        
        # Map 3D camera to firmware sensor
        self._camera_map[camera_3d.config.device_id] = fw_device_id
        
        if room_name not in self._room_sensors:
            self._room_sensors[room_name] = {}
        self._room_sensors[room_name]["light"] = fw_sensor
        
        return fw_device_id
    
    def add_environmental_sensors(self, room_name: str):
        """
        Add temperature and humidity sensors for a room.
        These react to room occupancy.
        """
        if not self.config.enable_environmental:
            return
            
        if room_name not in self._room_sensors:
            self._room_sensors[room_name] = {}
            
        # Temperature sensor
        temp_id = f"{room_name.replace(' ', '_')}_temp_fw"
        temp_config = SensorConfig(
            sensor_type=SensorType.TEMPERATURE,
            device_id=temp_id,
            location=room_name,
            update_interval=self.config.temp_update_interval,
            initial_value=self.config.base_temperature,
            noise_level=0.2,
        )
        temp_sensor = self.sensor_network.add_sensor(temp_config)
        self._room_sensors[room_name]["temperature"] = temp_sensor
        
        # Humidity sensor
        humidity_id = f"{room_name.replace(' ', '_')}_humidity_fw"
        humidity_config = SensorConfig(
            sensor_type=SensorType.HUMIDITY,
            device_id=humidity_id,
            location=room_name,
            update_interval=self.config.humidity_update_interval,
            initial_value=self.config.base_humidity,
            noise_level=0.5,
        )
        humidity_sensor = self.sensor_network.add_sensor(humidity_config)
        self._room_sensors[room_name]["humidity"] = humidity_sensor
        
        # Initialize occupancy tracking
        self._room_occupancy[room_name] = False
        self._last_occupancy_change[room_name] = time.time()
    
    def on_3d_motion_detected(
        self,
        sensor_3d_id: str,
        target_position: np.ndarray,
        room_name: str,
    ):
        """
        Called when a 3D motion sensor detects movement.
        Triggers the corresponding firmware sensor.
        """
        fw_device_id = self._motion_sensor_map.get(sensor_3d_id)
        if not fw_device_id:
            return
            
        # Trigger firmware motion sensor
        fw_sensor = self.sensor_network.sensors.get(fw_device_id)
        if fw_sensor and hasattr(fw_sensor, '_motion_detected'):
            # Force motion detection
            fw_sensor._motion_detected = True
            fw_sensor._last_motion = time.time()
            
        # Update room occupancy
        self._update_room_occupancy(room_name, occupied=True)
    
    def on_3d_motion_cleared(self, sensor_3d_id: str, room_name: str):
        """
        Called when motion clears in a room.
        """
        # Update room occupancy
        self._update_room_occupancy(room_name, occupied=False)
    
    def _update_room_occupancy(self, room_name: str, occupied: bool):
        """
        Update room occupancy and adjust environmental sensors.
        """
        if room_name not in self._room_occupancy:
            return
            
        # Check if state changed
        if self._room_occupancy[room_name] == occupied:
            return
            
        self._room_occupancy[room_name] = occupied
        self._last_occupancy_change[room_name] = time.time()
        
        # Adjust environmental sensors
        room_sensors = self._room_sensors.get(room_name, {})
        
        # Temperature increases when occupied (body heat)
        temp_sensor = room_sensors.get("temperature")
        if temp_sensor and hasattr(temp_sensor, '_temperature'):
            if occupied:
                # Increase temperature
                temp_sensor._temperature += self.config.occupancy_temp_increase
            else:
                # Gradually return to baseline
                temp_sensor._temperature -= self.config.occupancy_temp_increase * 0.5
                
        # Humidity increases when occupied (breathing)
        humidity_sensor = room_sensors.get("humidity")
        if humidity_sensor and hasattr(humidity_sensor, '_humidity'):
            if occupied:
                humidity_sensor._humidity += self.config.occupancy_humidity_increase
            else:
                humidity_sensor._humidity -= self.config.occupancy_humidity_increase * 0.5
    
    def _on_firmware_sensor_data(self, device_id: str, key: str, value: Any):
        """
        Handle sensor data from firmware sensors and publish to event bus.
        """
        if not self.event_bus:
            return
            
        # Find which room this sensor belongs to
        room_name = "unknown"
        for room, sensors in self._room_sensors.items():
            for sensor_type, sensor in sensors.items():
                if sensor.config.device_id == device_id:
                    room_name = room
                    break
        
        # Create event using core Event class
        event = Event(
            event_type=f"sensor_{key}",
            source_id=device_id,
            payload={
                "key": key,
                "value": value,
                "room": room_name,
                "device_id": device_id,
            },
            priority=EventPriority.NORMAL,
            timestamp=time.time(),
        )
        
        # Publish to event bus
        self.event_bus.publish(event)
    
    async def start(self):
        """Start the sensor network."""
        await self.sensor_network.start()
        
    async def stop(self):
        """Stop the sensor network."""
        await self.sensor_network.stop()
    
    def get_sensor_stats(self) -> Dict[str, Any]:
        """Get statistics about the sensor bridge."""
        return {
            "total_firmware_sensors": len(self.sensor_network.sensors),
            "motion_sensors": len(self._motion_sensor_map),
            "cameras": len(self._camera_map),
            "rooms_with_sensors": len(self._room_sensors),
            "room_occupancy": dict(self._room_occupancy),
        }
    
    def send_command_to_sensor(
        self,
        device_id: str,
        command: str,
        args: Optional[str] = None,
    ) -> str:
        """Send a command to a firmware sensor."""
        return self.sensor_network.send_command(device_id, command, args)


def create_sensor_bridge_for_scene(
    motion_sensors_3d: List[PIRMotionSensor],
    cameras_3d: List[SecurityCamera],
    room_sensor_state: Dict[str, Dict],
    event_bus: Optional[EventBus] = None,
    config: Optional[SensorBridgeConfig] = None,
) -> Sensor3DBridge:
    """
    Convenience function to create a sensor bridge for an entire scene.
    
    Args:
        motion_sensors_3d: List of 3D motion sensors from Habitat
        cameras_3d: List of 3D cameras from Habitat
        room_sensor_state: Dict mapping room names to their sensor states
        event_bus: Optional event bus for publishing events
        config: Optional bridge configuration
    
    Returns:
        Configured Sensor3DBridge instance
    """
    if config is None:
        config = SensorBridgeConfig()
        
    bridge = Sensor3DBridge(config, event_bus)
    
    # Add all motion sensors
    for room_name, state in room_sensor_state.items():
        motion_sensor = state.get("motion_sensor")
        if motion_sensor:
            bridge.add_motion_sensor_3d(motion_sensor, room_name)
            
        camera = state.get("camera")
        if camera:
            bridge.add_camera_3d(camera, room_name)
            
        # Add environmental sensors for each room
        bridge.add_environmental_sensors(room_name)
    
    return bridge
