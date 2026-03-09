"""
Bridge between firmware emulator and Vesper simulation.

Translates firmware messages to IoT device events.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from vesper.firmware.emulator import FirmwareEmulator, EmulatorConfig
from vesper.core.event_bus import EventBus
from vesper.protocol.messages import Message, EventMessage, CommandMessage

logger = logging.getLogger(__name__)


@dataclass
class BridgeConfig:
    """Configuration for firmware bridge."""
    device_id: str = "firmware_device"
    poll_interval: float = 0.1  # seconds
    auto_translate: bool = True
    
    # Message format
    message_prefix: str = ""
    message_suffix: str = ""


class FirmwareBridge:
    """
    Bridge between firmware emulator and Vesper.
    
    Responsibilities:
    - Forward commands from Vesper to firmware
    - Parse firmware output to Vesper events
    - Maintain connection health
    
    Example:
        emulator = FirmwareEmulator(EmulatorConfig())
        bridge = FirmwareBridge(emulator, event_bus)
        
        bridge.start()
        
        # Firmware output appears as events
        event_bus.subscribe("firmware.*", handle_firmware_event)
        
        # Commands go to firmware
        bridge.send_command("GET_TEMP")
    """
    
    def __init__(
        self,
        emulator: FirmwareEmulator,
        event_bus: Optional[EventBus] = None,
        config: Optional[BridgeConfig] = None,
    ):
        self.emulator = emulator
        self._event_bus = event_bus
        self.config = config or BridgeConfig()
        
        self._running = False
        self._poll_thread: Optional[threading.Thread] = None
        self._message_handlers: Dict[str, Callable] = {}
        
        self._stats = {
            "messages_bridged": 0,
            "commands_sent": 0,
            "parse_errors": 0,
        }
        
        # Register default parsers
        self._register_default_handlers()
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def stats(self) -> Dict[str, int]:
        return self._stats.copy()
    
    def _register_default_handlers(self) -> None:
        """Register default message handlers."""
        self._message_handlers = {
            "TEMP": self._handle_temperature,
            "HUMIDITY": self._handle_humidity,
            "MOTION": self._handle_motion,
            "DOOR": self._handle_door,
            "LED": self._handle_led,
            "STATUS": self._handle_status,
        }
    
    def register_handler(self, prefix: str, handler: Callable) -> None:
        """Register custom message handler."""
        self._message_handlers[prefix] = handler
    
    def start(self) -> bool:
        """Start the bridge."""
        if self._running:
            return True
        
        # Ensure emulator is running
        if not self.emulator.is_running:
            if not self.emulator.start():
                logger.error("Failed to start emulator")
                return False
        
        self._running = True
        
        # Start polling thread
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
        )
        self._poll_thread.start()
        
        logger.info("Firmware bridge started")
        return True
    
    def stop(self) -> None:
        """Stop the bridge."""
        self._running = False
        if self._poll_thread:
            self._poll_thread.join(timeout=2.0)
            self._poll_thread = None
        logger.info("Firmware bridge stopped")
    
    def _poll_loop(self) -> None:
        """Poll emulator for output."""
        while self._running:
            try:
                messages = self.emulator.receive_all()
                for msg in messages:
                    self._process_message(msg)
            except Exception as e:
                logger.error(f"Poll error: {e}")
            
            time.sleep(self.config.poll_interval)
    
    def _process_message(self, raw: str) -> None:
        """Process a raw message from firmware."""
        if not raw:
            return
        
        try:
            # Parse message format: PREFIX:VALUE or PREFIX:KEY=VALUE
            if ":" in raw:
                prefix, value = raw.split(":", 1)
                prefix = prefix.upper()
            else:
                prefix = raw.upper()
                value = ""
            
            # Find handler
            handler = self._message_handlers.get(prefix)
            if handler:
                handler(value)
                self._stats["messages_bridged"] += 1
            else:
                # Generic firmware event
                self._emit_event(f"firmware.{prefix.lower()}", {"raw": raw, "value": value})
                self._stats["messages_bridged"] += 1
                
        except Exception as e:
            logger.warning(f"Parse error for '{raw}': {e}")
            self._stats["parse_errors"] += 1
    
    def _emit_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Emit event to event bus."""
        if self._event_bus:
            self._event_bus.emit(
                event_type,
                payload=payload,
                source_id=self.config.device_id,
            )
    
    # Default message handlers
    
    def _handle_temperature(self, value: str) -> None:
        try:
            temp = float(value)
            self._emit_event("sensor.temperature", {"value": temp, "unit": "celsius"})
        except ValueError:
            pass
    
    def _handle_humidity(self, value: str) -> None:
        try:
            humidity = float(value)
            self._emit_event("sensor.humidity", {"value": humidity, "unit": "percent"})
        except ValueError:
            pass
    
    def _handle_motion(self, value: str) -> None:
        detected = value in ("1", "true", "TRUE", "detected")
        self._emit_event("motion_detected", {"detected": detected})
    
    def _handle_door(self, value: str) -> None:
        is_open = value in ("1", "open", "OPEN")
        self._emit_event("door.state", {"is_open": is_open})
    
    def _handle_led(self, value: str) -> None:
        is_on = value in ("1", "on", "ON")
        self._emit_event("actuator.led", {"is_on": is_on})
    
    def _handle_status(self, value: str) -> None:
        self._emit_event("firmware.status", {"status": value})
    
    # Command sending
    
    def send_command(self, command: str) -> bool:
        """Send command to firmware."""
        formatted = f"{self.config.message_prefix}{command}{self.config.message_suffix}"
        success = self.emulator.send(formatted)
        if success:
            self._stats["commands_sent"] += 1
        return success
    
    def get_temperature(self) -> bool:
        return self.send_command("GET_TEMP")
    
    def get_humidity(self) -> bool:
        return self.send_command("GET_HUMIDITY")
    
    def set_led(self, on: bool) -> bool:
        return self.send_command(f"SET_LED:{1 if on else 0}")
    
    def get_status(self) -> bool:
        return self.send_command("STATUS")
    
    def get_state(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "device_id": self.config.device_id,
            "emulator_state": self.emulator.state.value,
            "stats": self._stats,
        }
