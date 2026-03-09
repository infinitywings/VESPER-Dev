"""
Embodied agent bridge between Vesper agents and Habitat-Sim.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from vesper.agents.base import Agent, Observation, Action

if TYPE_CHECKING:
    from vesper.habitat.simulator import HabitatSimulator

logger = logging.getLogger(__name__)


@dataclass
class EmbodimentConfig:
    """Configuration for agent embodiment."""
    height: float = 1.5
    radius: float = 0.1
    move_speed: float = 1.0
    turn_speed: float = 90.0
    use_pathfinding: bool = True


@dataclass
class EmbodiedState:
    """Current state of an embodied agent."""
    position: Tuple[float, float, float]
    rotation: float
    is_moving: bool = False
    target_position: Optional[Tuple[float, float, float]] = None


class EmbodiedAgent:
    """Bridge between Vesper agent and Habitat-Sim."""
    
    def __init__(
        self,
        agent: Agent,
        simulator: "HabitatSimulator",
        config: Optional[EmbodimentConfig] = None,
        position: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        rotation: float = 0.0,
    ):
        self.agent = agent
        self.simulator = simulator
        self.config = config or EmbodimentConfig()
        self.state = EmbodiedState(position=position, rotation=rotation)
        self._sim_agent_id: Optional[int] = None
        self._last_observations: Dict[str, Any] = {}
        self._stats = {"distance_traveled": 0.0, "actions_executed": 0}
        
        if simulator.is_initialized:
            self._sim_agent_id = simulator.add_agent(position, rotation)
            agent.position = position
    
    @property
    def position(self) -> Tuple[float, float, float]:
        return self.state.position
    
    def update(self, dt: float) -> None:
        """Update embodied agent."""
        if self.state.is_moving and self.state.target_position:
            self._update_movement(dt)
        self._update_observations()
        self.agent.position = self.state.position
    
    def _update_movement(self, dt: float) -> None:
        target = self.state.target_position
        current = self.state.position
        dx, dz = target[0] - current[0], target[2] - current[2]
        distance = math.sqrt(dx*dx + dz*dz)
        
        if distance < 0.1:
            self.state.position = target
            self.state.is_moving = False
            self.state.target_position = None
            return
        
        move_dist = min(self.config.move_speed * dt, distance)
        factor = move_dist / distance
        new_pos = (current[0] + dx*factor, current[1], current[2] + dz*factor)
        self.state.rotation = math.degrees(math.atan2(dx, dz))
        self._stats["distance_traveled"] += move_dist
        
        if self._sim_agent_id is not None:
            self.simulator.set_agent_position(self._sim_agent_id, new_pos)
        self.state.position = new_pos
    
    def _update_observations(self) -> None:
        if self._sim_agent_id is not None:
            self._last_observations = self.simulator.get_observations(self._sim_agent_id)
    
    def move_to(self, target: Tuple[float, float, float]) -> bool:
        self.state.target_position = target
        self.state.is_moving = True
        return True
    
    def stop(self) -> None:
        self.state.is_moving = False
        self.state.target_position = None
    
    def get_rgb_observation(self) -> Optional[Any]:
        return self._last_observations.get("rgb")
    
    def get_depth_observation(self) -> Optional[Any]:
        return self._last_observations.get("depth")
    
    def get_state(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent.agent_id,
            "position": self.state.position,
            "rotation": self.state.rotation,
            "is_moving": self.state.is_moving,
            "stats": self._stats,
        }
