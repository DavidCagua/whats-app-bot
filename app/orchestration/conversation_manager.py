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
from ..database.conversation_service import conversation_service
from .dispatcher import dispatch
from .router import (
    route as router_route,
    DOMAIN_ORDER,
    DOMAIN_CUSTOMER_SERVICE,
    DOMAIN_CHAT,
)
from .turn_context import build_turn_context


# Maps a router-classified domain to the agent_type that should handle it.
# Domains for which no dedicated agent/handler exists yet map to None,
# meaning "fall back to the business's primary agent."
#
# Note: there is no `catalog` domain — browsing is part of the "order"
# user concern. See docs/agents-vs-services.md for the principle.
_DOMAIN_TO_AGENT_TYPE = {
    DOMAIN_ORDER: "order",
    DOMAIN_CUSTOMER_SERVICE: "customer_service",
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


def _coalesce_by_domain_and_agent(
    triples: List[Tuple[str, str, str]],
) -> List[Tuple[str, str]]:
    """
    Merge consecutive segments that share BOTH the router's emitted domain
    AND the resolved agent_type.

    Why both: two segments only represent the same logical user intent
    when the router itself called them the same domain. Different
    router domains that happen to fall back to the same agent (e.g.
    catalog → order via primary fallback) are distinct intents and must
    stay as separate dispatcher invocations — otherwise the receiving
    agent's planner has to compress N intents into one classification
    and ends up dropping all but the most prominent one.

    Returns (agent_type, text) pairs ready for the dispatcher.
    """
    if not triples:
        return []
    out: List[Tuple[str, str, str]] = []
    for domain, agent_type, text in triples:
        if out and out[-1][0] == domain and out[-1][1] == agent_type:
            merged_text = f"{out[-1][2]}\n{text}"
            out[-1] = (domain, agent_type, merged_text)
        else:
            out.append((domain, agent_type, text))
    return [(agent_type, text) for _domain, agent_type, text in out]


def _build_dispatch_segments(
    router_segments: Optional[List[Tuple[str, str]]],
    enabled_agents: List[dict],
    primary_agent_type: str,
    full_message: str,
) -> List[Tuple[str, str]]:
    """
    Build the (agent_type, text) list the dispatcher will run.

    Steps:
      1. Map each router segment's domain → agent_type. Unmapped or
         unavailable agents fall back to the primary agent.
      2. Coalesce only when consecutive segments have the same router
         domain AND the same final agent_type. Different domains stay
         separate even if they end up on the same agent (preserves the
         user's distinct intents).
    """
    if not router_segments:
        # Classifier failed or empty — run primary on the whole message.
        return [(primary_agent_type, full_message)]

    enabled_types = {a["agent_type"] for a in enabled_agents}

    triples: List[Tuple[str, str, str]] = []
    for domain, text in router_segments:
        target = _DOMAIN_TO_AGENT_TYPE.get(domain)
        if not target or target not in enabled_types:
            target = primary_agent_type
        triples.append((domain, target, text))

    return _coalesce_by_domain_and_agent(triples)


# Backward-compat alias kept for tests that import the old name. The
# old _coalesce signature took (agent_type, text) pairs; the new
# coalesce key includes domain so we can't preserve that exact shape.
# Tests of the old function should switch to _coalesce_by_domain_and_agent.
def _coalesce(segments: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Deprecated: use _coalesce_by_domain_and_agent. Kept for tests."""
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
        attachments: Optional[list] = None,
    ) -> str:
        """
        Process incoming message, return the final user-facing reply.
        """
        business_id = business_context.get("business_id") if business_context else None

        # Refresh business.settings from the DB on every turn. The
        # business_context arrives from a 5-min TTL cache (see
        # business_service._phone_ctx_cache), which is fine for static
        # fields (name, whatsapp_number_id) but unacceptable for
        # operator-controlled toggles on the orders page — pausing
        # deliveries or changing the ETA override has to take effect
        # on the next message, not after 5 min or a runtime restart.
        # Single indexed PK lookup; cost is negligible. The rest of
        # business_context stays cached.
        if business_id and business_context is not None:
            try:
                from ..database.business_service import business_service
                fresh_settings = business_service.get_business_settings_fresh(
                    str(business_id),
                )
                biz = business_context.get("business")
                if isinstance(biz, dict):
                    biz["settings"] = fresh_settings
            except Exception as exc:
                logging.warning(
                    "[CONVERSATION_MANAGER] fresh-settings overlay failed "
                    "business=%s: %s", business_id, exc,
                )

        # Build the per-turn snapshot ONCE — shared by router and (via
        # dispatcher) downstream planners. Avoids each layer re-reading
        # session / history / latest order on its own.
        turn_ctx = build_turn_context(wa_id=wa_id, business_id=business_id)

        # Order-availability gate, computed once per turn. Pure-data
        # check; no LLM. The router uses it to announce the closed
        # state on greetings, and the order agent uses it to block
        # cart-mutating intents (it'll re-load it inside its own
        # execute() — the gate function is cheap and idempotent).
        # Honors business.settings.order_gate_enabled (default True).
        # When the business has no availability rows configured,
        # is_taking_orders_now returns can_take_orders=True so the
        # gate is effectively no-op for unconfigured tenants.
        order_gate: Optional[dict] = None
        try:
            _bg_settings = ((business_context or {}).get("business") or {}).get("settings") or {}
            if _bg_settings.get("order_gate_enabled", True) is not False:
                from ..services import business_info_service as _bi_svc
                order_gate = _bi_svc.is_taking_orders_now(
                    str(business_id),
                    business_settings=_bg_settings,
                )
        except Exception as exc:
            logging.warning(
                "[ORDER_GATE] greeting compute failed (defaulting to open): %s", exc,
            )
            order_gate = None

        # 1. Router fast-path: pure greetings reply directly.
        # Pass wa_id + message_id (used as turn_id) into the router so its
        # LangSmith spans are filterable by user and so all LLM spans
        # within this turn share a correlation id — debug-conversation
        # depends on this to reconstruct router → planner → response as
        # one tree per inbound message.
        router_result = router_route(
            message_body=message_body,
            business_context=business_context,
            customer_name=name,
            ctx=turn_ctx,
            wa_id=wa_id,
            turn_id=message_id,
            gate=order_gate,
            attachments=attachments,
        )
        if router_result.direct_reply is not None:
            logging.warning("[CONVERSATION_MANAGER] Router fast-path: direct reply, no agent dispatch")
            whatsapp_number_id = (business_context or {}).get("whatsapp_number_id")

            # Send welcome as a Twilio CTA Content Template (button card)
            # when the business has the appropriate SID configured. Two
            # templates are supported per business:
            #   - welcome_content_sid (open-state)
            #   - welcome_closed_content_sid (closed-state, optional)
            # The gate computed above selects which one — when closed
            # AND no closed SID is configured, cta_welcome_payload
            # returns None so we fall back to the plain-text greeting
            # (which already includes the closed sentence inline).
            cta = None
            try:
                from ..services import business_greeting as _bg
                cta = _bg.cta_welcome_payload(business_context, name, gate=order_gate)
                _gate_reason = (order_gate or {}).get("reason") or "unknown"
                _kind = (cta or {}).get("kind") or ("plaintext" if cta is None else "cta")
                logging.warning(
                    "[ORDER_GATE] greeting business=%s wa_id=%s gate=%s kind=%s",
                    business_id, wa_id, _gate_reason, _kind,
                )
            except Exception as exc:
                logging.warning(f"[CONVERSATION_MANAGER] cta_welcome_payload failed: {exc}")

            if cta is not None:
                try:
                    from ..utils.whatsapp_utils import send_twilio_cta
                    sent = send_twilio_cta(
                        content_sid=cta["content_sid"],
                        variables=cta["variables"],
                        to=wa_id,
                        business_context=business_context,
                    )
                except Exception as exc:
                    logging.error(f"[CONVERSATION_MANAGER] CTA send raised: {exc}")
                    sent = None
                if sent is not None:
                    try:
                        conversation_service.store_conversation_message(
                            wa_id=wa_id,
                            message=cta["rendered_body"],
                            role="assistant",
                            business_id=business_id,
                            whatsapp_number_id=whatsapp_number_id,
                        )
                    except Exception as e:
                        logging.error(f"[CONVERSATION_MANAGER] CTA persist failed: {e}")
                    logging.warning("[CONVERSATION_MANAGER] greeting sent via CTA Content Template")
                    return "__SUPPRESS_SEND__"
                logging.warning(
                    "[CONVERSATION_MANAGER] CTA send failed — falling back to plain-text greeting"
                )

            # Agent paths persist their own assistant turns inline; the fast-path
            # bypasses dispatch entirely, so persist here. Without this, greetings
            # show no bot turn in the inbox UI and never fire the inbox_event NOTIFY.
            try:
                conversation_service.store_conversation_message(
                    wa_id=wa_id,
                    message=router_result.direct_reply,
                    role="assistant",
                    business_id=business_id,
                    whatsapp_number_id=whatsapp_number_id,
                )
            except Exception as e:
                logging.error(f"[CONVERSATION_MANAGER] Failed to persist fast-path assistant turn: {e}")
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

        # If the router detected a multi-word catalog product name in
        # the message, surface it on the per-turn snapshot so the
        # order planner sees ``Producto reconocido en el mensaje:``
        # in its prompt context and won't redirect to a previously-
        # listed option.
        if router_result.recognized_product:
            from dataclasses import replace as _dc_replace
            turn_ctx = _dc_replace(turn_ctx, recognized_product=router_result.recognized_product)

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
            turn_ctx=turn_ctx,
            attachments=attachments,
        )

        if dispatch_result.handoff_chain and len(dispatch_result.handoff_chain) > 1:
            logging.warning(
                "[CONVERSATION_MANAGER] Multi-agent turn chain=%s",
                dispatch_result.handoff_chain,
            )

        # Dispatcher's abort path consumes the abort flag + requeues the
        # text but returns an empty message. Surface the aborted state via
        # the __ABORTED__ sentinel so the handler drops the send instead
        # of falling back to "Lo siento, no pude procesar tu mensaje."
        # — which would land in the customer's chat as a spurious reply
        # right when their newer message is about to be processed.
        if getattr(dispatch_result, "aborted", False):
            return "__ABORTED__"

        return dispatch_result.message or "Lo siento, no pude procesar tu mensaje."


# Global instance
conversation_manager = ConversationManager()
