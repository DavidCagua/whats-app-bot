"""
AgentExecutor: Invokes a single agent and returns its output.
For order agent: loads session first and passes it so backend is single source of truth.
"""

import logging
from typing import Dict, List, Optional

# NOTE: `get_agent` is imported lazily inside execute_agent() to avoid a
# circular import at module-load time. The chain is:
#   app.agents.__init__ -> registry -> OrderAgent -> order_flow ->
#   orchestration/__init__ -> conversation_manager -> agent_executor ->
#   from ..agents import get_agent  <-- app.agents not yet fully loaded
# Deferring the import until first call breaks the cycle.
from ..database.conversation_service import conversation_service
from ..database.session_state_service import session_state_service
from . import turn_cache


def execute_agent(
    agent_type: str,
    message_body: str,
    wa_id: str,
    name: str,
    business_context: Optional[Dict],
    message_id: Optional[str] = None,
    stale_turn: bool = False,
) -> Dict:
    """
    Execute the specified agent and return AgentOutput.

    Args:
        agent_type: e.g. "booking", "order"
        message_body: User message
        wa_id: WhatsApp ID
        name: Customer name
        business_context: Business context from routing
        message_id: Optional for tracing

    Returns:
        AgentOutput: { "agent_type", "message", "state_update" }
    """
    from ..agents import get_agent  # lazy: breaks circular import chain
    agent = get_agent(agent_type)
    if not agent:
        logging.error(f"[AGENT_EXECUTOR] Agent not found: {agent_type}")
        return {
            "agent_type": agent_type,
            "message": "Lo siento, no pude procesar tu solicitud. Intenta más tarde.",
            "state_update": {},
        }

    business_id = business_context.get("business_id") if business_context else None
    conversation_history = conversation_service.get_conversation_history(
        wa_id, limit=10, business_id=business_id
    )

    kwargs = dict(
        message_body=message_body,
        wa_id=wa_id,
        name=name,
        business_context=business_context,
        conversation_history=conversation_history,
        message_id=message_id,
        stale_turn=stale_turn,
    )

    if agent_type in ("order", "booking") and business_id:
        load_result = turn_cache.current().get_session(
            wa_id, str(business_id),
            loader=lambda: session_state_service.load(wa_id, str(business_id)),
        )
        session = load_result.get("session", {})
        kwargs["session"] = session

    output = agent.execute(**kwargs)
    return output
