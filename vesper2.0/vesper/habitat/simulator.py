"""
Habitat-Sim simulator wrapper.

Provides a unified interface for the Habitat-Sim simulator.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING
from enum import Enum

# Conditional import for Habitat-Sim
try:
    import habitat_sim
    from habitat_sim import Simulator, Configuration, Agent, AgentConfiguration
    from habitat_sim.agent import AgentState
    from habitat_sim.utils.common import quat_from_angle_axis
    import numpy as np
    HABITAT_AVAILABLE = True
except ImportError:
    HABITAT_AVAILABLE = False
    habitat_sim = None
    np = None

from vesper.core.event_bus import EventBus
from vesper.core.environment import Environment


logger = logging.getLogger(__name__)


class RenderMode(str, Enum):
    """Rendering mode for the simulator."""
    HEADLESS = "headless"
    WINDOW = "window"
    OFFSCREEN = "offscreen"


@dataclass
class SimulatorConfig:
    """Configuration for Habitat-Sim simulator."""
    # Scene configuration
    scene_path: str = ""
    scene_dataset: str = "hssd-hab"
    
    # Rendering
    render_mode: RenderMode = RenderMode.HEADLESS
    width: int = 640
    height: int = 480
    enable_physics: bool = True
    physics_timestep: float = 1.0 / 60.0
    
    # Agent default config
    default_agent_height: float = 1.5
    default_agent_radius: float = 0.1
    default_sensor_height: float = 1.5
    
    # Performance
    frustum_culling: bool = True
    enable_hdr: bool = False
    
    # Paths
    data_path: str = "./data"


class HabitatSimulator:
    """
    Wrapper for Habitat-Sim simulator.
    
    Provides:
    - Scene loading and management
    - Physics simulation
    - Agent spawning and control
    - Sensor observations (RGB, depth, semantic)
    - Integration with Vesper event bus
    
    Example:
        config = SimulatorConfig(
            scene_path="data/scenes/house.glb",
            render_mode=RenderMode.HEADLESS,
        )
        
        sim = HabitatSimulator(config)
        sim.initialize()
        
        # Add an agent
        agent_id = sim.add_agent(position=(0, 0, 0))
        
        # Step simulation
        observations = sim.step()
        
        # Get RGB observation
        rgb = observations[agent_id]["rgb"]
    """
    
    def __init__(
        self,
        config: Optional[SimulatorConfig] = None,
        event_bus: Optional[EventBus] = None,
        environment: Optional[Environment] = None,
    ):
        """
        Initialize the simulator wrapper.
        
        Args:
            config: Simulator configuration
            event_bus: Event bus for IoT integration
            environment: Vesper environment for device management
        """
        self.config = config or SimulatorConfig()
        self._event_bus = event_bus
        self._environment = environment
        
        self._sim: Optional["Simulator"] = None
        self._agents: Dict[int, Dict[str, Any]] = {}
        self._is_initialized = False
        
        self._stats = {
            "steps": 0,
            "total_time": 0.0,
            "physics_steps": 0,
        }
        
        if not HABITAT_AVAILABLE:
            logger.warning(
                "Habitat-Sim not installed. Install with: "
                "conda install habitat-sim -c conda-forge -c aihabitat"
            )
    
    @property
    def is_available(self) -> bool:
        """Check if Habitat-Sim is available."""
        return HABITAT_AVAILABLE
    
    @property
    def is_initialized(self) -> bool:
        """Check if simulator is initialized."""
        return self._is_initialized
    
    @property
    def stats(self) -> Dict[str, Any]:
        """Simulator statistics."""
        return self._stats.copy()
    
    def initialize(self) -> bool:
        """
        Initialize the Habitat-Sim simulator.
        
        Returns:
            True if initialization successful
        """
        if not HABITAT_AVAILABLE:
            logger.error("Cannot initialize: Habitat-Sim not installed")
            return False
        
        if self._is_initialized:
            logger.warning("Simulator already initialized")
            return True
        
        try:
            # Create simulator configuration
            sim_cfg = self._create_sim_config()
            
            # Create simulator
            self._sim = habitat_sim.Simulator(sim_cfg)
            
            self._is_initialized = True
            logger.info(f"Habitat-Sim initialized with scene: {self.config.scene_path}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize Habitat-Sim: {e}")
            return False
    
    def _create_sim_config(self) -> "Configuration":
        """Create Habitat-Sim configuration object."""
        # Backend configuration
        backend_cfg = habitat_sim.SimulatorConfiguration()
        backend_cfg.scene_id = self.config.scene_path
        backend_cfg.enable_physics = self.config.enable_physics
        backend_cfg.physics_config_file = "./data/default.physics_config.json"
        backend_cfg.frustum_culling = self.config.frustum_culling
        
        if self.config.render_mode == RenderMode.HEADLESS:
            backend_cfg.create_renderer = False
        
        # Default agent configuration
        agent_cfg = self._create_agent_config()
        
        # Combined configuration
        cfg = habitat_sim.Configuration(backend_cfg, [agent_cfg])
        
        return cfg
    
    def _create_agent_config(
        self,
        height: Optional[float] = None,
        radius: Optional[float] = None,
    ) -> "AgentConfiguration":
        """Create agent configuration."""
        agent_cfg = habitat_sim.agent.AgentConfiguration()
        agent_cfg.height = height or self.config.default_agent_height
        agent_cfg.radius = radius or self.config.default_agent_radius
        
        # Configure sensors
        sensor_specs = []
        
        # RGB camera
        rgb_spec = habitat_sim.CameraSensorSpec()
        rgb_spec.uuid = "rgb"
        rgb_spec.sensor_type = habitat_sim.SensorType.COLOR
        rgb_spec.resolution = [self.config.height, self.config.width]
        rgb_spec.position = [0.0, self.config.default_sensor_height, 0.0]
        sensor_specs.append(rgb_spec)
        
        # Depth camera
        depth_spec = habitat_sim.CameraSensorSpec()
        depth_spec.uuid = "depth"
        depth_spec.sensor_type = habitat_sim.SensorType.DEPTH
        depth_spec.resolution = [self.config.height, self.config.width]
        depth_spec.position = [0.0, self.config.default_sensor_height, 0.0]
        sensor_specs.append(depth_spec)
        
        agent_cfg.sensor_specifications = sensor_specs
        
        # Action space
        agent_cfg.action_space = {
            "move_forward": habitat_sim.agent.ActionSpec(
                "move_forward", habitat_sim.agent.ActuationSpec(amount=0.25)
            ),
            "turn_left": habitat_sim.agent.ActionSpec(
                "turn_left", habitat_sim.agent.ActuationSpec(amount=10.0)
            ),
            "turn_right": habitat_sim.agent.ActionSpec(
                "turn_right", habitat_sim.agent.ActuationSpec(amount=10.0)
            ),
            "look_up": habitat_sim.agent.ActionSpec(
                "look_up", habitat_sim.agent.ActuationSpec(amount=10.0)
            ),
            "look_down": habitat_sim.agent.ActionSpec(
                "look_down", habitat_sim.agent.ActuationSpec(amount=10.0)
            ),
        }
        
        return agent_cfg
    
    def add_agent(
        self,
        position: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        rotation: float = 0.0,
        height: Optional[float] = None,
    ) -> int:
        """
        Add an agent to the simulation.
        
        Args:
            position: Initial position (x, y, z)
            rotation: Initial rotation in degrees
            height: Agent height (uses default if None)
            
        Returns:
            Agent ID
        """
        if not self._is_initialized:
            raise RuntimeError("Simulator not initialized")
        
        agent_id = len(self._agents)
        
        # Get agent from simulator
        agent = self._sim.get_agent(agent_id)
        
        # Set initial state
        agent_state = agent.get_state()
        agent_state.position = np.array(position)
        agent_state.rotation = quat_from_angle_axis(
            np.radians(rotation), np.array([0.0, 1.0, 0.0])
        )
        agent.set_state(agent_state)
        
        # Store agent info
        self._agents[agent_id] = {
            "agent": agent,
            "position": position,
            "rotation": rotation,
        }
        
        logger.info(f"Added agent {agent_id} at position {position}")
        
        return agent_id
    
    def get_agent_state(self, agent_id: int) -> Optional[Dict[str, Any]]:
        """Get the current state of an agent."""
        if agent_id not in self._agents:
            return None
        
        agent = self._agents[agent_id]["agent"]
        state = agent.get_state()
        
        return {
            "position": tuple(state.position),
            "rotation": state.rotation,
            "sensor_states": {
                name: sensor.get_state()
                for name, sensor in agent._sensors.items()
            },
        }
    
    def set_agent_position(
        self,
        agent_id: int,
        position: Tuple[float, float, float],
    ) -> bool:
        """Set an agent's position."""
        if agent_id not in self._agents:
            return False
        
        agent = self._agents[agent_id]["agent"]
        state = agent.get_state()
        state.position = np.array(position)
        agent.set_state(state)
        
        self._agents[agent_id]["position"] = position
        return True
    
    def step(self, actions: Optional[Dict[int, str]] = None) -> Dict[int, Dict[str, Any]]:
        """
        Step the simulation forward.
        
        Args:
            actions: Dictionary of agent_id -> action_name
            
        Returns:
            Dictionary of agent_id -> observations
        """
        if not self._is_initialized:
            raise RuntimeError("Simulator not initialized")
        
        start_time = time.time()
        observations = {}
        
        # Execute actions for each agent
        for agent_id, agent_info in self._agents.items():
            action = actions.get(agent_id, None) if actions else None
            
            if action:
                obs = self._sim.step(action)
            else:
                obs = self._sim.get_sensor_observations(agent_id)
            
            observations[agent_id] = obs
        
        # Step physics if enabled
        if self.config.enable_physics:
            self._sim.step_physics(self.config.physics_timestep)
            self._stats["physics_steps"] += 1
        
        self._stats["steps"] += 1
        self._stats["total_time"] += time.time() - start_time
        
        return observations
    
    def get_observations(self, agent_id: int = 0) -> Dict[str, Any]:
        """Get current sensor observations for an agent."""
        if not self._is_initialized:
            return {}
        
        return self._sim.get_sensor_observations(agent_id)
    
    def get_navigable_point(self) -> Tuple[float, float, float]:
        """Get a random navigable point in the scene."""
        if not self._is_initialized:
            return (0.0, 0.0, 0.0)
        
        point = self._sim.pathfinder.get_random_navigable_point()
        return tuple(point)
    
    def is_navigable(self, position: Tuple[float, float, float]) -> bool:
        """Check if a position is navigable."""
        if not self._is_initialized:
            return False
        
        return self._sim.pathfinder.is_navigable(np.array(position))
    
    def get_shortest_path(
        self,
        start: Tuple[float, float, float],
        goal: Tuple[float, float, float],
    ) -> Optional[List[Tuple[float, float, float]]]:
        """
        Get the shortest path between two points.
        
        Returns:
            List of waypoints, or None if no path exists
        """
        if not self._is_initialized:
            return None
        
        path = habitat_sim.ShortestPath()
        path.requested_start = np.array(start)
        path.requested_end = np.array(goal)
        
        found = self._sim.pathfinder.find_path(path)
        
        if found:
            return [tuple(p) for p in path.points]
        return None
    
    def raycast(
        self,
        origin: Tuple[float, float, float],
        direction: Tuple[float, float, float],
        max_distance: float = 10.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Cast a ray and find intersection.
        
        Returns:
            Hit info dictionary or None if no hit
        """
        if not self._is_initialized:
            return None
        
        ray = habitat_sim.geo.Ray(
            np.array(origin),
            np.array(direction),
        )
        
        hit = self._sim.cast_ray(ray, max_distance)
        
        if hit.has_hits():
            closest = hit.hits[0]
            return {
                "point": tuple(closest.point),
                "normal": tuple(closest.normal),
                "distance": closest.ray_distance,
                "object_id": closest.object_id,
            }
        return None
    
    def close(self) -> None:
        """Close and clean up the simulator."""
        if self._sim:
            self._sim.close()
            self._sim = None
        
        self._is_initialized = False
        self._agents.clear()
        logger.info("Habitat-Sim closed")
    
    def __enter__(self) -> "HabitatSimulator":
        self.initialize()
        return self
    
    def __exit__(self, *args) -> None:
        self.close()
    
    def __del__(self):
        self.close()


# Mock simulator for testing without Habitat-Sim installed
class MockHabitatSimulator:
    """
    Mock simulator for testing without Habitat-Sim.
    
    Provides the same interface but with simulated behavior.
    """
    
    def __init__(
        self,
        config: Optional[SimulatorConfig] = None,
        event_bus: Optional[EventBus] = None,
        environment: Optional[Environment] = None,
    ):
        self.config = config or SimulatorConfig()
        self._event_bus = event_bus
        self._environment = environment
        self._agents: Dict[int, Dict[str, Any]] = {}
        self._is_initialized = False
        self._stats = {"steps": 0, "total_time": 0.0, "physics_steps": 0}
    
    @property
    def is_available(self) -> bool:
        return True  # Mock is always available
    
    @property
    def is_initialized(self) -> bool:
        return self._is_initialized
    
    @property
    def stats(self) -> Dict[str, Any]:
        return self._stats.copy()
    
    def initialize(self) -> bool:
        self._is_initialized = True
        logger.info("Mock Habitat simulator initialized")
        return True
    
    def add_agent(
        self,
        position: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        rotation: float = 0.0,
        height: Optional[float] = None,
    ) -> int:
        agent_id = len(self._agents)
        self._agents[agent_id] = {
            "position": list(position),
            "rotation": rotation,
            "height": height or 1.5,
        }
        return agent_id
    
    def get_agent_state(self, agent_id: int) -> Optional[Dict[str, Any]]:
        if agent_id not in self._agents:
            return None
        return {
            "position": tuple(self._agents[agent_id]["position"]),
            "rotation": self._agents[agent_id]["rotation"],
        }
    
    def set_agent_position(
        self,
        agent_id: int,
        position: Tuple[float, float, float],
    ) -> bool:
        if agent_id not in self._agents:
            return False
        self._agents[agent_id]["position"] = list(position)
        return True
    
    def step(self, actions: Optional[Dict[int, str]] = None) -> Dict[int, Dict[str, Any]]:
        self._stats["steps"] += 1
        observations = {}
        
        for agent_id in self._agents:
            # Simulate movement
            if actions and agent_id in actions:
                action = actions[agent_id]
                pos = self._agents[agent_id]["position"]
                
                if action == "move_forward":
                    pos[2] += 0.25
                elif action == "turn_left":
                    self._agents[agent_id]["rotation"] += 10
                elif action == "turn_right":
                    self._agents[agent_id]["rotation"] -= 10
            
            # Mock observations
            observations[agent_id] = {
                "rgb": self._generate_mock_rgb(),
                "depth": self._generate_mock_depth(),
            }
        
        return observations
    
    def _generate_mock_rgb(self) -> Any:
        """Generate mock RGB observation."""
        if np is not None:
            return np.zeros((self.config.height, self.config.width, 3), dtype=np.uint8)
        return None
    
    def _generate_mock_depth(self) -> Any:
        """Generate mock depth observation."""
        if np is not None:
            return np.ones((self.config.height, self.config.width), dtype=np.float32) * 5.0
        return None
    
    def get_observations(self, agent_id: int = 0) -> Dict[str, Any]:
        return {
            "rgb": self._generate_mock_rgb(),
            "depth": self._generate_mock_depth(),
        }
    
    def get_navigable_point(self) -> Tuple[float, float, float]:
        import random
        return (random.uniform(-5, 5), 0.0, random.uniform(-5, 5))
    
    def is_navigable(self, position: Tuple[float, float, float]) -> bool:
        return True
    
    def get_shortest_path(
        self,
        start: Tuple[float, float, float],
        goal: Tuple[float, float, float],
    ) -> Optional[List[Tuple[float, float, float]]]:
        return [start, goal]  # Direct path
    
    def raycast(
        self,
        origin: Tuple[float, float, float],
        direction: Tuple[float, float, float],
        max_distance: float = 10.0,
    ) -> Optional[Dict[str, Any]]:
        return None  # No hits in mock
    
    def close(self) -> None:
        self._is_initialized = False
        self._agents.clear()
    
    def __enter__(self) -> "MockHabitatSimulator":
        self.initialize()
        return self
    
    def __exit__(self, *args) -> None:
        self.close()


def create_simulator(
    config: Optional[SimulatorConfig] = None,
    use_mock: bool = False,
    **kwargs,
) -> HabitatSimulator:
    """
    Factory function to create appropriate simulator.
    
    Args:
        config: Simulator configuration
        use_mock: Force use of mock simulator
        **kwargs: Additional arguments
        
    Returns:
        HabitatSimulator or MockHabitatSimulator instance
    """
    if use_mock or not HABITAT_AVAILABLE:
        return MockHabitatSimulator(config, **kwargs)
    return HabitatSimulator(config, **kwargs)
