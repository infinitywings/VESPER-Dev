#!/usr/bin/env python3
"""
VESPER QEMU Firmware Simulation Demo

This script demonstrates running IoT device firmware in QEMU
and bridging it to VESPER's event bus.

Usage:
    python scripts/qemu_firmware_demo.py [--firmware PATH]

Prerequisites:
    - QEMU with ARM support: brew install qemu (macOS) or apt install qemu-system-arm (Linux)
    - (Optional) ARM GCC to build firmware: brew install arm-none-eabi-gcc

The demo will:
1. Start a QEMU instance running simulated sensor firmware
2. Bridge firmware serial output to VESPER events
3. Send commands to firmware and display responses
4. Show real-time sensor data updates
"""

import argparse
import asyncio
import logging
import os
import sys
import time

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from vesper.firmware.qemu_runner import (
    QEMURunner,
    QEMUConfig,
    QEMUState,
    BoardType,
    Architecture,
)
from vesper.firmware.vesper_bridge import (
    VesperFirmwareBridge,
    VesperBridgeConfig,
    ProtocolMode,
)
from vesper.core.event_bus import EventBus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("qemu_demo")


class SimulatedFirmware:
    """
    Software-simulated firmware for testing without QEMU.
    
    This provides the same interface as QEMU but runs purely in Python,
    useful for testing on systems without QEMU installed.
    """
    
    def __init__(self):
        self._running = False
        self._rx_queue = asyncio.Queue()
        self._temperature = 22.5
        self._humidity = 45.0
        self._led = False
        self._callbacks = []
        self._update_task = None
    
    @property
    def state(self):
        return QEMUState.RUNNING if self._running else QEMUState.STOPPED
    
    @property
    def is_running(self):
        return self._running
    
    @property
    def uptime(self):
        return time.time() - self._start_time if self._running else 0
    
    async def start(self) -> bool:
        logger.info("Starting simulated firmware...")
        self._running = True
        self._start_time = time.time()
        
        # Send boot messages
        await self._send("BOOTED")
        await self._send("DEVICE:VESPER_SENSOR_V1")
        await self._send("READY")
        
        # Start sensor update task
        self._update_task = asyncio.create_task(self._sensor_loop())
        
        logger.info("Simulated firmware running")
        return True
    
    async def stop(self):
        self._running = False
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
        logger.info("Simulated firmware stopped")
    
    async def _send(self, msg: str):
        """Send message to RX queue."""
        data = f"{msg}\n".encode()
        await self._rx_queue.put(data)
        for callback in self._callbacks:
            try:
                callback(data)
            except:
                pass
    
    async def _sensor_loop(self):
        """Update sensor values periodically."""
        import random
        
        while self._running:
            try:
                await asyncio.sleep(5.0)
                
                # Update sensors
                self._temperature += random.uniform(-0.5, 0.5)
                self._temperature = max(15, min(35, self._temperature))
                
                self._humidity += random.uniform(-2, 2)
                self._humidity = max(20, min(80, self._humidity))
                
                # Occasional motion event
                if random.random() < 0.1:
                    await self._send("EVENT:MOTION")
                    
            except asyncio.CancelledError:
                break
    
    async def serial_write(self, data: bytes) -> bool:
        """Process command from VESPER."""
        cmd = data.decode().strip()
        logger.debug(f"Firmware received: {cmd}")
        
        if cmd == "GET_TEMP":
            await self._send(f"TEMP:{self._temperature:.1f}")
        elif cmd == "GET_HUMIDITY":
            await self._send(f"HUMIDITY:{self._humidity:.1f}")
        elif cmd == "GET_ALL":
            await self._send(f"TEMP:{self._temperature:.1f}")
            await self._send(f"HUMIDITY:{self._humidity:.1f}")
            await self._send(f"LED:{1 if self._led else 0}")
        elif cmd.startswith("SET_LED:"):
            self._led = cmd.split(":")[1] in ("1", "ON")
            await self._send(f"LED:{1 if self._led else 0}")
            await self._send("ACK:SET_LED")
        elif cmd == "STATUS":
            await self._send("STATUS:OK")
        elif cmd == "IDENTIFY":
            await self._send("DEVICE:VESPER_SENSOR_V1")
            await self._send("TYPE:TEMPERATURE_HUMIDITY")
            await self._send("FIRMWARE:1.0.0_SIM")
        elif cmd == "REBOOT":
            await self._send("ACK:REBOOT")
            await asyncio.sleep(0.5)
            await self._send("BOOTED")
            await self._send("READY")
        else:
            await self._send(f"ERROR:UNKNOWN_CMD:{cmd}")
        
        return True
    
    def serial_write_sync(self, data: bytes) -> bool:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self.serial_write(data))
    
    async def serial_read(self, timeout: float = 1.0):
        try:
            return await asyncio.wait_for(self._rx_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
    
    def serial_read_sync(self, timeout: float = 1.0):
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self.serial_read(timeout))
    
    def on_serial_rx(self, callback):
        self._callbacks.append(callback)
    
    def on_state_change(self, callback):
        pass
    
    def on_exit(self, callback):
        pass
    
    def get_stats(self):
        return {
            "state": self.state.value,
            "uptime": self.uptime,
            "mode": "simulated",
        }


async def run_interactive_demo(firmware, bridge):
    """Run interactive command demo."""
    print("\n" + "="*60)
    print("VESPER QEMU Firmware Simulation Demo")
    print("="*60)
    print("\nFirmware is running. Commands available:")
    print("  temp      - Get temperature")
    print("  humidity  - Get humidity")
    print("  all       - Get all sensor data")
    print("  led on    - Turn on LED")
    print("  led off   - Turn off LED")
    print("  status    - Get firmware status")
    print("  identify  - Get device info")
    print("  reboot    - Reboot firmware")
    print("  stats     - Show bridge statistics")
    print("  quit      - Exit demo")
    print("-"*60)
    
    while True:
        try:
            # Read command
            cmd = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("\n> ").strip().lower()
            )
            
            if cmd in ("quit", "exit", "q"):
                print("Exiting...")
                break
            
            elif cmd == "temp":
                temp = await bridge.get_temperature()
                print(f"Temperature: {temp}°C" if temp else "No response")
            
            elif cmd == "humidity":
                humidity = await bridge.get_humidity()
                print(f"Humidity: {humidity}%" if humidity else "No response")
            
            elif cmd == "all":
                response = await bridge.send_command("get_sensors")
                print("Sensor data:")
                for sensor, data in bridge.sensor_data.items():
                    print(f"  {sensor}: {data['value']}{data['unit']}")
            
            elif cmd == "led on":
                success = await bridge.set_led(True)
                print("LED on" if success else "Failed")
            
            elif cmd == "led off":
                success = await bridge.set_led(False)
                print("LED off" if success else "Failed")
            
            elif cmd == "status":
                response = await bridge.send_command("status")
                print(f"Status: {response}" if response else "No response")
            
            elif cmd == "identify":
                response = await bridge.send_command("identify")
                print(f"Device: {response}" if response else "No response")
            
            elif cmd == "reboot":
                success = await bridge.reboot()
                print("Rebooting..." if success else "Failed")
                await asyncio.sleep(1)
                print("Reboot complete")
            
            elif cmd == "stats":
                stats = bridge.stats
                print("Bridge statistics:")
                for key, value in stats.items():
                    print(f"  {key}: {value}")
            
            elif cmd:
                # Send raw command
                response = await bridge.send_command(cmd)
                print(f"Response: {response}" if response else "No response")
                
        except KeyboardInterrupt:
            print("\nInterrupted")
            break
        except Exception as e:
            print(f"Error: {e}")


async def run_automated_demo(firmware, bridge):
    """Run automated test sequence."""
    print("\n" + "="*60)
    print("VESPER QEMU Firmware - Automated Test")
    print("="*60)
    
    tests = [
        ("Identify device", "identify"),
        ("Get status", "status"),
        ("Read temperature", "get_temperature"),
        ("Read humidity", "get_humidity"),
        ("Turn on LED", "set_led"),
        ("Get all sensors", "get_sensors"),
        ("Turn off LED", "set_led"),
    ]
    
    passed = 0
    failed = 0
    
    for test_name, command in tests:
        print(f"\n[TEST] {test_name}...")
        try:
            if command == "set_led":
                if "on" in test_name.lower():
                    response = await bridge.set_led(True)
                else:
                    response = await bridge.set_led(False)
                success = response
            else:
                response = await bridge.send_command(command)
                success = response is not None
            
            if success:
                print(f"  ✓ PASS: {response}")
                passed += 1
            else:
                print(f"  ✗ FAIL: No response")
                failed += 1
                
        except Exception as e:
            print(f"  ✗ FAIL: {e}")
            failed += 1
    
    # Show final stats
    print("\n" + "-"*60)
    print(f"Results: {passed} passed, {failed} failed")
    print("\nBridge statistics:")
    for key, value in bridge.stats.items():
        print(f"  {key}: {value}")


async def main():
    parser = argparse.ArgumentParser(
        description="VESPER QEMU Firmware Simulation Demo"
    )
    parser.add_argument(
        "--firmware", "-f",
        help="Path to firmware ELF file",
    )
    parser.add_argument(
        "--simulated", "-s",
        action="store_true",
        help="Use simulated firmware (no QEMU required)",
    )
    parser.add_argument(
        "--auto", "-a",
        action="store_true",
        help="Run automated test instead of interactive mode",
    )
    parser.add_argument(
        "--board", "-b",
        default="lm3s6965evb",
        help="QEMU board type (default: lm3s6965evb)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Create event bus
    event_bus = EventBus()
    
    # Create firmware runner
    if args.simulated:
        logger.info("Using simulated firmware (no QEMU)")
        firmware = SimulatedFirmware()
    else:
        # Check for QEMU
        import shutil
        qemu_path = shutil.which("qemu-system-arm")
        if not qemu_path:
            logger.warning("QEMU not found, falling back to simulated mode")
            logger.info("Install QEMU with: brew install qemu (macOS) or apt install qemu-system-arm (Linux)")
            firmware = SimulatedFirmware()
        else:
            # Check for firmware
            if args.firmware and os.path.exists(args.firmware):
                firmware_path = args.firmware
            else:
                # Look for sample firmware
                sample_path = os.path.join(
                    PROJECT_ROOT, "vesper", "firmware", "samples", "sensor_firmware.elf"
                )
                if os.path.exists(sample_path):
                    firmware_path = sample_path
                else:
                    logger.warning("No firmware found, using simulated mode")
                    logger.info(f"Build sample firmware with: cd {PROJECT_ROOT}/vesper/firmware/samples && make")
                    firmware = SimulatedFirmware()
                    firmware_path = None
            
            if firmware_path:
                logger.info(f"Using QEMU with firmware: {firmware_path}")
                config = QEMUConfig(
                    board=BoardType.LM3S6965,
                    architecture=Architecture.ARM_CORTEX_M3,
                    firmware_path=firmware_path,
                    enable_serial=True,
                )
                firmware = QEMURunner(config)
    
    # Create bridge
    bridge_config = VesperBridgeConfig(
        device_id="qemu_sensor_1",
        device_type="temperature_humidity_sensor",
        room="test_room",
        protocol_mode=ProtocolMode.TEXT,
    )
    
    bridge = VesperFirmwareBridge(
        qemu_runner=firmware,
        event_bus=event_bus,
        config=bridge_config,
    )
    
    # Subscribe to firmware events
    def on_firmware_event(event):
        if event.data.get("key") not in ("STATUS",):  # Skip status messages
            logger.info(f"[EVENT] {event.data}")
    
    event_bus.subscribe("firmware.*", on_firmware_event)
    
    try:
        # Start bridge
        logger.info("Starting firmware bridge...")
        if not await bridge.start():
            logger.error("Failed to start bridge")
            return
        
        # Wait for firmware to boot
        await asyncio.sleep(1.0)
        
        # Run demo
        if args.auto:
            await run_automated_demo(firmware, bridge)
        else:
            await run_interactive_demo(firmware, bridge)
        
    finally:
        # Cleanup
        await bridge.stop()
        if hasattr(firmware, 'stop'):
            if asyncio.iscoroutinefunction(firmware.stop):
                await firmware.stop()
            else:
                firmware.stop()
    
    print("\nDemo complete.")


if __name__ == "__main__":
    asyncio.run(main())
