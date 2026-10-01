"""
Matter Adapter — Bridges Matter nodes into VESPER's IoT ecosystem.

This is the heart of the integration. It mirrors the architecture of
``homeassistant/components/matter/adapter.py`` but adapted for VESPER:

HA's MatterAdapter:
    - Creates HA device-registry entries
    - Runs discovery schemas to create HA entities (lights, sensors, etc.)
    - Subscribes to node events for real-time state updates

VESPER's MatterAdapter:
    - Discovers nodes and builds ``MatterDeviceNode`` objects
    - Registers them with ``VirtualHub`` (DeviceRecord + TrafficRecord)
    - Pushes state changes to ``EventBus`` → Dashboard
    - Provides cluster-level commands for security experiments

Usage::

    from vesper.matter.adapter import MatterAdapter
    from vesper.matter.client import VesperMatterClient

    client = VesperMatterClient("ws://localhost:5580/ws")
    await client.connect()

    adapter = MatterAdapter(client)
    await adapter.setup()          # discovers all nodes
    devices = adapter.devices      # dict[node_id, MatterDeviceNode]
    await adapter.turn_on(1, 1)    # OnOff.On on node 1, endpoint 1
    await adapter.shutdown()

Reference:
    https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/adapter.py
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Optional

from .client import VesperMatterClient, _HAS_CHIP_CLUSTERS, _HAS_MATTER_CLIENT
from .const import (
    CLUSTER_ID_COLOR_CONTROL,
    CLUSTER_ID_DOOR_LOCK,
    CLUSTER_ID_FAN_CONTROL,
    CLUSTER_ID_LEVEL_CONTROL,
    CLUSTER_ID_ON_OFF,
    CLUSTER_ID_THERMOSTAT,
    LOGGER,
)
from .device import MatterDeviceNode, build_device_from_node

# Conditional chip imports for cluster commands
if _HAS_CHIP_CLUSTERS:
    from chip.clusters import Objects as clusters
else:
    clusters = None  # type: ignore[assignment]

# Conditional event type import
if _HAS_MATTER_CLIENT:
    from matter_server.common.models import EventType
else:
    EventType = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class MatterAdapter:
    """
    Adapt Matter nodes into VESPER's device model.

    Responsibilities:
        1. Discover all nodes from python-matter-server
        2. Build ``MatterDeviceNode`` objects for each
        3. Subscribe to node events (added / updated / removed)
        4. Provide high-level device commands (turn_on, lock, set_temp…)
        5. Integrate with VESPER Hub + EventBus + Dashboard
    """

    def __init__(
        self,
        client: VesperMatterClient,
        event_bus: Optional[Any] = None,
        hub: Optional[Any] = None,
    ):
        self._client = client
        self._event_bus = event_bus
        self._hub = hub
        self._devices: dict[int, MatterDeviceNode] = {}
        self._state_callbacks: list[Callable] = []
        self._unsubscribes: list[Callable] = []
        self._setup_done = False

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def setup(self) -> None:
        """
        Discover all existing nodes and subscribe to future events.

        This mirrors HA's ``MatterAdapter.setup_nodes()``.
        """
        if not self._client.connected:
            logger.warning("MatterAdapter.setup called but client not connected")
            return

        # 1. Discover existing nodes
        self._discover_existing_nodes()

        # 2. Subscribe to node lifecycle events
        self._subscribe_events()

        self._setup_done = True
        logger.info(
            "MatterAdapter: Setup complete — %d device(s) discovered",
            len(self._devices),
        )

    def _discover_existing_nodes(self) -> None:
        """Build MatterDeviceNode for every node the server already knows."""
        for raw_node in self._client.get_nodes():
            try:
                device = build_device_from_node(raw_node)
                self._devices[device.node_id] = device
                self._register_with_hub(device)
                logger.info(
                    "  → Node %d: %s (%s) — %s",
                    device.node_id,
                    device.name,
                    device.primary_vesper_category,
                    "online" if device.available else "offline",
                )
            except Exception as exc:
                logger.error(
                    "Failed to process node %s: %s",
                    getattr(raw_node, "node_id", "?"),
                    exc,
                )

    def _subscribe_events(self) -> None:
        """
        Subscribe to node events from python-matter-server.

        Mirrors HA's adapter which subscribes to:
            NODE_ADDED, NODE_UPDATED, NODE_REMOVED,
            ENDPOINT_ADDED, ENDPOINT_REMOVED
        """
        if EventType is None:
            return

        unsub = self._client.subscribe(
            callback=self._on_node_added,
            event_filter=EventType.NODE_ADDED,
        )
        self._unsubscribes.append(unsub)

        unsub = self._client.subscribe(
            callback=self._on_node_updated,
            event_filter=EventType.NODE_UPDATED,
        )
        self._unsubscribes.append(unsub)

        unsub = self._client.subscribe(
            callback=self._on_node_removed,
            event_filter=EventType.NODE_REMOVED,
        )
        self._unsubscribes.append(unsub)

    def _on_node_added(self, event_type: Any, node: Any) -> None:
        """Handle a new node being commissioned."""
        try:
            device = build_device_from_node(node)
            self._devices[device.node_id] = device
            self._register_with_hub(device)
            self._emit_event("matter_node_added", device.to_dict())
            logger.info(
                "MatterAdapter: Node %d added — %s",
                device.node_id,
                device.name,
            )
        except Exception as exc:
            logger.error("Error processing added node: %s", exc)

    def _on_node_updated(self, event_type: Any, node: Any) -> None:
        """Handle node attribute updates (state changes)."""
        try:
            device = build_device_from_node(node)
            old_device = self._devices.get(device.node_id)
            self._devices[device.node_id] = device

            # Emit state-change event with old/new comparison
            event_data = device.to_dict()
            if old_device:
                event_data["previous_available"] = old_device.available
            self._emit_event("matter_node_updated", event_data)

            # Notify registered callbacks
            for cb in self._state_callbacks:
                try:
                    cb(device)
                except Exception:
                    pass

        except Exception as exc:
            logger.error("Error processing updated node: %s", exc)

    def _on_node_removed(self, event_type: Any, node_id: int) -> None:
        """Handle node removal from the fabric."""
        removed = self._devices.pop(node_id, None)
        name = removed.name if removed else f"node-{node_id}"
        self._emit_event(
            "matter_node_removed",
            {"node_id": node_id, "name": name},
        )
        logger.info("MatterAdapter: Node %d removed — %s", node_id, name)

    async def shutdown(self) -> None:
        """Unsubscribe from events and clean up."""
        for unsub in self._unsubscribes:
            try:
                unsub()
            except Exception:
                pass
        self._unsubscribes.clear()
        self._setup_done = False
        logger.info("MatterAdapter: Shutdown complete")

    # ── Device Access ──────────────────────────────────────────────────

    @property
    def devices(self) -> dict[int, MatterDeviceNode]:
        """All discovered Matter devices keyed by node_id."""
        return self._devices

    def get_device(self, node_id: int) -> Optional[MatterDeviceNode]:
        """Get a specific device by node_id."""
        return self._devices.get(node_id)

    def get_devices_by_category(self, category: str) -> list[MatterDeviceNode]:
        """Get all devices matching a VESPER category."""
        return [
            d for d in self._devices.values()
            if d.primary_vesper_category == category
        ]

    def on_state_change(self, callback: Callable[[MatterDeviceNode], None]) -> None:
        """Register a callback for when any Matter device state changes."""
        self._state_callbacks.append(callback)

    # ── High-Level Commands ────────────────────────────────────────────
    # These mirror the real HA entity methods (async_turn_on, etc.) but
    # send CHIP cluster commands through the matter-server directly.

    async def turn_on(
        self,
        node_id: int,
        endpoint_id: int = 1,
    ) -> bool:
        """Send OnOff.On() command to a device."""
        if clusters is None:
            logger.error("CHIP clusters not available")
            return False
        try:
            await self._client.send_command(
                node_id, endpoint_id,
                clusters.OnOff.Commands.On(),
            )
            self._record_command(node_id, endpoint_id, "OnOff.On")
            return True
        except Exception as exc:
            logger.error("turn_on failed for node %d: %s", node_id, exc)
            return False

    async def turn_off(
        self,
        node_id: int,
        endpoint_id: int = 1,
    ) -> bool:
        """Send OnOff.Off() command."""
        if clusters is None:
            return False
        try:
            await self._client.send_command(
                node_id, endpoint_id,
                clusters.OnOff.Commands.Off(),
            )
            self._record_command(node_id, endpoint_id, "OnOff.Off")
            return True
        except Exception as exc:
            logger.error("turn_off failed for node %d: %s", node_id, exc)
            return False

    async def set_brightness(
        self,
        node_id: int,
        level: int,
        transition_time: int = 0,
        endpoint_id: int = 1,
    ) -> bool:
        """
        Send LevelControl.MoveToLevel() command.

        Args:
            level:           0–254
            transition_time: tenths of a second
        """
        if clusters is None:
            return False
        try:
            await self._client.send_command(
                node_id, endpoint_id,
                clusters.LevelControl.Commands.MoveToLevel(
                    level=level,
                    transitionTime=transition_time,
                    optionsMask=0,
                    optionsOverride=0,
                ),
            )
            self._record_command(
                node_id, endpoint_id,
                f"LevelControl.MoveToLevel({level})",
            )
            return True
        except Exception as exc:
            logger.error("set_brightness failed: %s", exc)
            return False

    async def lock(
        self,
        node_id: int,
        pin_code: Optional[bytes] = None,
        endpoint_id: int = 1,
    ) -> bool:
        """Send DoorLock.LockDoor() command."""
        if clusters is None:
            return False
        try:
            await self._client.send_command(
                node_id, endpoint_id,
                clusters.DoorLock.Commands.LockDoor(
                    PINCode=pin_code,
                ),
            )
            self._record_command(node_id, endpoint_id, "DoorLock.LockDoor")
            return True
        except Exception as exc:
            logger.error("lock failed: %s", exc)
            return False

    async def unlock(
        self,
        node_id: int,
        pin_code: Optional[bytes] = None,
        endpoint_id: int = 1,
    ) -> bool:
        """Send DoorLock.UnlockDoor() command."""
        if clusters is None:
            return False
        try:
            await self._client.send_command(
                node_id, endpoint_id,
                clusters.DoorLock.Commands.UnlockDoor(
                    PINCode=pin_code,
                ),
            )
            self._record_command(node_id, endpoint_id, "DoorLock.UnlockDoor")
            return True
        except Exception as exc:
            logger.error("unlock failed: %s", exc)
            return False

    async def set_thermostat(
        self,
        node_id: int,
        heating_setpoint_c: Optional[float] = None,
        cooling_setpoint_c: Optional[float] = None,
        mode: Optional[int] = None,
        endpoint_id: int = 1,
    ) -> bool:
        """
        Write thermostat setpoints / mode.

        Setpoints are in °C; they are converted to centi-degrees
        for the CHIP attribute write.
        """
        if clusters is None:
            return False
        try:
            if heating_setpoint_c is not None:
                await self._client.write_attribute(
                    node_id, endpoint_id,
                    clusters.Thermostat.Attributes.OccupiedHeatingSetpoint,
                    int(heating_setpoint_c * 100),
                )
            if cooling_setpoint_c is not None:
                await self._client.write_attribute(
                    node_id, endpoint_id,
                    clusters.Thermostat.Attributes.OccupiedCoolingSetpoint,
                    int(cooling_setpoint_c * 100),
                )
            if mode is not None:
                await self._client.write_attribute(
                    node_id, endpoint_id,
                    clusters.Thermostat.Attributes.SystemMode,
                    mode,
                )
            self._record_command(
                node_id, endpoint_id,
                f"Thermostat(heat={heating_setpoint_c}, cool={cooling_setpoint_c}, mode={mode})",
            )
            return True
        except Exception as exc:
            logger.error("set_thermostat failed: %s", exc)
            return False

    async def send_raw_command(
        self,
        node_id: int,
        endpoint_id: int,
        command: Any,
    ) -> Any:
        """
        Send an arbitrary CHIP cluster command.

        This is the escape-hatch for security experiments that need
        to send unusual or crafted commands.
        """
        result = await self._client.send_command(node_id, endpoint_id, command)
        self._record_command(
            node_id, endpoint_id,
            f"raw:{type(command).__name__}",
        )
        return result

    # ── Hub / EventBus Integration ─────────────────────────────────────

    def _register_with_hub(self, device: MatterDeviceNode) -> None:
        """Register a discovered Matter device with the VESPER Hub."""
        if self._hub is None:
            return

        try:
            # Import here to avoid circular dependency
            from vesper.hub.base import DeviceRecord

            record = DeviceRecord(
                device_id=f"matter-{device.node_id}",
                name=device.name,
                device_type=device.primary_vesper_category,
                protocol="matter",
                metadata={
                    "node_id": device.node_id,
                    "vendor": device.vendor_name,
                    "product": device.product_name,
                    "serial": device.serial_number,
                    "hw": device.hw_version,
                    "sw": device.sw_version,
                    "network": device.network_type,
                    "is_bridge": device.is_bridge,
                    "clusters": [
                        cn
                        for ep in device.endpoints
                        for cn in ep.cluster_names
                    ],
                },
            )
            self._hub.register_device(record)
        except Exception as exc:
            logger.debug("Hub registration skipped: %s", exc)

    def _record_command(
        self,
        node_id: int,
        endpoint_id: int,
        command_desc: str,
    ) -> None:
        """Log a command to the Hub traffic log and EventBus."""
        if self._hub is not None:
            try:
                from vesper.hub.base import HubTrafficRecord

                record = HubTrafficRecord(
                    timestamp=time.time(),
                    source_id="vesper-matter-adapter",
                    target_id=f"matter-{node_id}",
                    protocol="matter",
                    payload={
                        "command": command_desc,
                        "endpoint": endpoint_id,
                    },
                    direction="outbound",
                )
                self._hub.record_traffic(record)
            except Exception:
                pass

        if self._event_bus is not None:
            try:
                self._event_bus.publish(
                    "matter.command",
                    {
                        "node_id": node_id,
                        "endpoint_id": endpoint_id,
                        "command": command_desc,
                        "timestamp": time.time(),
                    },
                )
            except Exception:
                pass

    def _emit_event(self, event_type: str, data: dict) -> None:
        """Push an event to the VESPER EventBus (if available)."""
        if self._event_bus is not None:
            try:
                self._event_bus.publish(event_type, data)
            except Exception:
                pass

    # ── Diagnostics ────────────────────────────────────────────────────

    def get_diagnostics(self) -> dict[str, Any]:
        """Return adapter-level diagnostics for the dashboard."""
        return {
            "setup_done": self._setup_done,
            "total_devices": len(self._devices),
            "devices_by_category": self._count_by_category(),
            "client": self._client.get_diagnostics(),
            "devices": [d.to_dict() for d in self._devices.values()],
        }

    def _count_by_category(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for d in self._devices.values():
            cat = d.primary_vesper_category
            counts[cat] = counts.get(cat, 0) + 1
        return counts
