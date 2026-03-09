"""
SmartThings Schema Connector for VESPER.

Implements the SmartThings Schema protocol as a webhook server,
allowing virtual devices to appear as cloud-connected devices
in the SmartThings app.

Architecture:
    SmartThings Cloud <--> Schema Connector (webhook) <--> Virtual Device Registry
                                   |
                           Docker/QEMU Devices

Reference: https://developer.smartthings.com/docs/devices/cloud-connected/interaction-types
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    web = None

try:
    import aiohttp
    AIOHTTP_CLIENT_AVAILABLE = True
except ImportError:
    AIOHTTP_CLIENT_AVAILABLE = False
    aiohttp = None

logger = logging.getLogger(__name__)


# =============================================================================
# SmartThings Schema Protocol Constants
# =============================================================================

SCHEMA_VERSION = "1.0"
SCHEMA_TYPE = "st-schema"


class InteractionType(str, Enum):
    """SmartThings Schema interaction types."""
    DISCOVERY_REQUEST = "discoveryRequest"
    DISCOVERY_RESPONSE = "discoveryResponse"
    STATE_REFRESH_REQUEST = "stateRefreshRequest"
    STATE_REFRESH_RESPONSE = "stateRefreshResponse"
    COMMAND_REQUEST = "commandRequest"
    COMMAND_RESPONSE = "commandResponse"
    GRANT_CALLBACK_ACCESS = "grantCallbackAccess"
    ACCESS_TOKEN_REQUEST = "accessTokenRequest"
    ACCESS_TOKEN_RESPONSE = "accessTokenResponse"
    REFRESH_ACCESS_TOKENS = "refreshAccessTokens"
    STATE_CALLBACK = "stateCallback"
    DISCOVERY_CALLBACK = "discoveryCallback"
    INTERACTION_RESULT = "interactionResult"
    INTEGRATION_DELETED = "integrationDeleted"


class DeviceHandlerType(str, Enum):
    """Common SmartThings device handler types (c2c = cloud-to-cloud)."""
    # Switches & Lights
    SWITCH = "c2c-switch"
    DIMMER = "c2c-dimmer"
    COLOR_BULB = "c2c-color-bulb"
    RGBW_COLOR_BULB = "c2c-rgbw-color-bulb"
    COLOR_TEMPERATURE_BULB = "c2c-color-temperature-bulb"
    
    # Sensors
    MOTION_SENSOR = "c2c-motion"
    CONTACT_SENSOR = "c2c-contact"
    PRESENCE_SENSOR = "c2c-presence"
    TEMPERATURE_SENSOR = "c2c-temperature"
    HUMIDITY_SENSOR = "c2c-humidity"
    WATER_LEAK_SENSOR = "c2c-water-leak"
    SMOKE_DETECTOR = "c2c-smoke"
    CO_DETECTOR = "c2c-co"
    
    # Locks & Security
    LOCK = "c2c-lock"
    GARAGE_DOOR = "c2c-garage-door"
    
    # Climate
    THERMOSTAT = "c2c-thermostat"
    FAN = "c2c-fan"
    
    # Other
    BUTTON = "c2c-button"
    WINDOW_SHADE = "c2c-window-shade"
    OUTLET = "c2c-switch"  # Outlets use switch handler


class Capability(str, Enum):
    """SmartThings capabilities (st. prefix used in protocol)."""
    SWITCH = "st.switch"
    SWITCH_LEVEL = "st.switchLevel"
    COLOR_CONTROL = "st.colorControl"
    COLOR_TEMPERATURE = "st.colorTemperature"
    MOTION_SENSOR = "st.motionSensor"
    CONTACT_SENSOR = "st.contactSensor"
    PRESENCE_SENSOR = "st.presenceSensor"
    TEMPERATURE_MEASUREMENT = "st.temperatureMeasurement"
    RELATIVE_HUMIDITY = "st.relativeHumidityMeasurement"
    LOCK = "st.lock"
    DOOR_CONTROL = "st.doorControl"
    THERMOSTAT = "st.thermostat"
    THERMOSTAT_MODE = "st.thermostatMode"
    THERMOSTAT_HEATING_SETPOINT = "st.thermostatHeatingSetpoint"
    THERMOSTAT_COOLING_SETPOINT = "st.thermostatCoolingSetpoint"
    BUTTON = "st.button"
    BATTERY = "st.battery"
    HEALTH_CHECK = "st.healthCheck"


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class SchemaConnectorConfig:
    """Configuration for the Schema Connector."""
    
    # Server settings
    host: str = "0.0.0.0"
    port: int = 8443
    webhook_path: str = "/schema"  # SmartThings expects /schema
    
    # OAuth settings (your cloud's OAuth)
    oauth_client_id: str = ""
    oauth_client_secret: str = ""
    oauth_authorization_url: str = ""
    oauth_token_url: str = ""
    
    # SmartThings credentials (received during registration)
    smartthings_client_id: str = ""
    smartthings_client_secret: str = ""
    
    # SSL settings (required for production)
    ssl_cert_path: Optional[str] = None
    ssl_key_path: Optional[str] = None
    
    # Callback storage
    callback_urls: Dict[str, str] = field(default_factory=dict)
    callback_tokens: Dict[str, str] = field(default_factory=dict)
    
    @classmethod
    def from_env(cls) -> "SchemaConnectorConfig":
        """Create config from environment variables."""
        return cls(
            host=os.getenv("VESPER_SCHEMA_HOST", "0.0.0.0"),
            port=int(os.getenv("VESPER_SCHEMA_PORT", "8443")),
            oauth_client_id=os.getenv("VESPER_OAUTH_CLIENT_ID", ""),
            oauth_client_secret=os.getenv("VESPER_OAUTH_CLIENT_SECRET", ""),
            smartthings_client_id=os.getenv("SMARTTHINGS_CLIENT_ID", ""),
            smartthings_client_secret=os.getenv("SMARTTHINGS_CLIENT_SECRET", ""),
        )


@dataclass
class VirtualDeviceDefinition:
    """Definition of a virtual device for SmartThings."""
    
    # Device identification
    external_device_id: str  # Your unique device ID
    friendly_name: str
    device_handler_type: DeviceHandlerType
    
    # Manufacturer info
    manufacturer_name: str = "VESPER"
    model_name: str = "Virtual Device"
    hw_version: str = "1.0"
    sw_version: str = "1.0.0"
    
    # Context
    room_name: Optional[str] = None
    groups: List[str] = field(default_factory=list)
    
    # Capabilities (derived from handler type or explicit)
    capabilities: List[Capability] = field(default_factory=list)
    
    # Current state
    state: Dict[str, Any] = field(default_factory=dict)
    
    # Health status
    is_online: bool = True
    
    # Cookie for pass-through data
    device_cookie: Dict[str, Any] = field(default_factory=dict)
    
    def get_capabilities_for_handler(self) -> List[Capability]:
        """Get standard capabilities for the device handler type."""
        handler_caps = {
            DeviceHandlerType.SWITCH: [Capability.SWITCH],
            DeviceHandlerType.DIMMER: [Capability.SWITCH, Capability.SWITCH_LEVEL],
            DeviceHandlerType.COLOR_BULB: [
                Capability.SWITCH, Capability.SWITCH_LEVEL,
                Capability.COLOR_CONTROL
            ],
            DeviceHandlerType.RGBW_COLOR_BULB: [
                Capability.SWITCH, Capability.SWITCH_LEVEL,
                Capability.COLOR_CONTROL, Capability.COLOR_TEMPERATURE
            ],
            DeviceHandlerType.COLOR_TEMPERATURE_BULB: [
                Capability.SWITCH, Capability.SWITCH_LEVEL,
                Capability.COLOR_TEMPERATURE
            ],
            DeviceHandlerType.MOTION_SENSOR: [Capability.MOTION_SENSOR],
            DeviceHandlerType.CONTACT_SENSOR: [Capability.CONTACT_SENSOR],
            DeviceHandlerType.PRESENCE_SENSOR: [Capability.PRESENCE_SENSOR],
            DeviceHandlerType.LOCK: [Capability.LOCK],
            DeviceHandlerType.THERMOSTAT: [
                Capability.THERMOSTAT, Capability.THERMOSTAT_MODE,
                Capability.THERMOSTAT_HEATING_SETPOINT,
                Capability.THERMOSTAT_COOLING_SETPOINT,
                Capability.TEMPERATURE_MEASUREMENT
            ],
        }
        
        caps = handler_caps.get(self.device_handler_type, [])
        # Always include health check
        if Capability.HEALTH_CHECK not in caps:
            caps = [Capability.HEALTH_CHECK] + caps
        return caps
    
    def get_default_state(self) -> Dict[str, Any]:
        """Get default state based on capabilities."""
        state = {
            "st.healthCheck.healthStatus": "online" if self.is_online else "offline"
        }
        
        if self.device_handler_type in [
            DeviceHandlerType.SWITCH, DeviceHandlerType.DIMMER,
            DeviceHandlerType.COLOR_BULB, DeviceHandlerType.RGBW_COLOR_BULB,
            DeviceHandlerType.COLOR_TEMPERATURE_BULB
        ]:
            state["st.switch.switch"] = "off"
        
        if self.device_handler_type in [
            DeviceHandlerType.DIMMER, DeviceHandlerType.COLOR_BULB,
            DeviceHandlerType.RGBW_COLOR_BULB, DeviceHandlerType.COLOR_TEMPERATURE_BULB
        ]:
            state["st.switchLevel.level"] = 100
        
        if self.device_handler_type == DeviceHandlerType.MOTION_SENSOR:
            state["st.motionSensor.motion"] = "inactive"
        
        if self.device_handler_type == DeviceHandlerType.CONTACT_SENSOR:
            state["st.contactSensor.contact"] = "closed"
        
        if self.device_handler_type == DeviceHandlerType.LOCK:
            state["st.lock.lock"] = "locked"
        
        return state
    
    def to_discovery_response(self) -> Dict[str, Any]:
        """Convert to SmartThings discovery response format."""
        return {
            "externalDeviceId": self.external_device_id,
            "friendlyName": self.friendly_name,
            "deviceHandlerType": self.device_handler_type.value,
            "manufacturerInfo": {
                "manufacturerName": self.manufacturer_name,
                "modelName": self.model_name,
                "hwVersion": self.hw_version,
                "swVersion": self.sw_version,
            },
            "deviceContext": {
                "roomName": self.room_name,
                "groups": self.groups,
            },
            "deviceCookie": self.device_cookie,
        }


# =============================================================================
# Schema Connector Server
# =============================================================================

class SmartThingsSchemaConnector:
    """
    SmartThings Schema Connector webhook server.
    
    Handles all SmartThings Schema interaction types:
    - discoveryRequest: Return list of virtual devices
    - stateRefreshRequest: Return current device states
    - commandRequest: Execute commands on virtual devices
    - grantCallbackAccess: Store callback tokens for proactive updates
    - integrationDeleted: Clean up when user removes integration
    
    Usage:
        connector = SmartThingsSchemaConnector(config)
        
        # Register a virtual device
        device = VirtualDeviceDefinition(
            external_device_id="vesper-switch-001",
            friendly_name="Virtual Kitchen Light",
            device_handler_type=DeviceHandlerType.SWITCH,
            room_name="Kitchen",
        )
        connector.register_device(device)
        
        # Start the server
        await connector.start()
        
        # Update device state (triggers callback to SmartThings)
        await connector.update_device_state("vesper-switch-001", {
            "st.switch.switch": "on"
        })
    """
    
    def __init__(self, config: Optional[SchemaConnectorConfig] = None):
        """Initialize the Schema Connector."""
        if not AIOHTTP_AVAILABLE:
            raise RuntimeError("aiohttp required. Install with: pip install aiohttp")
        
        self.config = config or SchemaConnectorConfig.from_env()
        
        # Device registry
        self._devices: Dict[str, VirtualDeviceDefinition] = {}
        
        # User token storage (user_token -> user_id mapping)
        self._user_tokens: Dict[str, str] = {}
        
        # Callback URLs and tokens per user
        self._callback_urls: Dict[str, Dict[str, str]] = {}  # user_id -> {oauthToken, stateCallback}
        self._callback_tokens: Dict[str, str] = {}  # user_id -> access_token
        
        # Credential persistence path
        self._creds_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "logs", "smartthings_callback_creds.json"
        )
        self._load_callback_credentials()
        
        # State change callbacks (for integration with simulation)
        self._command_handlers: List[Callable[[str, str, str, List[Any]], bool]] = []
        
        # Web server
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        
        logger.info("SmartThings Schema Connector initialized")
    
    # =========================================================================
    # Device Registration
    # =========================================================================
    
    def register_device(self, device: VirtualDeviceDefinition) -> None:
        """Register a virtual device."""
        # Initialize default state if not set
        if not device.state:
            device.state = device.get_default_state()
        
        self._devices[device.external_device_id] = device
        logger.info(f"Registered virtual device: {device.friendly_name} ({device.external_device_id})")
    
    def unregister_device(self, device_id: str) -> None:
        """Unregister a virtual device."""
        if device_id in self._devices:
            device = self._devices.pop(device_id)
            logger.info(f"Unregistered virtual device: {device.friendly_name}")
    
    def get_device(self, device_id: str) -> Optional[VirtualDeviceDefinition]:
        """Get a registered device by ID."""
        return self._devices.get(device_id)
    
    def list_devices(self) -> List[VirtualDeviceDefinition]:
        """List all registered devices."""
        return list(self._devices.values())
    
    # =========================================================================
    # Callback Credential Persistence
    # =========================================================================

    def _load_callback_credentials(self) -> None:
        """Load saved callback credentials from disk (survive restarts)."""
        try:
            if os.path.exists(self._creds_path):
                with open(self._creds_path, "r") as f:
                    data = json.load(f)
                self._callback_urls = data.get("callback_urls", {})
                self._callback_tokens = data.get("callback_tokens", {})
                if self._callback_urls:
                    logger.info(
                        f"✅ Loaded callback credentials for {len(self._callback_urls)} user(s) "
                        f"— proactive state updates ENABLED"
                    )
                else:
                    logger.info("No saved callback credentials found")
        except Exception as e:
            logger.debug(f"Could not load callback creds: {e}")

    def _save_callback_credentials(self) -> None:
        """Persist callback credentials to disk so they survive restarts."""
        try:
            os.makedirs(os.path.dirname(self._creds_path), exist_ok=True)
            with open(self._creds_path, "w") as f:
                json.dump({
                    "callback_urls": self._callback_urls,
                    "callback_tokens": self._callback_tokens,
                    "saved_at": datetime.utcnow().isoformat(),
                }, f, indent=2)
            logger.info(f"💾 Saved callback credentials to {self._creds_path}")
        except Exception as e:
            logger.error(f"Failed to save callback creds: {e}")
    
    # =========================================================================
    # Command Handlers
    # =========================================================================
    
    def on_command(self, handler: Callable[[str, str, str, List[Any]], bool]) -> None:
        """
        Register a command handler.
        
        Handler signature: (device_id, capability, command, arguments) -> success
        """
        self._command_handlers.append(handler)
    
    async def _execute_command(
        self,
        device_id: str,
        capability: str,
        command: str,
        arguments: List[Any],
    ) -> bool:
        """Execute a command on a device."""
        device = self._devices.get(device_id)
        if not device:
            logger.error(f"Device not found: {device_id}")
            return False
        
        # Call registered handlers
        success = True
        for handler in self._command_handlers:
            try:
                result = handler(device_id, capability, command, arguments)
                if asyncio.iscoroutine(result):
                    result = await result
                success = success and result
            except Exception as e:
                logger.error(f"Command handler error: {e}")
                success = False
        
        # Update device state based on command
        if success:
            self._apply_command_to_state(device, capability, command, arguments)
        
        return success
    
    def _apply_command_to_state(
        self,
        device: VirtualDeviceDefinition,
        capability: str,
        command: str,
        arguments: List[Any],
    ) -> None:
        """Apply a command to device state."""
        # Common command -> state mappings
        state_updates = {
            ("st.switch", "on"): {"st.switch.switch": "on"},
            ("st.switch", "off"): {"st.switch.switch": "off"},
            ("st.lock", "lock"): {"st.lock.lock": "locked"},
            ("st.lock", "unlock"): {"st.lock.lock": "unlocked"},
        }
        
        key = (capability, command)
        if key in state_updates:
            device.state.update(state_updates[key])
        elif command == "setLevel" and arguments:
            device.state["st.switchLevel.level"] = arguments[0]
        elif command == "setColor" and arguments:
            color = arguments[0]
            device.state["st.colorControl.hue"] = color.get("hue", 0)
            device.state["st.colorControl.saturation"] = color.get("saturation", 100)
        elif command == "setColorTemperature" and arguments:
            device.state["st.colorTemperature.colorTemperature"] = arguments[0]
    
    # =========================================================================
    # State Updates & Callbacks
    # =========================================================================
    
    async def update_device_state(
        self,
        device_id: str,
        state_updates: Dict[str, Any],
        trigger_callback: bool = True,
    ) -> bool:
        """
        Update device state and optionally push to SmartThings.
        
        Args:
            device_id: Device external ID
            state_updates: Dict of capability.attribute -> value
            trigger_callback: Whether to send stateCallback to SmartThings
            
        Returns:
            True if successful
        """
        device = self._devices.get(device_id)
        if not device:
            logger.error(f"Device not found: {device_id}")
            return False
        
        # Update local state
        device.state.update(state_updates)
        logger.debug(f"Updated state for {device_id}: {state_updates}")
        
        # Send callback to SmartThings
        if trigger_callback:
            await self._send_state_callback([device_id])
        
        return True
    
    async def _send_state_callback(self, device_ids: List[str]) -> bool:
        """Send state callback to SmartThings for the specified devices."""
        if not AIOHTTP_CLIENT_AVAILABLE:
            logger.warning("Cannot send callback: aiohttp not available")
            return False

        if not self._callback_urls:
            logger.debug(
                f"State callback skipped for {device_ids}: no callback credentials yet. "
                f"SmartThings will see the update on next stateRefreshRequest poll."
            )
            return False

        # Send to all registered users
        for user_id, callback_urls in self._callback_urls.items():
            state_callback_url = callback_urls.get("stateCallback")
            access_token = self._callback_tokens.get(user_id)
            
            if not state_callback_url or not access_token:
                continue
            
            # Build state callback payload
            device_states = []
            for device_id in device_ids:
                device = self._devices.get(device_id)
                if not device:
                    continue
                
                states = []
                for key, value in device.state.items():
                    parts = key.split(".")
                    if len(parts) >= 2:
                        capability = ".".join(parts[:-1])
                        attribute = parts[-1]
                        states.append({
                            "component": "main",
                            "capability": capability,
                            "attribute": attribute,
                            "value": value,
                            "timestamp": int(time.time() * 1000),
                        })
                
                device_states.append({
                    "externalDeviceId": device_id,
                    "states": states,
                })
            
            payload = {
                "headers": {
                    "schema": SCHEMA_TYPE,
                    "version": SCHEMA_VERSION,
                    "interactionType": InteractionType.STATE_CALLBACK.value,
                    "requestId": str(uuid.uuid4()),
                },
                "authentication": {
                    "tokenType": "Bearer",
                    "token": access_token,
                },
                "deviceState": device_states,
            }
            
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        state_callback_url,
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    ) as response:
                        if response.status == 200:
                            logger.info(f"State callback sent for {device_ids}")
                            return True
                        else:
                            text = await response.text()
                            logger.error(f"State callback failed: {response.status} - {text}")
            except Exception as e:
                logger.error(f"State callback error: {e}")
        
        return False
    
    async def trigger_discovery_callback(self) -> bool:
        """Trigger discovery callback to add newly registered devices."""
        if not AIOHTTP_CLIENT_AVAILABLE:
            return False
        
        for user_id, callback_urls in self._callback_urls.items():
            # Discovery uses stateCallback URL but with discoveryCallback type
            callback_url = callback_urls.get("stateCallback")
            access_token = self._callback_tokens.get(user_id)
            
            if not callback_url or not access_token:
                continue
            
            devices = [
                device.to_discovery_response()
                for device in self._devices.values()
            ]
            
            payload = {
                "headers": {
                    "schema": SCHEMA_TYPE,
                    "version": SCHEMA_VERSION,
                    "interactionType": InteractionType.DISCOVERY_CALLBACK.value,
                    "requestId": str(uuid.uuid4()),
                },
                "authentication": {
                    "tokenType": "Bearer",
                    "token": access_token,
                },
                "devices": devices,
            }
            
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        callback_url,
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    ) as response:
                        if response.status == 200:
                            logger.info(f"Discovery callback sent with {len(devices)} devices")
                            return True
                        else:
                            text = await response.text()
                            logger.error(f"Discovery callback failed: {response.status} - {text}")
            except Exception as e:
                logger.error(f"Discovery callback error: {e}")
        
        return False
    
    # =========================================================================
    # Schema Protocol Handlers
    # =========================================================================
    
    async def _handle_webhook(self, request: web.Request) -> web.Response:
        """Handle incoming SmartThings Schema webhook request."""
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400
            )
        
        headers = payload.get("headers", {})
        interaction_type = headers.get("interactionType")
        request_id = headers.get("requestId", str(uuid.uuid4()))
        
        logger.info(f"Received {interaction_type} (requestId: {request_id})")
        logger.debug(f"Payload: {json.dumps(payload, indent=2)}")
        
        # Route to appropriate handler
        handlers = {
            InteractionType.DISCOVERY_REQUEST.value: self._handle_discovery,
            InteractionType.STATE_REFRESH_REQUEST.value: self._handle_state_refresh,
            InteractionType.COMMAND_REQUEST.value: self._handle_command,
            InteractionType.GRANT_CALLBACK_ACCESS.value: self._handle_grant_callback,
            InteractionType.INTEGRATION_DELETED.value: self._handle_integration_deleted,
            InteractionType.INTERACTION_RESULT.value: self._handle_interaction_result,
        }
        
        handler = handlers.get(interaction_type)
        if handler:
            response = await handler(payload, request_id)
            logger.debug(f"Response: {json.dumps(response, indent=2)}")
            return web.json_response(response)
        else:
            logger.warning(f"Unknown interaction type: {interaction_type}")
            return web.json_response(
                {"error": f"Unknown interaction type: {interaction_type}"},
                status=400
            )
    
    async def _handle_discovery(
        self,
        payload: Dict[str, Any],
        request_id: str,
    ) -> Dict[str, Any]:
        """Handle discoveryRequest - return list of devices."""
        devices = [
            device.to_discovery_response()
            for device in self._devices.values()
        ]
        
        return {
            "headers": {
                "schema": SCHEMA_TYPE,
                "version": SCHEMA_VERSION,
                "interactionType": InteractionType.DISCOVERY_RESPONSE.value,
                "requestId": request_id,
            },
            "devices": devices,
        }
    
    async def _handle_state_refresh(
        self,
        payload: Dict[str, Any],
        request_id: str,
    ) -> Dict[str, Any]:
        """Handle stateRefreshRequest - return current device states."""
        requested_devices = payload.get("devices", [])
        
        device_states = []
        for req in requested_devices:
            device_id = req.get("externalDeviceId")
            device = self._devices.get(device_id)
            
            if not device:
                # Device deleted
                device_states.append({
                    "externalDeviceId": device_id,
                    "deviceError": [{
                        "errorEnum": "DEVICE-DELETED",
                        "detail": "Device no longer exists",
                    }],
                })
                continue
            
            if not device.is_online:
                # Device offline
                device_states.append({
                    "externalDeviceId": device_id,
                    "states": [{
                        "component": "main",
                        "capability": "st.healthCheck",
                        "attribute": "healthStatus",
                        "value": "offline",
                    }],
                })
                continue
            
            # Build states array
            states = []
            for key, value in device.state.items():
                parts = key.split(".")
                if len(parts) >= 2:
                    capability = ".".join(parts[:-1])
                    attribute = parts[-1]
                    states.append({
                        "component": "main",
                        "capability": capability,
                        "attribute": attribute,
                        "value": value,
                    })
            
            # Always include health check
            if not any(s["capability"] == "st.healthCheck" for s in states):
                states.append({
                    "component": "main",
                    "capability": "st.healthCheck",
                    "attribute": "healthStatus",
                    "value": "online",
                })
            
            device_states.append({
                "externalDeviceId": device_id,
                "states": states,
            })
        
        return {
            "headers": {
                "schema": SCHEMA_TYPE,
                "version": SCHEMA_VERSION,
                "interactionType": InteractionType.STATE_REFRESH_RESPONSE.value,
                "requestId": request_id,
            },
            "deviceState": device_states,
        }
    
    async def _handle_command(
        self,
        payload: Dict[str, Any],
        request_id: str,
    ) -> Dict[str, Any]:
        """Handle commandRequest - execute commands on devices."""
        requested_devices = payload.get("devices", [])
        
        device_states = []
        for req in requested_devices:
            device_id = req.get("externalDeviceId")
            device = self._devices.get(device_id)
            
            if not device:
                device_states.append({
                    "externalDeviceId": device_id,
                    "deviceError": [{
                        "errorEnum": "DEVICE-DELETED",
                        "detail": "Device no longer exists",
                    }],
                })
                continue
            
            if not device.is_online:
                device_states.append({
                    "externalDeviceId": device_id,
                    "deviceError": [{
                        "errorEnum": "DEVICE-OFFLINE",
                        "detail": "Device is offline",
                    }],
                })
                continue
            
            # Execute each command
            commands = req.get("commands", [])
            for cmd in commands:
                capability = cmd.get("capability")
                command = cmd.get("command")
                arguments = cmd.get("arguments", [])
                
                logger.info(f"Executing {capability}.{command}({arguments}) on {device_id}")
                await self._execute_command(device_id, capability, command, arguments)
            
            # Return updated states
            states = []
            for key, value in device.state.items():
                parts = key.split(".")
                if len(parts) >= 2:
                    capability = ".".join(parts[:-1])
                    attribute = parts[-1]
                    states.append({
                        "component": "main",
                        "capability": capability,
                        "attribute": attribute,
                        "value": value,
                    })
            
            # Always include health check
            states.append({
                "component": "main",
                "capability": "st.healthCheck",
                "attribute": "healthStatus",
                "value": "online",
            })
            
            device_states.append({
                "externalDeviceId": device_id,
                "states": states,
            })
        
        return {
            "headers": {
                "schema": SCHEMA_TYPE,
                "version": SCHEMA_VERSION,
                "interactionType": InteractionType.COMMAND_RESPONSE.value,
                "requestId": request_id,
            },
            "deviceState": device_states,
        }
    
    async def _handle_grant_callback(
        self,
        payload: Dict[str, Any],
        request_id: str,
    ) -> Dict[str, Any]:
        """Handle grantCallbackAccess - store callback tokens for proactive updates."""
        auth = payload.get("authentication", {})
        user_token = auth.get("token", "")
        
        callback_auth = payload.get("callbackAuthentication", {})
        callback_code = callback_auth.get("code", "")
        # SmartThings sends ITS OWN clientId here — we MUST use it
        # (not our local OAuth clientId) when exchanging for an access token.
        st_client_id = callback_auth.get("clientId", "")
        
        callback_urls = payload.get("callbackUrls", {})
        oauth_token_url = callback_urls.get("oauthToken", "")
        state_callback_url = callback_urls.get("stateCallback", "")
        
        # Generate a user ID from the token (in production, use proper user management)
        user_id = hashlib.sha256(user_token.encode()).hexdigest()[:16]
        
        # Store callback URLs
        self._callback_urls[user_id] = {
            "oauthToken": oauth_token_url,
            "stateCallback": state_callback_url,
        }
        
        # Store callback URLs regardless — we need the stateCallback URL
        logger.info(
            f"Grant callback: user={user_id}, "
            f"st_clientId={st_client_id or 'MISSING'}, "
            f"oauthToken={'yes' if oauth_token_url else 'NO'}, "
            f"stateCallback={'yes' if state_callback_url else 'NO'}, "
            f"code={'yes' if callback_code else 'NO'}"
        )

        # Request access token using the callback code
        # IMPORTANT: Use the SmartThings-provided clientId, NOT our local one.
        # The clientSecret must match what's in SmartThings Developer Portal.
        if oauth_token_url and callback_code:
            access_token = await self._request_callback_access_token(
                oauth_token_url,
                callback_code,
                st_client_id=st_client_id,
            )
            if access_token:
                self._callback_tokens[user_id] = access_token
                self._save_callback_credentials()
                logger.info(
                    f"✅ Stored callback credentials for user {user_id} — "
                    f"proactive state updates ENABLED"
                )
            else:
                logger.warning(
                    f"⚠️ Failed to get callback access token for user {user_id} — "
                    f"proactive state updates DISABLED, will rely on stateRefreshRequest polling"
                )
        else:
            logger.warning(
                f"⚠️ grantCallbackAccess missing oauthToken or code for user {user_id}"
            )
        
        return {
            "headers": {
                "schema": SCHEMA_TYPE,
                "version": SCHEMA_VERSION,
                "interactionType": "grantCallbackAccessResponse",
                "requestId": request_id,
            },
        }
    
    async def _request_callback_access_token(
        self,
        oauth_token_url: str,
        code: str,
        st_client_id: str = "",
    ) -> Optional[str]:
        """Request callback access token from SmartThings.
        
        IMPORTANT: The clientId and clientSecret sent here must match the
        App credentials in the SmartThings Developer Portal, NOT our local
        OAuth credentials. SmartThings provides its clientId in the
        grantCallbackAccess payload.
        """
        if not AIOHTTP_CLIENT_AVAILABLE:
            return None
        
        # Use ST-provided clientId if available, fall back to our config
        client_id = st_client_id or self.config.smartthings_client_id
        client_secret = self.config.smartthings_client_secret
        
        logger.info(
            f"Exchanging callback code for access token: "
            f"url={oauth_token_url}, clientId={client_id}"
        )
        
        payload = {
            "headers": {
                "schema": SCHEMA_TYPE,
                "version": SCHEMA_VERSION,
                "interactionType": InteractionType.ACCESS_TOKEN_REQUEST.value,
                "requestId": str(uuid.uuid4()),
            },
            "callbackAuthentication": {
                "grantType": "authorization_code",
                "code": code,
                "clientId": client_id,
                "clientSecret": client_secret,
            },
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    oauth_token_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        callback_auth = data.get("callbackAuthentication", {})
                        return callback_auth.get("accessToken")
                    else:
                        text = await response.text()
                        logger.error(f"Failed to get callback token: {response.status} - {text}")
        except Exception as e:
            logger.error(f"Callback token request error: {e}")
        
        return None
    
    async def _handle_integration_deleted(
        self,
        payload: Dict[str, Any],
        request_id: str,
    ) -> Dict[str, Any]:
        """Handle integrationDeleted - clean up when user removes integration."""
        auth = payload.get("authentication", {})
        user_token = auth.get("token", "")
        user_id = hashlib.sha256(user_token.encode()).hexdigest()[:16]
        
        # Clean up user data
        self._callback_urls.pop(user_id, None)
        self._callback_tokens.pop(user_id, None)
        
        logger.info(f"Integration deleted for user {user_id}")
        
        return {
            "headers": {
                "schema": SCHEMA_TYPE,
                "version": SCHEMA_VERSION,
                "interactionType": "integrationDeletedResponse",
                "requestId": request_id,
            },
        }
    
    async def _handle_interaction_result(
        self,
        payload: Dict[str, Any],
        request_id: str,
    ) -> Dict[str, Any]:
        """Handle interactionResult - acknowledgement from SmartThings."""
        result = payload.get("deviceState", [])
        global_error = payload.get("globalError")
        
        if global_error:
            logger.warning(f"Interaction result error: {global_error}")
        else:
            logger.info(f"Interaction result received successfully")
        
        # Just acknowledge - this is informational
        return {
            "headers": {
                "schema": SCHEMA_TYPE,
                "version": SCHEMA_VERSION,
                "interactionType": "interactionResultResponse",
                "requestId": request_id,
            },
        }
    
    # =========================================================================
    # Server Lifecycle
    # =========================================================================
    
    async def start(self) -> None:
        """Start the webhook server."""
        self._app = web.Application()
        self._app.router.add_post(self.config.webhook_path, self._handle_webhook)
        
        # Add health check endpoint
        self._app.router.add_get("/health", self._handle_health)
        
        # Add OAuth endpoints (required for SmartThings account linking)
        self._app.router.add_get("/oauth/authorize", self._handle_oauth_authorize)
        self._app.router.add_post("/oauth/authorize", self._handle_oauth_authorize_post)
        self._app.router.add_post("/oauth/token", self._handle_oauth_token)
        self._app.router.add_get("/oauth/callback", self._handle_oauth_callback)
        
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        
        # SSL context if configured
        ssl_context = None
        if self.config.ssl_cert_path and self.config.ssl_key_path:
            import ssl
            ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_context.load_cert_chain(
                self.config.ssl_cert_path,
                self.config.ssl_key_path,
            )
        
        self._site = web.TCPSite(
            self._runner,
            self.config.host,
            self.config.port,
            ssl_context=ssl_context,
        )
        await self._site.start()
        
        protocol = "https" if ssl_context else "http"
        logger.info(
            f"SmartThings Schema Connector running at "
            f"{protocol}://{self.config.host}:{self.config.port}{self.config.webhook_path}"
        )
    
    async def stop(self) -> None:
        """Stop the webhook server."""
        try:
            if self._runner:
                await self._runner.cleanup()
        except Exception:
            pass  # Ignore cleanup errors on shutdown
        self._site = None
        self._runner = None
        logger.info("SmartThings Schema Connector stopped")
    
    async def _handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({
            "status": "healthy",
            "devices": len(self._devices),
            "timestamp": datetime.utcnow().isoformat(),
        })
    
    # =========================================================================
    # OAuth Endpoints (for SmartThings account linking)
    # =========================================================================
    
    async def _handle_oauth_authorize(self, request: web.Request) -> web.Response:
        """
        Handle OAuth authorize request (GET).
        SmartThings redirects users here during account linking.
        """
        # Get OAuth parameters from SmartThings
        client_id = request.query.get("client_id", "")
        redirect_uri = request.query.get("redirect_uri", "")
        state = request.query.get("state", "")
        response_type = request.query.get("response_type", "code")
        
        logger.info(f"OAuth authorize request: client_id={client_id}, redirect_uri={redirect_uri}")
        
        # For a simple demo, auto-authorize and redirect back
        # In production, you'd show a login page here
        
        # Generate authorization code
        auth_code = secrets.token_urlsafe(32)
        
        # Store the code with redirect info (expires in 10 minutes)
        self._auth_codes = getattr(self, '_auth_codes', {})
        self._auth_codes[auth_code] = {
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "expires": time.time() + 600,
            "user_id": "vesper_user_001",  # Demo user
        }
        
        # Show a simple authorization page
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>VESPER Smart Home - Authorize</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ 
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
                    color: white;
                    min-height: 100vh;
                    margin: 0;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                }}
                .container {{
                    background: rgba(255,255,255,0.1);
                    padding: 40px;
                    border-radius: 20px;
                    text-align: center;
                    max-width: 400px;
                    backdrop-filter: blur(10px);
                }}
                h1 {{ margin-bottom: 10px; }}
                p {{ color: #aaa; margin-bottom: 30px; }}
                .btn {{
                    background: #00a8ff;
                    color: white;
                    border: none;
                    padding: 15px 40px;
                    font-size: 16px;
                    border-radius: 10px;
                    cursor: pointer;
                    text-decoration: none;
                    display: inline-block;
                }}
                .btn:hover {{ background: #0097e6; }}
                .devices {{ 
                    text-align: left; 
                    margin: 20px 0;
                    background: rgba(0,0,0,0.2);
                    padding: 15px;
                    border-radius: 10px;
                }}
                .device {{ padding: 5px 0; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🏠 VESPER</h1>
                <p>Smart Home Integration</p>
                <p>SmartThings wants to access your VESPER virtual devices:</p>
                <div class="devices">
                    {"".join(f'<div class="device">• {d.friendly_name}</div>' for d in self._devices.values())}
                </div>
                <form method="POST">
                    <input type="hidden" name="code" value="{auth_code}">
                    <input type="hidden" name="state" value="{state}">
                    <input type="hidden" name="redirect_uri" value="{redirect_uri}">
                    <button type="submit" class="btn">Authorize</button>
                </form>
            </div>
        </body>
        </html>
        """
        return web.Response(text=html, content_type="text/html")
    
    async def _handle_oauth_authorize_post(self, request: web.Request) -> web.Response:
        """Handle OAuth authorize POST (user clicked authorize)."""
        data = await request.post()
        code = data.get("code", "")
        state = data.get("state", "")
        redirect_uri = data.get("redirect_uri", "")
        
        logger.info(f"OAuth authorize granted, redirecting with code")
        
        # Redirect back to SmartThings with the authorization code
        redirect_url = f"{redirect_uri}?code={code}&state={state}"
        raise web.HTTPFound(redirect_url)
    
    async def _handle_oauth_token(self, request: web.Request) -> web.Response:
        """
        Handle OAuth token request (POST).
        SmartThings exchanges auth code for access token.
        """
        try:
            if request.content_type == "application/json":
                data = await request.json()
            else:
                data = await request.post()
        except Exception:
            data = {}
        
        grant_type = data.get("grant_type", "")
        code = data.get("code", "")
        refresh_token_req = data.get("refresh_token", "")
        client_id = data.get("client_id", "")
        client_secret = data.get("client_secret", "")
        
        logger.info(f"OAuth token request: grant_type={grant_type}")
        
        self._auth_codes = getattr(self, '_auth_codes', {})
        self._refresh_tokens = getattr(self, '_refresh_tokens', {})
        
        if grant_type == "authorization_code":
            # Exchange code for tokens
            code_data = self._auth_codes.get(code)
            
            if not code_data or code_data.get("expires", 0) < time.time():
                return web.json_response(
                    {"error": "invalid_grant", "error_description": "Invalid or expired code"},
                    status=400
                )
            
            # Generate tokens
            access_token = secrets.token_urlsafe(32)
            refresh_token = secrets.token_urlsafe(32)
            
            # Store refresh token
            self._refresh_tokens[refresh_token] = {
                "user_id": code_data.get("user_id"),
                "client_id": client_id,
            }
            
            # Clean up used code
            del self._auth_codes[code]
            
            logger.info(f"Issued tokens for user {code_data.get('user_id')}")
            
            return web.json_response({
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": 86400,  # 24 hours
                "refresh_token": refresh_token,
            })
            
        elif grant_type == "refresh_token":
            # Refresh access token
            token_data = self._refresh_tokens.get(refresh_token_req)
            
            if not token_data:
                return web.json_response(
                    {"error": "invalid_grant", "error_description": "Invalid refresh token"},
                    status=400
                )
            
            # Generate new tokens
            access_token = secrets.token_urlsafe(32)
            new_refresh_token = secrets.token_urlsafe(32)
            
            # Update stored refresh token
            del self._refresh_tokens[refresh_token_req]
            self._refresh_tokens[new_refresh_token] = token_data
            
            return web.json_response({
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": 86400,
                "refresh_token": new_refresh_token,
            })
        
        return web.json_response(
            {"error": "unsupported_grant_type"},
            status=400
        )
    
    async def _handle_oauth_callback(self, request: web.Request) -> web.Response:
        """Handle OAuth callback (for debugging)."""
        return web.json_response({
            "status": "ok",
            "message": "OAuth callback received",
            "params": dict(request.query),
        })


# =============================================================================
# Convenience Functions
# =============================================================================

def create_switch_device(
    device_id: str,
    name: str,
    room: Optional[str] = None,
) -> VirtualDeviceDefinition:
    """Create a virtual switch device."""
    return VirtualDeviceDefinition(
        external_device_id=device_id,
        friendly_name=name,
        device_handler_type=DeviceHandlerType.SWITCH,
        room_name=room,
    )


def create_dimmer_device(
    device_id: str,
    name: str,
    room: Optional[str] = None,
) -> VirtualDeviceDefinition:
    """Create a virtual dimmer device."""
    return VirtualDeviceDefinition(
        external_device_id=device_id,
        friendly_name=name,
        device_handler_type=DeviceHandlerType.DIMMER,
        room_name=room,
    )


def create_motion_sensor_device(
    device_id: str,
    name: str,
    room: Optional[str] = None,
) -> VirtualDeviceDefinition:
    """Create a virtual motion sensor device."""
    return VirtualDeviceDefinition(
        external_device_id=device_id,
        friendly_name=name,
        device_handler_type=DeviceHandlerType.MOTION_SENSOR,
        room_name=room,
    )


def create_contact_sensor_device(
    device_id: str,
    name: str,
    room: Optional[str] = None,
) -> VirtualDeviceDefinition:
    """Create a virtual contact sensor device."""
    return VirtualDeviceDefinition(
        external_device_id=device_id,
        friendly_name=name,
        device_handler_type=DeviceHandlerType.CONTACT_SENSOR,
        room_name=room,
    )


def create_lock_device(
    device_id: str,
    name: str,
    room: Optional[str] = None,
) -> VirtualDeviceDefinition:
    """Create a virtual lock device."""
    return VirtualDeviceDefinition(
        external_device_id=device_id,
        friendly_name=name,
        device_handler_type=DeviceHandlerType.LOCK,
        room_name=room,
    )
