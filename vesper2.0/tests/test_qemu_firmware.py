"""
Tests for VESPER QEMU Firmware Simulation.
"""

import asyncio
import pytest
import time
from unittest.mock import Mock, patch, AsyncMock

from vesper.firmware.qemu_runner import (
    QEMURunner,
    QEMUConfig,
    QEMUState,
    Architecture,
    BoardType,
)
from vesper.firmware.vesper_bridge import (
    VesperFirmwareBridge,
    VesperBridgeConfig,
    ProtocolMode,
    FirmwareMessage,
)


class TestQEMUConfig:
    """Tests for QEMUConfig."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = QEMUConfig()
        
        assert config.board == BoardType.LM3S6965
        assert config.architecture == Architecture.ARM_CORTEX_M3
        assert config.ram_size == "128K"
        assert config.enable_serial is True
        assert config.headless is True
    
    def test_custom_config(self):
        """Test custom configuration."""
        config = QEMUConfig(
            board=BoardType.STM32F4_DISCOVERY,
            architecture=Architecture.ARM_CORTEX_M4,
            firmware_path="/path/to/firmware.elf",
            ram_size="256K",
            enable_gdb=True,
            gdb_port=3333,
        )
        
        assert config.board == BoardType.STM32F4_DISCOVERY
        assert config.architecture == Architecture.ARM_CORTEX_M4
        assert config.firmware_path == "/path/to/firmware.elf"
        assert config.ram_size == "256K"
        assert config.enable_gdb is True
        assert config.gdb_port == 3333


class TestQEMURunner:
    """Tests for QEMURunner."""
    
    def test_init(self):
        """Test QEMURunner initialization."""
        runner = QEMURunner()
        
        assert runner.state == QEMUState.STOPPED
        assert not runner.is_running
        assert runner.pid is None
        assert runner.uptime == 0
    
    def test_find_qemu_binary(self):
        """Test QEMU binary detection."""
        runner = QEMURunner()
        
        # May or may not find QEMU depending on system
        binary = runner._find_qemu_binary()
        # Just ensure it doesn't crash
    
    def test_generate_mac(self):
        """Test MAC address generation."""
        runner = QEMURunner()
        
        mac1 = runner._generate_mac()
        mac2 = runner._generate_mac()
        
        # Valid MAC format
        assert len(mac1.split(":")) == 6
        assert all(len(part) == 2 for part in mac1.split(":"))
        
        # Should be different each time
        assert mac1 != mac2
    
    def test_build_command_no_firmware(self):
        """Test command building without firmware."""
        config = QEMUConfig(firmware_path=None)
        runner = QEMURunner(config)
        
        # Should work without firmware for basic testing
        with patch.object(runner, '_find_qemu_binary', return_value='/usr/bin/qemu-system-arm'):
            cmd = runner._build_command()
            assert '/usr/bin/qemu-system-arm' in cmd
            assert '-machine' in cmd
    
    def test_build_command_with_gdb(self):
        """Test command building with GDB enabled."""
        config = QEMUConfig(
            firmware_path=None,
            enable_gdb=True,
            gdb_port=1234,
        )
        runner = QEMURunner(config)
        
        with patch.object(runner, '_find_qemu_binary', return_value='/usr/bin/qemu-system-arm'):
            cmd = runner._build_command()
            assert '-gdb' in cmd
            assert 'tcp::1234' in cmd
            assert '-S' in cmd
    
    def test_state_changes(self):
        """Test state change tracking."""
        runner = QEMURunner()
        state_changes = []
        
        runner.on_state_change(lambda s: state_changes.append(s))
        
        runner._set_state(QEMUState.STARTING)
        runner._set_state(QEMUState.RUNNING)
        
        assert state_changes == [QEMUState.STARTING, QEMUState.RUNNING]
    
    def test_stats(self):
        """Test statistics tracking."""
        runner = QEMURunner()
        
        stats = runner.get_stats()
        assert 'state' in stats
        assert 'bytes_rx' in stats
        assert 'bytes_tx' in stats
        assert stats['state'] == 'stopped'


class TestFirmwareMessage:
    """Tests for FirmwareMessage."""
    
    def test_create_message(self):
        """Test message creation."""
        msg = FirmwareMessage(
            msg_type="sensor",
            key="TEMP",
            value=22.5,
            raw=b"TEMP:22.5\n",
        )
        
        assert msg.msg_type == "sensor"
        assert msg.key == "TEMP"
        assert msg.value == 22.5
        assert msg.timestamp is not None
    
    def test_to_dict(self):
        """Test conversion to dictionary."""
        msg = FirmwareMessage(
            msg_type="sensor",
            key="HUMIDITY",
            value=45.0,
            raw=b"HUMIDITY:45.0\n",
        )
        
        d = msg.to_dict()
        assert d["type"] == "sensor"
        assert d["key"] == "HUMIDITY"
        assert d["value"] == 45.0
    
    def test_repr(self):
        """Test string representation."""
        msg = FirmwareMessage(
            msg_type="event",
            key="MOTION",
            value=True,
            raw=b"MOTION:1\n",
        )
        
        assert "FirmwareMessage" in repr(msg)
        assert "event" in repr(msg)
        assert "MOTION" in repr(msg)


class TestVesperBridgeConfig:
    """Tests for VesperBridgeConfig."""
    
    def test_default_config(self):
        """Test default configuration."""
        config = VesperBridgeConfig()
        
        assert config.device_id == "fw_device"
        assert config.device_type == "generic_sensor"
        assert config.protocol_mode == ProtocolMode.TEXT
        assert config.heartbeat_interval == 10.0
    
    def test_custom_config(self):
        """Test custom configuration."""
        config = VesperBridgeConfig(
            device_id="temp_sensor_1",
            device_type="temperature_sensor",
            room="living_room",
            protocol_mode=ProtocolMode.JSON,
            heartbeat_interval=30.0,
        )
        
        assert config.device_id == "temp_sensor_1"
        assert config.room == "living_room"
        assert config.protocol_mode == ProtocolMode.JSON


class TestVesperFirmwareBridge:
    """Tests for VesperFirmwareBridge."""
    
    def test_init(self):
        """Test bridge initialization."""
        mock_runner = Mock()
        mock_runner.is_running = False
        
        bridge = VesperFirmwareBridge(qemu_runner=mock_runner)
        
        assert not bridge.is_running
        assert bridge.sensor_data == {}
    
    def test_parse_text_sensor(self):
        """Test parsing text protocol sensor data."""
        mock_runner = Mock()
        bridge = VesperFirmwareBridge(qemu_runner=mock_runner)
        
        # Parse temperature
        msg = bridge._parse_text("TEMP:22.5", b"TEMP:22.5")
        assert msg is not None
        assert msg.msg_type == "sensor"
        assert msg.key == "TEMP"
        assert msg.value == 22.5
    
    def test_parse_text_boolean(self):
        """Test parsing boolean values."""
        mock_runner = Mock()
        bridge = VesperFirmwareBridge(qemu_runner=mock_runner)
        
        msg = bridge._parse_text("MOTION:1", b"MOTION:1")
        assert msg.value is True
        
        msg = bridge._parse_text("DOOR:OFF", b"DOOR:OFF")
        assert msg.value is False
    
    def test_parse_value(self):
        """Test value parsing."""
        mock_runner = Mock()
        bridge = VesperFirmwareBridge(qemu_runner=mock_runner)
        
        # Boolean
        assert bridge._parse_value("TRUE") is True
        assert bridge._parse_value("FALSE") is False
        assert bridge._parse_value("1") is True
        assert bridge._parse_value("0") is False
        assert bridge._parse_value("ON") is True
        assert bridge._parse_value("OFF") is False
        
        # Numbers
        assert bridge._parse_value("42") == 42
        assert bridge._parse_value("22.5") == 22.5
        
        # String
        assert bridge._parse_value("hello") == "hello"
    
    def test_command_mapping(self):
        """Test VESPER to firmware command mapping."""
        assert VesperFirmwareBridge.COMMAND_MAP["get_temperature"] == "GET_TEMP"
        assert VesperFirmwareBridge.COMMAND_MAP["get_humidity"] == "GET_HUMIDITY"
        assert VesperFirmwareBridge.COMMAND_MAP["set_led"] == "SET_LED"
    
    def test_sensor_types(self):
        """Test sensor type definitions."""
        sensor_info = VesperFirmwareBridge.SENSOR_TYPES["TEMP"]
        assert sensor_info == ("temperature", "°C")
        
        sensor_info = VesperFirmwareBridge.SENSOR_TYPES["HUMIDITY"]
        assert sensor_info == ("humidity", "%")
    
    def test_handle_sensor_message(self):
        """Test sensor message handling."""
        mock_runner = Mock()
        bridge = VesperFirmwareBridge(qemu_runner=mock_runner)
        
        msg = FirmwareMessage(
            msg_type="sensor",
            key="TEMP",
            value=25.0,
            raw=b"TEMP:25.0",
        )
        
        bridge._handle_sensor_message(msg)
        
        assert "temperature" in bridge.sensor_data
        assert bridge.sensor_data["temperature"]["value"] == 25.0
        assert bridge.sensor_data["temperature"]["unit"] == "°C"
    
    def test_stats(self):
        """Test statistics."""
        mock_runner = Mock()
        mock_runner.uptime = 100.0
        mock_runner.state = QEMUState.RUNNING
        
        bridge = VesperFirmwareBridge(qemu_runner=mock_runner)
        
        stats = bridge.stats
        assert "messages_rx" in stats
        assert "messages_tx" in stats
        assert "events_published" in stats
        assert "uptime" in stats


class TestProtocolModes:
    """Tests for different protocol modes."""
    
    def test_text_protocol(self):
        """Test text protocol parsing."""
        mock_runner = Mock()
        config = VesperBridgeConfig(protocol_mode=ProtocolMode.TEXT)
        bridge = VesperFirmwareBridge(qemu_runner=mock_runner, config=config)
        
        # Standard format
        msg = bridge._parse_text("TEMP:22.5", b"TEMP:22.5")
        assert msg.key == "TEMP"
        assert msg.value == 22.5
        
        # Equals format
        msg = bridge._parse_text("LED=1", b"LED=1")
        assert msg.key == "LED"
        assert msg.value is True
    
    def test_json_protocol(self):
        """Test JSON protocol parsing."""
        mock_runner = Mock()
        config = VesperBridgeConfig(protocol_mode=ProtocolMode.JSON)
        bridge = VesperFirmwareBridge(qemu_runner=mock_runner, config=config)
        
        json_data = '{"type": "sensor", "key": "TEMP", "value": 22.5}'
        msg = bridge._parse_json(json_data, json_data.encode())
        
        assert msg.msg_type == "sensor"
        assert msg.key == "TEMP"
        assert msg.value == 22.5
    
    def test_json_fallback_to_text(self):
        """Test JSON mode falling back to text for non-JSON."""
        mock_runner = Mock()
        config = VesperBridgeConfig(protocol_mode=ProtocolMode.JSON)
        bridge = VesperFirmwareBridge(qemu_runner=mock_runner, config=config)
        
        # Non-JSON input should fallback to text parsing
        msg = bridge._parse_json("TEMP:22.5", b"TEMP:22.5")
        assert msg.key == "TEMP"
        assert msg.value == 22.5


class TestArchitectureSupport:
    """Tests for different MCU architectures."""
    
    def test_arm_cortex_m_binaries(self):
        """Test ARM Cortex-M binary names."""
        assert QEMURunner.QEMU_BINARIES[Architecture.ARM_CORTEX_M0] == "qemu-system-arm"
        assert QEMURunner.QEMU_BINARIES[Architecture.ARM_CORTEX_M3] == "qemu-system-arm"
        assert QEMURunner.QEMU_BINARIES[Architecture.ARM_CORTEX_M4] == "qemu-system-arm"
    
    def test_esp32_binaries(self):
        """Test ESP32 binary names."""
        assert QEMURunner.QEMU_BINARIES[Architecture.ESP32] == "qemu-system-xtensa"
        assert QEMURunner.QEMU_BINARIES[Architecture.ESP32_C3] == "qemu-system-riscv32"
    
    def test_riscv_binaries(self):
        """Test RISC-V binary names."""
        assert QEMURunner.QEMU_BINARIES[Architecture.RISCV32] == "qemu-system-riscv32"
        assert QEMURunner.QEMU_BINARIES[Architecture.RISCV64] == "qemu-system-riscv64"
    
    def test_cpu_types(self):
        """Test CPU type mapping."""
        assert QEMURunner.CPU_TYPES[Architecture.ARM_CORTEX_M3] == "cortex-m3"
        assert QEMURunner.CPU_TYPES[Architecture.ARM_CORTEX_M4] == "cortex-m4f"
        assert QEMURunner.CPU_TYPES[Architecture.ESP32] == "esp32"


# Async tests require pytest-asyncio
@pytest.mark.asyncio
class TestAsyncOperations:
    """Tests for async operations."""
    
    async def test_bridge_start_stop(self):
        """Test bridge start and stop."""
        mock_runner = AsyncMock()
        mock_runner.is_running = False
        mock_runner.start = AsyncMock(return_value=True)
        mock_runner.state = QEMUState.RUNNING
        mock_runner.uptime = 0.0
        
        bridge = VesperFirmwareBridge(qemu_runner=mock_runner)
        
        # Start
        success = await bridge.start()
        assert success
        assert bridge.is_running
        
        # Stop
        await bridge.stop()
        assert not bridge.is_running
    
    async def test_bridge_context_manager(self):
        """Test async context manager."""
        mock_runner = AsyncMock()
        mock_runner.is_running = False
        mock_runner.start = AsyncMock(return_value=True)
        mock_runner.state = QEMUState.RUNNING
        mock_runner.uptime = 0.0
        
        config = VesperBridgeConfig(heartbeat_interval=0)  # Disable heartbeat
        
        async with VesperFirmwareBridge(mock_runner, config=config) as bridge:
            assert bridge.is_running
        
        assert not bridge.is_running
