"""
Constants for the VESPER Matter integration.

These constants mirror the real Home Assistant Matter component at
homeassistant/components/matter/const.py and use the same CHIP/Matter
cluster identifiers so VESPER can interoperate with python-matter-server.

Reference:
    https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/const.py
    https://github.com/home-assistant-libs/python-matter-server
"""

from __future__ import annotations

import logging

DOMAIN = "matter"
LOGGER = logging.getLogger(__name__)

# ── python-matter-server WebSocket defaults ────────────────────────────
DEFAULT_MATTER_SERVER_URL = "ws://localhost:5580/ws"
CONNECT_TIMEOUT = 10          # seconds to wait for WS handshake
LISTEN_READY_TIMEOUT = 30     # seconds to wait for server init

# ── Matter Cluster IDs (from CHIP SDK chip.clusters.Objects) ───────────
# These are the standard cluster IDs defined in the Matter specification.
# We enumerate the most common ones that VESPER maps to IoT device types.
CLUSTER_ID_ON_OFF = 0x0006
CLUSTER_ID_LEVEL_CONTROL = 0x0008
CLUSTER_ID_COLOR_CONTROL = 0x0300
CLUSTER_ID_DOOR_LOCK = 0x0101
CLUSTER_ID_THERMOSTAT = 0x0201
CLUSTER_ID_FAN_CONTROL = 0x0202
CLUSTER_ID_TEMPERATURE_MEASUREMENT = 0x0402
CLUSTER_ID_RELATIVE_HUMIDITY_MEASUREMENT = 0x0405
CLUSTER_ID_OCCUPANCY_SENSING = 0x0406
CLUSTER_ID_ILLUMINANCE_MEASUREMENT = 0x0400
CLUSTER_ID_PRESSURE_MEASUREMENT = 0x0403
CLUSTER_ID_BOOLEAN_STATE = 0x0045          # contact sensor
CLUSTER_ID_POWER_SOURCE = 0x002F
CLUSTER_ID_BASIC_INFORMATION = 0x0028
CLUSTER_ID_BRIDGED_DEVICE_BASIC = 0x0039

# ── Matter Device Type IDs (from the Matter Application Cluster spec) ──
DEVICE_TYPE_ON_OFF_LIGHT = 0x0100
DEVICE_TYPE_DIMMABLE_LIGHT = 0x0101
DEVICE_TYPE_COLOR_TEMPERATURE_LIGHT = 0x0102
DEVICE_TYPE_EXTENDED_COLOR_LIGHT = 0x010D
DEVICE_TYPE_ON_OFF_PLUG = 0x010A
DEVICE_TYPE_DIMMABLE_PLUG = 0x010B
DEVICE_TYPE_CONTACT_SENSOR = 0x0015
DEVICE_TYPE_OCCUPANCY_SENSOR = 0x0107
DEVICE_TYPE_TEMPERATURE_SENSOR = 0x0302
DEVICE_TYPE_HUMIDITY_SENSOR = 0x0307
DEVICE_TYPE_LIGHT_SENSOR = 0x0106
DEVICE_TYPE_PRESSURE_SENSOR = 0x0305
DEVICE_TYPE_DOOR_LOCK = 0x000A
DEVICE_TYPE_THERMOSTAT = 0x0301
DEVICE_TYPE_FAN = 0x002B
DEVICE_TYPE_AIR_PURIFIER = 0x002D
DEVICE_TYPE_WINDOW_COVERING = 0x0202
DEVICE_TYPE_PUMP = 0x0303

# Human-readable names for dashboard display
DEVICE_TYPE_NAMES: dict[int, str] = {
    DEVICE_TYPE_ON_OFF_LIGHT: "On/Off Light",
    DEVICE_TYPE_DIMMABLE_LIGHT: "Dimmable Light",
    DEVICE_TYPE_COLOR_TEMPERATURE_LIGHT: "Color Temp Light",
    DEVICE_TYPE_EXTENDED_COLOR_LIGHT: "Extended Color Light",
    DEVICE_TYPE_ON_OFF_PLUG: "On/Off Plug",
    DEVICE_TYPE_DIMMABLE_PLUG: "Dimmable Plug",
    DEVICE_TYPE_CONTACT_SENSOR: "Contact Sensor",
    DEVICE_TYPE_OCCUPANCY_SENSOR: "Occupancy Sensor",
    DEVICE_TYPE_TEMPERATURE_SENSOR: "Temperature Sensor",
    DEVICE_TYPE_HUMIDITY_SENSOR: "Humidity Sensor",
    DEVICE_TYPE_LIGHT_SENSOR: "Light Sensor",
    DEVICE_TYPE_PRESSURE_SENSOR: "Pressure Sensor",
    DEVICE_TYPE_DOOR_LOCK: "Door Lock",
    DEVICE_TYPE_THERMOSTAT: "Thermostat",
    DEVICE_TYPE_FAN: "Fan",
    DEVICE_TYPE_WINDOW_COVERING: "Window Covering",
}

CLUSTER_NAMES: dict[int, str] = {
    CLUSTER_ID_ON_OFF: "OnOff",
    CLUSTER_ID_LEVEL_CONTROL: "LevelControl",
    CLUSTER_ID_COLOR_CONTROL: "ColorControl",
    CLUSTER_ID_DOOR_LOCK: "DoorLock",
    CLUSTER_ID_THERMOSTAT: "Thermostat",
    CLUSTER_ID_FAN_CONTROL: "FanControl",
    CLUSTER_ID_TEMPERATURE_MEASUREMENT: "TemperatureMeasurement",
    CLUSTER_ID_RELATIVE_HUMIDITY_MEASUREMENT: "RelativeHumidityMeasurement",
    CLUSTER_ID_OCCUPANCY_SENSING: "OccupancySensing",
    CLUSTER_ID_ILLUMINANCE_MEASUREMENT: "IlluminanceMeasurement",
    CLUSTER_ID_PRESSURE_MEASUREMENT: "PressureMeasurement",
    CLUSTER_ID_BOOLEAN_STATE: "BooleanState",
    CLUSTER_ID_POWER_SOURCE: "PowerSource",
    CLUSTER_ID_BASIC_INFORMATION: "BasicInformation",
    CLUSTER_ID_BRIDGED_DEVICE_BASIC: "BridgedDeviceBasicInformation",
}

# ── VESPER ↔ Matter mapping ────────────────────────────────────────────
# Maps VESPER device categories to the Matter device-type IDs they match.
VESPER_TO_MATTER_TYPE: dict[str, list[int]] = {
    "smart_light": [
        DEVICE_TYPE_ON_OFF_LIGHT,
        DEVICE_TYPE_DIMMABLE_LIGHT,
        DEVICE_TYPE_COLOR_TEMPERATURE_LIGHT,
        DEVICE_TYPE_EXTENDED_COLOR_LIGHT,
    ],
    "smart_plug": [DEVICE_TYPE_ON_OFF_PLUG, DEVICE_TYPE_DIMMABLE_PLUG],
    "motion_sensor": [DEVICE_TYPE_OCCUPANCY_SENSOR],
    "door_sensor": [DEVICE_TYPE_CONTACT_SENSOR],
    "temperature_sensor": [DEVICE_TYPE_TEMPERATURE_SENSOR],
    "humidity_sensor": [DEVICE_TYPE_HUMIDITY_SENSOR],
    "smart_door": [DEVICE_TYPE_DOOR_LOCK],
    "thermostat": [DEVICE_TYPE_THERMOSTAT],
    "fan": [DEVICE_TYPE_FAN],
}

# Reverse map: Matter device-type ID → VESPER category
MATTER_TYPE_TO_VESPER: dict[int, str] = {}
for _vesper_cat, _matter_ids in VESPER_TO_MATTER_TYPE.items():
    for _mid in _matter_ids:
        MATTER_TYPE_TO_VESPER[_mid] = _vesper_cat
