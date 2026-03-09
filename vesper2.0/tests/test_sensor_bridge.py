#!/usr/bin/env python3
"""
Quick test of 3D sensor bridge integration.

This script demonstrates how simulated firmware sensors are triggered
by 3D spatial events in the Habitat environment.
"""

import sys
from pathlib import Path

# Add project root
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import numpy as np
import time
import asyncio

from vesper.habitat.sensors import PIRMotionSensor, MotionSensorConfig, SensitivityLevel
from vesper.habitat.sensor_bridge import Sensor3DBridge, SensorBridgeConfig
from vesper.core.event_bus import EventBus


async def main():
    print("=" * 60)
    print("3D Sensor Bridge Integration Test")
    print("=" * 60)
    print()
    
    # Create event bus
    event_bus = EventBus()
    
    # Create 3D motion sensor (as it would exist in Habitat)
    motion_3d = PIRMotionSensor(MotionSensorConfig(
        device_id="living_room_motion_3d",
        position=np.array([5.0, 1.5, 3.0]),
        room="living_room",
        detection_range=5.0,
        detection_angle=90.0,
        orientation=0.0,
        sensitivity=SensitivityLevel.HIGH,
    ))
    
    # Create sensor bridge
    config = SensorBridgeConfig(
        enable_motion_sensors=True,
        enable_environmental=True,
        occupancy_temp_increase=1.5,
    )
    bridge = Sensor3DBridge(config, event_bus)
    
    # Add 3D sensor to bridge (creates firmware sensor automatically)
    fw_id = bridge.add_motion_sensor_3d(motion_3d, "living_room")
    print(f"✅ Created firmware sensor: {fw_id}")
    
    # Add environmental sensors
    bridge.add_environmental_sensors("living_room")
    print(f"✅ Created environmental sensors for living_room")
    print()
    
    # Subscribe to events
    events_received = []
    def on_event(event):
        events_received.append(event)
        print(f"📡 Event: {event.event_name} from {event.source_id}")
        print(f"   Payload: {event.payload}")
    
    event_bus.subscribe("sensor.*", on_event)
    
    # Start the bridge
    print("🚀 Starting firmware sensor network...")
    await bridge.start()
    
    stats = bridge.get_sensor_stats()
    print(f"   Total firmware sensors: {stats['total_firmware_sensors']}")
    print(f"   Motion sensors: {stats['motion_sensors']}")
    print(f"   Rooms: {stats['rooms_with_sensors']}")
    print()
    
    # Simulate humanoid movement
    print("👤 Simulating humanoid entering living room...")
    humanoid_pos = np.array([5.0, 0.0, 3.0])  # Near the sensor
    
    # Trigger 3D motion detection
    bridge.on_3d_motion_detected(
        motion_3d.config.device_id,
        humanoid_pos,
        "living_room",
    )
    
    # Let firmware sensors update
    print("⏳ Waiting for firmware sensor events...")
    await asyncio.sleep(2)
    
    print()
    print("📊 Room Occupancy:")
    stats = bridge.get_sensor_stats()
    for room, occupied in stats['room_occupancy'].items():
        status = "🟢 OCCUPIED" if occupied else "⚫ EMPTY"
        print(f"   {room}: {status}")
    
    print()
    print("🧪 Testing sensor commands...")
    
    # Send commands to firmware sensors
    response = bridge.send_command_to_sensor(fw_id, "GET_MOTION")
    print(f"   GET_MOTION → {response}")
    
    response = bridge.send_command_to_sensor(fw_id, "IDENTIFY")
    print(f"   IDENTIFY → {response}")
    
    # Check environmental sensors
    temp_sensor_id = "living_room_temp_fw"
    response = bridge.send_command_to_sensor(temp_sensor_id, "GET_TEMP")
    print(f"   GET_TEMP → {response}")
    
    print()
    print("⏳ Running for 5 more seconds to observe environmental changes...")
    await asyncio.sleep(5)
    
    # Check temp again after occupancy
    response = bridge.send_command_to_sensor(temp_sensor_id, "GET_TEMP")
    print(f"   GET_TEMP (after occupancy) → {response}")
    
    # Simulate leaving
    print()
    print("🚪 Simulating humanoid leaving living room...")
    bridge.on_3d_motion_cleared(motion_3d.config.device_id, "living_room")
    
    await asyncio.sleep(2)
    
    stats = bridge.get_sensor_stats()
    print()
    print("📊 Final Room Occupancy:")
    for room, occupied in stats['room_occupancy'].items():
        status = "🟢 OCCUPIED" if occupied else "⚫ EMPTY"
        print(f"   {room}: {status}")
    
    print()
    print(f"📬 Total events received: {len(events_received)}")
    
    # Stop bridge
    await bridge.stop()
    print()
    print("✅ Test complete!")


if __name__ == "__main__":
    asyncio.run(main())
