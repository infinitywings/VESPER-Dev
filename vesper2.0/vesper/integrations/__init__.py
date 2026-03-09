"""
VESPER Integrations with External IoT Platforms.

This module provides integration with SmartThings and other IoT platforms,
enabling bi-directional synchronization between virtual devices and real
smart home ecosystems.

Components:
- SmartThings API Client: Direct API access for existing devices
- Schema Connector: Webhook server for cloud-connected virtual devices
- Docker Device Manager: Container-based virtual device management
- Device Registry: SQLite-backed device and state storage
- Sync Bridge: Bi-directional state synchronization orchestrator

Architecture:
    ┌─────────────────────────────────────────────────────────────────────┐
    │                        VESPER Integrations                           │
    │                                                                      │
    │  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────┐  │
    │  │ SmartThings  │    │   Schema     │    │   Docker Device      │  │
    │  │ API Client   │    │  Connector   │    │   Manager            │  │
    │  │ (existing    │    │  (webhook    │    │   (QEMU firmware     │  │
    │  │  devices)    │    │   server)    │    │    in containers)    │  │
    │  └──────┬───────┘    └──────┬───────┘    └──────────┬───────────┘  │
    │         │                   │                       │               │
    │         └───────────────────┼───────────────────────┘               │
    │                             │                                       │
    │                    ┌────────┴────────┐                              │
    │                    │   Sync Bridge   │                              │
    │                    │ (orchestrates   │                              │
    │                    │  all sync)      │                              │
    │                    └────────┬────────┘                              │
    │                             │                                       │
    │                    ┌────────┴────────┐                              │
    │                    │ Device Registry │                              │
    │                    │   (SQLite)      │                              │
    │                    └─────────────────┘                              │
    └─────────────────────────────────────────────────────────────────────┘
"""

# SmartThings API Client (for existing devices)
from vesper.integrations.smartthings import (
    SmartThingsClient,
    SmartThingsDevice,
    SmartThingsConfig,
    SmartThingsCapability,
)

# SmartThings Schema Connector (for virtual devices)
from vesper.integrations.schema_connector import (
    SmartThingsSchemaConnector,
    SchemaConnectorConfig,
    VirtualDeviceDefinition,
    DeviceHandlerType,
    Capability,
    InteractionType,
    # Convenience functions
    create_switch_device,
    create_dimmer_device,
    create_motion_sensor_device,
    create_contact_sensor_device,
    create_lock_device,
)

# Docker Virtual Device Manager
from vesper.integrations.docker_device_manager import (
    DockerDeviceManager,
    DeviceManagerConfig,
    VirtualDeviceConfig,
    VirtualDevice,
    DeviceType,
    ContainerStatus,
)

# Device Registry & State Store
from vesper.integrations.device_registry import (
    DeviceRegistry,
    DeviceMetadata,
    DeviceState,
    DeviceCategory,
    EventType,
    StateHistoryEntry,
    EventLogEntry,
)

# Bi-directional Sync Bridge
from vesper.integrations.sync_bridge import (
    SmartThingsSyncBridge,
    SyncBridgeConfig,
    SyncSource,
    ConflictResolution,
    create_smartthings_bridge,
)

__all__ = [
    # SmartThings API Client
    "SmartThingsClient",
    "SmartThingsDevice",
    "SmartThingsConfig",
    "SmartThingsCapability",
    
    # Schema Connector
    "SmartThingsSchemaConnector",
    "SchemaConnectorConfig",
    "VirtualDeviceDefinition",
    "DeviceHandlerType",
    "Capability",
    "InteractionType",
    "create_switch_device",
    "create_dimmer_device",
    "create_motion_sensor_device",
    "create_contact_sensor_device",
    "create_lock_device",
    
    # Docker Device Manager
    "DockerDeviceManager",
    "DeviceManagerConfig",
    "VirtualDeviceConfig",
    "VirtualDevice",
    "DeviceType",
    "ContainerStatus",
    
    # Device Registry
    "DeviceRegistry",
    "DeviceMetadata",
    "DeviceState",
    "DeviceCategory",
    "EventType",
    "StateHistoryEntry",
    "EventLogEntry",
    
    # Sync Bridge
    "SmartThingsSyncBridge",
    "SyncBridgeConfig",
    "SyncSource",
    "ConflictResolution",
    "create_smartthings_bridge",
]
