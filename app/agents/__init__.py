"""
Multi-agent architecture: modular agents with domain-specific logic.
"""

from .base_agent import BaseAgent, AgentOutput
from .registry import AGENT_REGISTRY, get_agent, get_all_types

__all__ = ["BaseAgent", "AgentOutput", "AGENT_REGISTRY", "get_agent", "get_all_types"]
