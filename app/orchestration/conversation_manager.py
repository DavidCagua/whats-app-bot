"""
ConversationManager: Entry point for message processing.
Loads business context, enabled agents; routes to AgentExecutor.
Persists agent state_update to session after execution.
"""

import logging
from typing import Optional

from ..database.business_agent_service import business_agent_service
from ..database.session_state_service import session_state_service
from .agent_executor import execute_agent


class ConversationManager:
    """Orchestrates message flow: load agents, route, execute, persist session state."""

    def process(
        self,
        message_body: str,
        wa_id: str,
        name: str,
        business_context: Optional[dict],
        message_id: Optional[str] = None,
    ) -> str:
        """
        Process incoming message. Phase 1: single-agent fast path.
        Persists state_update from agent to session when non-empty.

        Returns:
            Final response text to send to user.
        """
        business_id = business_context.get("business_id") if business_context else None

        # Load enabled agents for this business (ordered by priority ascending).
        enabled_agents = business_agent_service.get_enabled_agents(business_id or "")

        agent_type = "booking"
        if enabled_agents:
            biz = (business_context or {}).get("business") or {}
            settings = biz.get("settings") or {}
            primary = str(settings.get("conversation_primary_agent") or "").strip().lower()
            if primary:
                match = next(
                    (a for a in enabled_agents if a["agent_type"] == primary),
                    None,
                )
                if match:
                    agent_type = primary
                else:
                    agent_type = enabled_agents[0]["agent_type"]
                    logging.warning(
                        "[CONVERSATION_MANAGER] conversation_primary_agent=%r not in enabled agents; "
                        "using first by priority: %s",
                        primary,
                        agent_type,
                    )
            else:
                agent_type = enabled_agents[0]["agent_type"]

        agents_summary = (
            ", ".join(f"{a['agent_type']}:{a.get('priority')}" for a in enabled_agents)
            if enabled_agents
            else "(none, default booking)"
        )
        logging.warning(
            "[CONVERSATION_MANAGER] Routed to agent=%s | enabled by priority: [%s]",
            agent_type,
            agents_summary,
        )

        output = execute_agent(
            agent_type=agent_type,
            message_body=message_body,
            wa_id=wa_id,
            name=name,
            business_context=business_context,
            message_id=message_id,
        )

        # Persist state_update to session (order completion, active_agents, etc.)
        state_update = output.get("state_update") or {}
        if state_update and wa_id and business_id:
            try:
                session_state_service.save(wa_id, business_id, state_update)
                logging.debug("[CONVERSATION_MANAGER] Persisted state_update to session")
            except Exception as e:
                logging.error(f"[CONVERSATION_MANAGER] Failed to persist state_update: {e}")

        return output.get("message", "Lo siento, no pude procesar tu mensaje.")


# Global instance
conversation_manager = ConversationManager()
