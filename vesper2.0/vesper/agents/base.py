"""
Base agent class for simulation agents.

Defines the interface and common functionality for all agents.
"""

from __future__ import annotations

import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from vesper.protocol.messages import Message, CommandMessage, EventMessage
from vesper.core.event_bus import EventBus


logger = logging.getLogger(__name__)


class AgentState(str, Enum):
    """Agent operational states."""
    IDLE = "idle"
    THINKING = "thinking"
    ACTING = "acting"
    MOVING = "moving"
    WAITING = "waiting"
    ERROR = "error"


@dataclass
class AgentConfig:
    """Configuration for an agent."""
    # Identity
    name: str = "Agent"
    agent_type: str = "generic"
    
    # LLM settings
    model: str = "openai/gpt-oss-120b"
    temperature: float = 0.7
    max_tokens: int = 512
    
    # Behavior settings
    think_interval: float = 1.0  # Seconds between thinking cycles
    max_actions_per_cycle: int = 3
    observation_radius: float = 5.0  # Meters
    
    # Capabilities
    can_move: bool = True
    can_interact: bool = True
    can_communicate: bool = True
    
    # System prompt for LLM
    system_prompt: Optional[str] = None


@dataclass
class Observation:
    """What the agent perceives in the environment."""
    timestamp: float
    position: Tuple[float, float, float]
    
    # Nearby entities
    visible_devices: List[Dict[str, Any]] = field(default_factory=list)
    visible_agents: List[Dict[str, Any]] = field(default_factory=list)
    
    # Recent events
    recent_events: List[Dict[str, Any]] = field(default_factory=list)
    
    # Current goals/tasks
    current_task: Optional[str] = None
    pending_commands: List[Dict[str, Any]] = field(default_factory=list)
    
    def to_prompt(self) -> str:
        """Convert observation to a prompt string for LLM."""
        lines = [
            f"Time: {time.strftime('%H:%M:%S', time.localtime(self.timestamp))}",
            f"Position: ({self.position[0]:.1f}, {self.position[1]:.1f}, {self.position[2]:.1f})",
        ]
        
        if self.visible_devices:
            lines.append("\nVisible devices:")
            for device in self.visible_devices:
                lines.append(f"  - {device.get('name', device.get('id'))}: {device.get('state', 'unknown')}")
        
        if self.visible_agents:
            lines.append("\nOther agents nearby:")
            for agent in self.visible_agents:
                lines.append(f"  - {agent.get('name', agent.get('id'))}")
        
        if self.recent_events:
            lines.append("\nRecent events:")
            for event in self.recent_events[-5:]:  # Last 5 events
                lines.append(f"  - {event.get('type')}: {event.get('description', '')}")
        
        if self.current_task:
            lines.append(f"\nCurrent task: {self.current_task}")
        
        return "\n".join(lines)


@dataclass 
class Action:
    """An action the agent wants to perform."""
    action_type: str
    target_id: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    reasoning: Optional[str] = None
    priority: int = 0
    
    def to_command(self, source_id: str) -> CommandMessage:
        """Convert to a protocol command message."""
        return CommandMessage.create(
            command_name=self.action_type,
            target_id=self.target_id or "",
            parameters=self.parameters,
            source_id=source_id,
        )


class Agent(ABC):
    """
    Base class for simulation agents.
    
    Agents can:
    - Perceive their environment (observation)
    - Think/reason (using LLM or rules)
    - Take actions (commands to devices, movement)
    - Communicate (with other agents)
    
    Subclasses must implement:
    - think(): Generate actions from observations
    - act(): Execute actions in the environment
    """
    
    def __init__(
        self,
        config: Optional[AgentConfig] = None,
        agent_id: Optional[str] = None,
        event_bus: Optional[EventBus] = None,
        position: Tuple[float, float, float] = (0.0, 0.0, 0.0),
    ):
        """
        Initialize the agent.
        
        Args:
            config: Agent configuration
            agent_id: Unique identifier (auto-generated if None)
            event_bus: Event bus for communication
            position: Initial position in the environment
        """
        self.config = config or AgentConfig()
        self._agent_id = agent_id or f"agent_{str(uuid.uuid4())[:8]}"
        self._event_bus = event_bus
        self._position = position
        
        self._state = AgentState.IDLE
        self._last_think_time = 0.0
        self._action_queue: List[Action] = []
        self._observation_history: List[Observation] = []
        self._message_history: List[Dict[str, Any]] = []
        
        # Statistics
        self._stats = {
            "think_cycles": 0,
            "actions_taken": 0,
            "events_received": 0,
            "errors": 0,
        }
        
        # Subscribe to events if event bus provided
        if self._event_bus:
            self._event_bus.subscribe("*", self._on_event)
    
    @property
    def agent_id(self) -> str:
        """Unique agent identifier."""
        return self._agent_id
    
    @property
    def name(self) -> str:
        """Agent name from config."""
        return self.config.name
    
    @property
    def position(self) -> Tuple[float, float, float]:
        """Current position."""
        return self._position
    
    @position.setter
    def position(self, value: Tuple[float, float, float]) -> None:
        """Update position."""
        self._position = value
    
    @property
    def state(self) -> AgentState:
        """Current operational state."""
        return self._state
    
    @property
    def stats(self) -> Dict[str, int]:
        """Agent statistics."""
        return self._stats.copy()
    
    def _on_event(self, event) -> None:
        """Handle incoming events."""
        self._stats["events_received"] += 1
        
        # Store in history for observation
        event_data = {
            "type": event.event_type,
            "source": event.source_id,
            "timestamp": event.timestamp,
            "payload": event.payload,
        }
        
        # Keep limited history
        if len(self._message_history) > 100:
            self._message_history = self._message_history[-50:]
        self._message_history.append(event_data)
    
    def observe(self, environment: Any) -> Observation:
        """
        Gather observations from the environment.
        
        Args:
            environment: The simulation environment
            
        Returns:
            Current observation
        """
        # Build observation from environment
        observation = Observation(
            timestamp=time.time(),
            position=self._position,
        )
        
        # Get visible devices within observation radius
        if hasattr(environment, 'get_devices_near'):
            devices = environment.get_devices_near(
                self._position, 
                self.config.observation_radius
            )
            for device in devices:
                observation.visible_devices.append({
                    "id": device.device_id,
                    "name": getattr(device, 'name', device.device_id),
                    "type": device.device_type,
                    "state": device.get_state(),
                    "location": device.location,
                })
        
        # Get recent events from history
        cutoff_time = time.time() - 60  # Last minute
        observation.recent_events = [
            e for e in self._message_history
            if e.get("timestamp", 0) > cutoff_time
        ]
        
        # Store observation
        self._observation_history.append(observation)
        if len(self._observation_history) > 100:
            self._observation_history = self._observation_history[-50:]
        
        return observation
    
    @abstractmethod
    def think(self, observation: Observation) -> List[Action]:
        """
        Process observation and decide on actions.
        
        Args:
            observation: Current observation of the environment
            
        Returns:
            List of actions to take
        """
        pass
    
    @abstractmethod
    def act(self, action: Action, environment: Any) -> bool:
        """
        Execute an action in the environment.
        
        Args:
            action: Action to execute
            environment: The simulation environment
            
        Returns:
            True if action was successful
        """
        pass
    
    def update(self, dt: float, environment: Any) -> None:
        """
        Update agent state for one simulation tick.
        
        Args:
            dt: Time delta in seconds
            environment: The simulation environment
        """
        current_time = time.time()
        
        # Check if it's time to think
        if current_time - self._last_think_time >= self.config.think_interval:
            try:
                self._state = AgentState.THINKING
                
                # Observe environment
                observation = self.observe(environment)
                
                # Think and generate actions
                actions = self.think(observation)
                self._action_queue.extend(actions)
                
                self._last_think_time = current_time
                self._stats["think_cycles"] += 1
                
            except Exception as e:
                logger.error(f"Agent {self._agent_id} think error: {e}")
                self._stats["errors"] += 1
                self._state = AgentState.ERROR
        
        # Execute queued actions
        if self._action_queue:
            self._state = AgentState.ACTING
            
            actions_this_cycle = min(
                len(self._action_queue),
                self.config.max_actions_per_cycle
            )
            
            for _ in range(actions_this_cycle):
                if not self._action_queue:
                    break
                    
                action = self._action_queue.pop(0)
                try:
                    success = self.act(action, environment)
                    if success:
                        self._stats["actions_taken"] += 1
                except Exception as e:
                    logger.error(f"Agent {self._agent_id} action error: {e}")
                    self._stats["errors"] += 1
        else:
            self._state = AgentState.IDLE
    
    def send_command(self, action: Action) -> bool:
        """
        Send a command via the event bus.
        
        Args:
            action: Action to send as command
            
        Returns:
            True if sent successfully
        """
        if not self._event_bus:
            logger.warning(f"Agent {self._agent_id} has no event bus")
            return False
        
        command = action.to_command(self._agent_id)
        # Emit as event (protocol handler would process this)
        return self._event_bus.emit(
            f"command.{action.action_type}",
            payload=command.to_dict(),
            source_id=self._agent_id,
        )
    
    def move_to(self, target: Tuple[float, float, float]) -> None:
        """
        Start moving toward a target position.
        
        Args:
            target: Target position (x, y, z)
        """
        # In a real simulation, this would use pathfinding
        self._position = target
        self._state = AgentState.MOVING
    
    def get_state(self) -> Dict[str, Any]:
        """Get the current agent state as a dictionary."""
        return {
            "agent_id": self._agent_id,
            "name": self.config.name,
            "type": self.config.agent_type,
            "position": self._position,
            "state": self._state.value,
            "action_queue_length": len(self._action_queue),
            "stats": self._stats,
        }
    
    def __repr__(self) -> str:
        return f"Agent(id={self._agent_id[:8]}, name={self.config.name}, state={self._state.value})"
