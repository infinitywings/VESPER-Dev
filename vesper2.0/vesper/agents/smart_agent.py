"""
SmartAgent: LLM-powered agent for smart home control.

Uses LLM for reasoning and decision-making based on environment observations.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from vesper.agents.base import Agent, AgentConfig, AgentState, Observation, Action
from vesper.agents.llm_client import LLMClient, LLMConfig, LLMMessage
from vesper.core.event_bus import EventBus


logger = logging.getLogger(__name__)


# Default system prompt for smart home agent
DEFAULT_SYSTEM_PROMPT = """You are an intelligent smart home agent operating in a simulated environment.

Your responsibilities:
1. Monitor the environment using sensors (motion, contact, light)
2. Make decisions to ensure comfort, security, and efficiency
3. Control smart devices (doors, lights, locks) based on context

You can perform these actions:
- move_to: Move to a location {"x": float, "y": float, "z": float}
- open_door: Open a door {"target_id": "door_id"}
- close_door: Close a door {"target_id": "door_id"}
- toggle_light: Toggle a light {"target_id": "light_id"}
- lock_door: Lock a smart door {"target_id": "door_id"}
- unlock_door: Unlock a smart door {"target_id": "door_id"}
- wait: Do nothing this cycle {"duration": seconds}
- speak: Communicate {"message": "text"}

When responding, output a JSON object with:
{
    "thoughts": "Your reasoning about the current situation",
    "actions": [
        {"action": "action_name", "target_id": "optional_target", "params": {...}}
    ]
}

Be proactive but not excessive. Respond to motion events, manage doors appropriately, 
and maintain a secure environment."""


@dataclass
class SmartAgentConfig(AgentConfig):
    """Extended configuration for SmartAgent."""
    agent_type: str = "smart_home"
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    
    # LLM-specific settings
    use_llm: bool = True
    fallback_to_rules: bool = True
    
    # Behavior rules (used when LLM disabled or as fallback)
    auto_close_doors: bool = True
    auto_close_delay: float = 30.0  # seconds
    respond_to_motion: bool = True
    security_mode: bool = False


class SmartAgent(Agent):
    """
    LLM-powered smart home agent.
    
    Uses an LLM for reasoning about the environment and making decisions.
    Falls back to rule-based behavior when LLM is unavailable.
    
    Example:
        config = SmartAgentConfig(
            name="HomeAssistant",
            model="openai/gpt-oss-120b",
        )
        
        agent = SmartAgent(config=config)
        
        # In simulation loop:
        observation = agent.observe(environment)
        actions = agent.think(observation)
        for action in actions:
            agent.act(action, environment)
    """
    
    def __init__(
        self,
        config: Optional[SmartAgentConfig] = None,
        agent_id: Optional[str] = None,
        event_bus: Optional[EventBus] = None,
        position: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        llm_client: Optional[LLMClient] = None,
    ):
        """
        Initialize the SmartAgent.
        
        Args:
            config: Agent configuration
            agent_id: Unique identifier
            event_bus: Event bus for communication
            position: Initial position
            llm_client: Pre-configured LLM client (creates one if None)
        """
        super().__init__(
            config=config or SmartAgentConfig(),
            agent_id=agent_id,
            event_bus=event_bus,
            position=position,
        )
        
        self.config: SmartAgentConfig  # Type hint
        
        # LLM client
        if llm_client:
            self._llm_client = llm_client
        elif self.config.use_llm:
            llm_config = LLMConfig(
                model=self.config.model,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
            self._llm_client = LLMClient(llm_config)
        else:
            self._llm_client = None
        
        # Conversation history for context
        self._conversation: List[LLMMessage] = [
            LLMMessage("system", self.config.system_prompt or DEFAULT_SYSTEM_PROMPT)
        ]
        
        # Track door states for auto-close
        self._door_open_times: Dict[str, float] = {}
        
        # Track recent motion
        self._last_motion_time: float = 0.0
        self._last_motion_location: Optional[Tuple[float, float, float]] = None
    
    def think(self, observation: Observation) -> List[Action]:
        """
        Process observation and generate actions.
        
        Uses LLM for reasoning, with rule-based fallback.
        """
        actions = []
        
        # Update internal state from observation
        self._process_observation(observation)
        
        # Try LLM reasoning
        if self._llm_client and self.config.use_llm:
            try:
                actions = self._think_with_llm(observation)
            except Exception as e:
                logger.warning(f"LLM reasoning failed: {e}")
                if self.config.fallback_to_rules:
                    actions = self._think_with_rules(observation)
        else:
            actions = self._think_with_rules(observation)
        
        return actions
    
    def _process_observation(self, observation: Observation) -> None:
        """Update internal state from observation."""
        # Track door states
        for device in observation.visible_devices:
            if device.get("type") == "smart_door":
                door_id = device["id"]
                state = device.get("state", {})
                is_open = state.get("is_open", False)
                
                if is_open and door_id not in self._door_open_times:
                    self._door_open_times[door_id] = time.time()
                elif not is_open and door_id in self._door_open_times:
                    del self._door_open_times[door_id]
        
        # Track motion events
        for event in observation.recent_events:
            if event.get("type") == "motion_detected":
                self._last_motion_time = event.get("timestamp", time.time())
                payload = event.get("payload", {})
                if "agent_location" in payload:
                    self._last_motion_location = tuple(payload["agent_location"])
    
    def _think_with_llm(self, observation: Observation) -> List[Action]:
        """Use LLM for reasoning."""
        # Build prompt from observation
        obs_prompt = observation.to_prompt()
        
        # Add any special context
        context_lines = []
        if self.config.security_mode:
            context_lines.append("SECURITY MODE ACTIVE: Be extra vigilant about unauthorized access.")
        
        if self._door_open_times:
            doors = list(self._door_open_times.keys())
            context_lines.append(f"Doors currently open: {', '.join(doors)}")
        
        if context_lines:
            obs_prompt += "\n\nContext:\n" + "\n".join(context_lines)
        
        # Add to conversation
        self._conversation.append(LLMMessage("user", obs_prompt))
        
        # Limit conversation history
        if len(self._conversation) > 10:
            # Keep system prompt and last 8 messages
            self._conversation = [self._conversation[0]] + self._conversation[-8:]
        
        # Call LLM
        response = self._llm_client.chat(self._conversation)
        
        # Add response to history
        self._conversation.append(LLMMessage("assistant", response.content))
        
        # Parse actions from response
        actions = self._parse_llm_response(response.content)
        
        return actions
    
    def _parse_llm_response(self, content: str) -> List[Action]:
        """Parse LLM response into actions."""
        actions = []
        
        try:
            # Try to extract JSON
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            
            data = json.loads(content.strip())
            
            # Extract actions
            action_list = data.get("actions", [])
            if isinstance(action_list, dict):
                action_list = [action_list]
            
            for action_data in action_list:
                action_type = action_data.get("action", action_data.get("action_type", ""))
                target_id = action_data.get("target_id", action_data.get("target"))
                params = action_data.get("params", action_data.get("parameters", {}))
                
                if action_type:
                    actions.append(Action(
                        action_type=action_type,
                        target_id=target_id,
                        parameters=params,
                        reasoning=data.get("thoughts", ""),
                    ))
            
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse LLM response as JSON: {content[:100]}")
            # Try to extract action from natural language
            actions = self._parse_natural_language(content)
        
        return actions
    
    def _parse_natural_language(self, content: str) -> List[Action]:
        """Attempt to parse actions from natural language response."""
        actions = []
        content_lower = content.lower()
        
        # Simple keyword matching
        if "open" in content_lower and "door" in content_lower:
            actions.append(Action(action_type="open_door"))
        elif "close" in content_lower and "door" in content_lower:
            actions.append(Action(action_type="close_door"))
        elif "lock" in content_lower:
            actions.append(Action(action_type="lock_door"))
        elif "unlock" in content_lower:
            actions.append(Action(action_type="unlock_door"))
        elif "light" in content_lower:
            actions.append(Action(action_type="toggle_light"))
        
        return actions
    
    def _think_with_rules(self, observation: Observation) -> List[Action]:
        """Rule-based decision making (fallback)."""
        actions = []
        current_time = time.time()
        
        # Rule 1: Auto-close doors that have been open too long
        if self.config.auto_close_doors:
            for door_id, open_time in list(self._door_open_times.items()):
                if current_time - open_time > self.config.auto_close_delay:
                    actions.append(Action(
                        action_type="close_door",
                        target_id=door_id,
                        reasoning=f"Door has been open for {self.config.auto_close_delay}s",
                    ))
        
        # Rule 2: Respond to motion
        if self.config.respond_to_motion:
            for event in observation.recent_events:
                if event.get("type") == "motion_detected":
                    # Could turn on lights, send notification, etc.
                    logger.info(f"Motion detected at {event.get('payload', {}).get('agent_location')}")
        
        # Rule 3: Security mode - lock all unlocked doors
        if self.config.security_mode:
            for device in observation.visible_devices:
                if device.get("type") == "smart_door":
                    state = device.get("state", {})
                    if not state.get("is_locked", True):
                        actions.append(Action(
                            action_type="lock_door",
                            target_id=device["id"],
                            reasoning="Security mode: locking unsecured door",
                        ))
        
        return actions
    
    def act(self, action: Action, environment: Any) -> bool:
        """
        Execute an action in the environment.
        
        Args:
            action: Action to execute
            environment: The simulation environment
            
        Returns:
            True if successful
        """
        action_type = action.action_type
        target_id = action.target_id
        
        logger.info(f"Agent {self._agent_id} executing: {action_type} -> {target_id}")
        
        try:
            if action_type == "move_to":
                # Movement action
                params = action.parameters
                target = (
                    params.get("x", 0.0),
                    params.get("y", 0.0),
                    params.get("z", 0.0),
                )
                self.move_to(target)
                return True
            
            elif action_type in ("open_door", "close_door", "lock_door", "unlock_door"):
                # Door commands
                if hasattr(environment, 'get_device'):
                    device = environment.get_device(target_id)
                    if device:
                        if action_type == "open_door":
                            device.open()
                        elif action_type == "close_door":
                            device.close()
                        elif action_type == "lock_door":
                            device.lock()
                        elif action_type == "unlock_door":
                            device.unlock()
                        return True
                
                # Fall back to event bus command
                return self.send_command(action)
            
            elif action_type == "toggle_light":
                # Light control
                if hasattr(environment, 'get_device'):
                    device = environment.get_device(target_id)
                    if device and hasattr(device, 'toggle'):
                        device.toggle()
                        return True
                return self.send_command(action)
            
            elif action_type == "wait":
                # Do nothing
                return True
            
            elif action_type == "speak":
                # Communication
                message = action.parameters.get("message", "")
                logger.info(f"Agent {self._agent_id} says: {message}")
                if self._event_bus:
                    self._event_bus.emit(
                        "agent_speech",
                        payload={"agent_id": self._agent_id, "message": message},
                        source_id=self._agent_id,
                    )
                return True
            
            else:
                # Unknown action - send as generic command
                logger.warning(f"Unknown action type: {action_type}")
                return self.send_command(action)
                
        except Exception as e:
            logger.error(f"Action execution error: {e}")
            return False
    
    def set_task(self, task: str) -> None:
        """
        Set a task for the agent to work on.
        
        Args:
            task: Natural language description of the task
        """
        # Add task context to conversation
        self._conversation.append(LLMMessage(
            "user",
            f"NEW TASK: {task}\n\nPlease work on this task during your next cycles."
        ))
    
    def close(self) -> None:
        """Clean up resources."""
        if self._llm_client:
            self._llm_client.close()
    
    def __del__(self):
        self.close()
