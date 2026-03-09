# 3D Sensor Integration Guide

## Overview

The sensor bridge connects **3D spatial sensors** (PIR motion, cameras) in Habitat-sim with **simulated firmware sensors** (pure Python), creating a complete IoT simulation pipeline.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   Habitat 3D Environment                     │
│  ┌──────────────┐                    ┌──────────────┐       │
│  │  Humanoid    │─────────moves──────>│ 3D Motion   │       │
│  │  Avatar      │                    │  Sensors     │       │
│  └──────────────┘                    └───────┬──────┘       │
└────────────────────────────────────────────│────────────────┘
                                              │
                     3D spatial detection     │
                                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Sensor3DBridge                            │
│  • Maps 3D sensors to firmware sensors                      │
│  • Triggers firmware events from 3D events                  │
│  • Manages room occupancy state                             │
└───────────────────────────┬─────────────────────────────────┘
                            │
         triggers firmware  │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              Simulated Firmware Sensors                      │
│  ┌────────────┐  ┌───────────────┐  ┌──────────────┐       │
│  │  Motion    │  │  Temperature  │  │  Humidity    │       │
│  │  Sensor    │  │  Sensor       │  │  Sensor      │       │
│  └──────┬─────┘  └───────┬───────┘  └──────┬───────┘       │
└─────────│─────────────────│──────────────────│──────────────┘
          │                 │                  │
          └─────────────────┴──────────────────┘
                            │
               realistic sensor events
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    VESPER Event Bus                          │
│  • Collects all sensor events                               │
│  • Routes to subscribers                                     │
│  • Enables analytics, ML training, automation                │
└─────────────────────────────────────────────────────────────┘
```

## How It Works

### 1. Sensor Mapping

When you create the bridge, it automatically creates firmware sensors for each 3D sensor:

```python
# 3D motion sensor in living room (Habitat-sim)
motion_3d = PIRMotionSensor(...)

# Bridge creates corresponding firmware sensor
bridge.add_motion_sensor_3d(motion_3d, "living_room")
# → Creates "living_room_motion_3d_fw" firmware sensor

# Bridge also adds environmental sensors
bridge.add_environmental_sensors("living_room")
# → Creates "living_room_temp_fw" and "living_room_humidity_fw"
```

### 2. Event Flow

```python
# 1. Humanoid moves in 3D space
humanoid_position = (5.0, 0.0, 3.0)

# 2. 3D motion sensor detects via spatial geometry
detection = motion_3d.update({"humanoid": humanoid_position})

# 3. Bridge is notified
bridge.on_3d_motion_detected(
    sensor_3d_id="living_room_motion_3d",
    target_position=humanoid_position,
    room_name="living_room",
)

# 4. Bridge triggers firmware motion sensor
# → firmware_sensor._motion_detected = True

# 5. Bridge updates room occupancy
# → room_occupancy["living_room"] = True

# 6. Room occupancy affects environmental sensors
# → temperature increases by 1.5°C (body heat)
# → humidity increases by 5% (breathing)

# 7. Firmware sensors emit events
# → Event(type="sensor_motion", value=True)
# → Event(type="sensor_temperature", value=23.5)
# → Event(type="sensor_humidity", value=50.0)

# 8. Events published to VESPER event bus
# → Available for logging, analytics, automation
```

### 3. Sensor Types Created

For each room with 3D sensors, the bridge creates:

| 3D Sensor | Firmware Sensor | Trigger |
|-----------|----------------|---------|
| PIR Motion | Motion Sensor | 3D spatial detection |
| Camera | Light Sensor | Camera presence |
| (None) | Temperature | Room occupancy |
| (None) | Humidity | Room occupancy |

### 4. Occupancy Effects

When a room becomes occupied:
- **Temperature**: +1.5°C (configurable) - simulates body heat
- **Humidity**: +5% (configurable) - simulates breathing

When a room becomes empty:
- Temperature gradually returns to baseline
- Humidity gradually returns to baseline

## Usage in vesper_objectnav_camera_humanoid.py

The integration is already complete! The demo script:

1. **Creates 3D sensors** for each room (motion + camera)
2. **Creates sensor bridge** automatically
3. **Updates sensors** every frame with humanoid position
4. **Triggers bridge** when motion is detected/cleared

### Key Code Sections

```python
# In setup_sensors():
self.sensor_bridge = create_sensor_bridge_for_scene(
    motion_sensors_3d=self.motion_sensors,
    cameras_3d=self.security_cameras,
    room_sensor_state=self.room_sensor_state,
    event_bus=self.vesper.event_bus,
)
await self.sensor_bridge.start()

# In update_sensors():
if is_detecting and not was_detecting:
    # Motion detected!
    self.sensor_bridge.on_3d_motion_detected(
        sensor_id, target_position, room_name
    )
elif not is_detecting and was_detecting:
    # Motion cleared
    self.sensor_bridge.on_3d_motion_cleared(
        sensor_id, room_name
    )
```

## Event Bus Integration

All firmware sensor events are published to the VESPER event bus:

```python
# Subscribe to all sensor events
event_bus.subscribe("sensor_*", lambda event: 
    print(f"Sensor {event.source_id}: {event.payload}")
)

# Subscribe to specific rooms
event_bus.subscribe("sensor_motion", on_motion)
event_bus.subscribe("sensor_temperature", on_temperature)
```

### Event Format

```python
Event(
    event_type="sensor_motion",     # or sensor_temperature, etc.
    source_id="living_room_motion_3d_fw",
    payload={
        "key": "motion",            # sensor data key
        "value": True,              # sensor data value
        "room": "living_room",      # which room
        "device_id": "living_room_motion_3d_fw",
    },
    timestamp=1738533478.23,
    priority=EventPriority.NORMAL,
)
```

## Configuration

```python
config = SensorBridgeConfig(
    enable_motion_sensors=True,           # Create motion firmware sensors
    enable_cameras=True,                  # Create light firmware sensors
    enable_environmental=True,            # Create temp/humidity sensors
    
    temp_update_interval=30.0,           # Seconds between temp updates
    humidity_update_interval=60.0,       # Seconds between humidity updates
    
    base_temperature=22.0,               # Baseline room temp (°C)
    base_humidity=45.0,                  # Baseline room humidity (%)
    
    occupancy_temp_increase=1.5,         # Temp increase when occupied
    occupancy_humidity_increase=5.0,     # Humidity increase when occupied
)
```

## Benefits

### 1. **Realistic IoT Simulation**
- Temperature rises when room is occupied
- Humidity changes with breathing
- Motion sensors have realistic behavior (cooldown, sensitivity)

### 2. **No Hardware Required**
- Everything is simulated in Python
- No ESP32, no QEMU, no ARM toolchain needed
- Works on any platform

### 3. **Event-Driven Architecture**
- All sensor data flows through event bus
- Easy to add analytics, logging, automation
- Decoupled components

### 4. **ML Training Data**
- Generate realistic sensor event streams
- Label data with ground truth (humanoid position)
- Train occupancy detection models

### 5. **Testing & Development**
- Test automation logic without hardware
- Simulate edge cases (sensor failures, etc.)
- Rapid iteration

## Example: Running the Full System

```bash
# Run the ObjectNav demo with integrated sensors
python scripts/vesper_objectnav_camera_humanoid.py
```

What happens:
1. **3D environment loads** with rooms, furniture, humanoid
2. **Sensors are placed** at room corners automatically
3. **Firmware sensors created** for each 3D sensor
4. **Humanoid walks** through the house
5. **Motion sensors detect** the humanoid spatially
6. **Firmware sensors emit** realistic IoT events
7. **Events flow** to VESPER event bus
8. **You can subscribe** to events and build automations

## Statistics

Get runtime statistics:

```python
stats = bridge.get_sensor_stats()
print(stats)
# {
#     "total_firmware_sensors": 24,      # Total firmware sensors
#     "motion_sensors": 8,                # Motion sensors mapped
#     "cameras": 8,                       # Cameras mapped
#     "rooms_with_sensors": 8,            # Rooms covered
#     "room_occupancy": {                 # Current occupancy
#         "living_room": True,
#         "bedroom": False,
#         ...
#     }
# }
```

## Advanced: Custom Sensor Types

You can extend the bridge to support additional sensor types:

```python
# Add a door sensor
from vesper.firmware import SensorConfig, SensorType

door_config = SensorConfig(
    sensor_type=SensorType.DOOR_WINDOW,
    device_id="front_door_fw",
    location="entrance",
)
bridge.sensor_network.add_sensor(door_config)

# Trigger it from 3D events
if humanoid_near_door:
    door_sensor.handle_command("SIMULATE_OPEN")
```

## Troubleshooting

### No events received?
Check that event bus is connected:
```python
bridge = Sensor3DBridge(config, event_bus=your_event_bus)
```

### Temperature not changing?
Verify occupancy detection is working:
```python
stats = bridge.get_sensor_stats()
print(stats["room_occupancy"])
```

### Motion not triggering?
Check 3D sensor detection first:
```python
detection = motion_3d.update(targets, time.time())
if detection:
    print("3D sensor working!")
    bridge.on_3d_motion_detected(...)
```

## Next Steps

1. ✅ Run `python scripts/vesper_objectnav_camera_humanoid.py`
2. ✅ Watch sensors trigger as humanoid moves
3. ✅ Subscribe to events in your code
4. ✅ Build automations (turn on lights, etc.)
5. ✅ Collect data for ML training
