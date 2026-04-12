"""
Lazy agent registry. Stores agent CLASSES keyed by type, instantiates
the first time an agent is requested. Keeps import of this module cheap
(no network / LLM init side effects) so Alembic, tests and scripts can
load app.* without tripping OpenAI/API-key requirements.
"""

import logging
from typing import Dict, List, Optional, Type

from .base_agent import BaseAgent
from .booking_agent import BookingAgent
from .order_agent import OrderAgent
from .sales_agent import SalesAgent


# Map of agent_type → class. Classes are cheap; instantiation may be
# expensive (LLM client creation, tool loading, etc.) so we defer it.
_AGENT_CLASSES: Dict[str, Type[BaseAgent]] = {
    "booking": BookingAgent,
    "order": OrderAgent,
    "sales": SalesAgent,
}

# Cache of instantiated singletons, populated on first get_agent() call.
_AGENT_INSTANCES: Dict[str, BaseAgent] = {}


def register(agent_type: str, agent: BaseAgent) -> None:
    """Register an already-constructed agent instance. Used for tests or
    programmatic overrides."""
    _AGENT_INSTANCES[agent_type] = agent
    logging.info(f"[REGISTRY] Registered agent instance: {agent_type}")


def register_class(agent_type: str, agent_cls: Type[BaseAgent]) -> None:
    """Register an agent class for lazy instantiation."""
    _AGENT_CLASSES[agent_type] = agent_cls
    _AGENT_INSTANCES.pop(agent_type, None)  # invalidate any cached instance


def get_agent(agent_type: str) -> Optional[BaseAgent]:
    """Return the agent for a given type, instantiating on first use."""
    if agent_type not in _AGENT_INSTANCES:
        cls = _AGENT_CLASSES.get(agent_type)
        if cls is None:
            return None
        _AGENT_INSTANCES[agent_type] = cls()
    return _AGENT_INSTANCES[agent_type]


def get_all_types() -> List[str]:
    """Return all registered agent types."""
    return list(_AGENT_CLASSES.keys())


# Backward-compat alias: some callers may still reach for AGENT_REGISTRY.
# Exposing a read-only mapping that instantiates on access keeps them
# working without reintroducing eager init.
class _LazyRegistry:
    def __getitem__(self, key: str) -> BaseAgent:
        agent = get_agent(key)
        if agent is None:
            raise KeyError(key)
        return agent

    def __contains__(self, key: str) -> bool:
        return key in _AGENT_CLASSES

    def get(self, key: str, default=None):
        return get_agent(key) or default

    def keys(self):
        return _AGENT_CLASSES.keys()

    def items(self):
        return [(k, get_agent(k)) for k in _AGENT_CLASSES]

    def values(self):
        return [get_agent(k) for k in _AGENT_CLASSES]


AGENT_REGISTRY = _LazyRegistry()
