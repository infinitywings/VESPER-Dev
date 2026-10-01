# Geometric sensors and 3D integration

The geometric PIR model is `PIRMotionSensor` in `vesper.habitat.sensors.motion_sensor`; its configuration includes position, orientation, tilt, detection range, angle, sensitivity, cooldown and minimum tracked-motion speed. It can be tested without a 3D renderer:

```bash
python scripts/verify_claims.py --claim sensors
```

At each update, the model checks the target's distance and angle relative to the sensor's forward vector. If a previous position and positive time interval exist, it then checks estimated speed. Confidence and cooldown also affect detection. A first observation may trigger without velocity history; do not describe the speed threshold as unconditional.

`vesper/habitat/sensor_bridge.py` connects geometric sensor observations to software sensor state/event handling. Actual embodied motion requires the optional Habitat environment and assets. `vesper/devices/motion_sensor.py` is a separate, simpler proximity model; the offline `--demo` is not a validation of the geometric cone.

LOW and HIGH sensitivity set nominal range multipliers 0.7 and 1.2, respectively. Their ratio is 58.33%, but each level also changes angular and confidence parameters. These are generic model assumptions, not empirical PIR calibration.

For visual overlays, install the `visualization` extra. Numerical sensor imports do not require pygame. Use [Habitat setup](habitat.md) for optional scene execution.
