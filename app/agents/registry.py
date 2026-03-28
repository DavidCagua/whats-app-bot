"""
Explicit agent registry. Maps agent_type string to agent class/instance.
"""

import logging
from typing import Dict, List, Optional

from .base_agent import BaseAgent
from .booking_agent import BookingAgent
from .order_agent import OrderAgent
from .sales_agent import SalesAgent

# Explicit registry: no hardcoding in router
AGENT_REGISTRY: Dict[str, BaseAgent] = {
    "booking": BookingAgent(),
    "order": OrderAgent(),
    "sales": SalesAgent(),
}


def register(agent_type: str, agent: BaseAgent) -> None:
    """Register an agent type."""
    AGENT_REGISTRY[agent_type] = agent
    logging.info(f"[REGISTRY] Registered agent: {agent_type}")


def get_agent(agent_type: str) -> Optional[BaseAgent]:
    """Get agent by type."""
    return AGENT_REGISTRY.get(agent_type)


def get_all_types() -> List[str]:
    """Return all registered agent types."""
    return list(AGENT_REGISTRY.keys())
