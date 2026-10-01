"""
VESPER Matter Integration — Direct connection to python-matter-server.

This module enables VESPER to discover, control, and monitor Matter-
compatible IoT devices through the python-matter-server WebSocket API.
It mirrors the architecture of Home Assistant's Matter integration
(homeassistant/components/matter/) but operates independently.

Architecture:
    Matter Device ↔ Thread/WiFi ↔ python-matter-server (Docker)
                                        ↕ WebSocket (ws://host:5580/ws)
                                   VesperMatterClient
                                        ↕
                                   MatterAdapter → Hub + EventBus + Dashboard

Modules:
    client.py   — WebSocket connection to python-matter-server
    adapter.py  — Node discovery, state tracking, command dispatch
    device.py   — MatterDeviceNode data model
    const.py    — Cluster IDs, device-type mappings, constants

Reference:
    https://github.com/home-assistant/core/tree/dev/homeassistant/components/matter
    https://github.com/home-assistant-libs/python-matter-server
    https://www.home-assistant.io/integrations/matter/
"""

from vesper.matter.client import VesperMatterClient, matter_sdk_available
from vesper.matter.adapter import MatterAdapter
from vesper.matter.device import MatterDeviceNode, MatterEndpointInfo
from vesper.matter.bridge_client import MatterBridgeClient
from vesper.matter.const import (
    CLUSTER_NAMES,
    DEFAULT_MATTER_SERVER_URL,
    DEVICE_TYPE_NAMES,
    MATTER_TYPE_TO_VESPER,
    VESPER_TO_MATTER_TYPE,
)

__all__ = [
    # Core classes
    "VesperMatterClient",
    "MatterAdapter",
    "MatterDeviceNode",
    "MatterEndpointInfo",
    "MatterBridgeClient",
    # Utilities
    "matter_sdk_available",
    # Constants
    "CLUSTER_NAMES",
    "DEFAULT_MATTER_SERVER_URL",
    "DEVICE_TYPE_NAMES",
    "MATTER_TYPE_TO_VESPER",
    "VESPER_TO_MATTER_TYPE",
]
