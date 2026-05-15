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
    abort_key: Optional[str] = None,
    handoff_context: Optional[Dict] = None,
    turn_ctx: Optional[object] = None,
    attachments: Optional[List[Dict]] = None,
) -> Dict:
    """
    Execute the specified agent and return AgentOutput.

    Args:
        agent_type: e.g. "booking", "order", "customer_service"
        message_body: User message (or handoff segment text when invoked
            by the dispatcher as part of a handoff chain).
        wa_id: WhatsApp ID
        name: Customer name
        business_context: Business context from routing
        message_id: Optional for tracing
        handoff_context: When this invocation is a mid-turn handoff from
            another agent, the source agent's context payload (e.g.
            {"booking_id": "..."}). The target agent reads via **kwargs.

    Returns:
        AgentOutput: {
            "agent_type": str,
            "message": str,
            "state_update": dict,
            "handoff": Optional[{"to": str, "segment": str, "context": dict}]
        }
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
        abort_key=abort_key,
    )
    # Only pass handoff_context when non-empty so existing agents that
    # don't declare **kwargs aren't affected (all current agents accept
    # **kwargs, but keep this minimal as a defensive default).
    if handoff_context:
        kwargs["handoff_context"] = handoff_context

    # Per-turn snapshot built once in conversation_manager. Agents that
    # care (e.g. customer_service for the cancel guard) read it via
    # **kwargs; others ignore it.
    if turn_ctx is not None:
        kwargs["turn_ctx"] = turn_ctx

    # Inbound media (post-Supabase URLs) for the current turn. Vision-
    # capable agents pick this up via **kwargs and build multimodal
    # content arrays for the LLM. Forwarded only when non-empty so
    # text-only turns are unaffected.
    if attachments:
        kwargs["attachments"] = attachments

    # customer_service reads session (read-only — order_context.items for the
    # "mi pedido" ambiguity guard) but only writes its own slot.
    if agent_type in ("order", "booking", "customer_service") and business_id:
        load_result = turn_cache.current().get_session(
            wa_id, str(business_id),
            loader=lambda: session_state_service.load(wa_id, str(business_id)),
        )
        session = load_result.get("session", {})
        kwargs["session"] = session

    output = agent.execute(**kwargs)
    return output
