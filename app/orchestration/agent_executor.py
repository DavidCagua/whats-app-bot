"""
AgentExecutor: Invokes a single agent and returns its output.
Phase 1: executes one agent. Expects AgentOutput from agent.
"""

import logging
from typing import Dict, List, Optional

from ..agents import get_agent
from ..database.conversation_service import conversation_service


def execute_agent(
    agent_type: str,
    message_body: str,
    wa_id: str,
    name: str,
    business_context: Optional[Dict],
    message_id: Optional[str] = None,
) -> Dict:
    """
    Execute the specified agent and return AgentOutput.

    Args:
        agent_type: e.g. "booking"
        message_body: User message
        wa_id: WhatsApp ID
        name: Customer name
        business_context: Business context from routing
        message_id: Optional for tracing

    Returns:
        AgentOutput: { "agent_type", "message", "state_update" }
    """
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

    output = agent.execute(
        message_body=message_body,
        wa_id=wa_id,
        name=name,
        business_context=business_context,
        conversation_history=conversation_history,
        message_id=message_id,
    )
    return output
