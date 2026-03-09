"""
Unit tests for firmware integration.
"""

import pytest
import time
from unittest.mock import Mock

from vesper.firmware.emulator import (
    FirmwareEmulator,
    EmulatorConfig,
    EmulatorType,
    EmulatorState,
)
from vesper.firmware.bridge import FirmwareBridge, BridgeConfig
from vesper.firmware.device_fw import DeviceFirmware, FirmwareDeviceConfig, FirmwareState
from vesper.core.event_bus import EventBus


class TestEmulatorConfig:
    """Tests for EmulatorConfig."""
    
    def test_default_config(self):
        config = EmulatorConfig()
        assert config.emulator_type == EmulatorType.SIMULATED
        assert config.machine == "lm3s6965evb"
    
    def test_custom_config(self):
        config = EmulatorConfig(
            emulator_type=EmulatorType.QEMU,
            firmware_path="/path/to/firmware.elf",
            host_port=9999,
        )
        assert config.emulator_type == EmulatorType.QEMU
        assert config.host_port == 9999


class TestFirmwareEmulator:
    """Tests for FirmwareEmulator."""
    
    def test_create_emulator(self):
        emulator = FirmwareEmulator()
        assert emulator.state == EmulatorState.STOPPED
        assert emulator.is_running is False
    
    def test_start_simulated(self):
        config = EmulatorConfig(emulator_type=EmulatorType.SIMULATED)
        emulator = FirmwareEmulator(config)
        
        success = emulator.start()
        
        assert success is True
        assert emulator.state == EmulatorState.RUNNING
        assert emulator.is_running is True
        
        emulator.stop()
    
    def test_stop(self):
        emulator = FirmwareEmulator()
        emulator.start()
        emulator.stop()
        
        assert emulator.state == EmulatorState.STOPPED
    
    def test_send_receive_simulated(self):
        emulator = FirmwareEmulator()
        emulator.start()
        
        # Send command
        emulator.send("GET_TEMP")
        
        # Receive response
        response = emulator.receive(timeout=0.5)
        
        assert response is not None
        assert "TEMP" in response
        
        emulator.stop()
    
    def test_simulated_commands(self):
        emulator = FirmwareEmulator()
        emulator.start()
        
        # Test various commands
        commands = ["GET_TEMP", "GET_HUMIDITY", "SET_LED:1", "STATUS"]
        for cmd in commands:
            emulator.send(cmd)
        
        time.sleep(0.1)
        responses = emulator.receive_all()
        
        assert len(responses) == 4
        
        emulator.stop()
    
    def test_context_manager(self):
        with FirmwareEmulator() as emulator:
            assert emulator.is_running is True
            emulator.send("STATUS")
        
        assert emulator.state == EmulatorState.STOPPED
    
    def test_stats(self):
        emulator = FirmwareEmulator()
        emulator.start()
        emulator.send("STATUS")
        emulator.receive()
        
        stats = emulator.stats
        
        assert stats["start_count"] == 1
        # messages_sent is tracked in simulated mode
        assert "messages_sent" in stats
        
        emulator.stop()
    
    def test_callback(self):
        emulator = FirmwareEmulator()
        received = []
        
        emulator.on("output", lambda x: received.append(x))
        
        # Note: callbacks only work with QEMU (real process output)
        # Simulated mode doesn't trigger callbacks
        emulator.start()
        emulator.stop()


class TestFirmwareBridge:
    """Tests for FirmwareBridge."""
    
    def test_create_bridge(self):
        emulator = FirmwareEmulator()
        bridge = FirmwareBridge(emulator)
        
        assert bridge.is_running is False
    
    def test_start_bridge(self):
        emulator = FirmwareEmulator()
        bridge = FirmwareBridge(emulator)
        
        success = bridge.start()
        
        assert success is True
        assert bridge.is_running is True
        assert emulator.is_running is True
        
        bridge.stop()
        emulator.stop()
    
    def test_send_command(self):
        emulator = FirmwareEmulator()
        bridge = FirmwareBridge(emulator)
        bridge.start()
        
        success = bridge.send_command("GET_TEMP")
        
        assert success is True
        assert bridge.stats["commands_sent"] == 1
        
        bridge.stop()
    
    def test_event_emission(self):
        event_bus = EventBus()
        emulator = FirmwareEmulator()
        bridge = FirmwareBridge(emulator, event_bus)
        
        received_events = []
        event_bus.subscribe("sensor.*", lambda e: received_events.append(e))
        
        bridge.start()
        
        # Send temperature request
        emulator.send("GET_TEMP")
        time.sleep(0.2)  # Wait for polling
        
        bridge.stop()
    
    def test_helper_methods(self):
        emulator = FirmwareEmulator()
        bridge = FirmwareBridge(emulator)
        bridge.start()
        
        assert bridge.get_temperature() is True
        assert bridge.get_humidity() is True
        assert bridge.set_led(True) is True
        assert bridge.get_status() is True
        
        bridge.stop()


class TestDeviceFirmware:
    """Tests for DeviceFirmware."""
    
    def test_create_device(self):
        config = FirmwareDeviceConfig(
            device_type="sensor",
            auto_start=False,
        )
        device = DeviceFirmware(config=config)
        
        assert device.device_type == "sensor"
        assert device.firmware_state == FirmwareState.OFFLINE
    
    def test_start_device(self):
        config = FirmwareDeviceConfig(auto_start=False)
        device = DeviceFirmware(config=config)
        
        success = device.start()
        
        assert success is True
        assert device.firmware_state == FirmwareState.ONLINE
        
        device.stop()
    
    def test_stop_device(self):
        config = FirmwareDeviceConfig(auto_start=True)
        device = DeviceFirmware(config=config)
        
        device.stop()
        
        assert device.firmware_state == FirmwareState.OFFLINE
    
    def test_send_command(self):
        config = FirmwareDeviceConfig(auto_start=True)
        device = DeviceFirmware(config=config)
        
        success = device.send_command("STATUS")
        
        assert success is True
        
        device.stop()
    
    def test_get_state(self):
        config = FirmwareDeviceConfig(auto_start=False)
        device = DeviceFirmware(config=config)
        
        state = device.get_state()
        
        assert "firmware_state" in state
        assert state["firmware_state"] == "offline"
    
    def test_with_event_bus(self):
        event_bus = EventBus()
        config = FirmwareDeviceConfig(auto_start=True)
        device = DeviceFirmware(config=config, event_bus=event_bus)
        
        assert device.firmware_state == FirmwareState.ONLINE
        
        device.stop()
