"""
Base agent interface. All agents return structured AgentOutput.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

# Structured output from agents - enables session state updates
AgentOutput = Dict  # { "agent_type": str, "message": str, "state_update": dict }


class BaseAgent(ABC):
    """Abstract base for all agents. Focus on domain logic."""

    agent_type: str = "base"

    @abstractmethod
    def get_system_prompt(
        self,
        business_context: Optional[Dict],
        current_date: str,
        current_year: int,
        wa_id: str,
        name: str,
    ) -> str:
        """Build system prompt for this agent."""
        pass

    @abstractmethod
    def get_tools(self) -> List:
        """Return list of LangChain tools for this agent."""
        pass

    @abstractmethod
    def execute(
        self,
        message_body: str,
        wa_id: str,
        name: str,
        business_context: Optional[Dict],
        conversation_history: List[Dict],
        message_id: Optional[str] = None,
    ) -> AgentOutput:
        """
        Execute agent logic. Return structured AgentOutput.

        Returns:
            { "agent_type": str, "message": str, "state_update": dict }
        """
        pass
