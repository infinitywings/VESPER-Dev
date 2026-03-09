"""
Firmware emulator for IoT devices.

Provides QEMU-based or simulated firmware execution.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import os

logger = logging.getLogger(__name__)


class EmulatorType(str, Enum):
    """Type of firmware emulator."""
    QEMU = "qemu"
    SIMULATED = "simulated"
    DOCKER = "docker"


class EmulatorState(str, Enum):
    """Emulator runtime state."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class EmulatorConfig:
    """Configuration for firmware emulator."""
    # Emulator type
    emulator_type: EmulatorType = EmulatorType.SIMULATED
    
    # QEMU settings
    qemu_path: str = "qemu-system-arm"
    machine: str = "lm3s6965evb"  # ARM Cortex-M3 board
    cpu: str = "cortex-m3"
    memory: str = "64M"
    
    # Firmware
    firmware_path: Optional[str] = None
    firmware_format: str = "elf"  # elf, bin, hex
    
    # Networking
    enable_network: bool = True
    network_type: str = "user"  # user, tap, bridge
    host_port: int = 5555
    guest_port: int = 5555
    
    # Debug
    enable_gdb: bool = False
    gdb_port: int = 1234
    
    # Execution
    timeout: float = 30.0
    auto_restart: bool = False


class FirmwareEmulator:
    """
    Emulator for running IoT device firmware.
    
    Supports:
    - QEMU ARM emulation for real firmware
    - Simulated mode for testing without QEMU
    - Network bridge to Vesper simulation
    
    Example:
        config = EmulatorConfig(
            firmware_path="firmware/esp32_sensor.elf",
            emulator_type=EmulatorType.QEMU,
        )
        
        emulator = FirmwareEmulator(config)
        emulator.start()
        
        # Send command to firmware
        emulator.send("GET_SENSOR_DATA")
        response = emulator.receive()
    """
    
    def __init__(self, config: Optional[EmulatorConfig] = None):
        self.config = config or EmulatorConfig()
        
        self._state = EmulatorState.STOPPED
        self._process: Optional[subprocess.Popen] = None
        self._output_thread: Optional[threading.Thread] = None
        self._output_buffer: List[str] = []
        self._callbacks: Dict[str, List[Callable]] = {}
        
        self._stats = {
            "start_count": 0,
            "messages_sent": 0,
            "messages_received": 0,
            "errors": 0,
        }
    
    @property
    def state(self) -> EmulatorState:
        return self._state
    
    @property
    def is_running(self) -> bool:
        return self._state == EmulatorState.RUNNING
    
    @property
    def stats(self) -> Dict[str, int]:
        return self._stats.copy()
    
    def start(self) -> bool:
        """Start the firmware emulator."""
        if self._state == EmulatorState.RUNNING:
            logger.warning("Emulator already running")
            return True
        
        self._state = EmulatorState.STARTING
        
        try:
            if self.config.emulator_type == EmulatorType.QEMU:
                success = self._start_qemu()
            elif self.config.emulator_type == EmulatorType.DOCKER:
                success = self._start_docker()
            else:
                success = self._start_simulated()
            
            if success:
                self._state = EmulatorState.RUNNING
                self._stats["start_count"] += 1
                logger.info(f"Emulator started ({self.config.emulator_type.value})")
            else:
                self._state = EmulatorState.ERROR
            
            return success
            
        except Exception as e:
            logger.error(f"Failed to start emulator: {e}")
            self._state = EmulatorState.ERROR
            self._stats["errors"] += 1
            return False
    
    def _start_qemu(self) -> bool:
        """Start QEMU emulator."""
        if not self.config.firmware_path:
            logger.error("No firmware path specified")
            return False
        
        firmware = Path(self.config.firmware_path)
        if not firmware.exists():
            logger.error(f"Firmware not found: {firmware}")
            return False
        
        # Build QEMU command
        cmd = [
            self.config.qemu_path,
            "-machine", self.config.machine,
            "-cpu", self.config.cpu,
            "-m", self.config.memory,
            "-nographic",
            "-kernel", str(firmware),
        ]
        
        # Add network options
        if self.config.enable_network:
            cmd.extend([
                "-netdev", f"user,id=net0,hostfwd=tcp::{self.config.host_port}-:{self.config.guest_port}",
                "-device", "virtio-net-device,netdev=net0",
            ])
        
        # Add GDB server
        if self.config.enable_gdb:
            cmd.extend(["-gdb", f"tcp::{self.config.gdb_port}", "-S"])
        
        logger.debug(f"QEMU command: {' '.join(cmd)}")
        
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            
            # Start output reader thread
            self._output_thread = threading.Thread(
                target=self._read_output,
                daemon=True,
            )
            self._output_thread.start()
            
            return True
            
        except FileNotFoundError:
            logger.error(f"QEMU not found: {self.config.qemu_path}")
            return False
    
    def _start_docker(self) -> bool:
        """Start Docker-based emulator."""
        # Placeholder for Docker-based firmware execution
        logger.warning("Docker emulation not yet implemented")
        return self._start_simulated()
    
    def _start_simulated(self) -> bool:
        """Start simulated firmware (no actual emulation)."""
        logger.info("Starting simulated firmware")
        self._simulated_state = {
            "running": True,
            "sensors": {"temperature": 22.5, "humidity": 45.0},
            "actuators": {"led": False, "relay": False},
        }
        return True
    
    def _read_output(self) -> None:
        """Read output from QEMU process."""
        if not self._process:
            return
        
        try:
            for line in self._process.stdout:
                line = line.strip()
                self._output_buffer.append(line)
                self._stats["messages_received"] += 1
                
                # Call registered callbacks
                self._trigger_callbacks("output", line)
                
                # Keep buffer limited
                if len(self._output_buffer) > 1000:
                    self._output_buffer = self._output_buffer[-500:]
                    
        except Exception as e:
            logger.error(f"Output reader error: {e}")
    
    def stop(self) -> None:
        """Stop the emulator."""
        if self._state == EmulatorState.STOPPED:
            return
        
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
        
        if hasattr(self, '_simulated_state'):
            self._simulated_state["running"] = False
        
        self._state = EmulatorState.STOPPED
        logger.info("Emulator stopped")
    
    def send(self, data: str) -> bool:
        """Send data to the firmware."""
        if not self.is_running:
            logger.warning("Emulator not running")
            return False
        
        if self._process and self._process.stdin:
            try:
                self._process.stdin.write(data + "\n")
                self._process.stdin.flush()
                self._stats["messages_sent"] += 1
                return True
            except Exception as e:
                logger.error(f"Send error: {e}")
                return False
        
        # Simulated mode
        if hasattr(self, '_simulated_state'):
            self._handle_simulated_command(data)
            return True
        
        return False
    
    def _handle_simulated_command(self, cmd: str) -> None:
        """Handle command in simulated mode."""
        cmd = cmd.upper().strip()
        
        if cmd == "GET_TEMP":
            self._output_buffer.append(f"TEMP:{self._simulated_state['sensors']['temperature']}")
        elif cmd == "GET_HUMIDITY":
            self._output_buffer.append(f"HUMIDITY:{self._simulated_state['sensors']['humidity']}")
        elif cmd.startswith("SET_LED:"):
            value = cmd.split(":")[1] == "1"
            self._simulated_state['actuators']['led'] = value
            self._output_buffer.append(f"LED:{1 if value else 0}")
        elif cmd == "STATUS":
            self._output_buffer.append("STATUS:OK")
        else:
            self._output_buffer.append(f"UNKNOWN:{cmd}")
    
    def receive(self, timeout: float = 1.0) -> Optional[str]:
        """Receive next output line."""
        start = time.time()
        while time.time() - start < timeout:
            if self._output_buffer:
                return self._output_buffer.pop(0)
            time.sleep(0.01)
        return None
    
    def receive_all(self) -> List[str]:
        """Get all buffered output."""
        output = self._output_buffer.copy()
        self._output_buffer.clear()
        return output
    
    def on(self, event: str, callback: Callable) -> None:
        """Register event callback."""
        if event not in self._callbacks:
            self._callbacks[event] = []
        self._callbacks[event].append(callback)
    
    def _trigger_callbacks(self, event: str, data: Any) -> None:
        """Trigger registered callbacks."""
        for callback in self._callbacks.get(event, []):
            try:
                callback(data)
            except Exception as e:
                logger.error(f"Callback error: {e}")
    
    def get_state(self) -> Dict[str, Any]:
        """Get emulator state."""
        return {
            "state": self._state.value,
            "type": self.config.emulator_type.value,
            "firmware": self.config.firmware_path,
            "stats": self._stats,
        }
    
    def __enter__(self) -> "FirmwareEmulator":
        self.start()
        return self
    
    def __exit__(self, *args) -> None:
        self.stop()
