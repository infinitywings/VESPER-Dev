"""
VESPER Firmware Module.

Provides ESP32-based IoT device firmware emulation using
Espressif's QEMU fork (qemu-system-xtensa) with SmartThings
Device SDK C integration.

Architecture:
    ESP32 (Xtensa LX6) running FreeRTOS + SmartThings SDK
    ↕ WiFi (via mac80211_hwsim / Mininet-WiFi)
    ↕ Matter bridge for device communication
    ↕ VESPER attack framework

Supported Device Types:
    - smart_light (switch, brightness, color temperature)
    - motion_sensor (PIR motion detection)
    - temperature_sensor (ambient temperature)
    - humidity_sensor (relative humidity)
    - door_sensor (open/close contact)
    - smart_plug (on/off, power metering)

Simulated Sensors (no QEMU required):
    - SensorNetwork, SensorConfig, SensorType
    - Individual sensor classes for software-only testing
"""

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
    # Simulated sensors (software-only, no QEMU required)
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
