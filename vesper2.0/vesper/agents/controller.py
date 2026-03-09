"""
Agent Controller for managing multiple agents in the simulation.

Coordinates agent updates, communication, and lifecycle.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Type

from vesper.agents.base import Agent, AgentConfig, AgentState
from vesper.agents.smart_agent import SmartAgent, SmartAgentConfig
from vesper.core.event_bus import EventBus


logger = logging.getLogger(__name__)


@dataclass
class ControllerConfig:
    """Configuration for the agent controller."""
    max_agents: int = 10
    update_interval: float = 0.1  # Seconds between update cycles
    parallel_updates: bool = False  # Whether to update agents in parallel
    auto_respawn: bool = False  # Whether to respawn agents on error


class AgentController:
    """
    Manages multiple agents in the simulation.
    
    Responsibilities:
    - Creating and destroying agents
    - Updating agents each simulation tick
    - Coordinating agent communication
    - Monitoring agent health
    
    Example:
        controller = AgentController(event_bus=event_bus)
        
        # Spawn agents
        agent1 = controller.spawn(SmartAgent, SmartAgentConfig(name="Assistant"))
        agent2 = controller.spawn(SmartAgent, SmartAgentConfig(name="Security"))
        
        # In simulation loop:
        controller.update(dt, environment)
        
        # Get agent status
        for agent in controller.agents:
            print(agent.get_state())
    """
    
    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        config: Optional[ControllerConfig] = None,
    ):
        """
        Initialize the agent controller.
        
        Args:
            event_bus: Shared event bus for agents
            config: Controller configuration
        """
        self._event_bus = event_bus
        self._config = config or ControllerConfig()
        
        self._agents: Dict[str, Agent] = {}
        self._agent_types: Dict[str, Type[Agent]] = {
            "smart": SmartAgent,
        }
        
        self._running = False
        self._update_thread: Optional[threading.Thread] = None
        
        self._stats = {
            "agents_created": 0,
            "agents_destroyed": 0,
            "update_cycles": 0,
            "total_actions": 0,
        }
    
    @property
    def agents(self) -> List[Agent]:
        """List of all managed agents."""
        return list(self._agents.values())
    
    @property
    def agent_count(self) -> int:
        """Number of active agents."""
        return len(self._agents)
    
    @property
    def stats(self) -> Dict[str, int]:
        """Controller statistics."""
        return self._stats.copy()
    
    def register_agent_type(self, name: str, agent_class: Type[Agent]) -> None:
        """
        Register a custom agent type.
        
        Args:
            name: Type name for spawning
            agent_class: Agent class to instantiate
        """
        self._agent_types[name] = agent_class
        logger.info(f"Registered agent type: {name}")
    
    def spawn(
        self,
        agent_class: Optional[Type[Agent]] = None,
        config: Optional[AgentConfig] = None,
        agent_type: str = "smart",
        position: tuple = (0.0, 0.0, 0.0),
        **kwargs,
    ) -> Agent:
        """
        Create and register a new agent.
        
        Args:
            agent_class: Agent class to instantiate (or use agent_type)
            config: Agent configuration
            agent_type: Type name if agent_class not provided
            position: Initial position
            **kwargs: Additional arguments for agent constructor
            
        Returns:
            The created agent
        """
        if len(self._agents) >= self._config.max_agents:
            raise RuntimeError(f"Maximum agents ({self._config.max_agents}) reached")
        
        # Get agent class
        if agent_class is None:
            agent_class = self._agent_types.get(agent_type)
            if agent_class is None:
                raise ValueError(f"Unknown agent type: {agent_type}")
        
        # Create agent
        agent = agent_class(
            config=config,
            event_bus=self._event_bus,
            position=position,
            **kwargs,
        )
        
        # Register
        self._agents[agent.agent_id] = agent
        self._stats["agents_created"] += 1
        
        logger.info(f"Spawned agent: {agent}")
        
        return agent
    
    def get_agent(self, agent_id: str) -> Optional[Agent]:
        """Get an agent by ID."""
        return self._agents.get(agent_id)
    
    def destroy(self, agent_id: str) -> bool:
        """
        Remove an agent from the simulation.
        
        Args:
            agent_id: ID of agent to destroy
            
        Returns:
            True if agent was found and destroyed
        """
        agent = self._agents.pop(agent_id, None)
        if agent:
            # Clean up resources
            if hasattr(agent, 'close'):
                agent.close()
            
            self._stats["agents_destroyed"] += 1
            logger.info(f"Destroyed agent: {agent_id}")
            return True
        return False
    
    def destroy_all(self) -> int:
        """
        Destroy all agents.
        
        Returns:
            Number of agents destroyed
        """
        count = len(self._agents)
        for agent_id in list(self._agents.keys()):
            self.destroy(agent_id)
        return count
    
    def update(self, dt: float, environment: Any) -> None:
        """
        Update all agents for one simulation tick.
        
        Args:
            dt: Time delta in seconds
            environment: The simulation environment
        """
        self._stats["update_cycles"] += 1
        
        agents = list(self._agents.values())
        
        if self._config.parallel_updates and len(agents) > 1:
            self._update_parallel(agents, dt, environment)
        else:
            self._update_sequential(agents, dt, environment)
    
    def _update_sequential(
        self,
        agents: List[Agent],
        dt: float,
        environment: Any,
    ) -> None:
        """Update agents sequentially."""
        for agent in agents:
            try:
                prev_actions = agent.stats.get("actions_taken", 0)
                agent.update(dt, environment)
                new_actions = agent.stats.get("actions_taken", 0)
                self._stats["total_actions"] += new_actions - prev_actions
                
            except Exception as e:
                logger.error(f"Agent update error ({agent.agent_id}): {e}")
                if self._config.auto_respawn and agent.state == AgentState.ERROR:
                    self._respawn_agent(agent)
    
    def _update_parallel(
        self,
        agents: List[Agent],
        dt: float,
        environment: Any,
    ) -> None:
        """Update agents in parallel using threads."""
        threads = []
        
        for agent in agents:
            thread = threading.Thread(
                target=self._update_agent_safe,
                args=(agent, dt, environment),
            )
            thread.start()
            threads.append(thread)
        
        # Wait for all to complete
        for thread in threads:
            thread.join(timeout=5.0)
    
    def _update_agent_safe(
        self,
        agent: Agent,
        dt: float,
        environment: Any,
    ) -> None:
        """Safely update a single agent (for parallel execution)."""
        try:
            prev_actions = agent.stats.get("actions_taken", 0)
            agent.update(dt, environment)
            new_actions = agent.stats.get("actions_taken", 0)
            self._stats["total_actions"] += new_actions - prev_actions
        except Exception as e:
            logger.error(f"Agent update error ({agent.agent_id}): {e}")
    
    def _respawn_agent(self, agent: Agent) -> None:
        """Respawn a failed agent with the same configuration."""
        agent_id = agent.agent_id
        config = agent.config
        position = agent.position
        agent_class = type(agent)
        
        # Destroy old agent
        self.destroy(agent_id)
        
        # Create new one
        try:
            self.spawn(
                agent_class=agent_class,
                config=config,
                position=position,
            )
            logger.info(f"Respawned agent: {agent_id}")
        except Exception as e:
            logger.error(f"Failed to respawn agent: {e}")
    
    def broadcast_task(self, task: str) -> int:
        """
        Assign a task to all agents.
        
        Args:
            task: Task description
            
        Returns:
            Number of agents that received the task
        """
        count = 0
        for agent in self._agents.values():
            if hasattr(agent, 'set_task'):
                agent.set_task(task)
                count += 1
        return count
    
    def get_agents_by_state(self, state: AgentState) -> List[Agent]:
        """Get all agents in a particular state."""
        return [a for a in self._agents.values() if a.state == state]
    
    def get_agents_near(
        self,
        position: tuple,
        radius: float,
    ) -> List[Agent]:
        """Get agents within a radius of a position."""
        result = []
        for agent in self._agents.values():
            dx = agent.position[0] - position[0]
            dy = agent.position[1] - position[1]
            dz = agent.position[2] - position[2]
            distance = (dx*dx + dy*dy + dz*dz) ** 0.5
            if distance <= radius:
                result.append(agent)
        return result
    
    def start_background_updates(
        self,
        environment: Any,
        update_rate: float = 10.0,  # Hz
    ) -> None:
        """
        Start background thread for continuous agent updates.
        
        Args:
            environment: The simulation environment
            update_rate: Updates per second
        """
        if self._running:
            return
        
        self._running = True
        dt = 1.0 / update_rate
        
        def update_loop():
            while self._running:
                start = time.time()
                self.update(dt, environment)
                elapsed = time.time() - start
                sleep_time = max(0, dt - elapsed)
                time.sleep(sleep_time)
        
        self._update_thread = threading.Thread(target=update_loop, daemon=True)
        self._update_thread.start()
        logger.info(f"Started background agent updates at {update_rate} Hz")
    
    def stop_background_updates(self) -> None:
        """Stop background update thread."""
        self._running = False
        if self._update_thread:
            self._update_thread.join(timeout=2.0)
            self._update_thread = None
        logger.info("Stopped background agent updates")
    
    def get_status(self) -> Dict[str, Any]:
        """Get overall controller status."""
        agent_states = {}
        for state in AgentState:
            count = len(self.get_agents_by_state(state))
            if count > 0:
                agent_states[state.value] = count
        
        return {
            "agent_count": len(self._agents),
            "max_agents": self._config.max_agents,
            "running": self._running,
            "agent_states": agent_states,
            "stats": self._stats,
        }
