"""
ConversationManager: Entry point for message processing.

Flow per turn:
  1. Greeting fast-path (router) — pure greetings reply directly.
  2. LLM classifier (router) — decomposes message into (domain, text)
     segments. Length 1 for single-intent, 2-3 for mixed-intent.
  3. Map each segment's domain → agent_type using the business's
     enabled agents. Unmapped / unavailable → fall back to primary.
  4. Coalesce consecutive same-agent segments (prevents double
     invocation when router over-decomposes).
  5. Dispatch the resulting list. Dispatcher handles handoff chains
     and state persistence.
"""

import logging
from typing import List, Optional, Tuple

from ..database.business_agent_service import business_agent_service
from .dispatcher import dispatch
from .router import (
    route as router_route,
    DOMAIN_ORDER,
    DOMAIN_CUSTOMER_SERVICE,
    DOMAIN_CATALOG,
    DOMAIN_CHAT,
)


# Maps a router-classified domain to the agent_type that should handle it.
# Domains for which no dedicated agent/handler exists yet map to None,
# meaning "fall back to the business's primary agent."
_DOMAIN_TO_AGENT_TYPE = {
    DOMAIN_ORDER: "order",
    DOMAIN_CUSTOMER_SERVICE: "customer_service",
    # catalog intents are currently handled by the order agent's
    # remaining planner branches — fall back to primary.
    DOMAIN_CATALOG: None,
    DOMAIN_CHAT: None,
}


def _resolve_primary_agent(
    enabled_agents: List[dict],
    business_context: Optional[dict],
) -> str:
    """
    Pick the business's primary agent for fallback cases: no classifier
    output, unmapped domains, or unenabled mapped agents.

    Priority order:
      1. business.settings.conversation_primary_agent if it maps to an
         enabled agent.
      2. First enabled agent by priority.
      3. Last-resort default: "booking".
    """
    if not enabled_agents:
        return "booking"

    biz = (business_context or {}).get("business") or {}
    settings = biz.get("settings") or {}
    primary = str(settings.get("conversation_primary_agent") or "").strip().lower()
    if primary and any(a["agent_type"] == primary for a in enabled_agents):
        return primary
    if primary:
        logging.warning(
            "[CONVERSATION_MANAGER] conversation_primary_agent=%r not in enabled agents; "
            "using first by priority",
            primary,
        )
    return enabled_agents[0]["agent_type"]


def _coalesce(segments: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """
    Merge consecutive segments that resolve to the same agent_type.
    Avoids double-invoking an agent when the router over-decomposes
    (e.g. splits a single-domain message into adjacent segments).
    """
    if not segments:
        return segments
    out: List[Tuple[str, str]] = []
    for agent_type, text in segments:
        if out and out[-1][0] == agent_type:
            merged_text = f"{out[-1][1]}\n{text}"
            out[-1] = (agent_type, merged_text)
        else:
            out.append((agent_type, text))
    return out


def _build_dispatch_segments(
    router_segments: Optional[List[Tuple[str, str]]],
    enabled_agents: List[dict],
    primary_agent_type: str,
    full_message: str,
) -> List[Tuple[str, str]]:
    """
    Build the (agent_type, text) list the dispatcher will run. Applies
    domain→agent mapping, fallback for unmapped/unavailable agents, and
    coalesces consecutive same-agent segments.
    """
    if not router_segments:
        # Classifier failed or empty — run primary on the whole message.
        return [(primary_agent_type, full_message)]

    enabled_types = {a["agent_type"] for a in enabled_agents}

    mapped: List[Tuple[str, str]] = []
    for domain, text in router_segments:
        target = _DOMAIN_TO_AGENT_TYPE.get(domain)
        if not target or target not in enabled_types:
            target = primary_agent_type
        mapped.append((target, text))

    return _coalesce(mapped)


class ConversationManager:
    """Orchestrates message flow: route → dispatch."""

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
        Process incoming message, return the final user-facing reply.
        """
        business_id = business_context.get("business_id") if business_context else None

        # 1. Router fast-path: pure greetings reply directly.
        router_result = router_route(
            message_body=message_body,
            business_context=business_context,
            customer_name=name,
        )
        if router_result.direct_reply is not None:
            logging.warning("[CONVERSATION_MANAGER] Router fast-path: direct reply, no agent dispatch")
            return router_result.direct_reply

        # 2. Pick primary + build dispatch segments from router output.
        enabled_agents = business_agent_service.get_enabled_agents(business_id or "")
        primary_agent_type = _resolve_primary_agent(enabled_agents, business_context)
        dispatch_segments = _build_dispatch_segments(
            router_segments=router_result.segments,
            enabled_agents=enabled_agents,
            primary_agent_type=primary_agent_type,
            full_message=message_body,
        )

        agents_summary = ", ".join(f"{a['agent_type']}:{a.get('priority')}" for a in enabled_agents) \
            if enabled_agents else "(none, default booking)"
        logging.warning(
            "[CONVERSATION_MANAGER] enabled=[%s] primary=%s router_segments=%s dispatch=%s",
            agents_summary,
            primary_agent_type,
            [d for d, _ in (router_result.segments or [])],
            [t for t, _ in dispatch_segments],
        )

        # 3. Dispatch. Dispatcher handles handoffs, state persistence,
        # abort check between hops, and composer invocation when
        # multiple agents produced non-empty output.
        dispatch_result = dispatch(
            segments=dispatch_segments,
            wa_id=wa_id,
            name=name,
            business_context=business_context,
            message_id=message_id,
            stale_turn=stale_turn,
            abort_key=abort_key,
        )

        if dispatch_result.handoff_chain and len(dispatch_result.handoff_chain) > 1:
            logging.warning(
                "[CONVERSATION_MANAGER] Multi-agent turn chain=%s",
                dispatch_result.handoff_chain,
            )

        return dispatch_result.message or "Lo siento, no pude procesar tu mensaje."


# Global instance
conversation_manager = ConversationManager()
