#!/usr/bin/env python3
"""
Simulated IoT Sensors Demo - NO HARDWARE REQUIRED!

This demo shows how VESPER can simulate realistic IoT sensor behavior
without any physical devices, QEMU, or firmware compilation.

Perfect for:
- Testing VESPER's event bus and device integration
- Generating realistic sensor data for ML training
- Development without any hardware

Usage:
    python scripts/simulated_sensors_demo.py                    # Run with default house sensors
    python scripts/simulated_sensors_demo.py --room kitchen     # Run kitchen sensors only
    python scripts/simulated_sensors_demo.py --interactive      # Interactive command mode
"""

import asyncio
import argparse
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from vesper.firmware.sensor_templates import (
    SensorNetwork,
    SensorConfig,
    SensorType,
    create_sensor,
    create_whole_house_sensors,
    create_living_room_sensors,
    create_bedroom_sensors,
    create_kitchen_sensors,
    create_bathroom_sensors,
)


def colorize(text: str, color: str) -> str:
    """Add ANSI color to text."""
    colors = {
        "red": "\033[91m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "blue": "\033[94m",
        "magenta": "\033[95m",
        "cyan": "\033[96m",
        "white": "\033[97m",
        "reset": "\033[0m",
    }
    return f"{colors.get(color, '')}{text}{colors['reset']}"


def format_sensor_data(device_id: str, key: str, value) -> str:
    """Format sensor data for display."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    
    # Color code by importance
    if key in ("motion", "leak", "alarm") and value:
        color = "red"
        icon = "🚨"
    elif key in ("state_change", "motion_start", "alert"):
        color = "yellow"
        icon = "⚡"
    elif key == "open" and value:
        color = "yellow"
        icon = "🚪"
    elif key == "temperature":
        color = "cyan"
        icon = "🌡️"
    elif key == "humidity":
        color = "blue"
        icon = "💧"
    elif key == "lux":
        color = "yellow"
        icon = "💡"
    elif key == "co2_ppm":
        color = "green"
        icon = "🌬️"
    elif key == "power_watts":
        color = "magenta"
        icon = "⚡"
    elif key == "hvac_state":
        icon = "❄️" if value == "cooling" else "🔥" if value == "heating" else "💤"
        color = "cyan"
    else:
        color = "white"
        icon = "📊"
    
    device_str = colorize(f"[{device_id}]", "green")
    key_str = colorize(key, color)
    
    return f"{timestamp} {icon} {device_str} {key_str}: {value}"


async def run_sensor_network(network: SensorNetwork, duration: float = 30.0):
    """Run the sensor network and display events."""
    print(colorize("\n🏠 Starting Simulated Sensor Network", "cyan"))
    print(colorize("=" * 50, "cyan"))
    print(f"Sensors: {len(network.sensors)}")
    for device_id, sensor in network.sensors.items():
        print(f"  • {device_id}: {sensor.config.sensor_type.value}")
    print(colorize("=" * 50, "cyan"))
    print()
    
    # Set up data callback
    def on_sensor_data(device_id: str, key: str, value):
        # Filter out non-eventful updates
        if key == "motion" and not value:
            return
        if key == "open" and not hasattr(on_sensor_data, f"last_{device_id}_{key}"):
            setattr(on_sensor_data, f"last_{device_id}_{key}", value)
            return
        if key == "open":
            last = getattr(on_sensor_data, f"last_{device_id}_{key}", None)
            if last == value:
                return
            setattr(on_sensor_data, f"last_{device_id}_{key}", value)
            
        print(format_sensor_data(device_id, key, value))
    
    network.on_sensor_data(on_sensor_data)
    
    # Start sensors
    await network.start()
    
    print(colorize(f"Running for {duration} seconds... (Ctrl+C to stop)\n", "yellow"))
    
    try:
        await asyncio.sleep(duration)
    except asyncio.CancelledError:
        pass
    finally:
        await network.stop()
        print(colorize("\n✅ Sensor network stopped", "green"))


async def interactive_mode(network: SensorNetwork):
    """Run in interactive mode with command input."""
    print(colorize("\n🎮 Interactive Sensor Control", "cyan"))
    print(colorize("=" * 50, "cyan"))
    print("Commands:")
    print("  list              - List all sensors")
    print("  <device> <cmd>    - Send command to device")
    print("  broadcast <cmd>   - Send command to all devices")
    print("  status            - Show all sensor statuses")
    print("  quit              - Exit")
    print()
    
    # Start sensors in background
    await network.start()
    
    # Set up minimal data callback (only alerts)
    def on_alert(device_id: str, key: str, value):
        if key in ("motion", "leak", "alarm", "state_change") and value:
            print(f"\n{format_sensor_data(device_id, key, value)}")
            print("> ", end="", flush=True)
    
    network.on_sensor_data(on_alert)
    
    try:
        while True:
            try:
                line = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("> ")
                )
            except EOFError:
                break
                
            line = line.strip()
            if not line:
                continue
                
            parts = line.split(maxsplit=2)
            cmd = parts[0].lower()
            
            if cmd == "quit" or cmd == "exit":
                break
            elif cmd == "list":
                for device_id, sensor in network.sensors.items():
                    print(f"  {device_id}: {sensor.config.sensor_type.value} @ {sensor.config.location}")
            elif cmd == "status":
                results = network.broadcast_command("STATUS")
                for device_id, result in results.items():
                    print(f"  {device_id}: {result}")
            elif cmd == "broadcast":
                if len(parts) < 2:
                    print("Usage: broadcast <command>")
                    continue
                results = network.broadcast_command(parts[1].upper())
                for device_id, result in results.items():
                    print(f"  {device_id}: {result}")
            else:
                # Assume it's a device command
                device_id = parts[0]
                if device_id not in network.sensors:
                    print(f"Unknown device: {device_id}")
                    print(f"Available: {', '.join(network.sensors.keys())}")
                    continue
                if len(parts) < 2:
                    print("Usage: <device> <command> [args]")
                    continue
                command = parts[1].upper()
                args = parts[2] if len(parts) > 2 else None
                result = network.send_command(device_id, command, args)
                print(f"  {result}")
                
    finally:
        await network.stop()
        print(colorize("\n✅ Interactive mode ended", "green"))


async def main():
    parser = argparse.ArgumentParser(
        description="Simulated IoT Sensors Demo - No hardware required!"
    )
    parser.add_argument(
        "--room",
        choices=["living_room", "bedroom", "kitchen", "bathroom", "all"],
        default="all",
        help="Which room's sensors to simulate"
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=30.0,
        help="How long to run (seconds)"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Run in interactive command mode"
    )
    parser.add_argument(
        "--single",
        choices=["motion", "temperature", "humidity", "door", "smoke", "thermostat", "plug"],
        help="Run a single sensor type"
    )
    
    args = parser.parse_args()
    
    # Create sensor network
    if args.single:
        # Single sensor mode
        sensor_map = {
            "motion": SensorType.MOTION,
            "temperature": SensorType.TEMPERATURE,
            "humidity": SensorType.HUMIDITY,
            "door": SensorType.DOOR_WINDOW,
            "smoke": SensorType.SMOKE,
            "thermostat": SensorType.THERMOSTAT,
            "plug": SensorType.SMART_PLUG,
        }
        network = SensorNetwork()
        network.add_sensor(SensorConfig(
            sensor_type=sensor_map[args.single],
            device_id=f"demo_{args.single}",
            location="demo_room",
            update_interval=1.0,
        ))
    elif args.room == "all":
        network = create_whole_house_sensors()
    else:
        network = SensorNetwork()
        room_creators = {
            "living_room": create_living_room_sensors,
            "bedroom": create_bedroom_sensors,
            "kitchen": create_kitchen_sensors,
            "bathroom": create_bathroom_sensors,
        }
        for config in room_creators[args.room]():
            network.add_sensor(config)
    
    # Run
    if args.interactive:
        await interactive_mode(network)
    else:
        await run_sensor_network(network, args.duration)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(colorize("\n\n👋 Goodbye!", "cyan"))
