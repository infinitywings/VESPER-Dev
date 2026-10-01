"""
Virtual Hub — Software-based hub for VESPER.

The Virtual Hub acts as the central routing point for all device
communication within VESPER. It aggregates Matter (via bridge +
Home Assistant) and SmartThings protocols into a unified control plane.

All traffic passes through the hub, enabling:
- Real-time packet inspection and logging
- Attack injection and manipulation
- Protocol translation between Matter ↔ SmartThings
- Centralized device state management
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from vesper.hub.base import (
    BaseHub,
    DeviceRecord,
    HubCapability,
    HubState,
    HubTrafficRecord,
)

logger = logging.getLogger(__name__)


class VirtualHub(BaseHub):
    """
    Software hub that routes all VESPER device traffic.

    The Virtual Hub connects to:
    1. Matter bridge (matter.js) for simulated IoT devices
    2. Home Assistant WebSocket API for Matter devices
    3. SmartThings Schema Connector for cloud devices
    4. VESPER EventBus for simulation events

    All protocols are unified through the hub's device registry,
    allowing cross-protocol automation and monitoring.
    """

    def __init__(
        self,
        hub_id: str = "vesper-virtual-hub",
        name: str = "VESPER Virtual Hub",
        event_bus=None,          # injected by VesperEngine
        registry=None,           # shared DeviceRegistry
        wifi_network=None,       # shared WiFiEmulator
        matter_bridge=None,      # shared MatterBridgeClient
        matter_bridge_url: str = "http://localhost:8484",  # kept for compat
        ha_url: Optional[str] = None,
        ha_token: Optional[str] = None,
    ):
        super().__init__(hub_id=hub_id, name=name)
        self._matter_bridge_url = matter_bridge_url
        self._ha_url = ha_url or "http://localhost:8123"
        self._ha_token = ha_token
        self._matter_bridge = matter_bridge   # use injected (no more _connect_matter_bridge)
        self._wifi_network = wifi_network
        self._registry = registry
        self._event_bus = event_bus
        self._ha_ws = None
        self._running = False
        self._tasks: list[asyncio.Task] = []

        # Protocol bridges
        self._command_queue: asyncio.Queue = asyncio.Queue()

        # Add capabilities
        self._capabilities = {
            HubCapability.WIFI,
            HubCapability.MATTER,
            HubCapability.HOMEASSISTANT,
            HubCapability.SMARTTHINGS,
        }

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the Virtual Hub and connect to all services."""
        logger.info(f"[{self.hub_id}] Starting Virtual Hub...")
        self._start_time = time.time()
        self.state = HubState.INITIALIZING

        try:
            # Connect to Matter bridge (prefer injected, fallback in compat mode)
            if self._matter_bridge is None and self._matter_bridge_url:
                import vesper.core.event_bus as _eb_mod
                if getattr(_eb_mod, '_STRICT_MODE', False):
                    raise RuntimeError(
                        "STRICT MODE: VirtualHub.start() has no injected MatterBridgeClient. "
                        "In strict mode, all shared objects must be injected by VesperEngine (INV-3)."
                    )
                logger.warning("COMPAT MODE: VirtualHub creating its own MatterBridgeClient (not recommended)")
                await self._connect_matter_bridge()  # fallback if not injected

            # Connect to Home Assistant (if configured)
            if self._ha_token:
                await self._connect_home_assistant()

            # Start background tasks
            self._running = True
            self._tasks.append(
                asyncio.create_task(self._traffic_monitor_loop())
            )
            self._tasks.append(
                asyncio.create_task(self._device_heartbeat_loop())
            )
            self._tasks.append(
                asyncio.create_task(self._command_processor_loop())
            )

            self.state = HubState.RUNNING
            logger.info(
                f"[{self.hub_id}] Virtual Hub running — "
                f"{len(self._devices)} devices, "
                f"capabilities: {[c.value for c in self._capabilities]}"
            )
        except Exception as e:
            self.state = HubState.ERROR
            logger.error(f"[{self.hub_id}] Failed to start: {e}")
            raise

    async def stop(self) -> None:
        """Stop the Virtual Hub and clean up."""
        logger.info(f"[{self.hub_id}] Stopping Virtual Hub...")
        self._running = False

        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        # Disconnect Matter bridge
        self._matter_bridge = None

        self.state = HubState.STOPPED
        logger.info(f"[{self.hub_id}] Virtual Hub stopped.")

    async def health_check(self) -> dict[str, Any]:
        """Return health status of hub services."""
        health = {
            "hub_id": self.hub_id,
            "state": self.state.name,
            "uptime_seconds": time.time() - self._start_time,
            "services": {
                "matter_bridge": {
                    "connected": self._matter_bridge is not None,
                    "url": self._matter_bridge_url,
                },
                "home_assistant": {
                    "connected": self._ha_ws is not None,
                    "url": self._ha_url,
                },
            },
            "devices": {
                "total": len(self._devices),
                "online": sum(1 for d in self._devices.values() if d.online),
            },
        }
        return health

    # ── Matter Bridge Integration ─────────────────────────────────────────

    async def _connect_matter_bridge(self) -> None:
        """Connect to the matter.js bridge REST API."""
        try:
            from vesper.matter.bridge_client import MatterBridgeClient

            bridge = MatterBridgeClient(base_url=self._matter_bridge_url)
            if bridge.wait_ready_sync(max_wait=15):
                self._matter_bridge = bridge
                logger.info(
                    f"[{self.hub_id}] Connected to Matter bridge at "
                    f"{self._matter_bridge_url}"
                )
            else:
                logger.warning(
                    f"[{self.hub_id}] Matter bridge not reachable at "
                    f"{self._matter_bridge_url}"
                )
        except ImportError:
            logger.warning(
                f"[{self.hub_id}] Matter bridge client not available"
            )
        except Exception as e:
            logger.error(f"[{self.hub_id}] Matter bridge connection error: {e}")

    def _handle_matter_message(self, device_id: str, state: dict) -> None:
        """Process an incoming Matter device state update and record traffic."""
        try:
            # Record traffic
            record = HubTrafficRecord(
                timestamp=time.time(),
                source_id=device_id,
                target_id=self.hub_id,
                protocol="matter",
                direction="inbound",
                topic=f"matter/devices/{device_id}/state",
                payload_size=len(json.dumps(state)),
                payload_summary=json.dumps(state)[:200],
            )
            self.record_traffic(record)

            # Update device state
            if device_id in self._devices:
                device = self._devices[device_id]
                device.state.update(state)
                device.last_seen = time.time()
                device.online = True

        except Exception as e:
            logger.error(f"[{self.hub_id}] Matter message handling error: {e}")

    async def _publish_matter(
        self, device_id: str, state: dict[str, Any]
    ) -> bool:
        """Send a state update to the Matter bridge, routing through WiFi when available."""
        try:
            if self._wifi_network:
                # Route through WiFi emulator (INV-4 compliant)
                self._wifi_network.route_to_bridge(
                    device_id, "PUT",
                    f"/devices/{device_id}/state", state,
                )
            elif self._matter_bridge:
                # Strict mode: WiFi is mandatory for bridge traffic (INV-4)
                import vesper.core.event_bus as _eb_mod
                if getattr(_eb_mod, '_STRICT_MODE', False):
                    raise RuntimeError(
                        f"STRICT MODE: _publish_matter() called without WiFiNetwork. "
                        f"All bridge traffic must route through WiFi (INV-4). "
                        f"Either enable WiFi or use strict=False."
                    )
                logger.warning("COMPAT MODE: direct bridge access (bypassing WiFi) for %s", device_id)
                self._matter_bridge.update_state_sync(device_id, state)
            else:
                logger.error("No WiFi network and no MatterBridge — cannot publish state for %s", device_id)
                return False

            record = HubTrafficRecord(
                timestamp=time.time(),
                source_id=self.hub_id,
                target_id=device_id,
                protocol="matter",
                direction="outbound",
                topic=f"matter/devices/{device_id}/cmd",
                payload_size=len(json.dumps(state)),
                payload_summary=json.dumps(state)[:200],
            )
            self.record_traffic(record)
            return True
        except Exception as e:
            logger.error(f"Matter bridge publish error: {e}")
            return False

    # ── Home Assistant Integration ─────────────────────────────────────

    async def _connect_home_assistant(self) -> None:
        """Connect to Home Assistant via WebSocket API."""
        try:
            import aiohttp

            url = f"{self._ha_url.replace('http', 'ws')}/api/websocket"
            session = aiohttp.ClientSession()
            ws = await session.ws_connect(url)

            # Authenticate
            msg = await ws.receive_json()
            if msg.get("type") == "auth_required":
                await ws.send_json(
                    {"type": "auth", "access_token": self._ha_token}
                )
                auth_result = await ws.receive_json()
                if auth_result.get("type") == "auth_ok":
                    logger.info(
                        f"[{self.hub_id}] Connected to Home Assistant at {self._ha_url}"
                    )
                    self._ha_ws = ws
                    self._ha_session = session

                    # Subscribe to state changes
                    await ws.send_json(
                        {
                            "id": 1,
                            "type": "subscribe_events",
                            "event_type": "state_changed",
                        }
                    )

                    # Start HA event listener
                    self._tasks.append(
                        asyncio.create_task(self._ha_event_listener())
                    )
                else:
                    logger.error(
                        f"[{self.hub_id}] HA auth failed: {auth_result}"
                    )
                    await session.close()
        except ImportError:
            logger.warning(f"[{self.hub_id}] aiohttp not installed — HA disabled")
        except Exception as e:
            logger.error(f"[{self.hub_id}] HA connection error: {e}")

    async def _ha_event_listener(self) -> None:
        """Listen for Home Assistant state change events."""
        if not self._ha_ws:
            return

        try:
            async for msg in self._ha_ws:
                if msg.type == 1:  # TEXT
                    data = json.loads(msg.data)
                    if data.get("type") == "event":
                        event_data = data.get("event", {}).get("data", {})
                        entity_id = event_data.get("entity_id", "")
                        new_state = event_data.get("new_state", {})

                        # Record traffic from HA
                        record = HubTrafficRecord(
                            timestamp=time.time(),
                            source_id=entity_id,
                            target_id=self.hub_id,
                            protocol="homeassistant",
                            direction="inbound",
                            topic=f"state_changed/{entity_id}",
                            payload_summary=json.dumps(
                                new_state.get("state", "")
                            )[:200],
                        )
                        self.record_traffic(record)

                        # Sync HA device state to hub registry
                        if entity_id in self._devices:
                            device = self._devices[entity_id]
                            if new_state:
                                device.state.update(
                                    {
                                        "state": new_state.get("state"),
                                        "attributes": new_state.get(
                                            "attributes", {}
                                        ),
                                    }
                                )
                                device.last_seen = time.time()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[{self.hub_id}] HA event listener error: {e}")

    async def call_ha_service(
        self,
        domain: str,
        service: str,
        entity_id: str,
        data: Optional[dict] = None,
    ) -> bool:
        """
        Call a Home Assistant service (e.g., light.turn_on).

        This allows VESPER to control Matter devices through HA.
        """
        if not self._ha_ws:
            logger.warning("Home Assistant not connected")
            return False

        try:
            service_data = {"entity_id": entity_id}
            if data:
                service_data.update(data)

            msg_id = int(time.time() * 1000) % 100000
            await self._ha_ws.send_json(
                {
                    "id": msg_id,
                    "type": "call_service",
                    "domain": domain,
                    "service": service,
                    "service_data": service_data,
                }
            )

            record = HubTrafficRecord(
                timestamp=time.time(),
                source_id=self.hub_id,
                target_id=entity_id,
                protocol="homeassistant",
                direction="outbound",
                topic=f"{domain}.{service}",
                payload_summary=json.dumps(service_data)[:200],
            )
            self.record_traffic(record)
            return True
        except Exception as e:
            logger.error(f"HA service call error: {e}")
            return False

    # ── Device Commands (unified interface) ────────────────────────────

    async def send_command(
        self,
        device_id: str,
        command: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Send a command to a device through the appropriate protocol.

        The hub automatically routes the command based on the device's
        registered protocol:
        - Matter devices: send state via Matter bridge REST API
        - Matter/HA devices: call HA service
        - SmartThings devices: call SmartThings API
        """
        device = self.get_device(device_id)
        if not device:
            return {"success": False, "error": f"Device {device_id} not found"}

        params = params or {}

        if device.protocol == "matter":
            success = await self._publish_matter(device_id, {"command": command, **params})
            return {"success": success, "protocol": "matter", "device_id": device_id}

        elif device.protocol in ("homeassistant",):
            # Route through Home Assistant
            # Map VESPER commands to HA service calls
            domain, service = self._map_command_to_ha_service(
                device, command, params
            )
            success = await self.call_ha_service(
                domain, service, device_id, params
            )
            return {
                "success": success,
                "protocol": "homeassistant",
                "service": f"{domain}.{service}",
            }

        elif device.protocol == "smartthings":
            # Route through SmartThings Schema Connector
            return await self._send_smartthings_command(
                device_id, command, params
            )

        else:
            return {
                "success": False,
                "error": f"Unknown protocol: {device.protocol}",
            }

    async def get_device_state(self, device_id: str) -> dict[str, Any]:
        """Get current device state from the hub registry."""
        device = self.get_device(device_id)
        if not device:
            return {"error": f"Device {device_id} not found"}
        return {
            "device_id": device.device_id,
            "online": device.online,
            "state": device.state,
            "last_seen": device.last_seen,
            "protocol": device.protocol,
        }

    async def set_device_state(
        self, device_id: str, state: dict[str, Any]
    ) -> bool:
        """Set device state through the hub."""
        device = self.get_device(device_id)
        if not device:
            return False

        # Route through the appropriate protocol
        if device.protocol == "matter":
            return await self._publish_matter(device_id, state)
        elif device.protocol in ("homeassistant",):
            # Map state to HA service call
            for key, value in state.items():
                if key == "on":
                    service = "turn_on" if value else "turn_off"
                    domain = device.device_type.split(".")[0] if "." in device.device_type else "switch"
                    await self.call_ha_service(domain, service, device_id)
            return True
        return False

    # ── Helper Methods ─────────────────────────────────────────────────

    def _map_command_to_ha_service(
        self,
        device: DeviceRecord,
        command: str,
        params: dict[str, Any],
    ) -> tuple[str, str]:
        """Map a VESPER command to a Home Assistant domain.service."""
        command_map = {
            "on": ("switch", "turn_on"),
            "off": ("switch", "turn_off"),
            "toggle": ("switch", "toggle"),
            "set_brightness": ("light", "turn_on"),
            "set_color": ("light", "turn_on"),
            "set_temperature": ("climate", "set_temperature"),
            "lock": ("lock", "lock"),
            "unlock": ("lock", "unlock"),
            "open": ("cover", "open_cover"),
            "close": ("cover", "close_cover"),
        }
        if command in command_map:
            return command_map[command]

        # Infer domain from device type
        domain = "homeassistant"
        if "light" in device.device_type:
            domain = "light"
        elif "switch" in device.device_type or "plug" in device.device_type:
            domain = "switch"
        elif "sensor" in device.device_type:
            domain = "sensor"

        return (domain, command)

    async def _send_smartthings_command(
        self,
        device_id: str,
        command: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Send a command through the SmartThings API."""
        # Delegate to the SmartThings integration
        try:
            from vesper.integrations.smartthings import SmartThingsClient
            # This will be connected during setup
            return {"success": False, "error": "SmartThings client not initialized"}
        except ImportError:
            return {"success": False, "error": "SmartThings integration not available"}

    # ── Background Tasks ───────────────────────────────────────────────

    async def _traffic_monitor_loop(self) -> None:
        """Background task to monitor and aggregate traffic stats."""
        while self._running:
            try:
                await asyncio.sleep(30)
                stats = self.get_stats()
                logger.debug(
                    f"[{self.hub_id}] Traffic stats: "
                    f"{stats['traffic_records']} records, "
                    f"{stats['online_devices']}/{stats['total_devices']} devices online"
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Traffic monitor error: {e}")

    async def _device_heartbeat_loop(self) -> None:
        """Mark devices as offline if not seen recently."""
        while self._running:
            try:
                await asyncio.sleep(60)
                now = time.time()
                timeout = 300  # 5 minutes
                for device in self._devices.values():
                    if device.online and (now - device.last_seen) > timeout:
                        device.online = False
                        logger.warning(
                            f"[{self.hub_id}] Device offline: {device.device_id}"
                        )
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def _command_processor_loop(self) -> None:
        """Process queued commands."""
        while self._running:
            try:
                cmd = await asyncio.wait_for(
                    self._command_queue.get(), timeout=5.0
                )
                device_id = cmd.get("device_id")
                command = cmd.get("command")
                params = cmd.get("params", {})
                await self.send_command(device_id, command, params)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Command processor error: {e}")

    # ── EventBus Integration ───────────────────────────────────────────

    def connect_event_bus(self, event_bus) -> None:
        """
        Connect the hub to VESPER's EventBus for simulation events.

        This bridges the simulation layer (3D environment, humanoid agents)
        with the networking layer (Matter, SmartThings).
        """
        self._event_bus = event_bus

        # Subscribe to device-related events
        event_bus.subscribe("device_state", self._on_eventbus_device_state)
        event_bus.subscribe("proximity", self._on_eventbus_proximity)
        event_bus.subscribe("hub_command", self._on_eventbus_command)

        logger.info(f"[{self.hub_id}] Connected to EventBus")

    def _on_eventbus_device_state(self, event) -> None:
        """Handle device state events from the EventBus."""
        device_id = event.payload.get("device_id", "")
        if device_id in self._devices:
            self._devices[device_id].state.update(event.payload.get("state", {}))
            self._devices[device_id].last_seen = time.time()

    def _on_eventbus_proximity(self, event) -> None:
        """Handle proximity events from humanoid agents."""
        # Proximity events can trigger device actions
        pass

    def _on_eventbus_command(self, event) -> None:
        """Handle command events from the EventBus."""
        self._command_queue.put_nowait(event.payload)

    # ── Discovery ──────────────────────────────────────────────────────

    async def discover_ha_devices(self) -> list[DeviceRecord]:
        """
        Discover devices from Home Assistant (including Matter devices).

        This queries HA's REST API to find all entities and registers
        them with the hub.
        """
        if not self._ha_token:
            return []

        devices = []
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {self._ha_token}",
                    "Content-Type": "application/json",
                }
                async with session.get(
                    f"{self._ha_url}/api/states", headers=headers
                ) as resp:
                    if resp.status == 200:
                        states = await resp.json()
                        for entity in states:
                            entity_id = entity.get("entity_id", "")
                            attrs = entity.get("attributes", {})

                            # Determine protocol (Matter devices have specific attributes)
                            protocol = "homeassistant"
                            if attrs.get("matter_node_id"):
                                protocol = "matter"

                            device = DeviceRecord(
                                device_id=entity_id,
                                device_type=entity_id.split(".")[0],
                                protocol=protocol,
                                name=attrs.get(
                                    "friendly_name", entity_id
                                ),
                                state={
                                    "state": entity.get("state"),
                                    "attributes": attrs,
                                },
                                last_seen=time.time(),
                                online=entity.get("state") != "unavailable",
                            )
                            self.register_device(device)
                            devices.append(device)

                        logger.info(
                            f"[{self.hub_id}] Discovered {len(devices)} HA devices "
                            f"({sum(1 for d in devices if d.protocol == 'matter')} Matter)"
                        )
        except Exception as e:
            logger.error(f"HA device discovery error: {e}")

        return devices
