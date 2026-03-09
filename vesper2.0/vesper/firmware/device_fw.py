"""
Device firmware abstraction.

Provides a high-level interface to firmware-backed devices.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from vesper.devices.base import IoTDevice
from vesper.firmware.emulator import FirmwareEmulator, EmulatorConfig
from vesper.firmware.bridge import FirmwareBridge, BridgeConfig
from vesper.core.event_bus import EventBus

logger = logging.getLogger(__name__)


class FirmwareState(str, Enum):
    """State of firmware device."""
    OFFLINE = "offline"
    BOOTING = "booting"
    ONLINE = "online"
    ERROR = "error"
    UPDATING = "updating"


@dataclass
class FirmwareDeviceConfig:
    """Configuration for firmware-backed device."""
    device_type: str = "generic"
    firmware_path: Optional[str] = None
    use_emulator: bool = True
    auto_start: bool = True


class DeviceFirmware(IoTDevice):
    """
    IoT device backed by real or emulated firmware.
    
    Combines:
    - Vesper device interface
    - Firmware emulator
    - Message bridge
    
    Example:
        config = FirmwareDeviceConfig(
            device_type="temperature_sensor",
            firmware_path="firmware/esp32_temp.elf",
        )
        
        device = DeviceFirmware(
            config=config,
            event_bus=event_bus,
        )
        
        device.start()
        temp = device.get_sensor_value("temperature")
    """
    
    def __init__(
        self,
        config: Optional[FirmwareDeviceConfig] = None,
        event_bus: Optional[EventBus] = None,
        location: tuple = (0.0, 0.0, 0.0),
        device_id: Optional[str] = None,
    ):
        super().__init__(
            device_type=config.device_type if config else "firmware",
            location=location,
            event_bus=event_bus,
            device_id=device_id,
        )
        
        self.fw_config = config or FirmwareDeviceConfig()
        self._fw_state = FirmwareState.OFFLINE
        self._emulator: Optional[FirmwareEmulator] = None
        self._bridge: Optional[FirmwareBridge] = None
        self._sensor_values: Dict[str, Any] = {}
        
        # Set up emulator if configured
        if self.fw_config.use_emulator:
            self._setup_emulator()
        
        if self.fw_config.auto_start:
            self.start()
    
    def _setup_emulator(self) -> None:
        """Set up firmware emulator."""
        emu_config = EmulatorConfig(
            firmware_path=self.fw_config.firmware_path,
        )
        self._emulator = FirmwareEmulator(emu_config)
        
        bridge_config = BridgeConfig(device_id=self.device_id)
        self._bridge = FirmwareBridge(
            self._emulator,
            self._event_bus,
            bridge_config,
        )
    
    @property
    def firmware_state(self) -> FirmwareState:
        return self._fw_state
    
    def start(self) -> bool:
        """Start the firmware device."""
        if self._fw_state == FirmwareState.ONLINE:
            return True
        
        self._fw_state = FirmwareState.BOOTING
        
        if self._bridge:
            if self._bridge.start():
                self._fw_state = FirmwareState.ONLINE
                self._enabled = True
                logger.info(f"Firmware device {self.device_id} started")
                return True
            else:
                self._fw_state = FirmwareState.ERROR
                return False
        
        # No emulator - just mark as online
        self._fw_state = FirmwareState.ONLINE
        self._enabled = True
        return True
    
    def stop(self) -> None:
        """Stop the firmware device."""
        if self._bridge:
            self._bridge.stop()
        if self._emulator:
            self._emulator.stop()
        
        self._fw_state = FirmwareState.OFFLINE
        self._enabled = False
    
    def send_command(self, command: str) -> bool:
        """Send command to firmware."""
        if self._bridge:
            return self._bridge.send_command(command)
        return False
    
    def get_sensor_value(self, sensor: str) -> Optional[Any]:
        """Get cached sensor value."""
        return self._sensor_values.get(sensor)
    
    def request_sensor_update(self, sensor: str) -> bool:
        """Request sensor to update its value."""
        command_map = {
            "temperature": "GET_TEMP",
            "humidity": "GET_HUMIDITY",
            "motion": "GET_MOTION",
        }
        cmd = command_map.get(sensor, f"GET_{sensor.upper()}")
        return self.send_command(cmd)
    
    def update(self, dt: float, **context) -> None:
        """Update device state."""
        super().update(dt, **context)
        
        # Check emulator health
        if self._emulator and not self._emulator.is_running:
            if self._fw_state == FirmwareState.ONLINE:
                self._fw_state = FirmwareState.ERROR
                logger.warning(f"Firmware device {self.device_id} lost connection")
    
    def get_state(self) -> Dict[str, Any]:
        """Get device state."""
        state = super().get_state() or {}
        state.update({
            "firmware_state": self._fw_state.value,
            "sensor_values": self._sensor_values.copy(),
            "has_emulator": self._emulator is not None,
        })
        return state
    
    def __del__(self):
        self.stop()
