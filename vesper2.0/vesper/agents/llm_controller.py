"""
LLM Agent Controller for VESPER.

Bridges LLM reasoning with Habitat 3.0 humanoid control and IoT device management.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Callable
from enum import Enum

from vesper.agents.llm_client import LLMClient, LLMConfig, LLMMessage, LLMResponse
from vesper.core.event_bus import EventBus, Event, EventPriority
from vesper.devices.manager import DeviceManager


logger = logging.getLogger(__name__)


class AgentRole(str, Enum):
    """Predefined agent roles with different behaviors."""
    HOME_ASSISTANT = "home_assistant"
    SECURITY_MONITOR = "security_monitor"
    RESIDENT = "resident"
    VISITOR = "visitor"
    CUSTOM = "custom"


# Role-specific system prompts
ROLE_PROMPTS = {
    AgentRole.HOME_ASSISTANT: """You are an intelligent home assistant AI controlling a humanoid robot in a smart home.

Your responsibilities:
1. Monitor sensors for activity (motion, door states, light levels)
2. Respond to events appropriately (greet visitors, secure doors, manage lighting)
3. Navigate the home efficiently
4. Communicate status and take proactive actions

You control a humanoid with these capabilities:
- MOVE: Navigate to locations in the home
- INTERACT: Open/close doors, toggle devices
- OBSERVE: Look around, check sensors
- SPEAK: Communicate with residents

Respond with a JSON action plan:
{
    "thoughts": "Your reasoning about the situation",
    "actions": [
        {"type": "MOVE", "target": "living_room"},
        {"type": "INTERACT", "device": "front_door", "action": "lock"},
        {"type": "SPEAK", "message": "Welcome home!"}
    ]
}""",

    AgentRole.SECURITY_MONITOR: """You are a security AI monitoring a smart home environment.

Your primary focus:
1. Detect unauthorized access or unusual activity
2. Ensure all entry points are secured when appropriate
3. Monitor motion patterns for anomalies
4. Alert on security events

Security actions:
- PATROL: Move to check different areas
- SECURE: Lock doors, arm sensors
- ALERT: Raise security alerts
- INVESTIGATE: Check suspicious activity

Respond with security-focused actions in JSON format.""",

    AgentRole.RESIDENT: """You are simulating a resident living in this smart home.

Your behavior:
1. Move naturally between rooms
2. Interact with doors and devices as a resident would
3. Follow daily routines (morning routine, cooking, relaxing)
4. Respond to home automation events

Resident actions:
- MOVE: Walk to different rooms
- INTERACT: Use doors, appliances
- WAIT: Stay in place for activities
- ROUTINE: Perform daily activities

Simulate realistic resident behavior.""",
}


@dataclass
class AgentAction:
    """An action to be executed by the agent."""
    action_type: str
    target: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    timestamp: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": action_type,
            "target": self.target,
            "parameters": self.parameters,
        }


@dataclass
class AgentState:
    """Current state of an LLM-controlled agent."""
    agent_id: str
    name: str
    role: AgentRole
    position: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    
    # Status
    is_active: bool = True
    is_thinking: bool = False
    current_task: Optional[str] = None
    
    # Action queue
    pending_actions: List[AgentAction] = field(default_factory=list)
    completed_actions: List[AgentAction] = field(default_factory=list)
    
    # LLM conversation
    conversation_length: int = 0
    last_llm_call: float = 0.0
    
    # Statistics
    total_llm_calls: int = 0
    total_actions: int = 0
    total_distance: float = 0.0


class LLMAgentController:
    """
    Controls LLM-powered agents in the VESPER simulation.
    
    Features:
    - Multiple agents with different roles
    - Integration with IoT device manager
    - Action planning and execution
    - Conversation context management
    - Rate limiting for LLM calls
    
    Example:
        from vesper.agents.llm_controller import LLMAgentController
        
        controller = LLMAgentController()
        
        # Create an agent
        agent = controller.create_agent(
            agent_id="assistant_1",
            name="HomeBot",
            role=AgentRole.HOME_ASSISTANT,
        )
        
        # Set a task
        controller.set_task("assistant_1", "Welcome the visitor and check all doors")
        
        # In simulation loop:
        actions = controller.think("assistant_1", current_observation)
        for action in actions:
            controller.execute_action("assistant_1", action)
    """
    
    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        device_manager: Optional[DeviceManager] = None,
        event_bus: Optional[EventBus] = None,
        think_interval: float = 1.0,  # Minimum seconds between LLM calls
    ):
        """
        Initialize the LLM agent controller.
        
        Args:
            llm_client: LLM client for inference
            device_manager: IoT device manager
            event_bus: Event bus for communication
            think_interval: Minimum time between LLM calls per agent
        """
        self._llm_client = llm_client or LLMClient()
        self._device_manager = device_manager
        self._event_bus = event_bus or EventBus()
        self._think_interval = think_interval
        
        # Agents
        self._agents: Dict[str, AgentState] = {}
        self._conversations: Dict[str, List[LLMMessage]] = {}
        
        # Action handlers
        self._action_handlers: Dict[str, Callable] = {}
        self._register_default_handlers()
        
        # Recent events for context
        self._recent_events: List[Event] = []
        self._max_events = 10
        
        # Subscribe to all events
        self._event_bus.subscribe("*", self._on_event)
        
        # Statistics
        self._stats = {
            "total_llm_calls": 0,
            "total_actions": 0,
            "total_agents": 0,
        }
    
    def _register_default_handlers(self) -> None:
        """Register default action handlers."""
        self._action_handlers["MOVE"] = self._handle_move
        self._action_handlers["INTERACT"] = self._handle_interact
        self._action_handlers["SPEAK"] = self._handle_speak
        self._action_handlers["WAIT"] = self._handle_wait
        self._action_handlers["OBSERVE"] = self._handle_observe
        self._action_handlers["SECURE"] = self._handle_secure
        self._action_handlers["PATROL"] = self._handle_patrol
        self._action_handlers["ALERT"] = self._handle_alert
        self._action_handlers["INVESTIGATE"] = self._handle_investigate
    
    def _on_event(self, event: Event) -> None:
        """Handle incoming events."""
        self._recent_events.append(event)
        if len(self._recent_events) > self._max_events:
            self._recent_events.pop(0)
    
    def create_agent(
        self,
        agent_id: str,
        name: str = "Agent",
        role: AgentRole = AgentRole.HOME_ASSISTANT,
        position: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        custom_prompt: Optional[str] = None,
    ) -> AgentState:
        """
        Create a new LLM-controlled agent.
        
        Args:
            agent_id: Unique identifier
            name: Human-readable name
            role: Agent role (determines behavior)
            position: Initial position
            custom_prompt: Custom system prompt (overrides role prompt)
            
        Returns:
            The created agent state
        """
        agent = AgentState(
            agent_id=agent_id,
            name=name,
            role=role,
            position=position,
        )
        self._agents[agent_id] = agent
        
        # Initialize conversation
        system_prompt = custom_prompt or ROLE_PROMPTS.get(role, ROLE_PROMPTS[AgentRole.HOME_ASSISTANT])
        self._conversations[agent_id] = [
            LLMMessage("system", system_prompt)
        ]
        
        self._stats["total_agents"] += 1
        logger.info(f"Created agent '{name}' ({agent_id}) with role: {role.value}")
        
        return agent
    
    def get_agent(self, agent_id: str) -> Optional[AgentState]:
        """Get an agent by ID."""
        return self._agents.get(agent_id)
    
    def set_task(self, agent_id: str, task: str) -> bool:
        """
        Set a task for an agent.
        
        Args:
            agent_id: Agent ID
            task: Task description
            
        Returns:
            True if task was set
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return False
        
        agent.current_task = task
        
        # Add task to conversation context
        self._conversations[agent_id].append(
            LLMMessage("user", f"New task assigned: {task}")
        )
        
        logger.info(f"Agent {agent_id} task: {task}")
        return True
    
    def update_position(
        self,
        agent_id: str,
        position: Tuple[float, float, float],
    ) -> bool:
        """Update an agent's position."""
        agent = self._agents.get(agent_id)
        if not agent:
            return False
        
        # Calculate distance traveled
        import numpy as np
        distance = np.linalg.norm(np.array(position) - np.array(agent.position))
        agent.total_distance += distance
        agent.position = position
        
        return True
    
    def think(
        self,
        agent_id: str,
        observation: Optional[str] = None,
        force: bool = False,
    ) -> List[AgentAction]:
        """
        Have an agent think and generate actions.
        
        Args:
            agent_id: Agent ID
            observation: Current observation (auto-generated if None)
            force: Force thinking even if within rate limit
            
        Returns:
            List of actions to execute
        """
        agent = self._agents.get(agent_id)
        if not agent or not agent.is_active:
            return []
        
        # Check rate limiting
        now = time.time()
        if not force and (now - agent.last_llm_call) < self._think_interval:
            # Return pending actions instead
            return agent.pending_actions[:1] if agent.pending_actions else []
        
        # Build observation if not provided
        if observation is None:
            observation = self._build_observation(agent_id)
        
        # Mark as thinking
        agent.is_thinking = True
        
        try:
            # Call LLM
            actions = self._llm_think(agent_id, observation)
            
            # Update state
            agent.last_llm_call = now
            agent.total_llm_calls += 1
            agent.pending_actions.extend(actions)
            
            self._stats["total_llm_calls"] += 1
            
            return actions
            
        finally:
            agent.is_thinking = False
    
    def _build_observation(self, agent_id: str) -> str:
        """Build observation string for an agent."""
        agent = self._agents.get(agent_id)
        if not agent:
            return ""
        
        lines = [
            f"Agent: {agent.name}",
            f"Position: {agent.position}",
            f"Task: {agent.current_task or 'None'}",
            "",
        ]
        
        # Add device states
        if self._device_manager:
            lines.append(self._device_manager.to_prompt())
            lines.append("")
        
        # Add recent events
        if self._recent_events:
            lines.append("Recent Events:")
            for event in self._recent_events[-5:]:
                lines.append(f"  - {event.event_type}: {event.payload}")
        
        return "\n".join(lines)
    
    def _llm_think(self, agent_id: str, observation: str) -> List[AgentAction]:
        """Call LLM to generate actions."""
        conversation = self._conversations.get(agent_id, [])
        
        # Add observation as user message
        conversation.append(LLMMessage("user", observation))
        
        # Limit conversation length
        if len(conversation) > 12:
            conversation = [conversation[0]] + conversation[-10:]
            self._conversations[agent_id] = conversation
        
        try:
            response = self._llm_client.chat(conversation)
            
            # Add response to conversation
            conversation.append(LLMMessage("assistant", response.content))
            
            # Parse actions
            return self._parse_actions(response.content)
            
        except Exception as e:
            logger.error(f"LLM call failed for agent {agent_id}: {e}")
            return []
    
    def _parse_actions(self, content: str) -> List[AgentAction]:
        """Parse LLM response into actions."""
        actions = []
        
        try:
            # Extract JSON from response
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            
            data = json.loads(content.strip())
            
            # Extract thoughts for logging
            thoughts = data.get("thoughts", "")
            if thoughts:
                logger.debug(f"Agent thoughts: {thoughts}")
            
            # Parse actions
            action_list = data.get("actions", [])
            if isinstance(action_list, dict):
                action_list = [action_list]
            
            for action_data in action_list:
                action_type = action_data.get("type", action_data.get("action", "")).upper()
                target = action_data.get("target", action_data.get("device"))
                params = {
                    k: v for k, v in action_data.items()
                    if k not in ("type", "action", "target", "device")
                }
                
                if action_type:
                    actions.append(AgentAction(
                        action_type=action_type,
                        target=target,
                        parameters=params,
                    ))
            
        except json.JSONDecodeError:
            # Try to extract simple action from text
            logger.warning("Could not parse JSON from LLM response")
        
        return actions
    
    def execute_action(
        self,
        agent_id: str,
        action: AgentAction,
    ) -> bool:
        """
        Execute an action for an agent.
        
        Args:
            agent_id: Agent ID
            action: Action to execute
            
        Returns:
            True if action was executed successfully
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return False
        
        handler = self._action_handlers.get(action.action_type)
        if handler:
            try:
                result = handler(agent_id, action)
                agent.total_actions += 1
                agent.completed_actions.append(action)
                
                # Remove from pending if present
                if action in agent.pending_actions:
                    agent.pending_actions.remove(action)
                
                self._stats["total_actions"] += 1
                return result
                
            except Exception as e:
                logger.error(f"Action execution failed: {e}")
                return False
        else:
            logger.warning(f"No handler for action type: {action.action_type}")
            return False
    
    # Default action handlers
    
    def _handle_move(self, agent_id: str, action: AgentAction) -> bool:
        """Handle MOVE action."""
        target = action.target
        logger.info(f"Agent {agent_id} moving to: {target}")
        
        # Emit movement event
        self._event_bus.emit(
            "agent_move",
            {"agent_id": agent_id, "target": target},
        )
        return True
    
    def _handle_interact(self, agent_id: str, action: AgentAction) -> bool:
        """Handle INTERACT action."""
        device = action.target
        device_action = action.parameters.get("action", "toggle")
        
        logger.info(f"Agent {agent_id} interacting with {device}: {device_action}")
        
        if self._device_manager:
            device_obj = self._device_manager.get_device(device)
            if device_obj:
                if hasattr(device_obj, device_action):
                    getattr(device_obj, device_action)()
                    return True
        
        # Emit interaction event
        self._event_bus.emit(
            "agent_interact",
            {"agent_id": agent_id, "device": device, "action": device_action},
        )
        return True
    
    def _handle_speak(self, agent_id: str, action: AgentAction) -> bool:
        """Handle SPEAK action."""
        message = action.parameters.get("message", "")
        logger.info(f"Agent {agent_id} says: {message}")
        
        self._event_bus.emit(
            "agent_speak",
            {"agent_id": agent_id, "message": message},
        )
        return True
    
    def _handle_wait(self, agent_id: str, action: AgentAction) -> bool:
        """Handle WAIT action."""
        duration = action.parameters.get("duration", 1.0)
        logger.debug(f"Agent {agent_id} waiting for {duration}s")
        return True
    
    def _handle_observe(self, agent_id: str, action: AgentAction) -> bool:
        """Handle OBSERVE action."""
        logger.debug(f"Agent {agent_id} observing environment")
        return True
    
    def _handle_secure(self, agent_id: str, action: AgentAction) -> bool:
        """Handle SECURE action (lock doors, etc.)."""
        target = action.target
        logger.info(f"Agent {agent_id} securing: {target}")
        
        if self._device_manager:
            # Lock all doors or specific target
            doors = self._device_manager.get_devices_by_type("smart_door")
            for door in doors:
                if target is None or target == "all" or door.device_id == target:
                    if hasattr(door, "lock"):
                        door.lock()
        
        return True
    
    def _handle_patrol(self, agent_id: str, action: AgentAction) -> bool:
        """Handle PATROL action (move to check different areas)."""
        area = action.target or action.parameters.get("area", "home")
        logger.info(f"Agent {agent_id} patrolling: {area}")
        
        self._event_bus.emit(
            "agent_patrol",
            {"agent_id": agent_id, "area": area},
        )
        return True
    
    def _handle_alert(self, agent_id: str, action: AgentAction) -> bool:
        """Handle ALERT action (raise security alerts)."""
        alert_type = action.parameters.get("type", "general")
        message = action.parameters.get("message", "Alert triggered")
        logger.warning(f"Agent {agent_id} ALERT: {alert_type} - {message}")
        
        self._event_bus.emit(
            "security_alert",
            {"agent_id": agent_id, "alert_type": alert_type, "message": message},
            priority=EventPriority.HIGH,
        )
        return True
    
    def _handle_investigate(self, agent_id: str, action: AgentAction) -> bool:
        """Handle INVESTIGATE action (check suspicious activity)."""
        target = action.target or action.parameters.get("location", "unknown")
        logger.info(f"Agent {agent_id} investigating: {target}")
        
        self._event_bus.emit(
            "agent_investigate",
            {"agent_id": agent_id, "target": target},
        )
        return True
    
    def register_action_handler(
        self,
        action_type: str,
        handler: Callable[[str, AgentAction], bool],
    ) -> None:
        """Register a custom action handler."""
        self._action_handlers[action_type.upper()] = handler
    
    def get_all_agent_states(self) -> Dict[str, Dict[str, Any]]:
        """Get state of all agents."""
        return {
            agent_id: {
                "name": agent.name,
                "role": agent.role.value,
                "position": agent.position,
                "is_active": agent.is_active,
                "current_task": agent.current_task,
                "pending_actions": len(agent.pending_actions),
                "total_actions": agent.total_actions,
                "total_llm_calls": agent.total_llm_calls,
            }
            for agent_id, agent in self._agents.items()
        }
    
    def step(self, dt: float = 1/30) -> Dict[str, List[AgentAction]]:
        """
        Step all agents (think and execute).
        
        Args:
            dt: Time delta
            
        Returns:
            Dictionary of agent_id -> actions taken
        """
        results = {}
        
        for agent_id, agent in self._agents.items():
            if not agent.is_active:
                continue
            
            # Think
            actions = self.think(agent_id)
            
            # Execute first action
            if actions:
                action = actions[0]
                self.execute_action(agent_id, action)
            
            results[agent_id] = actions
        
        return results
    
    @property
    def stats(self) -> Dict[str, Any]:
        return self._stats.copy()
    
    def close(self) -> None:
        """Clean up resources."""
        self._llm_client.close()
        self._agents.clear()
        self._conversations.clear()
