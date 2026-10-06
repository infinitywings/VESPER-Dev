"""
Agent module for LLM-controlled agents in Vesper.

Provides LLM integration and agent behavior framework.
"""

from vesper.agents.llm_client import LLMClient, LLMConfig, LLMResponse
from vesper.agents.base import Agent, AgentState, AgentConfig
from vesper.agents.smart_agent import SmartAgent, SmartAgentConfig
from vesper.agents.controller import AgentController
from vesper.agents.llm_controller import LLMAgentController, AgentRole

__all__ = [
    "LLMClient",
    "LLMConfig",
    "LLMResponse",
    "Agent",
    "AgentState",
    "AgentConfig",
    "SmartAgent",
    "SmartAgentConfig",
    "AgentController",
    "LLMAgentController",
    "AgentRole",
]
