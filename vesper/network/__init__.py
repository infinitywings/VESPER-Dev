"""
Network module for Vesper IoT communication.

Provides WiFi emulation, Matter bridge integration, and traffic tracking
for device communication.
"""

# Matter bridge support (REST API to matter.js bridge)
try:
    from vesper.matter.bridge_client import MatterBridgeClient
    MATTER_BRIDGE_AVAILABLE = True
except ImportError:
    MATTER_BRIDGE_AVAILABLE = False
    MatterBridgeClient = None  # type: ignore[assignment,misc]

# WiFi emulator (requires Docker + Mininet-WiFi)
try:
    from vesper.network.wifi_emulator import (
        WiFiEmulator,
        WiFiConfig,
        DeviceConfig,
        DeviceType,
        TrafficTracker,
        WiFiTrafficRecord,
    )
    WIFI_AVAILABLE = True
except ImportError:
    WIFI_AVAILABLE = False
    WiFiEmulator = None
    WiFiConfig = None
    DeviceConfig = None
    DeviceType = None
    TrafficTracker = None
    WiFiTrafficRecord = None

__all__ = [
    # Matter bridge
    "MatterBridgeClient",
    "MATTER_BRIDGE_AVAILABLE",
    # WiFi emulator
    "WiFiEmulator",
    "WiFiConfig",
    "DeviceConfig",
    "DeviceType",
    "TrafficTracker",
    "WiFiTrafficRecord",
    "WIFI_AVAILABLE",
]
