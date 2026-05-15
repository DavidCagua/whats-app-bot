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
        **kwargs,
    ) -> AgentOutput:
        """
        Execute agent logic. Return structured AgentOutput.

        Subclasses may accept additional keyword arguments (e.g.
        ``session``, ``stale_turn``, ``handoff_context``) via **kwargs.

        Return shape:
            {
                "agent_type": str,
                "message": str,
                "state_update": dict,
                # Optional: mid-turn handoff to another agent. Dispatcher
                # reads this and invokes the target agent with the
                # provided segment + context. Not setting it is the
                # default for agents that don't hand off.
                "handoff": Optional[{
                    "to": str,       # target agent_type
                    "segment": str,  # message to pass to the target
                    "context": dict, # structured payload (booking_id, etc.)
                }],
            }

        Handoffs are capped at MAX_HOPS=3 per turn and are acyclic
        (dispatcher rejects handoffs targeting an agent already in the
        chain). See app/orchestration/dispatcher.py.
        """
        pass
