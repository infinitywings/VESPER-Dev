"""
QEMU/Firmware integration module for Vesper.

Provides framework for running emulated IoT device firmware.

Supported Features:
- QEMU emulation for ARM Cortex-M, ESP32, RISC-V MCUs
- Serial/UART communication with firmware
- Bridge between firmware and VESPER event bus
- Simulated firmware mode for testing without QEMU

Example:
    from vesper.firmware import QEMURunner, QEMUConfig, VesperFirmwareBridge
    
    # Create QEMU runner
    config = QEMUConfig(
        firmware_path="firmware/sensor.elf",
        board=BoardType.STM32F4_DISCOVERY,
    )
    runner = QEMURunner(config)
    
    # Create bridge to VESPER
    bridge = VesperFirmwareBridge(runner, event_bus)
    await bridge.start()
"""

from vesper.firmware.emulator import FirmwareEmulator, EmulatorConfig
from vesper.firmware.bridge import FirmwareBridge, BridgeConfig
from vesper.firmware.device_fw import DeviceFirmware, FirmwareState
from vesper.firmware.qemu_runner import (
    QEMURunner,
    QEMUConfig,
    QEMUState,
    Architecture,
    BoardType,
    create_stm32_runner,
    create_nrf52_runner,
    create_esp32_runner,
    create_riscv_runner,
)
from vesper.firmware.vesper_bridge import (
    VesperFirmwareBridge,
    VesperBridgeConfig,
    ProtocolMode,
    FirmwareMessage,
)
from vesper.firmware.sensor_templates import (
    SensorNetwork,
    SensorConfig,
    SensorType,
    SimulatedSensor,
    create_sensor,
    create_whole_house_sensors,
    create_living_room_sensors,
    create_bedroom_sensors,
    create_kitchen_sensors,
    create_bathroom_sensors,
    # Individual sensor classes
    MotionSensor,
    TemperatureSensor,
    HumiditySensor,
    DoorWindowSensor,
    LightSensor,
    SmokeSensor,
    CO2Sensor,
    WaterLeakSensor,
    Thermostat,
    SmartPlug,
    MultiSensor,
)

__all__ = [
    # Legacy emulator
    "FirmwareEmulator",
    "EmulatorConfig",
    "FirmwareBridge",
    "BridgeConfig",
    "DeviceFirmware",
    "FirmwareState",
    # New QEMU runner
    "QEMURunner",
    "QEMUConfig",
    "QEMUState",
    "Architecture",
    "BoardType",
    "create_stm32_runner",
    "create_nrf52_runner",
    "create_esp32_runner",
    "create_riscv_runner",
    # VESPER bridge
    "VesperFirmwareBridge",
    "VesperBridgeConfig",
    "ProtocolMode",
    "FirmwareMessage",
    # Simulated sensors (NO HARDWARE REQUIRED!)
    "SensorNetwork",
    "SensorConfig",
    "SensorType",
    "SimulatedSensor",
    "create_sensor",
    "create_whole_house_sensors",
    "create_living_room_sensors",
    "create_bedroom_sensors",
    "create_kitchen_sensors",
    "create_bathroom_sensors",
    "MotionSensor",
    "TemperatureSensor",
    "HumiditySensor",
    "DoorWindowSensor",
    "LightSensor",
    "SmokeSensor",
    "CO2Sensor",
    "WaterLeakSensor",
    "Thermostat",
    "SmartPlug",
    "MultiSensor",
]

