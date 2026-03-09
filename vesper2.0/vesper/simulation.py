"""
Main simulation runner for Vesper.

Integrates all components into a unified simulation loop.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from vesper.config import Config, load_config
from vesper.core.event_bus import EventBus
from vesper.core.environment import Environment
from vesper.agents.controller import AgentController
from vesper.habitat.simulator import create_simulator, SimulatorConfig

logger = logging.getLogger(__name__)


@dataclass
class SimulationStats:
    """Simulation statistics."""
    ticks: int = 0
    elapsed_time: float = 0.0
    avg_tick_time: float = 0.0


class Simulation:
    """
    Main simulation runner.
    
    Coordinates:
    - Habitat-Sim for 3D environment
    - IoT devices and sensors
    - LLM-controlled agents
    - Event bus for communication
    
    Example:
        sim = Simulation()
        sim.initialize()
        sim.run(duration=60.0)  # Run for 60 seconds
        sim.close()
    """
    
    def __init__(self, config: Optional[Config] = None, config_path: Optional[str] = None):
        if config_path:
            self.config = load_config(config_path)
        else:
            self.config = config or Config()
        
        self.event_bus = EventBus(max_queue_size=self.config.event_bus.max_queue_size)
        self.environment = Environment(event_bus=self.event_bus)
        self.agent_controller = AgentController(event_bus=self.event_bus)
        self.simulator = None
        
        self._running = False
        self._stats = SimulationStats()
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def stats(self) -> SimulationStats:
        return self._stats
    
    def initialize(self, use_mock_sim: bool = True) -> bool:
        """Initialize all simulation components."""
        logger.info("Initializing Vesper simulation...")
        
        # Create simulator
        sim_config = SimulatorConfig(
            scene_path=self.config.environment.scene,
            render_mode="headless" if self.config.simulation.headless else "window",
        )
        self.simulator = create_simulator(sim_config, use_mock=use_mock_sim)
        self.simulator.initialize()
        
        # Set up devices from config
        self._setup_devices()
        
        logger.info("Simulation initialized")
        return True
    
    def _setup_devices(self) -> None:
        """Set up IoT devices from configuration."""
        from vesper.devices import MotionSensor, ContactSensor, SmartDoor, LightSensor
        
        device_configs = [
            ("motion_sensor", self.config.devices.motion_sensor, MotionSensor),
            ("contact_sensor", self.config.devices.contact_sensor, ContactSensor),
            ("smart_door", self.config.devices.smart_door, SmartDoor),
            ("light_sensor", self.config.devices.light_sensor, LightSensor),
        ]
        
        for name, dev_cfg, cls in device_configs:
            if dev_cfg.enabled:
                device = cls(event_bus=self.event_bus)
                self.environment.register_device(device)
                logger.debug(f"Registered {name}: {device.device_id}")
    
    def step(self, dt: Optional[float] = None) -> None:
        """Execute one simulation tick."""
        dt = dt or (1.0 / self.config.simulation.tick_rate)
        start = time.time()
        
        # Update simulator
        if self.simulator:
            self.simulator.step()
        
        # Update environment devices
        self.environment.tick(dt)
        
        # Update agents
        self.agent_controller.update(dt, self.environment)
        
        # Process events
        self.event_bus.process_events()
        
        self._stats.ticks += 1
        tick_time = time.time() - start
        self._stats.elapsed_time += tick_time
        self._stats.avg_tick_time = self._stats.elapsed_time / self._stats.ticks
    
    def run(self, duration: float = 10.0) -> None:
        """Run simulation for specified duration."""
        logger.info(f"Running simulation for {duration}s...")
        self._running = True
        
        dt = 1.0 / self.config.simulation.tick_rate
        end_time = time.time() + duration
        
        while self._running and time.time() < end_time:
            self.step(dt)
            time.sleep(max(0, dt - self._stats.avg_tick_time))
        
        self._running = False
        logger.info(f"Simulation complete: {self._stats.ticks} ticks")
    
    def stop(self) -> None:
        """Stop the simulation."""
        self._running = False
    
    def close(self) -> None:
        """Clean up all resources."""
        self.stop()
        self.agent_controller.destroy_all()
        if self.simulator:
            self.simulator.close()
        logger.info("Simulation closed")
    
    def __enter__(self) -> "Simulation":
        self.initialize()
        return self
    
    def __exit__(self, *args) -> None:
        self.close()
