"""
Physical Hub — Bridge to a physical Aeotec SmartThings Hub.

Connects VESPER to a real Aeotec Smart Home Hub (or any SmartThings-
compatible hub) via the SmartThings REST API. This enables:
- Control of real Z-Wave/Zigbee/WiFi devices connected to the hub
- Hybrid experiments mixing virtual and physical devices
- Real-world traffic generation for security analysis
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

# SmartThings API base URL
ST_API_BASE = "https://api.smartthings.com/v1"


class PhysicalHub(BaseHub):
    """
    Bridge to a physical Aeotec SmartThings Hub.

    Uses the SmartThings REST API to discover, monitor, and control
    real devices attached to a physical hub. All traffic between
    VESPER and the physical hub is logged for analysis.

    Requirements:
        - Aeotec Smart Home Hub (or Samsung SmartThings Hub)
        - SmartThings Personal Access Token (PAT)
        - Network connectivity to SmartThings cloud API
    """

    def __init__(
        self,
        hub_id: str = "vesper-physical-hub",
        name: str = "Aeotec SmartThings Hub",
        api_token: Optional[str] = None,
        location_id: Optional[str] = None,
        poll_interval: float = 10.0,
    ):
        super().__init__(hub_id=hub_id, name=name)
        self._api_token = api_token
        self._location_id = location_id
        self._poll_interval = poll_interval
        self._session = None
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._st_hub_id: Optional[str] = None  # SmartThings hub device ID

        self._capabilities = {
            HubCapability.SMARTTHINGS,
            HubCapability.ZIGBEE,
            HubCapability.ZWAVE,
            HubCapability.WIFI,
            HubCapability.MATTER,
        }

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the physical hub bridge."""
        if not self._api_token:
            raise ValueError(
                "SmartThings API token required. "
                "Get one at https://account.smartthings.com/tokens"
            )

        logger.info(f"[{self.hub_id}] Starting Physical Hub bridge...")
        self._start_time = time.time()
        self.state = HubState.INITIALIZING

        try:
            import aiohttp

            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self._api_token}",
                    "Content-Type": "application/json",
                }
            )

            # Discover the hub and location
            await self._discover_hub()

            # Discover all devices
            await self._discover_devices()

            # Start polling loop
            self._running = True
            self._tasks.append(
                asyncio.create_task(self._poll_device_states())
            )
            self._tasks.append(
                asyncio.create_task(self._device_heartbeat_loop())
            )

            self.state = HubState.RUNNING
            logger.info(
                f"[{self.hub_id}] Physical Hub bridge running — "
                f"{len(self._devices)} devices discovered"
            )
        except ImportError:
            self.state = HubState.ERROR
            logger.error("aiohttp required for Physical Hub")
            raise
        except Exception as e:
            self.state = HubState.ERROR
            logger.error(f"[{self.hub_id}] Failed to start: {e}")
            raise

    async def stop(self) -> None:
        """Stop the physical hub bridge."""
        logger.info(f"[{self.hub_id}] Stopping Physical Hub bridge...")
        self._running = False

        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        if self._session:
            await self._session.close()
            self._session = None

        self.state = HubState.STOPPED
        logger.info(f"[{self.hub_id}] Physical Hub bridge stopped.")

    async def health_check(self) -> dict[str, Any]:
        """Check physical hub connectivity."""
        health = {
            "hub_id": self.hub_id,
            "state": self.state.name,
            "uptime_seconds": time.time() - self._start_time,
            "st_hub_id": self._st_hub_id,
            "location_id": self._location_id,
            "api_connected": self._session is not None,
            "devices": {
                "total": len(self._devices),
                "online": sum(1 for d in self._devices.values() if d.online),
            },
        }

        # Ping SmartThings API
        if self._session:
            try:
                async with self._session.get(
                    f"{ST_API_BASE}/locations"
                ) as resp:
                    health["api_reachable"] = resp.status == 200
            except Exception:
                health["api_reachable"] = False

        return health

    # ── SmartThings API ────────────────────────────────────────────────

    async def _api_get(self, path: str) -> Optional[dict]:
        """Make a GET request to the SmartThings API."""
        if not self._session:
            return None
        try:
            async with self._session.get(f"{ST_API_BASE}{path}") as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logger.error(
                        f"ST API GET {path} failed: {resp.status}"
                    )
                    return None
        except Exception as e:
            logger.error(f"ST API error: {e}")
            return None

    async def _api_post(self, path: str, data: dict) -> Optional[dict]:
        """Make a POST request to the SmartThings API."""
        if not self._session:
            return None
        try:
            async with self._session.post(
                f"{ST_API_BASE}{path}", json=data
            ) as resp:
                if resp.status in (200, 201):
                    return await resp.json()
                else:
                    body = await resp.text()
                    logger.error(
                        f"ST API POST {path} failed: {resp.status} — {body}"
                    )
                    return None
        except Exception as e:
            logger.error(f"ST API error: {e}")
            return None

    async def _discover_hub(self) -> None:
        """Discover the SmartThings hub and location."""
        # Get locations
        locations = await self._api_get("/locations")
        if locations and "items" in locations:
            items = locations["items"]
            if self._location_id:
                loc = next(
                    (l for l in items if l["locationId"] == self._location_id),
                    None,
                )
            else:
                loc = items[0] if items else None

            if loc:
                self._location_id = loc["locationId"]
                logger.info(
                    f"[{self.hub_id}] Location: {loc.get('name')} ({self._location_id})"
                )

        # Get hub device
        if self._location_id:
            devices = await self._api_get(
                f"/devices?locationId={self._location_id}&type=HUB"
            )
            if devices and "items" in devices:
                for hub in devices["items"]:
                    self._st_hub_id = hub["deviceId"]
                    logger.info(
                        f"[{self.hub_id}] Found hub: "
                        f"{hub.get('label', hub.get('name'))} ({self._st_hub_id})"
                    )
                    break

    async def _discover_devices(self) -> None:
        """Discover all devices on the SmartThings hub."""
        path = "/devices"
        if self._location_id:
            path += f"?locationId={self._location_id}"

        result = await self._api_get(path)
        if not result or "items" not in result:
            return

        for item in result["items"]:
            device_id = item["deviceId"]
            label = item.get("label", item.get("name", device_id))
            device_type = item.get("type", "UNKNOWN")
            room_id = item.get("roomId", "")

            # Get capabilities
            caps = []
            for component in item.get("components", []):
                for cap in component.get("capabilities", []):
                    caps.append(cap.get("id", ""))

            # Determine protocol from device network type
            network_type = item.get("deviceNetworkType", "")
            protocol_map = {
                "ZIGBEE": "zigbee",
                "ZWAVE": "zwave",
                "WIFI": "wifi",
                "MATTER": "matter",
                "LAN": "lan",
                "HUB": "hub",
            }
            protocol = protocol_map.get(network_type, "smartthings")

            device = DeviceRecord(
                device_id=device_id,
                device_type=device_type,
                protocol=protocol,
                name=label,
                room=room_id,
                capabilities=caps,
                manufacturer=item.get("manufacturerName", ""),
                model=item.get("deviceTypeName", ""),
                last_seen=time.time(),
                online=item.get("status", "") != "OFFLINE",
                metadata={
                    "st_device_type": device_type,
                    "network_type": network_type,
                    "components": [
                        c.get("id") for c in item.get("components", [])
                    ],
                },
            )
            self.register_device(device)

        logger.info(
            f"[{self.hub_id}] Discovered {len(self._devices)} devices "
            f"from SmartThings"
        )

    # ── Device Commands ────────────────────────────────────────────────

    async def send_command(
        self,
        device_id: str,
        command: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Send a command to a device via SmartThings API."""
        device = self.get_device(device_id)
        if not device:
            return {"success": False, "error": f"Device {device_id} not found"}

        params = params or {}

        # Map VESPER commands to SmartThings commands
        st_command = self._map_to_st_command(command, params)

        result = await self._api_post(
            f"/devices/{device_id}/commands",
            {"commands": [st_command]},
        )

        # Record traffic
        record = HubTrafficRecord(
            timestamp=time.time(),
            source_id=self.hub_id,
            target_id=device_id,
            protocol="smartthings",
            direction="outbound",
            topic=f"command/{command}",
            payload_summary=json.dumps(st_command)[:200],
        )
        self.record_traffic(record)

        return {
            "success": result is not None,
            "protocol": "smartthings",
            "command": st_command,
            "response": result,
        }

    async def get_device_state(self, device_id: str) -> dict[str, Any]:
        """Get device state from SmartThings API."""
        result = await self._api_get(f"/devices/{device_id}/status")

        record = HubTrafficRecord(
            timestamp=time.time(),
            source_id=device_id,
            target_id=self.hub_id,
            protocol="smartthings",
            direction="inbound",
            topic=f"status/{device_id}",
            payload_summary=json.dumps(result or {})[:200],
        )
        self.record_traffic(record)

        if result:
            # Update hub registry
            device = self.get_device(device_id)
            if device:
                device.state = result
                device.last_seen = time.time()
                device.online = True

        return result or {"error": "Failed to get device state"}

    async def set_device_state(
        self, device_id: str, state: dict[str, Any]
    ) -> bool:
        """Set device state via commands."""
        for key, value in state.items():
            if key in ("on", "switch"):
                cmd = "on" if value else "off"
                result = await self.send_command(device_id, cmd)
                if not result.get("success"):
                    return False
            elif key == "level":
                result = await self.send_command(
                    device_id, "setLevel", {"level": value}
                )
                if not result.get("success"):
                    return False
        return True

    def _map_to_st_command(
        self, command: str, params: dict[str, Any]
    ) -> dict:
        """Map VESPER command to SmartThings command format."""
        command_map = {
            "on": {
                "component": "main",
                "capability": "switch",
                "command": "on",
            },
            "off": {
                "component": "main",
                "capability": "switch",
                "command": "off",
            },
            "set_brightness": {
                "component": "main",
                "capability": "switchLevel",
                "command": "setLevel",
                "arguments": [params.get("level", 100)],
            },
            "set_color": {
                "component": "main",
                "capability": "colorControl",
                "command": "setColor",
                "arguments": [params],
            },
            "lock": {
                "component": "main",
                "capability": "lock",
                "command": "lock",
            },
            "unlock": {
                "component": "main",
                "capability": "lock",
                "command": "unlock",
            },
        }

        if command in command_map:
            return command_map[command]

        # Generic pass-through
        return {
            "component": "main",
            "capability": params.get("capability", command),
            "command": command,
            "arguments": params.get("arguments", []),
        }

    # ── Background Tasks ───────────────────────────────────────────────

    async def _poll_device_states(self) -> None:
        """Periodically poll device states from SmartThings."""
        while self._running:
            try:
                for device_id in list(self._devices.keys()):
                    try:
                        await self.get_device_state(device_id)
                    except Exception:
                        pass
                    await asyncio.sleep(0.1)  # Rate limiting

                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Poll error: {e}")
                await asyncio.sleep(self._poll_interval)

    async def _device_heartbeat_loop(self) -> None:
        """Check device online status periodically."""
        while self._running:
            try:
                await asyncio.sleep(60)
                # Re-discover to catch new/removed devices
                await self._discover_devices()
            except asyncio.CancelledError:
                break
            except Exception:
                pass
