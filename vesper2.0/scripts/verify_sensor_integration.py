#!/usr/bin/env python3
"""
Quick verification that the 3D sensor integration is working.
"""

import sys
from pathlib import Path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

print("=" * 60)
print("VESPER 3D Sensor Integration - Status Check")
print("=" * 60)
print()

# Test imports
print("1. Testing imports...")
try:
    from vesper.habitat.sensor_bridge import Sensor3DBridge, SensorBridgeConfig
    print("   ✓ sensor_bridge module")
    from vesper.firmware import SensorNetwork, SensorType
    print("   ✓ firmware sensors module")
    from vesper.core.event_bus import EventBus
    print("   ✓ event bus module")
    print()
except Exception as e:
    print(f"   ✗ Import failed: {e}")
    sys.exit(1)

# Test bridge creation
print("2. Testing sensor bridge creation...")
try:
    config = SensorBridgeConfig(
        enable_motion_sensors=True,
        enable_environmental=True,
    )
    bridge = Sensor3DBridge(config)
    print("   ✓ Bridge created successfully")
    print()
except Exception as e:
    print(f"   ✗ Bridge creation failed: {e}")
    sys.exit(1)

# Test sensor network
print("3. Testing firmware sensor network...")
try:
    from vesper.firmware import SensorConfig
    
    # Add a test sensor
    test_config = SensorConfig(
        sensor_type=SensorType.MOTION,
        device_id="test_motion",
        location="test_room",
    )
    sensor = bridge.sensor_network.add_sensor(test_config)
    print(f"   ✓ Created firmware sensor: {sensor.config.device_id}")
    
    stats = bridge.get_sensor_stats()
    print(f"   ✓ Stats: {stats['total_firmware_sensors']} sensors")
    print()
except Exception as e:
    print(f"   ✗ Sensor network failed: {e}")
    sys.exit(1)

# Summary
print("=" * 60)
print("✅ All tests passed!")
print()
print("The 3D sensor integration is ready to use.")
print("Run: python scripts/vesper_objectnav_camera_humanoid.py")
print("=" * 60)
