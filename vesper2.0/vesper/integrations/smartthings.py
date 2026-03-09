"""
SmartThings Integration for VESPER.

Bridges virtual IoT devices with the Samsung SmartThings platform,
enabling synchronization between simulation and real devices.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    aiohttp = None

logger = logging.getLogger(__name__)


class SmartThingsCapability(Enum):
    """Common SmartThings device capabilities."""
    SWITCH = "switch"
    SWITCH_LEVEL = "switchLevel"
    MOTION_SENSOR = "motionSensor"
    CONTACT_SENSOR = "contactSensor"
    TEMPERATURE_MEASUREMENT = "temperatureMeasurement"
    HUMIDITY_MEASUREMENT = "relativeHumidityMeasurement"
    PRESENCE_SENSOR = "presenceSensor"
    LOCK = "lock"
    THERMOSTAT = "thermostat"
    COLOR_CONTROL = "colorControl"
    COLOR_TEMPERATURE = "colorTemperature"
    LIGHT = "light"
    DOOR_CONTROL = "doorControl"


@dataclass
class SmartThingsConfig:
    """Configuration for SmartThings integration."""
    
    # API credentials
    personal_access_token: Optional[str] = None
    
    # API settings
    api_base_url: str = "https://api.smartthings.com/v1"
    
    # Rate limiting
    requests_per_minute: int = 30
    
    # Sync settings
    sync_interval: float = 5.0  # seconds between state syncs
    
    # Device mapping (virtual ID -> SmartThings device ID)
    device_mapping: Dict[str, str] = field(default_factory=dict)
    
    @classmethod
    def from_env(cls) -> "SmartThingsConfig":
        """Create config from environment variables."""
        return cls(
            personal_access_token=os.getenv("SMARTTHINGS_TOKEN"),
        )


@dataclass
class SmartThingsDevice:
    """Represents a SmartThings device."""
    device_id: str
    name: str
    label: Optional[str] = None
    room_id: Optional[str] = None
    capabilities: List[str] = field(default_factory=list)
    components: Dict[str, List[str]] = field(default_factory=dict)
    
    # Current state
    state: Dict[str, Any] = field(default_factory=dict)
    
    def has_capability(self, capability: str) -> bool:
        """Check if device has a capability."""
        cap_name = capability.value if isinstance(capability, SmartThingsCapability) else capability
        return cap_name in self.capabilities
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "device_id": self.device_id,
            "name": self.name,
            "label": self.label,
            "room_id": self.room_id,
            "capabilities": self.capabilities,
            "state": self.state,
        }


class SmartThingsClient:
    """
    Client for SmartThings API integration.
    
    Provides:
    - Device discovery and listing
    - State reading and writing
    - Event subscription (via webhooks)
    - Bi-directional sync with virtual devices
    """
    
    def __init__(self, config: Optional[SmartThingsConfig] = None):
        """
        Initialize the SmartThings client.
        
        Args:
            config: SmartThings configuration
        """
        self.config = config or SmartThingsConfig.from_env()
        
        if not AIOHTTP_AVAILABLE:
            logger.warning("aiohttp not installed. Install with: pip install aiohttp")
        
        # Device cache
        self._devices: Dict[str, SmartThingsDevice] = {}
        
        # Rate limiting
        self._request_times: List[float] = []
        
        # Event callbacks
        self._state_callbacks: List[Callable[[str, Dict[str, Any]], None]] = []
        
        # Sync state
        self._sync_task: Optional[asyncio.Task] = None
        self._running = False
    
    @property
    def is_configured(self) -> bool:
        """Check if client is properly configured."""
        return self.config.personal_access_token is not None
    
    def _get_headers(self) -> Dict[str, str]:
        """Get API request headers."""
        return {
            "Authorization": f"Bearer {self.config.personal_access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    
    async def _rate_limit(self):
        """Enforce rate limiting."""
        now = time.time()
        
        # Remove old timestamps
        self._request_times = [t for t in self._request_times if now - t < 60]
        
        # Wait if at limit
        if len(self._request_times) >= self.config.requests_per_minute:
            wait_time = 60 - (now - self._request_times[0])
            if wait_time > 0:
                logger.debug(f"Rate limiting: waiting {wait_time:.1f}s")
                await asyncio.sleep(wait_time)
        
        self._request_times.append(time.time())
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Make an API request."""
        if not AIOHTTP_AVAILABLE:
            logger.error("aiohttp not available")
            return None
        
        if not self.is_configured:
            logger.error("SmartThings not configured (missing token)")
            return None
        
        await self._rate_limit()
        
        url = f"{self.config.api_base_url}{endpoint}"
        
        try:
            async with aiohttp.ClientSession() as session:
                kwargs = {"headers": self._get_headers()}
                if data:
                    kwargs["json"] = data
                
                async with session.request(method, url, **kwargs) as response:
                    if response.status == 200:
                        return await response.json()
                    elif response.status == 401:
                        logger.error("SmartThings authentication failed")
                        return None
                    elif response.status == 429:
                        logger.warning("SmartThings rate limit exceeded")
                        await asyncio.sleep(60)
                        return await self._request(method, endpoint, data)
                    else:
                        text = await response.text()
                        logger.error(f"SmartThings API error {response.status}: {text}")
                        return None
                        
        except Exception as e:
            logger.error(f"SmartThings request failed: {e}")
            return None
    
    async def list_devices(self) -> List[SmartThingsDevice]:
        """
        List all devices in the SmartThings account.
        
        Returns:
            List of SmartThingsDevice objects
        """
        response = await self._request("GET", "/devices")
        
        if not response or "items" not in response:
            return []
        
        devices = []
        for item in response["items"]:
            device = SmartThingsDevice(
                device_id=item["deviceId"],
                name=item.get("name", "Unknown"),
                label=item.get("label"),
                room_id=item.get("roomId"),
                capabilities=[
                    cap["id"] for comp in item.get("components", [])
                    for cap in comp.get("capabilities", [])
                ],
            )
            devices.append(device)
            self._devices[device.device_id] = device
        
        logger.info(f"Found {len(devices)} SmartThings devices")
        return devices
    
    async def get_device(self, device_id: str) -> Optional[SmartThingsDevice]:
        """Get a specific device by ID."""
        if device_id in self._devices:
            return self._devices[device_id]
        
        response = await self._request("GET", f"/devices/{device_id}")
        
        if not response:
            return None
        
        device = SmartThingsDevice(
            device_id=response["deviceId"],
            name=response.get("name", "Unknown"),
            label=response.get("label"),
            room_id=response.get("roomId"),
            capabilities=[
                cap["id"] for comp in response.get("components", [])
                for cap in comp.get("capabilities", [])
            ],
        )
        self._devices[device_id] = device
        return device
    
    async def get_device_status(self, device_id: str) -> Dict[str, Any]:
        """
        Get current status of a device.
        
        Returns:
            Dictionary of component -> capability -> attribute -> value
        """
        response = await self._request("GET", f"/devices/{device_id}/status")
        
        if not response or "components" not in response:
            return {}
        
        # Parse and flatten status
        status = {}
        for comp_name, comp_data in response.get("components", {}).items():
            for cap_name, cap_data in comp_data.items():
                for attr_name, attr_data in cap_data.items():
                    key = f"{cap_name}.{attr_name}"
                    status[key] = attr_data.get("value")
        
        # Update cached device
        if device_id in self._devices:
            self._devices[device_id].state = status
        
        return status
    
    async def execute_command(
        self,
        device_id: str,
        capability: str,
        command: str,
        arguments: Optional[List[Any]] = None,
        component: str = "main",
    ) -> bool:
        """
        Execute a command on a device.
        
        Args:
            device_id: SmartThings device ID
            capability: Capability name
            command: Command to execute
            arguments: Command arguments
            component: Component name (usually "main")
            
        Returns:
            True if successful
        """
        data = {
            "commands": [
                {
                    "component": component,
                    "capability": capability,
                    "command": command,
                    "arguments": arguments or [],
                }
            ]
        }
        
        response = await self._request("POST", f"/devices/{device_id}/commands", data)
        
        success = response is not None
        if success:
            logger.info(f"Executed {capability}.{command} on {device_id}")
        
        return success
    
    # Convenience methods for common commands
    
    async def turn_on(self, device_id: str) -> bool:
        """Turn on a switch device."""
        return await self.execute_command(device_id, "switch", "on")
    
    async def turn_off(self, device_id: str) -> bool:
        """Turn off a switch device."""
        return await self.execute_command(device_id, "switch", "off")
    
    async def set_level(self, device_id: str, level: int) -> bool:
        """Set dimmer level (0-100)."""
        return await self.execute_command(
            device_id, "switchLevel", "setLevel", [level]
        )
    
    async def set_color(
        self,
        device_id: str,
        hue: int,
        saturation: int,
    ) -> bool:
        """Set color (hue: 0-100, saturation: 0-100)."""
        return await self.execute_command(
            device_id, "colorControl", "setColor",
            [{"hue": hue, "saturation": saturation}]
        )
    
    async def lock(self, device_id: str) -> bool:
        """Lock a lock device."""
        return await self.execute_command(device_id, "lock", "lock")
    
    async def unlock(self, device_id: str) -> bool:
        """Unlock a lock device."""
        return await self.execute_command(device_id, "lock", "unlock")
    
    # Device mapping and sync
    
    def map_device(self, virtual_id: str, smartthings_id: str):
        """Map a virtual device ID to a SmartThings device ID."""
        self.config.device_mapping[virtual_id] = smartthings_id
        logger.info(f"Mapped virtual device {virtual_id} -> {smartthings_id}")
    
    async def sync_virtual_to_real(
        self,
        virtual_id: str,
        state: Dict[str, Any],
    ) -> bool:
        """
        Sync virtual device state to real SmartThings device.
        
        Args:
            virtual_id: Virtual device ID
            state: Virtual device state
            
        Returns:
            True if sync successful
        """
        smartthings_id = self.config.device_mapping.get(virtual_id)
        if not smartthings_id:
            logger.debug(f"No SmartThings mapping for virtual device {virtual_id}")
            return False
        
        success = True
        
        # Map common states
        if "on" in state:
            if state["on"]:
                success = await self.turn_on(smartthings_id) and success
            else:
                success = await self.turn_off(smartthings_id) and success
        
        if "level" in state:
            success = await self.set_level(smartthings_id, state["level"]) and success
        
        if "locked" in state:
            if state["locked"]:
                success = await self.lock(smartthings_id) and success
            else:
                success = await self.unlock(smartthings_id) and success
        
        return success
    
    def on_state_change(self, callback: Callable[[str, Dict[str, Any]], None]):
        """Register callback for device state changes."""
        self._state_callbacks.append(callback)
    
    async def start_sync(self):
        """Start background state synchronization."""
        if not self.is_configured:
            logger.warning("Cannot start sync: SmartThings not configured")
            return
        
        self._running = True
        self._sync_task = asyncio.create_task(self._sync_loop())
        logger.info("Started SmartThings sync")
    
    async def stop_sync(self):
        """Stop background synchronization."""
        self._running = False
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None
        logger.info("Stopped SmartThings sync")
    
    async def _sync_loop(self):
        """Background sync loop."""
        while self._running:
            try:
                # Refresh device states
                for device_id in list(self._devices.keys()):
                    old_state = self._devices[device_id].state.copy()
                    new_state = await self.get_device_status(device_id)
                    
                    # Check for changes
                    if new_state != old_state:
                        for callback in self._state_callbacks:
                            try:
                                callback(device_id, new_state)
                            except Exception as e:
                                logger.error(f"State callback error: {e}")
                
                await asyncio.sleep(self.config.sync_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Sync error: {e}")
                await asyncio.sleep(self.config.sync_interval)
    
    # List utilities
    
    async def list_rooms(self) -> List[Dict[str, str]]:
        """List all rooms/locations."""
        response = await self._request("GET", "/rooms")
        
        if not response or "items" not in response:
            return []
        
        return [
            {"room_id": room["roomId"], "name": room.get("name", "Unknown")}
            for room in response["items"]
        ]
    
    def get_cached_devices(self) -> List[SmartThingsDevice]:
        """Get cached device list."""
        return list(self._devices.values())
    
    def print_device_summary(self):
        """Print summary of cached devices."""
        print(f"\nSmartThings Devices ({len(self._devices)}):")
        print("-" * 50)
        for device in self._devices.values():
            label = device.label or device.name
            caps = ", ".join(device.capabilities[:3])
            if len(device.capabilities) > 3:
                caps += f" (+{len(device.capabilities) - 3} more)"
            print(f"  {label}: {caps}")
        print()
