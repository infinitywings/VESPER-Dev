"""
Unit tests for agent framework.
"""

import pytest
import time
from unittest.mock import Mock, MagicMock, patch

from vesper.agents.base import (
    Agent,
    AgentState,
    AgentConfig,
    Observation,
    Action,
)
from vesper.agents.smart_agent import SmartAgent, SmartAgentConfig
from vesper.agents.controller import AgentController, ControllerConfig
from vesper.agents.llm_client import LLMClient, LLMConfig, LLMMessage, LLMResponse


class MockAgent(Agent):
    """Concrete agent for testing."""
    
    def think(self, observation: Observation) -> list:
        # Simple rule: if we see devices, interact with first one
        if observation.visible_devices:
            device = observation.visible_devices[0]
            return [Action(
                action_type="interact",
                target_id=device.get("id"),
            )]
        return []
    
    def act(self, action: Action, environment) -> bool:
        return True


class TestAgentConfig:
    """Tests for AgentConfig."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = AgentConfig()
        assert config.name == "Agent"
        assert config.model == "openai/gpt-oss-120b"
        assert config.think_interval == 1.0
    
    def test_custom_config(self):
        """Test custom configuration."""
        config = AgentConfig(
            name="TestAgent",
            model="custom/model",
            temperature=0.5,
        )
        assert config.name == "TestAgent"
        assert config.model == "custom/model"
        assert config.temperature == 0.5


class TestObservation:
    """Tests for Observation class."""
    
    def test_create_observation(self):
        """Test observation creation."""
        obs = Observation(
            timestamp=time.time(),
            position=(1.0, 2.0, 3.0),
        )
        assert obs.position == (1.0, 2.0, 3.0)
        assert obs.visible_devices == []
    
    def test_observation_to_prompt(self):
        """Test converting observation to prompt."""
        obs = Observation(
            timestamp=time.time(),
            position=(0.0, 0.0, 0.0),
            visible_devices=[
                {"id": "door_1", "name": "Front Door", "state": "closed"},
            ],
        )
        
        prompt = obs.to_prompt()
        
        assert "Position:" in prompt
        assert "Front Door" in prompt


class TestAction:
    """Tests for Action class."""
    
    def test_create_action(self):
        """Test action creation."""
        action = Action(
            action_type="open_door",
            target_id="door_1",
            parameters={"force": True},
        )
        
        assert action.action_type == "open_door"
        assert action.target_id == "door_1"
        assert action.parameters["force"] is True
    
    def test_action_to_command(self):
        """Test converting action to command message."""
        action = Action(
            action_type="close_door",
            target_id="door_1",
        )
        
        command = action.to_command("agent_1")
        
        assert command.command_name == "close_door"
        assert command.target_id == "door_1"
        assert command.source_id == "agent_1"


class TestAgent:
    """Tests for base Agent class."""
    
    def test_create_agent(self):
        """Test agent creation."""
        agent = MockAgent(
            config=AgentConfig(name="TestBot"),
            position=(5.0, 0.0, 5.0),
        )
        
        assert agent.name == "TestBot"
        assert agent.position == (5.0, 0.0, 5.0)
        assert agent.state == AgentState.IDLE
    
    def test_agent_id_generation(self):
        """Test automatic ID generation."""
        agent1 = MockAgent()
        agent2 = MockAgent()
        
        assert agent1.agent_id != agent2.agent_id
        assert agent1.agent_id.startswith("agent_")
    
    def test_custom_agent_id(self):
        """Test custom agent ID."""
        agent = MockAgent(agent_id="my_agent_123")
        assert agent.agent_id == "my_agent_123"
    
    def test_observe(self):
        """Test observation gathering."""
        agent = MockAgent(position=(1.0, 2.0, 3.0))
        
        # Mock environment
        env = Mock()
        env.get_devices_near = Mock(return_value=[])
        
        obs = agent.observe(env)
        
        assert obs.position == (1.0, 2.0, 3.0)
        assert isinstance(obs.timestamp, float)
    
    def test_update_cycle(self):
        """Test agent update cycle."""
        config = AgentConfig(think_interval=0.01)  # Short interval for testing
        agent = MockAgent(config=config)
        env = Mock()
        env.get_devices_near = Mock(return_value=[])
        
        # Initial state
        assert agent.stats["think_cycles"] == 0
        
        # First update should trigger think
        time.sleep(0.02)
        agent.update(0.01, env)
        
        assert agent.stats["think_cycles"] == 1
    
    def test_move_to(self):
        """Test agent movement."""
        agent = MockAgent(position=(0.0, 0.0, 0.0))
        
        agent.move_to((10.0, 0.0, 5.0))
        
        assert agent.position == (10.0, 0.0, 5.0)
        assert agent.state == AgentState.MOVING


class TestSmartAgent:
    """Tests for SmartAgent class."""
    
    def test_create_smart_agent(self):
        """Test SmartAgent creation."""
        config = SmartAgentConfig(
            name="HomeAssistant",
            use_llm=False,  # Disable LLM for testing
        )
        agent = SmartAgent(config=config)
        
        assert agent.name == "HomeAssistant"
        assert agent.config.agent_type == "smart_home"
    
    def test_rule_based_thinking(self):
        """Test rule-based decision making."""
        config = SmartAgentConfig(
            use_llm=False,
            auto_close_doors=True,
            auto_close_delay=0.1,  # Short delay for testing
        )
        agent = SmartAgent(config=config)
        
        # Simulate door being open
        agent._door_open_times["door_1"] = time.time() - 1.0  # Open for 1 second
        
        obs = Observation(
            timestamp=time.time(),
            position=(0, 0, 0),
        )
        
        actions = agent.think(obs)
        
        # Should generate close_door action
        assert len(actions) >= 1
        assert any(a.action_type == "close_door" for a in actions)
    
    def test_security_mode(self):
        """Test security mode behavior."""
        config = SmartAgentConfig(
            use_llm=False,
            security_mode=True,
        )
        agent = SmartAgent(config=config)
        
        obs = Observation(
            timestamp=time.time(),
            position=(0, 0, 0),
            visible_devices=[
                {
                    "id": "door_1",
                    "type": "smart_door",
                    "state": {"is_locked": False},
                },
            ],
        )
        
        actions = agent.think(obs)
        
        # Should generate lock action
        assert any(a.action_type == "lock_door" for a in actions)
    
    def test_act_movement(self):
        """Test movement action execution."""
        config = SmartAgentConfig(use_llm=False)
        agent = SmartAgent(config=config)
        
        action = Action(
            action_type="move_to",
            parameters={"x": 5.0, "y": 0.0, "z": 3.0},
        )
        
        success = agent.act(action, Mock())
        
        assert success is True
        assert agent.position == (5.0, 0.0, 3.0)
    
    def test_set_task(self):
        """Test task assignment."""
        config = SmartAgentConfig(use_llm=False)
        agent = SmartAgent(config=config)
        
        initial_len = len(agent._conversation)
        agent.set_task("Turn on all the lights in the living room")
        
        assert len(agent._conversation) == initial_len + 1


class TestLLMClient:
    """Tests for LLMClient."""
    
    def test_create_config(self):
        """Test LLM config creation."""
        config = LLMConfig(
            model="test/model",
            temperature=0.5,
        )
        
        assert config.model == "test/model"
        assert config.temperature == 0.5
    
    def test_create_message(self):
        """Test LLM message creation."""
        msg = LLMMessage("user", "Hello, AI!")
        
        assert msg.role == "user"
        assert msg.content == "Hello, AI!"
        assert msg.to_dict() == {"role": "user", "content": "Hello, AI!"}
    
    def test_response_json_parsing(self):
        """Test parsing JSON from response."""
        response = LLMResponse(
            content='{"action": "open_door", "target": "door_1"}',
            model="test",
        )
        
        data = response.get_json()
        
        assert data is not None
        assert data["action"] == "open_door"
    
    def test_response_json_with_markdown(self):
        """Test parsing JSON wrapped in markdown."""
        response = LLMResponse(
            content='```json\n{"key": "value"}\n```',
            model="test",
        )
        
        data = response.get_json()
        
        assert data is not None
        assert data["key"] == "value"
    
    def test_client_stats(self):
        """Test client statistics tracking."""
        config = LLMConfig()
        client = LLMClient(config)
        
        stats = client.stats
        
        assert stats["requests"] == 0
        assert stats["successes"] == 0


class TestAgentController:
    """Tests for AgentController."""
    
    def test_create_controller(self):
        """Test controller creation."""
        controller = AgentController()
        
        assert controller.agent_count == 0
        assert len(controller.agents) == 0
    
    def test_spawn_agent(self):
        """Test spawning agents."""
        controller = AgentController()
        config = SmartAgentConfig(name="Test", use_llm=False)
        
        agent = controller.spawn(SmartAgent, config=config)
        
        assert controller.agent_count == 1
        assert agent.name == "Test"
        assert controller.get_agent(agent.agent_id) is agent
    
    def test_spawn_by_type(self):
        """Test spawning by registered type."""
        controller = AgentController()
        
        agent = controller.spawn(
            agent_type="smart",
            config=SmartAgentConfig(use_llm=False),
        )
        
        assert isinstance(agent, SmartAgent)
    
    def test_max_agents_limit(self):
        """Test maximum agents limit."""
        config = ControllerConfig(max_agents=2)
        controller = AgentController(config=config)
        
        controller.spawn(SmartAgent, SmartAgentConfig(use_llm=False))
        controller.spawn(SmartAgent, SmartAgentConfig(use_llm=False))
        
        with pytest.raises(RuntimeError):
            controller.spawn(SmartAgent, SmartAgentConfig(use_llm=False))
    
    def test_destroy_agent(self):
        """Test destroying agents."""
        controller = AgentController()
        agent = controller.spawn(SmartAgent, SmartAgentConfig(use_llm=False))
        
        assert controller.agent_count == 1
        
        success = controller.destroy(agent.agent_id)
        
        assert success is True
        assert controller.agent_count == 0
    
    def test_destroy_all(self):
        """Test destroying all agents."""
        controller = AgentController()
        for _ in range(3):
            controller.spawn(SmartAgent, SmartAgentConfig(use_llm=False))
        
        count = controller.destroy_all()
        
        assert count == 3
        assert controller.agent_count == 0
    
    def test_update_agents(self):
        """Test updating all agents."""
        controller = AgentController()
        config = SmartAgentConfig(use_llm=False, think_interval=0.01)
        
        agent = controller.spawn(SmartAgent, config=config)
        
        env = Mock()
        env.get_devices_near = Mock(return_value=[])
        
        time.sleep(0.02)
        controller.update(0.01, env)
        
        assert controller.stats["update_cycles"] == 1
    
    def test_get_agents_near(self):
        """Test finding agents near a position."""
        controller = AgentController()
        
        a1 = controller.spawn(SmartAgent, SmartAgentConfig(use_llm=False), position=(0, 0, 0))
        a2 = controller.spawn(SmartAgent, SmartAgentConfig(use_llm=False), position=(5, 0, 0))
        a3 = controller.spawn(SmartAgent, SmartAgentConfig(use_llm=False), position=(100, 0, 0))
        
        nearby = controller.get_agents_near((0, 0, 0), radius=10.0)
        
        assert len(nearby) == 2
        assert a1 in nearby
        assert a2 in nearby
        assert a3 not in nearby
    
    def test_broadcast_task(self):
        """Test broadcasting tasks to all agents."""
        controller = AgentController()
        
        for _ in range(3):
            controller.spawn(SmartAgent, SmartAgentConfig(use_llm=False))
        
        count = controller.broadcast_task("Turn on all lights")
        
        assert count == 3
    
    def test_get_status(self):
        """Test getting controller status."""
        controller = AgentController()
        controller.spawn(SmartAgent, SmartAgentConfig(use_llm=False))
        
        status = controller.get_status()
        
        assert status["agent_count"] == 1
        assert "stats" in status
