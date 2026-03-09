"""
VESPER Firmware Bridge.

Connects QEMU-emulated IoT firmware to VESPER's event bus,
enabling bidirectional communication between virtual devices
and emulated firmware.

Protocol Modes:
1. JSON Protocol - Structured messages
2. Text Protocol - Simple key:value format
3. Binary Protocol - Raw binary with headers
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import struct
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from vesper.firmware.qemu_runner import QEMURunner, QEMUConfig, QEMUState
from vesper.core.event_bus import EventBus
from vesper.protocol.messages import Message, EventMessage, CommandMessage

logger = logging.getLogger(__name__)


class ProtocolMode(str, Enum):
    """Firmware communication protocol."""
    TEXT = "text"       # Simple KEY:VALUE format
    JSON = "json"       # JSON objects
    BINARY = "binary"   # Binary with length header


@dataclass
class VesperBridgeConfig:
    """Configuration for VESPER firmware bridge."""
    # Device identification
    device_id: str = "fw_device"
    device_type: str = "generic_sensor"
    room: str = "unknown"
    
    # Protocol
    protocol_mode: ProtocolMode = ProtocolMode.TEXT
    line_ending: str = "\n"
    
    # Message parsing
    parse_sensor_data: bool = True
    parse_events: bool = True
    parse_commands: bool = True
    
    # Event topics
    event_prefix: str = "firmware"
    sensor_topic: str = "sensor"
    command_topic: str = "command"
    status_topic: str = "status"
    
    # Timing
    heartbeat_interval: float = 10.0
    command_timeout: float = 5.0
    
    # Auto-features
    auto_subscribe_commands: bool = True
    broadcast_sensor_updates: bool = True


class FirmwareMessage:
    """Parsed message from firmware."""
    
    def __init__(
        self,
        msg_type: str,
        key: str,
        value: Any,
        raw: bytes,
        timestamp: float = None,
    ):
        self.msg_type = msg_type  # sensor, event, response, error
        self.key = key
        self.value = value
        self.raw = raw
        self.timestamp = timestamp or time.time()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.msg_type,
            "key": self.key,
            "value": self.value,
            "timestamp": self.timestamp,
        }
    
    def __repr__(self) -> str:
        return f"FirmwareMessage({self.msg_type}: {self.key}={self.value})"


class VesperFirmwareBridge:
    """
    Bridge between QEMU firmware and VESPER simulation.
    
    Features:
    - Bidirectional message translation
    - Automatic sensor data parsing
    - Event bus integration
    - Command forwarding
    - Health monitoring
    
    Example:
        # Create QEMU runner
        qemu = QEMURunner(QEMUConfig(
            firmware_path="firmware/sensor.elf",
            board=BoardType.STM32F4_DISCOVERY,
        ))
        
        # Create bridge
        bridge = VesperFirmwareBridge(
            qemu_runner=qemu,
            event_bus=event_bus,
            config=VesperBridgeConfig(
                device_id="temp_sensor_1",
                device_type="temperature_sensor",
                room="living_room",
            ),
        )
        
        # Start bridge
        await bridge.start()
        
        # Firmware sensor data appears as events
        # Commands from VESPER go to firmware
    """
    
    # Known sensor types and their units
    SENSOR_TYPES = {
        "TEMP": ("temperature", "°C"),
        "HUMIDITY": ("humidity", "%"),
        "PRESSURE": ("pressure", "hPa"),
        "LIGHT": ("light", "lux"),
        "MOTION": ("motion", "bool"),
        "DOOR": ("door", "bool"),
        "CO2": ("co2", "ppm"),
        "VOC": ("voc", "ppb"),
        "PM25": ("pm25", "μg/m³"),
        "BATTERY": ("battery", "%"),
        "RSSI": ("rssi", "dBm"),
    }
    
    # Command mappings (VESPER -> Firmware)
    COMMAND_MAP = {
        "get_temperature": "GET_TEMP",
        "get_humidity": "GET_HUMIDITY",
        "get_sensors": "GET_ALL",
        "set_led": "SET_LED",
        "set_relay": "SET_RELAY",
        "reboot": "REBOOT",
        "status": "STATUS",
        "identify": "IDENTIFY",
    }
    
    def __init__(
        self,
        qemu_runner: QEMURunner,
        event_bus: Optional[EventBus] = None,
        config: Optional[VesperBridgeConfig] = None,
    ):
        self.qemu = qemu_runner
        self._event_bus = event_bus
        self.config = config or VesperBridgeConfig()
        
        self._running = False
        self._rx_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        
        # State
        self._sensor_cache: Dict[str, Any] = {}
        self._pending_commands: Dict[str, asyncio.Future] = {}
        self._message_handlers: Dict[str, List[Callable]] = {}
        
        # Stats
        self._stats = {
            "messages_rx": 0,
            "messages_tx": 0,
            "events_published": 0,
            "commands_processed": 0,
            "parse_errors": 0,
            "last_message_time": 0.0,
        }
        
        # Register default handlers
        self._register_default_handlers()
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def sensor_data(self) -> Dict[str, Any]:
        """Get cached sensor data."""
        return self._sensor_cache.copy()
    
    @property
    def stats(self) -> Dict[str, Any]:
        return {
            **self._stats,
            "uptime": self.qemu.uptime if self.qemu else 0,
            "qemu_state": self.qemu.state.value if self.qemu else "none",
        }
    
    def _register_default_handlers(self) -> None:
        """Register default message handlers."""
        for sensor_key, (sensor_name, _) in self.SENSOR_TYPES.items():
            self.on_message(sensor_key, self._handle_sensor_message)
        
        self.on_message("STATUS", self._handle_status_message)
        self.on_message("ERROR", self._handle_error_message)
        self.on_message("ACK", self._handle_ack_message)
        self.on_message("EVENT", self._handle_event_message)
    
    def on_message(self, prefix: str, handler: Callable) -> None:
        """Register message handler for a prefix."""
        if prefix not in self._message_handlers:
            self._message_handlers[prefix] = []
        self._message_handlers[prefix].append(handler)
    
    async def start(self) -> bool:
        """Start the bridge."""
        if self._running:
            return True
        
        # Start QEMU if not running
        if not self.qemu.is_running:
            logger.info("Starting QEMU runner...")
            if not await self.qemu.start():
                logger.error("Failed to start QEMU")
                return False
        
        self._running = True
        
        # Register QEMU callbacks
        self.qemu.on_serial_rx(self._on_serial_data)
        self.qemu.on_state_change(self._on_qemu_state_change)
        
        # Start RX processing task
        self._rx_task = asyncio.create_task(self._rx_loop())
        
        # Start heartbeat task
        if self.config.heartbeat_interval > 0:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        
        # Subscribe to command topic if event bus available
        if self._event_bus and self.config.auto_subscribe_commands:
            topic = f"{self.config.event_prefix}.{self.config.device_id}.{self.config.command_topic}"
            self._event_bus.subscribe(topic, self._on_command_event)
        
        # Announce device online
        await self._publish_status("online")
        
        logger.info(f"Firmware bridge started: {self.config.device_id}")
        return True
    
    async def stop(self) -> None:
        """Stop the bridge."""
        if not self._running:
            return
        
        self._running = False
        
        # Cancel tasks
        if self._rx_task:
            self._rx_task.cancel()
            try:
                await self._rx_task
            except asyncio.CancelledError:
                pass
        
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        
        # Announce device offline
        await self._publish_status("offline")
        
        logger.info(f"Firmware bridge stopped: {self.config.device_id}")
    
    def _on_serial_data(self, data: bytes) -> None:
        """Handle raw serial data from QEMU (callback)."""
        # Data is processed in _rx_loop
        pass
    
    def _on_qemu_state_change(self, new_state: QEMUState) -> None:
        """Handle QEMU state change."""
        if new_state == QEMUState.CRASHED:
            logger.error("QEMU crashed!")
            asyncio.create_task(self._publish_status("error", {"reason": "qemu_crash"}))
        elif new_state == QEMUState.STOPPED:
            asyncio.create_task(self._publish_status("offline"))
    
    async def _rx_loop(self) -> None:
        """Process received data."""
        buffer = b""
        
        while self._running:
            try:
                # Read from QEMU
                data = await self.qemu.serial_read(timeout=0.1)
                if data:
                    buffer += data
                    self._stats["last_message_time"] = time.time()
                    
                    # Process complete lines
                    while self.config.line_ending.encode() in buffer:
                        line, buffer = buffer.split(
                            self.config.line_ending.encode(), 1
                        )
                        if line:
                            await self._process_line(line)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"RX loop error: {e}")
                await asyncio.sleep(0.1)
    
    async def _process_line(self, raw: bytes) -> None:
        """Process a complete line from firmware."""
        self._stats["messages_rx"] += 1
        
        try:
            # Decode
            try:
                text = raw.decode("utf-8").strip()
            except UnicodeDecodeError:
                text = raw.hex()
                logger.warning(f"Non-UTF8 data: {text}")
                return
            
            if not text:
                return
            
            # Parse based on protocol mode
            if self.config.protocol_mode == ProtocolMode.JSON:
                message = self._parse_json(text, raw)
            elif self.config.protocol_mode == ProtocolMode.BINARY:
                message = self._parse_binary(raw)
            else:
                message = self._parse_text(text, raw)
            
            if message:
                await self._dispatch_message(message)
            
        except Exception as e:
            logger.error(f"Parse error: {e}")
            self._stats["parse_errors"] += 1
    
    def _parse_text(self, text: str, raw: bytes) -> Optional[FirmwareMessage]:
        """Parse text protocol: KEY:VALUE or KEY=VALUE."""
        # Try KEY:VALUE format
        match = re.match(r"^([A-Z0-9_]+)[:=](.*)$", text, re.IGNORECASE)
        if match:
            key = match.group(1).upper()
            value_str = match.group(2).strip()
            
            # Try to parse value
            value = self._parse_value(value_str)
            
            # Determine message type
            if key in self.SENSOR_TYPES:
                msg_type = "sensor"
            elif key in ("ACK", "OK", "DONE"):
                msg_type = "response"
            elif key == "ERROR":
                msg_type = "error"
            elif key == "EVENT":
                msg_type = "event"
            else:
                msg_type = "data"
            
            return FirmwareMessage(
                msg_type=msg_type,
                key=key,
                value=value,
                raw=raw,
            )
        
        # Simple status message
        if text.upper() in ("OK", "READY", "BOOTED"):
            return FirmwareMessage(
                msg_type="status",
                key="STATUS",
                value=text.upper(),
                raw=raw,
            )
        
        # Unknown format - log it
        logger.debug(f"Unparsed firmware output: {text}")
        return FirmwareMessage(
            msg_type="raw",
            key="RAW",
            value=text,
            raw=raw,
        )
    
    def _parse_json(self, text: str, raw: bytes) -> Optional[FirmwareMessage]:
        """Parse JSON protocol."""
        try:
            data = json.loads(text)
            
            msg_type = data.get("type", "data")
            key = data.get("key", data.get("sensor", "UNKNOWN"))
            value = data.get("value", data.get("data", data))
            
            return FirmwareMessage(
                msg_type=msg_type,
                key=key.upper(),
                value=value,
                raw=raw,
            )
        except json.JSONDecodeError:
            # Fall back to text parsing
            return self._parse_text(text, raw)
    
    def _parse_binary(self, raw: bytes) -> Optional[FirmwareMessage]:
        """Parse binary protocol with header."""
        if len(raw) < 4:
            return None
        
        # Expected format: [2-byte type][2-byte length][payload]
        msg_type_id, length = struct.unpack("<HH", raw[:4])
        payload = raw[4:4+length]
        
        # Map type IDs to names
        type_map = {
            0x0001: ("sensor", "TEMP"),
            0x0002: ("sensor", "HUMIDITY"),
            0x0003: ("sensor", "MOTION"),
            0x0010: ("response", "ACK"),
            0x0011: ("error", "ERROR"),
            0x00FF: ("event", "EVENT"),
        }
        
        msg_type, key = type_map.get(msg_type_id, ("data", "UNKNOWN"))
        
        # Parse payload based on type
        if msg_type == "sensor" and len(payload) >= 4:
            value = struct.unpack("<f", payload[:4])[0]
        elif len(payload) > 0:
            try:
                value = payload.decode("utf-8")
            except:
                value = payload.hex()
        else:
            value = None
        
        return FirmwareMessage(
            msg_type=msg_type,
            key=key,
            value=value,
            raw=raw,
        )
    
    def _parse_value(self, value_str: str) -> Any:
        """Parse a value string to appropriate type."""
        value_str = value_str.strip()
        
        # Boolean
        if value_str.upper() in ("TRUE", "1", "ON", "YES"):
            return True
        if value_str.upper() in ("FALSE", "0", "OFF", "NO"):
            return False
        
        # Number
        try:
            if "." in value_str:
                return float(value_str)
            return int(value_str)
        except ValueError:
            pass
        
        # String
        return value_str
    
    async def _dispatch_message(self, message: FirmwareMessage) -> None:
        """Dispatch message to handlers and event bus."""
        # Call registered handlers
        handlers = self._message_handlers.get(message.key, [])
        for handler in handlers:
            try:
                result = handler(message)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Handler error for {message.key}: {e}")
        
        # Resolve pending command if this is a response
        if message.msg_type in ("response", "error"):
            for cmd_id, future in list(self._pending_commands.items()):
                if not future.done():
                    future.set_result(message)
                    del self._pending_commands[cmd_id]
                    break
        
        # Publish to event bus
        if self._event_bus and self.config.broadcast_sensor_updates:
            await self._publish_message(message)
    
    def _handle_sensor_message(self, message: FirmwareMessage) -> None:
        """Handle sensor data message."""
        sensor_info = self.SENSOR_TYPES.get(message.key)
        if sensor_info:
            sensor_name, unit = sensor_info
            self._sensor_cache[sensor_name] = {
                "value": message.value,
                "unit": unit,
                "timestamp": message.timestamp,
            }
            logger.debug(f"Sensor update: {sensor_name}={message.value}{unit}")
    
    def _handle_status_message(self, message: FirmwareMessage) -> None:
        """Handle status message."""
        logger.info(f"Firmware status: {message.value}")
    
    def _handle_error_message(self, message: FirmwareMessage) -> None:
        """Handle error message."""
        logger.error(f"Firmware error: {message.value}")
    
    def _handle_ack_message(self, message: FirmwareMessage) -> None:
        """Handle acknowledgment message."""
        logger.debug(f"Firmware ACK: {message.value}")
    
    def _handle_event_message(self, message: FirmwareMessage) -> None:
        """Handle event message from firmware."""
        logger.info(f"Firmware event: {message.value}")
    
    async def _publish_message(self, message: FirmwareMessage) -> None:
        """Publish message to event bus."""
        if not self._event_bus:
            return
        
        topic = f"{self.config.event_prefix}.{self.config.device_id}.{self.config.sensor_topic}"
        
        event = EventMessage(
            source_id=self.config.device_id,
            event_name=f"firmware_{message.msg_type}",
            payload={
                "device_type": self.config.device_type,
                "room": self.config.room,
                **message.to_dict(),
            },
        )
        
        self._event_bus.publish(topic, event)
        self._stats["events_published"] += 1
    
    async def _publish_status(self, status: str, extra_data: Dict = None) -> None:
        """Publish device status to event bus."""
        if not self._event_bus:
            return
        
        topic = f"{self.config.event_prefix}.{self.config.device_id}.{self.config.status_topic}"
        
        event = EventMessage(
            source_id=self.config.device_id,
            event_name="firmware_status",
            payload={
                "status": status,
                "device_type": self.config.device_type,
                "room": self.config.room,
                **(extra_data or {}),
            },
        )
        
        self._event_bus.publish(topic, event)
    
    async def _on_command_event(self, event: EventMessage) -> None:
        """Handle command from event bus."""
        command = event.payload.get("command", "")
        args = event.payload.get("args", {})
        
        await self.send_command(command, **args)
    
    async def send_command(
        self,
        command: str,
        timeout: float = None,
        **kwargs,
    ) -> Optional[FirmwareMessage]:
        """Send command to firmware and wait for response."""
        # Map VESPER command to firmware command
        fw_cmd = self.COMMAND_MAP.get(command.lower(), command.upper())
        
        # Build command string
        if kwargs:
            args_str = ",".join(f"{k}={v}" for k, v in kwargs.items())
            fw_cmd = f"{fw_cmd}:{args_str}"
        
        # Send to QEMU
        logger.debug(f"Sending to firmware: {fw_cmd}")
        await self.qemu.serial_write(f"{fw_cmd}\n".encode())
        self._stats["messages_tx"] += 1
        self._stats["commands_processed"] += 1
        
        # Wait for response
        timeout = timeout or self.config.command_timeout
        cmd_id = str(time.time())
        future = asyncio.get_event_loop().create_future()
        self._pending_commands[cmd_id] = future
        
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
            return response
        except asyncio.TimeoutError:
            logger.warning(f"Command timeout: {fw_cmd}")
            del self._pending_commands[cmd_id]
            return None
    
    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeat/status requests."""
        while self._running:
            try:
                await asyncio.sleep(self.config.heartbeat_interval)
                
                # Request status from firmware
                await self.qemu.serial_write(b"STATUS\n")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
    
    # Convenience methods for common operations
    async def get_temperature(self) -> Optional[float]:
        """Get temperature from sensor."""
        response = await self.send_command("get_temperature")
        if response and response.msg_type == "sensor":
            return float(response.value)
        return self._sensor_cache.get("temperature", {}).get("value")
    
    async def get_humidity(self) -> Optional[float]:
        """Get humidity from sensor."""
        response = await self.send_command("get_humidity")
        if response and response.msg_type == "sensor":
            return float(response.value)
        return self._sensor_cache.get("humidity", {}).get("value")
    
    async def set_led(self, on: bool) -> bool:
        """Set LED state."""
        response = await self.send_command("set_led", value="1" if on else "0")
        return response is not None and response.msg_type != "error"
    
    async def reboot(self) -> bool:
        """Reboot firmware."""
        response = await self.send_command("reboot")
        return response is not None
    
    async def __aenter__(self) -> "VesperFirmwareBridge":
        await self.start()
        return self
    
    async def __aexit__(self, *args) -> None:
        await self.stop()
