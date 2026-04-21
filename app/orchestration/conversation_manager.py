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
from .router import (
    route as router_route,
    DOMAIN_ORDER,
    DOMAIN_CUSTOMER_SERVICE,
    DOMAIN_CATALOG,
    DOMAIN_CHAT,
)


# Maps a router-classified domain to the agent_type that should handle it.
# Domains for which no dedicated agent/handler exists yet map to None,
# meaning "fall back to the business's primary agent." This lets us ship
# the classifier now and observe its accuracy in LangSmith before wiring
# the dedicated agents.
_DOMAIN_TO_AGENT_TYPE = {
    DOMAIN_ORDER: "order",
    # DOMAIN_CUSTOMER_SERVICE: "customer_service",  # enable when agent lands
    DOMAIN_CUSTOMER_SERVICE: None,
    # DOMAIN_CATALOG: None  (no agent — catalog intents still owned by order)
    DOMAIN_CATALOG: None,
    DOMAIN_CHAT: None,
}


class ConversationManager:
    """Orchestrates message flow: load agents, route, execute, persist session state."""

    def process(
        self,
        message_body: str,
        wa_id: str,
        name: str,
        business_context: Optional[dict],
        message_id: Optional[str] = None,
        stale_turn: bool = False,
        abort_key: Optional[str] = None,
    ) -> str:
        """
        Process incoming message. Phase 1: single-agent fast path.
        Persists state_update from agent to session when non-empty.

        Returns:
            Final response text to send to user.
        """
        business_id = business_context.get("business_id") if business_context else None

        # Router fast-path: pure greetings (and, in later phases, menu
        # queries + business-info queries) are answered directly without
        # invoking any agent. Returns None → fall through to agent dispatch.
        router_result = router_route(
            message_body=message_body,
            business_context=business_context,
            customer_name=name,
        )
        if router_result.direct_reply is not None:
            logging.warning("[CONVERSATION_MANAGER] Router fast-path: direct reply, no agent dispatch")
            return router_result.direct_reply

        # Load enabled agents for this business (ordered by priority ascending).
        enabled_agents = business_agent_service.get_enabled_agents(business_id or "")

        # Router classifier may hint the agent_type. If the hint resolves
        # to an enabled agent, use it; otherwise fall through to the
        # primary agent selection logic below (unchanged behavior).
        # Domains without a dedicated agent (customer_service until Phase 2,
        # catalog, chat) return None and go to the primary agent.
        classifier_hint = None
        if router_result.domain:
            mapped = _DOMAIN_TO_AGENT_TYPE.get(router_result.domain)
            if mapped and any(a["agent_type"] == mapped for a in enabled_agents):
                classifier_hint = mapped
            logging.warning(
                "[CONVERSATION_MANAGER] Router domain=%s → agent_type=%s (hint_applied=%s)",
                router_result.domain, mapped, classifier_hint is not None,
            )

        agent_type = "booking"
        if classifier_hint:
            # Router classifier picked a domain that resolves to an enabled agent.
            # Take precedence over the business's `conversation_primary_agent`.
            agent_type = classifier_hint
        elif enabled_agents:
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
            stale_turn=stale_turn,
            abort_key=abort_key,
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
