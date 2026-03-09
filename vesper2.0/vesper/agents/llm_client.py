"""
LLM Client for agent reasoning and decision making.

Supports OpenWebUI API (OpenAI-compatible) for LLM inference.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable
from enum import Enum

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    httpx = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


logger = logging.getLogger(__name__)


class LLMProvider(str, Enum):
    """Supported LLM providers."""
    OPENWEBUI = "openwebui"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    LOCAL = "local"


@dataclass
class LLMConfig:
    """
    Configuration for LLM client.
    
    Supports OpenWebUI, OpenAI, and other compatible APIs.
    """
    # API Configuration
    api_url: str = field(default_factory=lambda: os.getenv(
        "OPENWEBUI_URL",
        "http://cci-siscluster1.charlotte.edu:8080/api/chat/completions"
    ))
    api_key: str = field(default_factory=lambda: os.getenv(
        "OPENWEBUI_API_KEY",
        ""
    ))
    
    # Model selection
    model: str = "openai/gpt-oss-120b"
    
    # Generation parameters
    temperature: float = 0.7
    max_tokens: int = 2048  # Increased for longer schedules
    top_p: float = 0.9
    
    # Request parameters
    timeout: float = 120.0  # Read timeout - local LLMs need time to generate
    connect_timeout: float = 5.0  # Connect timeout - fail fast if LM Studio not running
    max_retries: int = 1
    retry_delay: float = 1.0
    
    # Provider type
    provider: LLMProvider = LLMProvider.OPENWEBUI
    
    def validate(self) -> bool:
        """Check if configuration is valid."""
        if not self.api_url:
            logger.error("API URL is required")
            return False
        if not self.api_key and self.provider != LLMProvider.LOCAL:
            logger.warning("API key not set - requests may fail")
        return True


@dataclass
class LLMMessage:
    """A message in a conversation."""
    role: str  # "system", "user", "assistant"
    content: str
    
    def to_dict(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class LLMResponse:
    """Response from LLM inference."""
    content: str
    model: str
    finish_reason: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    latency_ms: float = 0.0
    raw_response: Optional[Dict[str, Any]] = None
    
    @property
    def is_complete(self) -> bool:
        """Check if response completed normally."""
        return self.finish_reason in ("stop", "end_turn", None)
    
    def get_json(self) -> Optional[Dict[str, Any]]:
        """Try to parse content as JSON."""
        try:
            # Handle markdown code blocks
            content = self.content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            return json.loads(content.strip())
        except json.JSONDecodeError:
            return None


class LLMClient:
    """
    Client for LLM inference via OpenWebUI or compatible APIs.
    
    Example:
        config = LLMConfig(model="openai/gpt-oss-120b")
        client = LLMClient(config)
        
        response = client.chat([
            LLMMessage("system", "You are a smart home assistant."),
            LLMMessage("user", "Turn on the living room lights.")
        ])
        
        print(response.content)
    """
    
    # Available models (from user's config)
    AVAILABLE_MODELS = [
        "openai/gpt-oss-120b",
        "OpenGVLab/InternVL3_5-30B-A3B",
        "Qwen/Qwen3-30B-A3B-Thinking-2507-FP8",
        "openai/gpt-oss-20b",
    ]
    
    def __init__(self, config: Optional[LLMConfig] = None):
        """
        Initialize the LLM client.
        
        Args:
            config: LLM configuration (uses defaults if None)
        """
        self.config = config or LLMConfig()
        self._client: Optional[httpx.Client] = None
        self._stats = {
            "requests": 0,
            "successes": 0,
            "failures": 0,
            "total_tokens": 0,
            "total_latency_ms": 0,
        }
        
        if not HTTPX_AVAILABLE:
            logger.warning("httpx not installed - LLM calls will fail. Install with: pip install httpx")
    
    @property
    def stats(self) -> Dict[str, Any]:
        """Client statistics."""
        stats = self._stats.copy()
        if stats["requests"] > 0:
            stats["avg_latency_ms"] = stats["total_latency_ms"] / stats["requests"]
        return stats
    
    def _get_client(self) -> "httpx.Client":
        """Get or create HTTP client."""
        if not HTTPX_AVAILABLE:
            raise RuntimeError("httpx is required for LLM calls")
        
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(
                    connect=self.config.connect_timeout,
                    read=self.config.timeout,
                    write=10.0,
                    pool=10.0,
                ),
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client
    
    def chat(
        self,
        messages: List[LLMMessage],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> LLMResponse:
        """
        Send a chat completion request.
        
        Args:
            messages: List of conversation messages
            model: Override model (uses config default if None)
            temperature: Override temperature
            max_tokens: Override max tokens
            **kwargs: Additional parameters for the API
            
        Returns:
            LLM response with generated content
        """
        self._stats["requests"] += 1
        start_time = time.time()
        
        # Build request payload
        payload = {
            "model": model or self.config.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature or self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
            "top_p": self.config.top_p,
            **kwargs,
        }
        
        # Retry logic
        last_error = None
        for attempt in range(self.config.max_retries):
            try:
                response = self._make_request(payload)
                latency_ms = (time.time() - start_time) * 1000
                
                self._stats["successes"] += 1
                self._stats["total_latency_ms"] += latency_ms
                
                if response.usage:
                    self._stats["total_tokens"] += response.usage.get("total_tokens", 0)
                
                response.latency_ms = latency_ms
                return response
                
            except Exception as e:
                last_error = e
                logger.warning(f"LLM request failed (attempt {attempt + 1}): {e}")
                if attempt < self.config.max_retries - 1:
                    time.sleep(self.config.retry_delay)
        
        self._stats["failures"] += 1
        raise RuntimeError(f"LLM request failed after {self.config.max_retries} retries: {last_error}")
    
    def _make_request(self, payload: Dict[str, Any]) -> LLMResponse:
        """Make the actual HTTP request."""
        client = self._get_client()
        
        response = client.post(self.config.api_url, json=payload)
        response.raise_for_status()
        
        data = response.json()
        
        # Parse OpenAI-compatible response
        if "choices" in data and len(data["choices"]) > 0:
            choice = data["choices"][0]
            message = choice.get("message", {})
            content = message.get("content", "")
            
            return LLMResponse(
                content=content,
                model=data.get("model", payload["model"]),
                finish_reason=choice.get("finish_reason"),
                usage=data.get("usage"),
                raw_response=data,
            )
        else:
            raise ValueError(f"Unexpected response format: {data}")
    
    def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> LLMResponse:
        """
        Simple completion with a single prompt.
        
        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            **kwargs: Additional parameters
            
        Returns:
            LLM response
        """
        messages = []
        if system_prompt:
            messages.append(LLMMessage("system", system_prompt))
        messages.append(LLMMessage("user", prompt))
        
        return self.chat(messages, **kwargs)
    
    def generate_action(
        self,
        observation: str,
        available_actions: List[str],
        context: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate an action based on observation.
        
        Args:
            observation: Current state/observation
            available_actions: List of valid actions
            context: Additional context
            system_prompt: System prompt override
            
        Returns:
            Parsed action as dictionary
        """
        if not system_prompt:
            system_prompt = """You are an AI agent controlling a smart home. 
Analyze the observation and select the best action from the available actions.
Respond with a JSON object containing:
- "action": the selected action name
- "parameters": any parameters for the action (as object)
- "reasoning": brief explanation of why this action was chosen
"""
        
        prompt = f"""Current observation:
{observation}

Available actions:
{json.dumps(available_actions, indent=2)}

{f"Context: {context}" if context else ""}

Select the best action and respond with JSON only."""
        
        response = self.complete(prompt, system_prompt=system_prompt)
        
        # Try to parse as JSON
        action_data = response.get_json()
        if action_data:
            return action_data
        
        # Fallback: return raw response
        return {
            "action": "none",
            "parameters": {},
            "reasoning": response.content,
            "raw_response": True,
        }
    
    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None
    
    def __enter__(self) -> "LLMClient":
        return self
    
    def __exit__(self, *args) -> None:
        self.close()


# Convenience function for quick inference
def quick_llm_call(
    prompt: str,
    model: str = "openai/gpt-oss-120b",
    system_prompt: Optional[str] = None,
) -> str:
    """
    Quick one-shot LLM call.
    
    Args:
        prompt: The prompt to send
        model: Model to use
        system_prompt: Optional system prompt
        
    Returns:
        Response content as string
    """
    config = LLMConfig(model=model)
    with LLMClient(config) as client:
        response = client.complete(prompt, system_prompt=system_prompt)
        return response.content
