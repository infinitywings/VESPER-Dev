#!/usr/bin/env python3
"""
VESPER Firmware + SmartThings Integration Demo.

This script demonstrates:
1. Running virtual devices with real QEMU-emulated ARM firmware
2. Bi-directional communication between firmware and Python
3. Integration with SmartThings Schema connector

Each virtual device runs as a QEMU process with the compiled firmware,
allowing realistic simulation of IoT device behavior.
"""

import asyncio
import logging
import os
import sys
import signal
import pty
import subprocess
import select
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Callable, Any

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configure logging
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/firmware_demo_{datetime.now():%Y%m%d_%H%M%S}.log"),
    ],
)
logger = logging.getLogger(__name__)


@dataclass
class FirmwareDeviceConfig:
    """Configuration for a QEMU-based firmware device."""
    device_id: str
    name: str
    firmware_path: str
    qemu_machine: str = "lm3s6965evb"
    qemu_cpu: str = "cortex-m3"


class QEMUFirmwareDevice:
    """
    Manages a single QEMU firmware device instance.
    
    Communicates with the firmware via serial port (PTY).
    """
    
    def __init__(self, config: FirmwareDeviceConfig):
        self.config = config
        self.process: Optional[subprocess.Popen] = None
        self.master_fd: Optional[int] = None
        self.slave_fd: Optional[int] = None
        self._running = False
        self._read_task: Optional[asyncio.Task] = None
        self._callbacks: list[Callable[[str, str], None]] = []
        
        # Device state (cached from firmware responses)
        self.state: Dict[str, Any] = {
            "switch": "off",
            "temperature": 22.5,
            "humidity": 45.0,
            "motion": "inactive",
        }
    
    def on_data(self, callback: Callable[[str, str], None]):
        """Register callback for data from firmware. (device_id, line)"""
        self._callbacks.append(callback)
    
    async def start(self) -> bool:
        """Start the QEMU firmware device."""
        if not Path(self.config.firmware_path).exists():
            logger.error(f"Firmware not found: {self.config.firmware_path}")
            return False
        
        # Create PTY for serial communication
        self.master_fd, self.slave_fd = pty.openpty()
        slave_name = os.ttyname(self.slave_fd)
        
        # Build QEMU command
        qemu_cmd = [
            "qemu-system-arm",
            "-M", self.config.qemu_machine,
            "-cpu", self.config.qemu_cpu,
            "-nographic",
            "-kernel", self.config.firmware_path,
            "-serial", f"pty:{slave_name}",
        ]
        
        logger.info(f"Starting QEMU device: {self.config.name}")
        logger.debug(f"Command: {' '.join(qemu_cmd)}")
        
        try:
            self.process = subprocess.Popen(
                qemu_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._running = True
            
            # Start reading from firmware
            self._read_task = asyncio.create_task(self._read_loop())
            
            # Wait for boot message
            await asyncio.sleep(0.5)
            
            # Query initial state
            await self.send_command("IDENTIFY")
            await self.send_command("GET_ALL")
            
            logger.info(f"✅ Device started: {self.config.name} (PID: {self.process.pid})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start QEMU: {e}")
            return False
    
    async def stop(self):
        """Stop the QEMU device."""
        self._running = False
        
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
        
        if self.master_fd:
            os.close(self.master_fd)
            self.master_fd = None
        if self.slave_fd:
            os.close(self.slave_fd)
            self.slave_fd = None
        
        logger.info(f"Device stopped: {self.config.name}")
    
    async def send_command(self, command: str) -> Optional[str]:
        """Send a command to the firmware and wait for response."""
        if not self._running or not self.master_fd:
            return None
        
        try:
            # Send command with newline
            cmd_bytes = (command + "\n").encode()
            os.write(self.master_fd, cmd_bytes)
            logger.debug(f"[{self.config.device_id}] TX: {command}")
            
            # Wait a bit for response
            await asyncio.sleep(0.1)
            return "OK"
            
        except Exception as e:
            logger.error(f"Failed to send command: {e}")
            return None
    
    async def _read_loop(self):
        """Background task to read data from firmware."""
        buffer = ""
        
        while self._running and self.master_fd:
            try:
                # Check if data available
                readable, _, _ = select.select([self.master_fd], [], [], 0.1)
                
                if readable:
                    data = os.read(self.master_fd, 1024)
                    if data:
                        text = data.decode("utf-8", errors="replace")
                        buffer += text
                        
                        # Process complete lines
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if line:
                                self._process_line(line)
                
                await asyncio.sleep(0.01)
                
            except Exception as e:
                if self._running:
                    logger.error(f"Read error: {e}")
                break
    
    def _process_line(self, line: str):
        """Process a line received from firmware."""
        logger.debug(f"[{self.config.device_id}] RX: {line}")
        
        # Parse state updates
        if line.startswith("SWITCH:"):
            self.state["switch"] = line.split(":")[1].strip()
        elif line.startswith("TEMP:"):
            try:
                self.state["temperature"] = float(line.split(":")[1])
            except ValueError:
                pass
        elif line.startswith("HUMIDITY:"):
            try:
                self.state["humidity"] = float(line.split(":")[1])
            except ValueError:
                pass
        elif line.startswith("MOTION:"):
            self.state["motion"] = line.split(":")[1].strip()
        elif line.startswith("EVENT:MOTION"):
            self.state["motion"] = "active"
        
        # Notify callbacks
        for callback in self._callbacks:
            try:
                callback(self.config.device_id, line)
            except Exception as e:
                logger.error(f"Callback error: {e}")
    
    # SmartThings-compatible methods
    async def turn_on(self):
        """Turn the device on (SmartThings switch capability)."""
        await self.send_command("ON")
        self.state["switch"] = "on"
    
    async def turn_off(self):
        """Turn the device off (SmartThings switch capability)."""
        await self.send_command("OFF")
        self.state["switch"] = "off"
    
    async def get_state(self) -> Dict[str, Any]:
        """Get current device state."""
        await self.send_command("GET_ALL")
        await asyncio.sleep(0.1)  # Wait for response
        return self.state.copy()


class FirmwareDeviceManager:
    """
    Manages multiple QEMU firmware devices.
    
    Acts as a bridge between firmware devices and SmartThings.
    """
    
    def __init__(self):
        self.devices: Dict[str, QEMUFirmwareDevice] = {}
        self._command_handler: Optional[Callable] = None
    
    def on_command(self, handler: Callable):
        """Register command handler from SmartThings."""
        self._command_handler = handler
    
    async def create_device(self, config: FirmwareDeviceConfig) -> Optional[QEMUFirmwareDevice]:
        """Create and start a new firmware device."""
        device = QEMUFirmwareDevice(config)
        
        # Register data callback
        device.on_data(self._on_device_data)
        
        if await device.start():
            self.devices[config.device_id] = device
            return device
        return None
    
    def _on_device_data(self, device_id: str, line: str):
        """Handle data received from a firmware device."""
        logger.info(f"📡 [{device_id}] {line}")
    
    async def handle_smartthings_command(
        self,
        device_id: str,
        capability: str,
        command: str,
        arguments: list,
    ) -> bool:
        """Handle a command from SmartThings."""
        device = self.devices.get(device_id)
        if not device:
            logger.warning(f"Device not found: {device_id}")
            return False
        
        logger.info(f"📱 SmartThings command: {device_id} -> {capability}.{command}")
        
        if capability == "st.switch":
            if command == "on":
                await device.turn_on()
            elif command == "off":
                await device.turn_off()
        elif capability == "st.switchLevel":
            if command == "setLevel" and arguments:
                # Dimmer level - our simple firmware just does on/off
                level = arguments[0]
                if level > 0:
                    await device.turn_on()
                else:
                    await device.turn_off()
        
        return True
    
    async def get_device_states(self) -> Dict[str, Dict[str, Any]]:
        """Get states for all devices."""
        states = {}
        for device_id, device in self.devices.items():
            states[device_id] = await device.get_state()
        return states
    
    async def stop_all(self):
        """Stop all firmware devices."""
        for device in self.devices.values():
            await device.stop()
        self.devices.clear()


async def main():
    """Main demo entry point."""
    print("\n" + "=" * 60)
    print("VESPER Firmware + SmartThings Demo")
    print("=" * 60)
    
    # Find firmware
    firmware_path = Path(__file__).parent.parent / "vesper/firmware/samples/sensor_firmware.elf"
    if not firmware_path.exists():
        print(f"\n❌ Firmware not found: {firmware_path}")
        print("Please compile it first:")
        print("  cd vesper/firmware/samples && make")
        return
    
    print(f"\n✅ Firmware found: {firmware_path}")
    
    # Create device manager
    manager = FirmwareDeviceManager()
    
    # Create demo devices
    devices_config = [
        FirmwareDeviceConfig(
            device_id="qemu-kitchen-light",
            name="Kitchen Light (QEMU)",
            firmware_path=str(firmware_path),
        ),
        FirmwareDeviceConfig(
            device_id="qemu-living-room",
            name="Living Room Sensor (QEMU)",
            firmware_path=str(firmware_path),
        ),
    ]
    
    print(f"\nStarting {len(devices_config)} firmware devices...")
    
    for config in devices_config:
        device = await manager.create_device(config)
        if device:
            print(f"  ✓ {config.name} ({config.device_id})")
        else:
            print(f"  ✗ Failed to start {config.name}")
    
    if not manager.devices:
        print("\n❌ No devices started. Check QEMU installation.")
        return
    
    print("\n" + "-" * 60)
    print("INTERACTIVE DEMO")
    print("-" * 60)
    print("""
Commands:
  on <device_id>   - Turn device on
  off <device_id>  - Turn device off
  state            - Show all device states
  temp <device_id> - Get temperature
  quit             - Exit

Device IDs:""")
    for device_id in manager.devices:
        print(f"  - {device_id}")
    print()
    
    # Interactive loop
    try:
        while True:
            try:
                cmd = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input(">>> ").strip()
                )
            except EOFError:
                break
            
            if not cmd:
                continue
            
            parts = cmd.split()
            action = parts[0].lower()
            
            if action == "quit" or action == "exit":
                break
            
            elif action == "state":
                states = await manager.get_device_states()
                print("\nDevice States:")
                for device_id, state in states.items():
                    print(f"  {device_id}:")
                    for key, value in state.items():
                        print(f"    {key}: {value}")
                print()
            
            elif action == "on" and len(parts) > 1:
                device_id = parts[1]
                if device_id in manager.devices:
                    await manager.handle_smartthings_command(
                        device_id, "st.switch", "on", []
                    )
                    print(f"✓ {device_id} turned ON")
                else:
                    print(f"✗ Unknown device: {device_id}")
            
            elif action == "off" and len(parts) > 1:
                device_id = parts[1]
                if device_id in manager.devices:
                    await manager.handle_smartthings_command(
                        device_id, "st.switch", "off", []
                    )
                    print(f"✓ {device_id} turned OFF")
                else:
                    print(f"✗ Unknown device: {device_id}")
            
            elif action == "temp" and len(parts) > 1:
                device_id = parts[1]
                if device_id in manager.devices:
                    await manager.devices[device_id].send_command("GET_TEMP")
                    await asyncio.sleep(0.2)
                    print(f"Temperature: {manager.devices[device_id].state.get('temperature')}°C")
                else:
                    print(f"✗ Unknown device: {device_id}")
            
            else:
                print(f"Unknown command: {cmd}")
    
    except KeyboardInterrupt:
        print("\n\nShutting down...")
    
    finally:
        await manager.stop_all()
        print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
