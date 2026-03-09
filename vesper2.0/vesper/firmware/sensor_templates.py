"""
Sensor Firmware Templates for QEMU Simulation.

This module provides simulated firmware behavior for different sensor types
WITHOUT requiring actual C firmware compilation. The templates simulate
how real firmware would behave, making it easy to test VESPER integration.

No QEMU, ARM toolchain, or ESP32 required - pure Python simulation!
"""

import asyncio
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Callable, Any
import math


class SensorType(Enum):
    """Types of sensors that can be simulated."""
    MOTION = "motion"
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    DOOR_WINDOW = "door_window"
    LIGHT = "light"
    SMOKE = "smoke"
    CO2 = "co2"
    WATER_LEAK = "water_leak"
    PRESSURE = "pressure"
    SOUND = "sound"
    CAMERA = "camera"
    THERMOSTAT = "thermostat"
    SMART_PLUG = "smart_plug"
    MULTI_SENSOR = "multi_sensor"


@dataclass
class SensorConfig:
    """Configuration for a simulated sensor."""
    sensor_type: SensorType
    device_id: str
    location: str = "unknown"
    update_interval: float = 1.0  # seconds
    noise_level: float = 0.1  # Random variation
    
    # Type-specific settings
    min_value: float = 0.0
    max_value: float = 100.0
    initial_value: Optional[float] = None
    
    # Motion sensor specific
    motion_probability: float = 0.1  # Chance of detecting motion per update
    motion_cooldown: float = 5.0  # Seconds after motion before can trigger again
    
    # Door/window specific
    open_probability: float = 0.05  # Chance of state change
    
    # Thermostat specific
    target_temperature: float = 22.0
    hvac_mode: str = "auto"  # off, heat, cool, auto


class SimulatedSensor(ABC):
    """Base class for simulated sensor firmware."""
    
    def __init__(self, config: SensorConfig):
        self.config = config
        self.is_running = False
        self._callbacks: List[Callable[[str, Any], None]] = []
        self._last_update = 0.0
        self._current_value: Any = None
        
    def on_data(self, callback: Callable[[str, Any], None]):
        """Register callback for sensor data."""
        self._callbacks.append(callback)
        
    def _emit(self, key: str, value: Any):
        """Emit sensor data to all callbacks."""
        for cb in self._callbacks:
            try:
                cb(key, value)
            except Exception as e:
                print(f"Callback error: {e}")
                
    @abstractmethod
    async def update(self) -> Dict[str, Any]:
        """Update sensor state and return current values."""
        pass
    
    async def run(self):
        """Run the sensor simulation loop."""
        self.is_running = True
        while self.is_running:
            try:
                data = await self.update()
                for key, value in data.items():
                    self._emit(key, value)
            except Exception as e:
                print(f"Sensor {self.config.device_id} error: {e}")
            await asyncio.sleep(self.config.update_interval)
            
    def stop(self):
        """Stop the sensor simulation."""
        self.is_running = False
        
    def handle_command(self, command: str, args: Optional[str] = None) -> str:
        """Handle incoming command. Override in subclasses."""
        if command == "IDENTIFY":
            return f"ID:{self.config.device_id},TYPE:{self.config.sensor_type.value},LOC:{self.config.location}"
        elif command == "STATUS":
            return "STATUS:OK"
        elif command == "GET_CONFIG":
            return f"INTERVAL:{self.config.update_interval},NOISE:{self.config.noise_level}"
        return "ERR:UNKNOWN_CMD"


class MotionSensor(SimulatedSensor):
    """Simulated PIR motion sensor."""
    
    def __init__(self, config: SensorConfig):
        super().__init__(config)
        self._last_motion = 0.0
        self._motion_detected = False
        
    async def update(self) -> Dict[str, Any]:
        now = time.time()
        
        # Check cooldown
        if now - self._last_motion < self.config.motion_cooldown:
            if self._motion_detected:
                # Motion just ended
                self._motion_detected = False
                return {"motion": False, "motion_end": True}
            return {}
        
        # Random motion detection
        if random.random() < self.config.motion_probability:
            self._motion_detected = True
            self._last_motion = now
            return {"motion": True, "motion_start": True}
            
        return {"motion": False}
    
    def handle_command(self, command: str, args: Optional[str] = None) -> str:
        if command == "GET_MOTION":
            return f"MOTION:{1 if self._motion_detected else 0}"
        elif command == "SET_SENSITIVITY":
            if args:
                try:
                    self.config.motion_probability = float(args) / 100.0
                    return "ACK:SET_SENSITIVITY"
                except ValueError:
                    return "ERR:INVALID_VALUE"
        return super().handle_command(command, args)


class TemperatureSensor(SimulatedSensor):
    """Simulated temperature sensor (DHT22, DS18B20, etc.)."""
    
    def __init__(self, config: SensorConfig):
        super().__init__(config)
        # Start at a reasonable room temperature
        self._temperature = config.initial_value or 22.0
        self._trend = 0.0  # Slow drift
        
    async def update(self) -> Dict[str, Any]:
        # Add slow drift
        self._trend += random.uniform(-0.01, 0.01)
        self._trend = max(-0.1, min(0.1, self._trend))
        
        # Add noise and drift
        noise = random.gauss(0, self.config.noise_level)
        self._temperature += self._trend + noise
        
        # Clamp to range
        self._temperature = max(self.config.min_value, 
                                min(self.config.max_value, self._temperature))
        
        return {"temperature": round(self._temperature, 1)}
    
    def handle_command(self, command: str, args: Optional[str] = None) -> str:
        if command == "GET_TEMP":
            return f"TEMP:{self._temperature:.1f}"
        elif command == "SET_OFFSET":
            if args:
                try:
                    offset = float(args)
                    self._temperature += offset
                    return "ACK:SET_OFFSET"
                except ValueError:
                    return "ERR:INVALID_VALUE"
        return super().handle_command(command, args)


class HumiditySensor(SimulatedSensor):
    """Simulated humidity sensor."""
    
    def __init__(self, config: SensorConfig):
        super().__init__(config)
        self._humidity = config.initial_value or 45.0
        
    async def update(self) -> Dict[str, Any]:
        # Humidity changes slowly
        change = random.gauss(0, self.config.noise_level * 0.5)
        self._humidity += change
        self._humidity = max(20, min(80, self._humidity))
        
        return {"humidity": round(self._humidity, 1)}
    
    def handle_command(self, command: str, args: Optional[str] = None) -> str:
        if command == "GET_HUMIDITY":
            return f"HUMIDITY:{self._humidity:.1f}"
        return super().handle_command(command, args)


class DoorWindowSensor(SimulatedSensor):
    """Simulated door/window contact sensor."""
    
    def __init__(self, config: SensorConfig):
        super().__init__(config)
        self._is_open = False
        self._last_change = 0.0
        
    async def update(self) -> Dict[str, Any]:
        # Random state changes
        if random.random() < self.config.open_probability:
            self._is_open = not self._is_open
            self._last_change = time.time()
            return {
                "open": self._is_open,
                "state_change": True,
                "state": "open" if self._is_open else "closed"
            }
        return {"open": self._is_open}
    
    def handle_command(self, command: str, args: Optional[str] = None) -> str:
        if command == "GET_STATE":
            return f"STATE:{'OPEN' if self._is_open else 'CLOSED'}"
        elif command == "SIMULATE_OPEN":
            self._is_open = True
            return "ACK:SIMULATE_OPEN"
        elif command == "SIMULATE_CLOSE":
            self._is_open = False
            return "ACK:SIMULATE_CLOSE"
        return super().handle_command(command, args)


class LightSensor(SimulatedSensor):
    """Simulated ambient light sensor (lux)."""
    
    def __init__(self, config: SensorConfig):
        super().__init__(config)
        self._lux = config.initial_value or 300.0  # Indoor lighting
        self._time_of_day_factor = 1.0
        
    async def update(self) -> Dict[str, Any]:
        # Simulate day/night cycle influence
        hour = time.localtime().tm_hour
        if 6 <= hour < 8:  # Sunrise
            self._time_of_day_factor = 0.3 + (hour - 6) * 0.35
        elif 8 <= hour < 18:  # Daytime
            self._time_of_day_factor = 1.0
        elif 18 <= hour < 20:  # Sunset
            self._time_of_day_factor = 1.0 - (hour - 18) * 0.35
        else:  # Night
            self._time_of_day_factor = 0.1
            
        base_lux = 500 * self._time_of_day_factor
        noise = random.gauss(0, self.config.noise_level * 50)
        self._lux = max(0, base_lux + noise)
        
        return {"lux": round(self._lux, 0), "light_level": self._categorize_light()}
    
    def _categorize_light(self) -> str:
        if self._lux < 10:
            return "dark"
        elif self._lux < 100:
            return "dim"
        elif self._lux < 500:
            return "normal"
        else:
            return "bright"
    
    def handle_command(self, command: str, args: Optional[str] = None) -> str:
        if command == "GET_LUX":
            return f"LUX:{self._lux:.0f}"
        elif command == "GET_LEVEL":
            return f"LEVEL:{self._categorize_light().upper()}"
        return super().handle_command(command, args)


class SmokeSensor(SimulatedSensor):
    """Simulated smoke/fire detector."""
    
    def __init__(self, config: SensorConfig):
        super().__init__(config)
        self._smoke_level = 0.0  # 0-100
        self._alarm_threshold = 50.0
        self._alarm_active = False
        
    async def update(self) -> Dict[str, Any]:
        # Very low chance of smoke
        if random.random() < 0.001:
            # Smoke event!
            self._smoke_level = random.uniform(30, 80)
        else:
            # Decay back to normal
            self._smoke_level = max(0, self._smoke_level - 1)
            
        alarm = self._smoke_level > self._alarm_threshold
        if alarm != self._alarm_active:
            self._alarm_active = alarm
            return {
                "smoke_level": round(self._smoke_level, 1),
                "alarm": alarm,
                "alarm_triggered": alarm
            }
            
        return {"smoke_level": round(self._smoke_level, 1), "alarm": alarm}
    
    def handle_command(self, command: str, args: Optional[str] = None) -> str:
        if command == "GET_SMOKE":
            return f"SMOKE:{self._smoke_level:.1f}"
        elif command == "TEST_ALARM":
            self._alarm_active = True
            return "ACK:TEST_ALARM"
        elif command == "SILENCE":
            self._alarm_active = False
            return "ACK:SILENCE"
        return super().handle_command(command, args)


class CO2Sensor(SimulatedSensor):
    """Simulated CO2 sensor (ppm)."""
    
    def __init__(self, config: SensorConfig):
        super().__init__(config)
        self._co2_ppm = config.initial_value or 400.0  # Normal outdoor level
        
    async def update(self) -> Dict[str, Any]:
        # CO2 fluctuates with occupancy simulation
        change = random.gauss(0, self.config.noise_level * 20)
        self._co2_ppm += change
        
        # Keep in realistic indoor range
        self._co2_ppm = max(350, min(2000, self._co2_ppm))
        
        # Categorize air quality
        if self._co2_ppm < 800:
            quality = "good"
        elif self._co2_ppm < 1200:
            quality = "moderate"
        else:
            quality = "poor"
            
        return {
            "co2_ppm": round(self._co2_ppm, 0),
            "air_quality": quality
        }
    
    def handle_command(self, command: str, args: Optional[str] = None) -> str:
        if command == "GET_CO2":
            return f"CO2:{self._co2_ppm:.0f}"
        elif command == "CALIBRATE":
            self._co2_ppm = 400  # Reset to baseline
            return "ACK:CALIBRATE"
        return super().handle_command(command, args)


class WaterLeakSensor(SimulatedSensor):
    """Simulated water leak detector."""
    
    def __init__(self, config: SensorConfig):
        super().__init__(config)
        self._leak_detected = False
        self._moisture_level = 0.0
        
    async def update(self) -> Dict[str, Any]:
        # Very rare leak events
        if not self._leak_detected and random.random() < 0.0001:
            self._leak_detected = True
            self._moisture_level = random.uniform(50, 100)
            return {
                "leak": True,
                "moisture": round(self._moisture_level, 1),
                "alert": True
            }
        elif self._leak_detected:
            # Leak persists until cleared
            return {"leak": True, "moisture": round(self._moisture_level, 1)}
            
        return {"leak": False, "moisture": 0.0}
    
    def handle_command(self, command: str, args: Optional[str] = None) -> str:
        if command == "GET_LEAK":
            return f"LEAK:{1 if self._leak_detected else 0}"
        elif command == "CLEAR_ALERT":
            self._leak_detected = False
            self._moisture_level = 0.0
            return "ACK:CLEAR_ALERT"
        elif command == "SIMULATE_LEAK":
            self._leak_detected = True
            self._moisture_level = 75.0
            return "ACK:SIMULATE_LEAK"
        return super().handle_command(command, args)


class Thermostat(SimulatedSensor):
    """Simulated smart thermostat."""
    
    def __init__(self, config: SensorConfig):
        super().__init__(config)
        self._current_temp = config.initial_value or 22.0
        self._target_temp = config.target_temperature
        self._mode = config.hvac_mode  # off, heat, cool, auto
        self._hvac_state = "idle"  # idle, heating, cooling
        
    async def update(self) -> Dict[str, Any]:
        # Simulate temperature changes based on HVAC
        if self._mode == "off":
            # Drift toward ambient (20°C)
            self._current_temp += (20 - self._current_temp) * 0.01
        elif self._mode in ("heat", "auto") and self._current_temp < self._target_temp - 0.5:
            self._hvac_state = "heating"
            self._current_temp += 0.1
        elif self._mode in ("cool", "auto") and self._current_temp > self._target_temp + 0.5:
            self._hvac_state = "cooling"
            self._current_temp -= 0.1
        else:
            self._hvac_state = "idle"
            
        # Add noise
        self._current_temp += random.gauss(0, 0.05)
        
        return {
            "current_temp": round(self._current_temp, 1),
            "target_temp": self._target_temp,
            "mode": self._mode,
            "hvac_state": self._hvac_state
        }
    
    def handle_command(self, command: str, args: Optional[str] = None) -> str:
        if command == "GET_TEMP":
            return f"TEMP:{self._current_temp:.1f}"
        elif command == "GET_TARGET":
            return f"TARGET:{self._target_temp:.1f}"
        elif command == "SET_TARGET":
            if args:
                try:
                    self._target_temp = float(args)
                    return f"ACK:SET_TARGET:{self._target_temp}"
                except ValueError:
                    return "ERR:INVALID_VALUE"
        elif command == "SET_MODE":
            if args and args.lower() in ("off", "heat", "cool", "auto"):
                self._mode = args.lower()
                return f"ACK:SET_MODE:{self._mode}"
            return "ERR:INVALID_MODE"
        elif command == "GET_STATE":
            return f"STATE:{self._hvac_state.upper()}"
        return super().handle_command(command, args)


class SmartPlug(SimulatedSensor):
    """Simulated smart plug with power monitoring."""
    
    def __init__(self, config: SensorConfig):
        super().__init__(config)
        self._is_on = False
        self._power_watts = 0.0
        self._energy_kwh = 0.0
        self._last_update = time.time()
        
    async def update(self) -> Dict[str, Any]:
        now = time.time()
        elapsed = now - self._last_update
        self._last_update = now
        
        if self._is_on:
            # Simulate power consumption
            base_power = random.uniform(50, 150)  # Watts
            self._power_watts = base_power + random.gauss(0, 5)
            
            # Accumulate energy
            self._energy_kwh += (self._power_watts * elapsed) / 3600000
        else:
            self._power_watts = random.uniform(0.1, 0.5)  # Standby power
            
        return {
            "on": self._is_on,
            "power_watts": round(self._power_watts, 1),
            "energy_kwh": round(self._energy_kwh, 3)
        }
    
    def handle_command(self, command: str, args: Optional[str] = None) -> str:
        if command == "GET_STATE":
            return f"STATE:{'ON' if self._is_on else 'OFF'}"
        elif command == "SET_ON" or command == "ON":
            self._is_on = True
            return "ACK:ON"
        elif command == "SET_OFF" or command == "OFF":
            self._is_on = False
            return "ACK:OFF"
        elif command == "TOGGLE":
            self._is_on = not self._is_on
            return f"ACK:{'ON' if self._is_on else 'OFF'}"
        elif command == "GET_POWER":
            return f"POWER:{self._power_watts:.1f}"
        elif command == "GET_ENERGY":
            return f"ENERGY:{self._energy_kwh:.3f}"
        elif command == "RESET_ENERGY":
            self._energy_kwh = 0.0
            return "ACK:RESET_ENERGY"
        return super().handle_command(command, args)


class MultiSensor(SimulatedSensor):
    """Simulated multi-sensor (motion + temperature + humidity + light)."""
    
    def __init__(self, config: SensorConfig):
        super().__init__(config)
        self._motion = False
        self._motion_last = 0.0
        self._temperature = 22.0
        self._humidity = 45.0
        self._lux = 300.0
        
    async def update(self) -> Dict[str, Any]:
        now = time.time()
        
        # Motion
        if now - self._motion_last > self.config.motion_cooldown:
            if random.random() < self.config.motion_probability:
                self._motion = True
                self._motion_last = now
            else:
                self._motion = False
        elif now - self._motion_last > 2:
            self._motion = False
            
        # Temperature
        self._temperature += random.gauss(0, 0.05)
        self._temperature = max(15, min(30, self._temperature))
        
        # Humidity
        self._humidity += random.gauss(0, 0.2)
        self._humidity = max(20, min(80, self._humidity))
        
        # Light
        self._lux += random.gauss(0, 10)
        self._lux = max(0, min(1000, self._lux))
        
        return {
            "motion": self._motion,
            "temperature": round(self._temperature, 1),
            "humidity": round(self._humidity, 1),
            "lux": round(self._lux, 0)
        }
    
    def handle_command(self, command: str, args: Optional[str] = None) -> str:
        if command == "GET_ALL":
            return f"MOTION:{1 if self._motion else 0},TEMP:{self._temperature:.1f},HUM:{self._humidity:.1f},LUX:{self._lux:.0f}"
        elif command == "GET_MOTION":
            return f"MOTION:{1 if self._motion else 0}"
        elif command == "GET_TEMP":
            return f"TEMP:{self._temperature:.1f}"
        elif command == "GET_HUMIDITY":
            return f"HUMIDITY:{self._humidity:.1f}"
        elif command == "GET_LUX":
            return f"LUX:{self._lux:.0f}"
        return super().handle_command(command, args)


# Factory function
def create_sensor(config: SensorConfig) -> SimulatedSensor:
    """Create a simulated sensor based on config."""
    sensor_classes = {
        SensorType.MOTION: MotionSensor,
        SensorType.TEMPERATURE: TemperatureSensor,
        SensorType.HUMIDITY: HumiditySensor,
        SensorType.DOOR_WINDOW: DoorWindowSensor,
        SensorType.LIGHT: LightSensor,
        SensorType.SMOKE: SmokeSensor,
        SensorType.CO2: CO2Sensor,
        SensorType.WATER_LEAK: WaterLeakSensor,
        SensorType.THERMOSTAT: Thermostat,
        SensorType.SMART_PLUG: SmartPlug,
        SensorType.MULTI_SENSOR: MultiSensor,
    }
    
    sensor_class = sensor_classes.get(config.sensor_type)
    if sensor_class is None:
        raise ValueError(f"Unknown sensor type: {config.sensor_type}")
        
    return sensor_class(config)


class SensorNetwork:
    """Manage a network of simulated sensors."""
    
    def __init__(self):
        self.sensors: Dict[str, SimulatedSensor] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._global_callbacks: List[Callable[[str, str, Any], None]] = []
        
    def add_sensor(self, config: SensorConfig) -> SimulatedSensor:
        """Add a sensor to the network."""
        sensor = create_sensor(config)
        self.sensors[config.device_id] = sensor
        
        # Wire up to global callbacks
        def on_data(key: str, value: Any):
            for cb in self._global_callbacks:
                cb(config.device_id, key, value)
        sensor.on_data(on_data)
        
        return sensor
    
    def on_sensor_data(self, callback: Callable[[str, str, Any], None]):
        """Register callback for all sensor data. Args: (device_id, key, value)"""
        self._global_callbacks.append(callback)
        
    async def start(self):
        """Start all sensors."""
        for device_id, sensor in self.sensors.items():
            task = asyncio.create_task(sensor.run())
            self._tasks[device_id] = task
            
    async def stop(self):
        """Stop all sensors."""
        for sensor in self.sensors.values():
            sensor.stop()
        for task in self._tasks.values():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        
    def send_command(self, device_id: str, command: str, args: Optional[str] = None) -> str:
        """Send command to a specific sensor."""
        sensor = self.sensors.get(device_id)
        if sensor is None:
            return f"ERR:UNKNOWN_DEVICE:{device_id}"
        return sensor.handle_command(command, args)
    
    def broadcast_command(self, command: str, args: Optional[str] = None) -> Dict[str, str]:
        """Send command to all sensors."""
        results = {}
        for device_id, sensor in self.sensors.items():
            results[device_id] = sensor.handle_command(command, args)
        return results


# Preset configurations for common room setups
def create_living_room_sensors(room_id: str = "living_room") -> List[SensorConfig]:
    """Create sensor configs for a typical living room."""
    return [
        SensorConfig(
            sensor_type=SensorType.MULTI_SENSOR,
            device_id=f"{room_id}_multi",
            location=room_id,
            update_interval=1.0,
            motion_probability=0.15,
        ),
        SensorConfig(
            sensor_type=SensorType.SMART_PLUG,
            device_id=f"{room_id}_tv_plug",
            location=room_id,
            update_interval=5.0,
        ),
        SensorConfig(
            sensor_type=SensorType.LIGHT,
            device_id=f"{room_id}_light",
            location=room_id,
            update_interval=2.0,
        ),
    ]


def create_bedroom_sensors(room_id: str = "bedroom") -> List[SensorConfig]:
    """Create sensor configs for a bedroom."""
    return [
        SensorConfig(
            sensor_type=SensorType.MOTION,
            device_id=f"{room_id}_motion",
            location=room_id,
            update_interval=0.5,
            motion_probability=0.05,
            motion_cooldown=10.0,
        ),
        SensorConfig(
            sensor_type=SensorType.TEMPERATURE,
            device_id=f"{room_id}_temp",
            location=room_id,
            update_interval=30.0,
            initial_value=20.0,
        ),
        SensorConfig(
            sensor_type=SensorType.DOOR_WINDOW,
            device_id=f"{room_id}_window",
            location=room_id,
            update_interval=1.0,
            open_probability=0.01,
        ),
    ]


def create_kitchen_sensors(room_id: str = "kitchen") -> List[SensorConfig]:
    """Create sensor configs for a kitchen."""
    return [
        SensorConfig(
            sensor_type=SensorType.MOTION,
            device_id=f"{room_id}_motion",
            location=room_id,
            motion_probability=0.2,
        ),
        SensorConfig(
            sensor_type=SensorType.SMOKE,
            device_id=f"{room_id}_smoke",
            location=room_id,
            update_interval=1.0,
        ),
        SensorConfig(
            sensor_type=SensorType.TEMPERATURE,
            device_id=f"{room_id}_temp",
            location=room_id,
            update_interval=10.0,
            initial_value=23.0,
            max_value=40.0,  # Kitchen can get hot
        ),
        SensorConfig(
            sensor_type=SensorType.WATER_LEAK,
            device_id=f"{room_id}_leak",
            location=room_id,
            update_interval=5.0,
        ),
    ]


def create_bathroom_sensors(room_id: str = "bathroom") -> List[SensorConfig]:
    """Create sensor configs for a bathroom."""
    return [
        SensorConfig(
            sensor_type=SensorType.MOTION,
            device_id=f"{room_id}_motion",
            location=room_id,
            motion_probability=0.1,
            motion_cooldown=30.0,
        ),
        SensorConfig(
            sensor_type=SensorType.HUMIDITY,
            device_id=f"{room_id}_humidity",
            location=room_id,
            update_interval=10.0,
            initial_value=50.0,
        ),
        SensorConfig(
            sensor_type=SensorType.WATER_LEAK,
            device_id=f"{room_id}_leak",
            location=room_id,
            update_interval=5.0,
        ),
    ]


def create_whole_house_sensors() -> SensorNetwork:
    """Create a complete sensor network for a typical house."""
    network = SensorNetwork()
    
    # Living room
    for config in create_living_room_sensors():
        network.add_sensor(config)
        
    # Bedroom
    for config in create_bedroom_sensors():
        network.add_sensor(config)
        
    # Kitchen
    for config in create_kitchen_sensors():
        network.add_sensor(config)
        
    # Bathroom
    for config in create_bathroom_sensors():
        network.add_sensor(config)
        
    # Thermostat (house-wide)
    network.add_sensor(SensorConfig(
        sensor_type=SensorType.THERMOSTAT,
        device_id="thermostat",
        location="hallway",
        update_interval=5.0,
        target_temperature=22.0,
    ))
    
    # Front door
    network.add_sensor(SensorConfig(
        sensor_type=SensorType.DOOR_WINDOW,
        device_id="front_door",
        location="entrance",
        update_interval=0.5,
        open_probability=0.02,
    ))
    
    return network
