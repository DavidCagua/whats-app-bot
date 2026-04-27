"""
TurnContext: the per-turn state snapshot shared across router, dispatcher,
and agent planners.

One DB read per turn for session, last assistant message, and the latest
order's cancellable status. One renderer that produces the prompt block,
so the wording stays uniform across router/order/CS prompts.

Built in ConversationManager.process(); threaded into router.route() and
the dispatcher → each agent. Agents may also build a TurnContext directly
(used by tests that bypass the manager).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ..database import order_lookup_service
from ..database.conversation_service import conversation_service
from ..database.session_state_service import (
    derive_order_state,
    session_state_service,
)
from ..services.order_modification_policy import can_customer_cancel


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnContext:
    """
    Snapshot of cross-cutting state every layer needs to disambiguate
    short / negative / cancel-ish messages.

    - `order_state`: GREETING | ORDERING | COLLECTING_DELIVERY | READY_TO_PLACE.
      Comes from the in-progress session, NOT from placed orders.
    - `has_active_cart`: True when items exist in session order_context.
    - `cart_summary`: one-line human summary of the cart, "" when empty.
    - `last_assistant_message`: most recent assistant turn from history,
      "" when there isn't one.
    - `has_recent_cancellable_order`: a placed order in a status the
      customer is allowed to cancel themselves.
    - `recent_order_id`: id of that order (None otherwise).
    """

    order_state: str = "GREETING"
    has_active_cart: bool = False
    cart_summary: str = ""
    last_assistant_message: str = ""
    has_recent_cancellable_order: bool = False
    recent_order_id: Optional[str] = None


def build_turn_context(
    wa_id: str,
    business_id: Optional[str],
) -> TurnContext:
    """
    Load the per-turn snapshot. Best-effort: every sub-load swallows
    failures and returns the conservative default rather than failing
    the whole turn.
    """
    if not business_id:
        return TurnContext()

    order_state = "GREETING"
    has_active_cart = False
    cart_summary = ""
    try:
        result = session_state_service.load(wa_id, str(business_id))
        session = (result or {}).get("session") or {}
        order_context = session.get("order_context") or {}
        order_state = derive_order_state(order_context)
        items = order_context.get("items") or []
        has_active_cart = bool(items)
        if has_active_cart:
            total = order_context.get("total") or 0
            lines = [
                f"{int(it.get('quantity', 0))}x {it.get('name', '')}".strip()
                for it in items if it.get("name")
            ]
            total_str = f"${int(total):,}".replace(",", ".") if total else ""
            cart_summary = "; ".join(lines)
            if total_str:
                cart_summary = f"{cart_summary}. Subtotal: {total_str}"
    except Exception as exc:
        logger.warning("[TURN_CONTEXT] session load failed: %s", exc)

    last_assistant_message = ""
    try:
        history = conversation_service.get_conversation_history(
            wa_id, limit=4, business_id=str(business_id),
        )
        for entry in reversed(history or []):
            if (entry.get("role") or "").lower() == "assistant":
                last_assistant_message = (entry.get("message") or "").strip()
                break
    except Exception as exc:
        logger.warning("[TURN_CONTEXT] history load failed: %s", exc)

    has_recent_cancellable_order = False
    recent_order_id: Optional[str] = None
    try:
        latest = order_lookup_service.get_latest_order(wa_id, str(business_id))
        if latest and can_customer_cancel(latest.get("status")):
            has_recent_cancellable_order = True
            recent_order_id = str(latest.get("id") or "") or None
    except Exception as exc:
        logger.warning("[TURN_CONTEXT] latest order load failed: %s", exc)

    return TurnContext(
        order_state=order_state,
        has_active_cart=has_active_cart,
        cart_summary=cart_summary,
        last_assistant_message=last_assistant_message,
        has_recent_cancellable_order=has_recent_cancellable_order,
        recent_order_id=recent_order_id,
    )


def render_for_prompt(ctx: TurnContext, include_last_assistant: bool = True) -> str:
    """
    Render the turn context block injected into router/order/CS planner
    prompts. One canonical wording so the three layers don't drift.
    Returns a single string, ready to drop into a system or user prompt.
    """
    lines = [f"Estado del pedido: {ctx.order_state}"]
    if ctx.has_active_cart and ctx.cart_summary:
        lines.append(f"Carrito actual: {ctx.cart_summary}")
    else:
        lines.append("Carrito actual: vacío")
    if ctx.has_recent_cancellable_order:
        lines.append("Pedido confirmado pendiente: sí (cancelable)")
    else:
        lines.append("Pedido confirmado pendiente: no")
    if include_last_assistant and ctx.last_assistant_message:
        snippet = ctx.last_assistant_message
        if len(snippet) > 240:
            snippet = snippet[:240].rstrip() + "…"
        lines.append(f'Última respuesta del bot: "{snippet}"')
    return "\n".join(lines)
