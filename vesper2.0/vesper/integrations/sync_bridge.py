"""
Bi-directional Sync Bridge for VESPER SmartThings Integration.

Orchestrates synchronization between:
- Virtual devices (Docker containers with QEMU firmware)
- Device Registry (SQLite state store)
- SmartThings cloud (via Schema Connector)
- 3D simulation environment

Architecture:
    ┌─────────────────────────────────────────────────────────────────────┐
    │                        Sync Bridge                                   │
    │  ┌───────────────────────────────────────────────────────────────┐  │
    │  │                    Event Router                                │  │
    │  │  - Routes state changes between components                     │  │
    │  │  - Prevents sync loops                                         │  │
    │  │  - Handles conflict resolution                                 │  │
    │  └───────────────────────────────────────────────────────────────┘  │
    │           ▲               ▲               ▲               ▲         │
    │           │               │               │               │         │
    │  ┌────────┴───┐  ┌────────┴───┐  ┌────────┴───┐  ┌────────┴───┐   │
    │  │  Docker    │  │  Registry  │  │ SmartThings│  │ Simulation │   │
    │  │  Devices   │  │  (SQLite)  │  │ (Schema)   │  │  (Habitat) │   │
    │  └────────────┘  └────────────┘  └────────────┘  └────────────┘   │
    └─────────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from .device_registry import (
    DeviceCategory,
    DeviceMetadata,
    DeviceRegistry,
    EventType,
)
from .docker_device_manager import (
    ContainerStatus,
    DeviceType,
    DockerDeviceManager,
    VirtualDevice,
    VirtualDeviceConfig,
)
from .schema_connector import (
    Capability,
    DeviceHandlerType,
    SmartThingsSchemaConnector,
    VirtualDeviceDefinition,
    create_contact_sensor_device,
    create_dimmer_device,
    create_lock_device,
    create_motion_sensor_device,
    create_switch_device,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

class SyncSource(str, Enum):
    """Sources of state changes."""
    LOCAL = "local"
    DOCKER = "docker"
    SMARTTHINGS = "smartthings"
    SIMULATION = "simulation"
    USER = "user"


class ConflictResolution(str, Enum):
    """Conflict resolution strategies."""
    LATEST_WINS = "latest_wins"  # Most recent change wins
    SMARTTHINGS_WINS = "smartthings_wins"  # SmartThings takes priority
    LOCAL_WINS = "local_wins"  # Local/simulation takes priority
    MANUAL = "manual"  # Require manual resolution


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class SyncBridgeConfig:
    """Configuration for the sync bridge."""
    
    # Sync settings
    sync_interval: float = 1.0  # Seconds between sync checks
    batch_size: int = 10  # Max devices per sync batch
    
    # Conflict resolution
    conflict_strategy: ConflictResolution = ConflictResolution.LATEST_WINS
    
    # Timeouts
    command_timeout: float = 5.0
    sync_timeout: float = 30.0
    
    # Loop prevention
    loop_prevention_window: float = 0.5  # Ignore duplicate updates within window
    
    # Auto-registration
    auto_register_to_smartthings: bool = True
    auto_create_containers: bool = False  # Create Docker containers for new devices


@dataclass
class PendingSync:
    """Represents a pending state sync."""
    device_id: str
    state: Dict[str, Any]
    source: SyncSource
    timestamp: float
    targets: Set[SyncSource]


@dataclass
class SyncStatistics:
    """Sync bridge statistics."""
    total_syncs: int = 0
    successful_syncs: int = 0
    failed_syncs: int = 0
    smartthings_to_local: int = 0
    local_to_smartthings: int = 0
    conflicts_resolved: int = 0
    last_sync_time: Optional[datetime] = None


# =============================================================================
# Type Mappings
# =============================================================================

# Map DeviceType to SmartThings DeviceHandlerType
DEVICE_TYPE_TO_HANDLER = {
    DeviceType.SWITCH: DeviceHandlerType.SWITCH,
    DeviceType.DIMMER: DeviceHandlerType.DIMMER,
    DeviceType.MOTION_SENSOR: DeviceHandlerType.MOTION_SENSOR,
    DeviceType.CONTACT_SENSOR: DeviceHandlerType.CONTACT_SENSOR,
    DeviceType.TEMPERATURE_SENSOR: DeviceHandlerType.TEMPERATURE_SENSOR,
    DeviceType.LOCK: DeviceHandlerType.LOCK,
    DeviceType.RGB_LIGHT: DeviceHandlerType.RGBW_COLOR_BULB,
    DeviceType.THERMOSTAT: DeviceHandlerType.THERMOSTAT,
}

# Map DeviceType to DeviceCategory
DEVICE_TYPE_TO_CATEGORY = {
    DeviceType.SWITCH: DeviceCategory.LIGHTING,
    DeviceType.DIMMER: DeviceCategory.LIGHTING,
    DeviceType.RGB_LIGHT: DeviceCategory.LIGHTING,
    DeviceType.MOTION_SENSOR: DeviceCategory.SENSORS,
    DeviceType.CONTACT_SENSOR: DeviceCategory.SENSORS,
    DeviceType.TEMPERATURE_SENSOR: DeviceCategory.SENSORS,
    DeviceType.HUMIDITY_SENSOR: DeviceCategory.SENSORS,
    DeviceType.LOCK: DeviceCategory.LOCKS,
    DeviceType.THERMOSTAT: DeviceCategory.CLIMATE,
}

# Map internal state keys to SmartThings capability.attribute format
STATE_KEY_TO_ST = {
    "switch": "st.switch.switch",
    "on": "st.switch.switch",  # "on" -> "st.switch.switch" with value mapping
    "level": "st.switchLevel.level",
    "brightness": "st.switchLevel.level",
    "motion": "st.motionSensor.motion",
    "contact": "st.contactSensor.contact",
    "locked": "st.lock.lock",
    "temperature": "st.temperatureMeasurement.temperature",
    "humidity": "st.relativeHumidityMeasurement.humidity",
}

# Map SmartThings capability.attribute to internal state keys
ST_TO_STATE_KEY = {v: k for k, v in STATE_KEY_TO_ST.items()}


# =============================================================================
# Sync Bridge
# =============================================================================

class SmartThingsSyncBridge:
    """
    Bi-directional synchronization bridge between VESPER and SmartThings.
    
    Coordinates state changes between:
    - Docker containers running virtual device firmware
    - Device Registry (SQLite database)
    - SmartThings cloud (via Schema Connector webhook)
    - 3D simulation environment
    
    Features:
    - Automatic device registration to SmartThings when created locally
    - Real-time state sync (sub-second latency)
    - Conflict resolution for simultaneous updates
    - Loop prevention to avoid infinite sync cycles
    - Batch processing for efficiency
    - Statistics and monitoring
    
    Usage:
        # Create components
        registry = DeviceRegistry()
        docker_manager = DockerDeviceManager()
        schema_connector = SmartThingsSchemaConnector()
        
        # Create bridge
        bridge = SmartThingsSyncBridge(
            registry=registry,
            docker_manager=docker_manager,
            schema_connector=schema_connector,
        )
        
        # Start bridge
        await bridge.start()
        
        # Create a device (automatically syncs to SmartThings)
        device = await bridge.create_device(
            device_id="switch-001",
            device_type=DeviceType.SWITCH,
            friendly_name="Kitchen Light",
            room="Kitchen",
        )
        
        # Update state (syncs both directions)
        await bridge.update_device_state("switch-001", {"switch": "on"})
        
        # Stop bridge
        await bridge.stop()
    """
    
    def __init__(
        self,
        registry: DeviceRegistry,
        docker_manager: Optional[DockerDeviceManager] = None,
        schema_connector: Optional[SmartThingsSchemaConnector] = None,
        config: Optional[SyncBridgeConfig] = None,
    ):
        """
        Initialize the sync bridge.
        
        Args:
            registry: Device registry for state storage
            docker_manager: Docker manager for container-based devices
            schema_connector: SmartThings Schema connector
            config: Bridge configuration
        """
        self.registry = registry
        self.docker_manager = docker_manager
        self.schema_connector = schema_connector
        self.config = config or SyncBridgeConfig()
        
        # Pending syncs queue
        self._pending_syncs: Dict[str, PendingSync] = {}
        self._sync_lock = asyncio.Lock()
        
        # Recent updates for loop prevention
        self._recent_updates: Dict[str, float] = {}  # device_id:source -> timestamp
        
        # Statistics
        self.stats = SyncStatistics()
        
        # Background tasks
        self._sync_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Simulation callback
        self._simulation_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None
        
        logger.info("SmartThings Sync Bridge initialized")
    
    # =========================================================================
    # Lifecycle
    # =========================================================================
    
    async def start(self) -> None:
        """Start the sync bridge."""
        # Initialize registry
        await self.registry.initialize()
        
        # Register callbacks
        self._register_callbacks()
        
        # Start Docker manager if available
        if self.docker_manager and self.docker_manager.is_available:
            await self.docker_manager.start()
        
        # Start Schema connector if available
        if self.schema_connector:
            await self.schema_connector.start()
            
            # Register existing devices with SmartThings
            if self.config.auto_register_to_smartthings:
                await self._register_existing_devices()
        
        # Start sync loop
        self._running = True
        self._sync_task = asyncio.create_task(self._sync_loop())
        
        logger.info("SmartThings Sync Bridge started")
    
    async def stop(self) -> None:
        """Stop the sync bridge."""
        self._running = False
        
        # Stop sync loop
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        
        # Stop components
        if self.schema_connector:
            await self.schema_connector.stop()
        
        if self.docker_manager:
            await self.docker_manager.stop()
        
        await self.registry.close()
        
        logger.info("SmartThings Sync Bridge stopped")
    
    def _register_callbacks(self) -> None:
        """Register callbacks with all components."""
        # Registry state changes
        self.registry.on_state_change(self._on_registry_state_change)
        
        # Docker device state changes
        if self.docker_manager:
            self.docker_manager.on_state_change(self._on_docker_state_change)
        
        # SmartThings commands
        if self.schema_connector:
            self.schema_connector.on_command(self._on_smartthings_command)
    
    async def _register_existing_devices(self) -> None:
        """Register existing devices with SmartThings Schema connector."""
        if not self.schema_connector:
            return
        
        devices = self.registry.list_devices()
        for metadata in devices:
            await self._register_with_smartthings(metadata)
        
        logger.info(f"Registered {len(devices)} existing devices with SmartThings")
    
    # =========================================================================
    # Device Management
    # =========================================================================
    
    async def create_device(
        self,
        device_id: str,
        device_type: DeviceType,
        friendly_name: str,
        room: Optional[str] = None,
        initial_state: Optional[Dict[str, Any]] = None,
        create_container: Optional[bool] = None,
    ) -> Optional[DeviceMetadata]:
        """
        Create a new virtual device.
        
        Creates the device in:
        1. Device Registry (always)
        2. Docker container (if enabled)
        3. SmartThings Schema connector (if enabled)
        
        Args:
            device_id: Unique device ID
            device_type: Type of device
            friendly_name: Human-readable name
            room: Room/location
            initial_state: Initial device state
            create_container: Create Docker container (overrides config)
            
        Returns:
            DeviceMetadata if successful
        """
        # Determine if we should create a container
        should_create_container = (
            create_container if create_container is not None
            else self.config.auto_create_containers
        )
        
        # Get handler type and category
        handler_type = DEVICE_TYPE_TO_HANDLER.get(device_type, DeviceHandlerType.SWITCH)
        category = DEVICE_TYPE_TO_CATEGORY.get(device_type, DeviceCategory.OTHER)
        
        # Create registry metadata
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type=device_type.value,
            friendly_name=friendly_name,
            room=room,
            category=category,
            smartthings_handler_type=handler_type.value,
        )
        
        # Register in registry
        if not await self.registry.register_device(metadata, initial_state):
            return None
        
        # Create Docker container if enabled
        if should_create_container and self.docker_manager:
            container_config = VirtualDeviceConfig(
                device_id=device_id,
                device_type=device_type,
                friendly_name=friendly_name,
                room=room,
                initial_state=initial_state or {},
            )
            virtual_device = await self.docker_manager.create_device(container_config)
            
            if virtual_device:
                await self.registry.update_device(device_id, {
                    "container_id": virtual_device.container_id,
                    "container_status": virtual_device.status.value,
                })
        
        # Register with SmartThings
        if self.config.auto_register_to_smartthings:
            await self._register_with_smartthings(metadata)
        
        logger.info(f"Created device: {friendly_name} ({device_id})")
        return metadata
    
    async def delete_device(self, device_id: str) -> bool:
        """
        Delete a device from all systems.
        
        Args:
            device_id: Device ID to delete
            
        Returns:
            True if successful
        """
        # Remove from Docker
        if self.docker_manager:
            await self.docker_manager.remove_device(device_id)
        
        # Remove from SmartThings connector
        if self.schema_connector:
            self.schema_connector.unregister_device(device_id)
        
        # Remove from registry
        return await self.registry.delete_device(device_id)
    
    async def _register_with_smartthings(self, metadata: DeviceMetadata) -> bool:
        """Register a device with SmartThings Schema connector."""
        if not self.schema_connector:
            return False
        
        # Map device type to handler
        handler_type = DeviceHandlerType(metadata.smartthings_handler_type) \
            if metadata.smartthings_handler_type else DeviceHandlerType.SWITCH
        
        # Create SmartThings device definition
        st_device = VirtualDeviceDefinition(
            external_device_id=metadata.device_id,
            friendly_name=metadata.friendly_name,
            device_handler_type=handler_type,
            manufacturer_name=metadata.manufacturer,
            model_name=metadata.model,
            sw_version=metadata.firmware_version,
            room_name=metadata.room,
        )
        
        # Get current state from registry
        state = await self.registry.get_state(metadata.device_id)
        if state:
            # Convert to SmartThings format
            st_state = self._convert_state_to_smartthings(state)
            st_device.state = st_state
        
        # Register with connector
        self.schema_connector.register_device(st_device)
        
        # Trigger discovery callback if we have callback credentials
        await self.schema_connector.trigger_discovery_callback()
        
        return True
    
    # =========================================================================
    # State Management
    # =========================================================================
    
    async def update_device_state(
        self,
        device_id: str,
        state_updates: Dict[str, Any],
        source: SyncSource = SyncSource.USER,
    ) -> bool:
        """
        Update device state and sync to all targets.
        
        Args:
            device_id: Device ID
            state_updates: State updates to apply
            source: Source of the update
            
        Returns:
            True if successful
        """
        # Check for loop prevention
        if self._is_duplicate_update(device_id, source):
            logger.debug(f"Skipping duplicate update for {device_id} from {source}")
            return True
        
        # Mark update
        self._mark_update(device_id, source)
        
        # Update registry
        await self.registry.update_state(device_id, state_updates, source.value)
        
        # Determine sync targets (all except source)
        targets = {SyncSource.DOCKER, SyncSource.SMARTTHINGS, SyncSource.SIMULATION}
        targets.discard(source)
        
        # Queue sync
        async with self._sync_lock:
            self._pending_syncs[device_id] = PendingSync(
                device_id=device_id,
                state=state_updates,
                source=source,
                timestamp=time.time(),
                targets=targets,
            )
        
        return True
    
    async def get_device_state(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Get current device state."""
        return await self.registry.get_state(device_id)
    
    # =========================================================================
    # Callbacks
    # =========================================================================
    
    def _on_registry_state_change(
        self,
        device_id: str,
        old_state: Dict[str, Any],
        new_state: Dict[str, Any],
    ) -> None:
        """Handle state change from registry."""
        # This is called after we've already processed the change
        # Used for logging/statistics
        logger.debug(f"Registry state change for {device_id}")
    
    def _on_docker_state_change(
        self,
        device_id: str,
        state: Dict[str, Any],
    ) -> None:
        """Handle state change from Docker container."""
        asyncio.create_task(
            self.update_device_state(device_id, state, SyncSource.DOCKER)
        )
    
    async def _on_smartthings_command(
        self,
        device_id: str,
        capability: str,
        command: str,
        arguments: List[Any],
    ) -> bool:
        """
        Handle command from SmartThings.
        
        This is called when a user controls a device via the SmartThings app.
        """
        logger.info(f"SmartThings command: {device_id} {capability}.{command}({arguments})")
        
        # Convert command to state update
        state_update = self._command_to_state_update(capability, command, arguments)
        
        if state_update:
            # Update state (this will sync to Docker/simulation)
            await self.update_device_state(device_id, state_update, SyncSource.SMARTTHINGS)
            
            # Log event
            await self.registry.log_event(
                device_id,
                EventType.COMMAND_RECEIVED,
                f"SmartThings command: {capability}.{command}",
                {"capability": capability, "command": command, "arguments": arguments},
            )
            
            self.stats.smartthings_to_local += 1
            return True
        
        return False
    
    def on_simulation_state_change(
        self,
        callback: Callable[[str, Dict[str, Any]], None],
    ) -> None:
        """Register callback for state changes to sync to simulation."""
        self._simulation_callback = callback
    
    async def update_from_simulation(
        self,
        device_id: str,
        state_updates: Dict[str, Any],
    ) -> bool:
        """Update state from 3D simulation."""
        return await self.update_device_state(
            device_id, state_updates, SyncSource.SIMULATION
        )
    
    # =========================================================================
    # Sync Loop
    # =========================================================================
    
    async def _sync_loop(self) -> None:
        """Background loop to process pending syncs."""
        while self._running:
            try:
                await self._process_pending_syncs()
                await asyncio.sleep(self.config.sync_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Sync loop error: {e}")
                await asyncio.sleep(self.config.sync_interval)
    
    async def _process_pending_syncs(self) -> None:
        """Process all pending state syncs."""
        async with self._sync_lock:
            if not self._pending_syncs:
                return
            
            # Get pending syncs
            pending = list(self._pending_syncs.values())
            self._pending_syncs.clear()
        
        # Process in batches
        for i in range(0, len(pending), self.config.batch_size):
            batch = pending[i:i + self.config.batch_size]
            await asyncio.gather(
                *[self._sync_device(sync) for sync in batch],
                return_exceptions=True,
            )
    
    async def _sync_device(self, sync: PendingSync) -> None:
        """Sync a single device to all targets."""
        self.stats.total_syncs += 1
        
        try:
            # Sync to Docker
            if SyncSource.DOCKER in sync.targets and self.docker_manager:
                await self._sync_to_docker(sync)
            
            # Sync to SmartThings
            if SyncSource.SMARTTHINGS in sync.targets and self.schema_connector:
                await self._sync_to_smartthings(sync)
            
            # Sync to Simulation
            if SyncSource.SIMULATION in sync.targets and self._simulation_callback:
                await self._sync_to_simulation(sync)
            
            self.stats.successful_syncs += 1
            self.stats.last_sync_time = datetime.utcnow()
            
        except Exception as e:
            logger.error(f"Sync failed for {sync.device_id}: {e}")
            self.stats.failed_syncs += 1
    
    async def _sync_to_docker(self, sync: PendingSync) -> None:
        """Sync state to Docker container."""
        if not self.docker_manager:
            return
        
        device = self.docker_manager.get_device(sync.device_id)
        if not device or device.status != ContainerStatus.RUNNING:
            return
        
        # Send state update to container
        await self.docker_manager.set_device_state(sync.device_id, sync.state)
    
    async def _sync_to_smartthings(self, sync: PendingSync) -> None:
        """Sync state to SmartThings."""
        if not self.schema_connector:
            return
        
        # Convert state to SmartThings format
        st_state = self._convert_state_to_smartthings(sync.state)
        
        # Update connector's device state
        await self.schema_connector.update_device_state(
            sync.device_id, st_state, trigger_callback=True
        )
        
        self.stats.local_to_smartthings += 1
    
    async def _sync_to_simulation(self, sync: PendingSync) -> None:
        """Sync state to 3D simulation."""
        if not self._simulation_callback:
            return
        
        try:
            result = self._simulation_callback(sync.device_id, sync.state)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.error(f"Simulation callback error: {e}")
    
    # =========================================================================
    # State Conversion
    # =========================================================================
    
    def _convert_state_to_smartthings(
        self,
        state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Convert internal state format to SmartThings format."""
        st_state = {}
        
        for key, value in state.items():
            st_key = STATE_KEY_TO_ST.get(key)
            if st_key:
                # Convert values
                if key in ("on", "switch"):
                    st_state[st_key] = "on" if value else "off"
                elif key == "locked":
                    st_state[st_key] = "locked" if value else "unlocked"
                elif key == "motion":
                    st_state[st_key] = "active" if value else "inactive"
                elif key == "contact":
                    st_state[st_key] = "open" if value else "closed"
                else:
                    st_state[st_key] = value
            else:
                # Keep as-is if already in st. format
                if key.startswith("st."):
                    st_state[key] = value
        
        # Always include health check
        st_state["st.healthCheck.healthStatus"] = "online"
        
        return st_state
    
    def _convert_state_from_smartthings(
        self,
        st_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Convert SmartThings state format to internal format."""
        state = {}
        
        for key, value in st_state.items():
            internal_key = ST_TO_STATE_KEY.get(key)
            if internal_key:
                # Convert values
                if internal_key in ("on", "switch"):
                    state[internal_key] = value == "on"
                elif internal_key == "locked":
                    state[internal_key] = value == "locked"
                elif internal_key == "motion":
                    state[internal_key] = value == "active"
                elif internal_key == "contact":
                    state[internal_key] = value == "open"
                else:
                    state[internal_key] = value
            else:
                # Keep st. prefixed keys
                state[key] = value
        
        return state
    
    def _command_to_state_update(
        self,
        capability: str,
        command: str,
        arguments: List[Any],
    ) -> Optional[Dict[str, Any]]:
        """Convert a SmartThings command to a state update."""
        # Common command mappings
        command_map = {
            ("st.switch", "on"): {"switch": True, "on": True},
            ("st.switch", "off"): {"switch": False, "on": False},
            ("st.lock", "lock"): {"locked": True},
            ("st.lock", "unlock"): {"locked": False},
        }
        
        key = (capability, command)
        if key in command_map:
            return command_map[key]
        
        # Parameterized commands
        if command == "setLevel" and arguments:
            return {"level": arguments[0], "brightness": arguments[0]}
        elif command == "setColor" and arguments:
            color = arguments[0] if arguments else {}
            return {
                "hue": color.get("hue", 0),
                "saturation": color.get("saturation", 100),
            }
        elif command == "setColorTemperature" and arguments:
            return {"colorTemperature": arguments[0]}
        
        logger.warning(f"Unknown command: {capability}.{command}")
        return None
    
    # =========================================================================
    # Loop Prevention
    # =========================================================================
    
    def _is_duplicate_update(self, device_id: str, source: SyncSource) -> bool:
        """Check if this is a duplicate update (loop prevention)."""
        key = f"{device_id}:{source.value}"
        last_update = self._recent_updates.get(key)
        
        if last_update:
            elapsed = time.time() - last_update
            if elapsed < self.config.loop_prevention_window:
                return True
        
        return False
    
    def _mark_update(self, device_id: str, source: SyncSource) -> None:
        """Mark an update for loop prevention."""
        key = f"{device_id}:{source.value}"
        self._recent_updates[key] = time.time()
        
        # Clean old entries
        now = time.time()
        self._recent_updates = {
            k: v for k, v in self._recent_updates.items()
            if now - v < self.config.loop_prevention_window * 2
        }
    
    # =========================================================================
    # Statistics
    # =========================================================================
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get sync bridge statistics."""
        return {
            "total_syncs": self.stats.total_syncs,
            "successful_syncs": self.stats.successful_syncs,
            "failed_syncs": self.stats.failed_syncs,
            "success_rate": (
                self.stats.successful_syncs / self.stats.total_syncs * 100
                if self.stats.total_syncs > 0 else 100
            ),
            "smartthings_to_local": self.stats.smartthings_to_local,
            "local_to_smartthings": self.stats.local_to_smartthings,
            "conflicts_resolved": self.stats.conflicts_resolved,
            "last_sync_time": (
                self.stats.last_sync_time.isoformat()
                if self.stats.last_sync_time else None
            ),
            "pending_syncs": len(self._pending_syncs),
        }


# =============================================================================
# Factory Function
# =============================================================================

async def create_smartthings_bridge(
    db_path: str = "data/vesper_devices.db",
    enable_docker: bool = False,
    schema_host: str = "0.0.0.0",
    schema_port: int = 8443,
) -> SmartThingsSyncBridge:
    """
    Factory function to create a fully configured SmartThings sync bridge.
    
    Args:
        db_path: Path to SQLite database
        enable_docker: Enable Docker device manager
        schema_host: Host for Schema webhook server
        schema_port: Port for Schema webhook server
        
    Returns:
        Configured and started SmartThingsSyncBridge
    """
    # Create registry
    registry = DeviceRegistry(db_path=db_path)
    
    # Create Docker manager if enabled
    docker_manager = None
    if enable_docker:
        from .docker_device_manager import DeviceManagerConfig
        docker_config = DeviceManagerConfig()
        docker_manager = DockerDeviceManager(docker_config)
    
    # Create Schema connector
    from .schema_connector import SchemaConnectorConfig
    schema_config = SchemaConnectorConfig(
        host=schema_host,
        port=schema_port,
    )
    schema_connector = SmartThingsSchemaConnector(schema_config)
    
    # Create bridge
    bridge = SmartThingsSyncBridge(
        registry=registry,
        docker_manager=docker_manager,
        schema_connector=schema_connector,
    )
    
    return bridge
