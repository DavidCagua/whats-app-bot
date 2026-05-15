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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..database import order_lookup_service
from ..database.conversation_service import conversation_service
from ..database.customer_service import customer_service
from ..database.session_state_service import (
    derive_order_state,
    session_state_service,
)
from ..services.order_modification_policy import can_customer_cancel
from ..services.order_status_machine import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    is_terminal,
)


logger = logging.getLogger(__name__)


# Terminal-status orders only count as "the latest order" for prompt
# context within this window. Without it, a months-old `completed`
# order would forever bias every fresh greeting toward thank-you
# templates. Active-status orders (pending / confirmed /
# out_for_delivery / ready_for_pickup) are always relevant — a
# customer asking about an order 90 min later is still talking about
# that order.
_TERMINAL_RELEVANCE_MINUTES = 30


# How many history messages every layer (router / order planner /
# CS planner) sees. Uniform across layers so the LLM has the same
# stateful view regardless of which planner is running. Each message
# is truncated to _HISTORY_MSG_MAX_CHARS so a long bot reply (full
# menu listing, order summary) doesn't blow the prompt budget.
_HISTORY_MAX_MESSAGES = 10
_HISTORY_MSG_MAX_CHARS = 240


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
      "" when there isn't one. (Backward-compat — the renderer prefers
      ``recent_history`` when set.)
    - `recent_history`: list of (role, message) tuples for the last
      ``_HISTORY_MAX_MESSAGES`` turns, oldest first. Each message is
      already truncated. Surfaces the full conversational state to
      every layer (router, order planner, CS planner) so they
      classify intent against context, not just the bare message.
    - `has_recent_cancellable_order`: a placed order in a status the
      customer is allowed to cancel themselves.
    - `recent_order_id`: id of that cancellable order (None otherwise).
    - `latest_order_status`: status of the most recent placed order
      (any of the 5 OrderStatus values) when relevant for prompt
      context: always for active states, only within
      _TERMINAL_RELEVANCE_MINUTES for terminal states. None when
      stale or absent. Used by planner / response generator to
      disambiguate polite-close turns ("si gracias") immediately
      after PLACE_ORDER.
    - `latest_order_id`: id of that order. None when
      ``latest_order_status`` is None.
    """

    order_state: str = "GREETING"
    has_active_cart: bool = False
    cart_summary: str = ""
    # Delivery info captured for the in-progress order. Empty dict
    # when nothing's been collected. Keys: name, address, phone,
    # payment_method. Surfaced via render_for_prompt so every layer
    # (router / order / CS) sees the same view of "what we already
    # have on the customer" — prevents the order agent from
    # redundantly re-saving and lets CS answer "what address did I
    # give?" without round-tripping a tool call.
    delivery_info: Dict[str, str] = field(default_factory=dict)
    # True between a ready_to_confirm dispatch and the user's reply.
    # Set by the order agent on CTA send; cleared on next-turn
    # envelope != ready_to_confirm. Surfacing it to the router lets
    # short affirmatives like "ok" / "dale" stay routed to order
    # instead of being mis-classified.
    awaiting_confirmation: bool = False
    # 'delivery' (default) or 'pickup'. Persisted in
    # ``order_context.fulfillment_type`` so it survives across turns and
    # flows into ``orders.fulfillment_type`` on place_order. Default is
    # delivery — pickup mode is set ONLY when the user explicitly says
    # so ("lo recojo", "para recoger", "en el local"). Surfaced to every
    # layer (router / order / CS) via render_for_prompt so the model
    # doesn't have to re-derive current mode from history each turn.
    fulfillment_type: str = "delivery"
    # Order-level notes captured during the conversation: pickup time,
    # callback requests, change/cash requests, "déjenlo en portería",
    # etc. NOT product modifications (those live on each cart line).
    # Flushed into ``orders.notes`` at place_order time. Surfaced to
    # every layer so the model never re-asks for a note already on
    # file.
    order_notes: str = ""
    last_assistant_message: str = ""
    recent_history: Tuple[Tuple[str, str], ...] = ()
    has_recent_cancellable_order: bool = False
    recent_order_id: Optional[str] = None
    latest_order_status: Optional[str] = None
    latest_order_id: Optional[str] = None
    # Set by the router when it detects a multi-word catalog product
    # name as a contiguous substring of the user's message. The
    # order planner reads this so it doesn't redirect to a
    # previously-listed option (Biela / 3147554464, 2026-05-06: user
    # said "Tienes la a la Vuelta?", planner picked HONEY BURGER from
    # a recent listing and stuffed "a la vuelta" into notes).
    recognized_product: Optional[str] = None


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
    delivery_info: Dict[str, str] = {}
    awaiting_confirmation = False
    fulfillment_type = "delivery"
    order_notes = ""
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
        # Pull saved delivery fields so every layer sees the same
        # "what's already on file" view. Keep only non-empty strings
        # so render_for_prompt can decide whether to print the line.
        raw_di = order_context.get("delivery_info") or {}
        if isinstance(raw_di, dict):
            delivery_info = {
                k: str(v).strip()
                for k, v in raw_di.items()
                if k in ("name", "address", "phone", "payment_method")
                and isinstance(v, (str, int))
                and str(v).strip()
            }
        awaiting_confirmation = bool(order_context.get("awaiting_confirmation"))
        raw_ftype = (order_context.get("fulfillment_type") or "").strip().lower()
        if raw_ftype in ("delivery", "pickup"):
            fulfillment_type = raw_ftype
        order_notes = (order_context.get("notes") or "").strip()

        # Hydrate the in-memory delivery_info from the customer DB on
        # every turn — including before a cart exists — so the planner
        # never re-prompts a returning customer for data we already
        # have on file ("Hola para un domicilio"). Writeback to the
        # session, however, is gated on an active cart: writing
        # delivery_info into session_state before any item is added
        # would let derive_order_state jump GREETING → READY_TO_PLACE
        # on the first add_item, skipping ORDERING. As soon as a cart
        # is open, the next turn's hydration writes through so
        # place_order / the renderer / the agent prompt all see the
        # same view. Session fields always win over DB fields, so an
        # explicit submit_delivery_info edit is never clobbered.
        # Pickup mode only needs name; address / phone / payment_method
        # don't apply.
        try:
            cust = customer_service.get_customer(wa_id) or {}
        except Exception as exc:
            logger.warning("[TURN_CONTEXT] customer load failed: %s", exc)
            cust = {}
        candidate_fields = (
            ("name",)
            if fulfillment_type == "pickup"
            else ("name", "address", "phone", "payment_method")
        )
        hydrated: Dict[str, str] = {}
        for fld in candidate_fields:
            if delivery_info.get(fld):
                continue
            raw = cust.get(fld)
            if isinstance(raw, (str, int)):
                val = str(raw).strip()
                if val:
                    hydrated[fld] = val
        if hydrated:
            delivery_info = {**delivery_info, **hydrated}
            if has_active_cart:
                try:
                    merged_di = dict(raw_di) if isinstance(raw_di, dict) else {}
                    for fld, val in hydrated.items():
                        if not (merged_di.get(fld) or ""):
                            merged_di[fld] = val
                    session_state_service.save(
                        wa_id,
                        str(business_id),
                        {"order_context": {"delivery_info": merged_di}},
                    )
                except Exception as exc:
                    logger.warning(
                        "[TURN_CONTEXT] delivery_info hydration writeback failed: %s",
                        exc,
                    )
    except Exception as exc:
        logger.warning("[TURN_CONTEXT] session load failed: %s", exc)

    last_assistant_message = ""
    recent_history: List[Tuple[str, str]] = []
    try:
        history = conversation_service.get_conversation_history(
            wa_id, limit=_HISTORY_MAX_MESSAGES, business_id=str(business_id),
        )
        for entry in (history or []):
            role = (entry.get("role") or "").strip().lower()
            msg = (entry.get("message") or "").strip()
            if not role or not msg:
                continue
            # Remap manually-typed operator messages so planners can see
            # the seam. Persisted as role='assistant' + agent_type='operator'
            # by the admin-send endpoint; here we expose them as
            # role='operator' in the rolling history so render_for_prompt
            # can label them distinctly. Bot-generated assistant turns keep
            # role='assistant' (agent_type is None for those).
            agent_type = (entry.get("agent_type") or "").strip().lower()
            if role == "assistant" and agent_type == "operator":
                role = "operator"
            if len(msg) > _HISTORY_MSG_MAX_CHARS:
                msg = msg[:_HISTORY_MSG_MAX_CHARS].rstrip() + "…"
            recent_history.append((role, msg))
        # Backward-compat: keep last_assistant_message populated from
        # the same history load so existing callers don't break. Operator
        # turns DON'T count — last_assistant_message is meant to be
        # "what the bot last said" so the planner can react to its own
        # prior question.
        for role, msg in reversed(recent_history):
            if role == "assistant":
                last_assistant_message = msg
                break
    except Exception as exc:
        logger.warning("[TURN_CONTEXT] history load failed: %s", exc)

    has_recent_cancellable_order = False
    recent_order_id: Optional[str] = None
    latest_order_status: Optional[str] = None
    latest_order_id: Optional[str] = None
    try:
        latest = order_lookup_service.get_latest_order(wa_id, str(business_id))
        if latest:
            if can_customer_cancel(latest.get("status")):
                has_recent_cancellable_order = True
                recent_order_id = str(latest.get("id") or "") or None
            if _latest_order_is_relevant(latest):
                latest_order_status = (latest.get("status") or "").strip() or None
                latest_order_id = str(latest.get("id") or "") or None
    except Exception as exc:
        logger.warning("[TURN_CONTEXT] latest order load failed: %s", exc)

    return TurnContext(
        order_state=order_state,
        has_active_cart=has_active_cart,
        cart_summary=cart_summary,
        delivery_info=delivery_info,
        awaiting_confirmation=awaiting_confirmation,
        fulfillment_type=fulfillment_type,
        order_notes=order_notes,
        last_assistant_message=last_assistant_message,
        recent_history=tuple(recent_history),
        has_recent_cancellable_order=has_recent_cancellable_order,
        recent_order_id=recent_order_id,
        latest_order_status=latest_order_status,
        latest_order_id=latest_order_id,
    )


def _latest_order_is_relevant(order: Dict[str, Any]) -> bool:
    """
    Decide whether the most-recent order should surface in the prompt.

    - Active states (not terminal) → always relevant.
    - Terminal states (completed / cancelled) → only within
      ``_TERMINAL_RELEVANCE_MINUTES`` of the terminal timestamp
      (``completed_at`` for completed, ``cancelled_at`` for cancelled).
      Without that timestamp we conservatively drop it.
    """
    status = (order.get("status") or "").strip()
    if not status:
        return False
    if not is_terminal(status):
        return True
    ts_raw: Optional[str] = None
    if status == STATUS_COMPLETED:
        ts_raw = order.get("completed_at")
    elif status == STATUS_CANCELLED:
        ts_raw = order.get("cancelled_at")
    if not ts_raw:
        return False
    ts = _parse_iso_aware(ts_raw)
    if ts is None:
        return False
    age = datetime.now(tz=timezone.utc) - ts
    return age <= timedelta(minutes=_TERMINAL_RELEVANCE_MINUTES)


def _parse_iso_aware(raw: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp; ensure it's timezone-aware (assume UTC if naive)."""
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    # datetime.fromisoformat handles "+00:00" but not the common "Z" suffix
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def render_for_prompt(ctx: TurnContext, include_last_assistant: bool = True) -> str:
    """
    Render the turn context block injected into router/order/CS planner
    prompts. One canonical wording so the three layers don't drift.
    Returns a single string, ready to drop into a system or user prompt.

    When ``ctx.recent_history`` is populated, emits a multi-turn
    ``Historial reciente:`` block (oldest first). Otherwise falls back
    to the ``Última respuesta del bot:`` single-line legacy form so
    callers that build TurnContext by hand (e.g. tests) still get
    something useful.
    """
    lines = [f"Estado del pedido: {ctx.order_state}"]
    if ctx.has_active_cart and ctx.cart_summary:
        lines.append(f"Carrito actual: {ctx.cart_summary}")
    else:
        lines.append("Carrito actual: vacío")
    # Mode line: render only when the customer explicitly switched to
    # pickup. The 'delivery' default doesn't need to clutter the
    # context — every layer already treats delivery as the default
    # behavior, and emitting "Modo: 🛵 Domicilio (default ...)" on
    # every turn introduces noise that can bias the router toward
    # "this user is mid-order" on greetings.
    if (ctx.fulfillment_type or "delivery") == "pickup":
        lines.append("Modo: 🏃 Recoger en local (pickup) — el cliente lo indicó explícitamente")
    # Order-level notes already on file. Surfacing this stops the
    # model from re-asking ("¿algo más para indicar?") when the user
    # already gave a note, and lets it amend cleanly when the user
    # changes their mind. Render only when present — the empty case
    # adds no signal.
    if ctx.order_notes:
        lines.append(f"Notas del pedido (ya guardadas): \"{ctx.order_notes}\"")
    # Delivery info already on file. Surfacing it to all layers stops
    # the order agent from re-saving fields it already has, lets CS
    # answer "what address did I give?" without a tool call, and gives
    # the router a stronger signal that the user is mid-checkout.
    if ctx.delivery_info:
        di_parts = []
        if ctx.delivery_info.get("name"):
            di_parts.append(f"nombre={ctx.delivery_info['name']}")
        if ctx.delivery_info.get("address"):
            di_parts.append(f"dirección={ctx.delivery_info['address']}")
        if ctx.delivery_info.get("phone"):
            di_parts.append(f"teléfono={ctx.delivery_info['phone']}")
        if ctx.delivery_info.get("payment_method"):
            di_parts.append(f"pago={ctx.delivery_info['payment_method']}")
        complete = all(
            ctx.delivery_info.get(k)
            for k in ("name", "address", "phone", "payment_method")
        )
        suffix = " (completos)" if complete else " (parciales)"
        lines.append("Datos de entrega ya guardados" + suffix + ": " + " | ".join(di_parts))
    # Empty case: omit the line entirely. "Datos de entrega: ninguno"
    # adds no signal and biases the LLM toward "this user is mid-checkout"
    # on greeting / chat turns where there's nothing to render.
    # awaiting_confirmation: set after the order agent dispatched a
    # ready_to_confirm prompt. Tells every layer "the next user
    # message is responding to a confirm prompt" so short
    # affirmatives ('ok', 'dale') stay routed to order and the order
    # agent itself doesn't re-send a second card.
    if ctx.awaiting_confirmation:
        lines.append(
            "Esperando confirmación del cliente: SÍ "
            "(el bot ya envió la tarjeta/mensaje de confirmación; "
            "una respuesta afirmativa significa colocar el pedido)"
        )
    if ctx.has_recent_cancellable_order:
        lines.append("Pedido confirmado pendiente: sí (cancelable)")
    else:
        lines.append("Pedido confirmado pendiente: no")
    if ctx.latest_order_status:
        # Visible to planners and response templates so they can branch
        # on whether the user just completed an order, has one in
        # flight, or is talking after a cancellation.
        lines.append(f"Último pedido (estado): {ctx.latest_order_status}")
    if ctx.recognized_product:
        # Surfaced by the router when a multi-word catalog product
        # name appears in the message. The order planner MUST honor
        # this and not redirect to a previously-listed option.
        lines.append(f"Producto reconocido en el mensaje: {ctx.recognized_product}")
    if ctx.recent_history:
        # Render the rolling window so every layer sees the same
        # stateful view (router was previously starved of user-turn
        # history; uniform 10-msg window closes that gap). Operator
        # turns get a distinct label so the planner doesn't treat
        # human-typed messages as its own prior reasoning.
        lines.append("Historial reciente (más antiguo arriba):")
        for role, msg in ctx.recent_history:
            if role == "user":
                label = "usuario"
            elif role == "assistant":
                label = "bot"
            elif role == "operator":
                label = "operador (humano)"
            else:
                label = role
            lines.append(f"  {label}: {msg}")
    elif include_last_assistant and ctx.last_assistant_message:
        snippet = ctx.last_assistant_message
        if len(snippet) > _HISTORY_MSG_MAX_CHARS:
            snippet = snippet[:_HISTORY_MSG_MAX_CHARS].rstrip() + "…"
        lines.append(f'Última respuesta del bot: "{snippet}"')
    return "\n".join(lines)
