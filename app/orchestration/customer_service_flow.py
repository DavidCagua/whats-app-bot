"""
Customer service flow: executor for the customer service agent.

Parallel to order_flow.py but much simpler — all intents here are
READ-ONLY. No cart mutation, no order creation, no schema writes.

Structure:
    INTENT_* constants            — labels the planner may emit
    RESULT_KIND_* constants       — routing signal for response generator
    execute_customer_service_intent(...) — main entry point

Result dict shape (returned to caller / response generator):
    {
      "result_kind": str,
      "success": bool,
      "business_id": str,
      "wa_id": str,
      # plus intent-specific payload fields
    }
"""

import logging
from typing import Any, Dict, List, Optional

from ..database import order_lookup_service
from ..database import order_modification_service
from ..services import business_info_service
from ..services import promotion_service
from ..services.order_eta import estimate_remaining_minutes
from ..services.order_modification_policy import (
    can_customer_cancel,
    CANCEL_REASON_CUSTOMER_WHATSAPP,
)
from ..services.order_status_machine import InvalidStatusTransition


logger = logging.getLogger(__name__)


# Intents (planner output)
INTENT_GET_BUSINESS_INFO = "GET_BUSINESS_INFO"
INTENT_GET_ORDER_STATUS = "GET_ORDER_STATUS"
INTENT_GET_ORDER_HISTORY = "GET_ORDER_HISTORY"
INTENT_CANCEL_ORDER = "CANCEL_ORDER"
INTENT_GET_PROMOS = "GET_PROMOS"
INTENT_SELECT_LISTED_PROMO = "SELECT_LISTED_PROMO"
INTENT_CUSTOMER_SERVICE_CHAT = "CUSTOMER_SERVICE_CHAT"

VALID_INTENTS = {
    INTENT_GET_BUSINESS_INFO,
    INTENT_GET_ORDER_STATUS,
    INTENT_GET_ORDER_HISTORY,
    INTENT_CANCEL_ORDER,
    INTENT_GET_PROMOS,
    INTENT_SELECT_LISTED_PROMO,
    INTENT_CUSTOMER_SERVICE_CHAT,
}


# Result kinds — drives response generator branching
RESULT_KIND_BUSINESS_INFO = "business_info"
RESULT_KIND_INFO_MISSING = "info_missing"
RESULT_KIND_ORDER_STATUS = "order_status"
RESULT_KIND_NO_ORDER = "no_order"
RESULT_KIND_ORDER_HISTORY = "order_history"
RESULT_KIND_ORDER_CANCELLED = "order_cancelled"
RESULT_KIND_CANCEL_NOT_ALLOWED = "cancel_not_allowed"
RESULT_KIND_PROMOS_LIST = "promos_list"
RESULT_KIND_NO_PROMOS = "no_promos"
RESULT_KIND_PROMO_NOT_RESOLVED = "promo_not_resolved"
RESULT_KIND_PROMO_AMBIGUOUS = "promo_ambiguous"
RESULT_KIND_CHAT_FALLBACK = "cs_chat_fallback"
RESULT_KIND_INTERNAL_ERROR = "cs_internal_error"
# Signal that the agent should hand off mid-turn. The agent's execute()
# translates this into the `handoff` field of its AgentOutput so the
# dispatcher picks up the next agent.
RESULT_KIND_HANDOFF = "cs_handoff"


def _base_result(
    wa_id: str,
    business_id: str,
    result_kind: str,
    success: bool = True,
    **extra: Any,
) -> Dict[str, Any]:
    result = {
        "result_kind": result_kind,
        "success": success,
        "business_id": business_id,
        "wa_id": wa_id,
    }
    result.update(extra)
    return result


def _clean_order_for_response(order: Dict[str, Any]) -> Dict[str, Any]:
    """Strip internal-only fields before passing to the response generator.

    Adds `eta_minutes`: an approximate remaining wait time in minutes,
    derived from the current state and (for `confirmed`) the elapsed
    time since `confirmed_at`. None for terminal states. The agent
    surfaces this only when the customer asks about timing.
    """
    return {
        "id": order.get("id"),
        "status": order.get("status"),
        "total_amount": order.get("total_amount"),
        "delivery_address": order.get("delivery_address"),
        "payment_method": order.get("payment_method"),
        "notes": order.get("notes"),
        "cancellation_reason": order.get("cancellation_reason"),
        "created_at": order.get("created_at"),
        "eta_minutes": estimate_remaining_minutes({
            "status": order.get("status"),
            "confirmed_at": order.get("confirmed_at"),
        }),
        "items": order.get("items") or [],
    }


def execute_customer_service_intent(
    *,
    wa_id: str,
    business_id: str,
    business_context: Optional[Dict[str, Any]],
    intent: str,
    params: Dict[str, Any],
    session: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Deterministic executor for customer service intents.

    Backend is single source of truth; response generator receives
    structured data and does not re-parse tool strings.

    `session` is read-only (passed through from agent_executor). Used
    to resolve ambiguous intents like "qué tengo en mi pedido?" where
    the router may have routed here but the user's active cart means
    the correct owner is the order agent — emit a handoff result.
    """
    intent = (intent or "").upper().strip()
    params = params or {}

    if intent not in VALID_INTENTS:
        logger.warning("[CS_FLOW] unknown intent %r — falling back to chat", intent)
        intent = INTENT_CUSTOMER_SERVICE_CHAT

    try:
        if intent == INTENT_GET_BUSINESS_INFO:
            return _handle_business_info(wa_id, business_id, business_context, params, session)

        if intent == INTENT_GET_ORDER_STATUS:
            return _handle_order_status(wa_id, business_id, session)

        if intent == INTENT_GET_ORDER_HISTORY:
            return _handle_order_history(wa_id, business_id, params)

        if intent == INTENT_CANCEL_ORDER:
            return _handle_cancel_order(wa_id, business_id)

        if intent == INTENT_GET_PROMOS:
            return _handle_get_promos(wa_id, business_id, business_context)

        if intent == INTENT_SELECT_LISTED_PROMO:
            return _handle_select_listed_promo(wa_id, business_id, params, session)

        # CUSTOMER_SERVICE_CHAT fallback.
        return _base_result(
            wa_id, business_id,
            RESULT_KIND_CHAT_FALLBACK,
            available_fields=business_info_service.supported_fields(),
        )

    except Exception as exc:
        logger.error("[CS_FLOW] executor failed for intent=%s: %s", intent, exc, exc_info=True)
        return _base_result(
            wa_id, business_id,
            RESULT_KIND_INTERNAL_ERROR,
            success=False,
            error=str(exc),
        )


# ── Intent handlers ────────────────────────────────────────────────

def _handle_business_info(
    wa_id: str,
    business_id: str,
    business_context: Optional[Dict[str, Any]],
    params: Dict[str, Any],
    session: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    field = (params.get("field") or "").strip().lower()
    if not field:
        return _base_result(
            wa_id, business_id,
            RESULT_KIND_INFO_MISSING,
            field=None,
            available_fields=business_info_service.supported_fields(),
        )

    # Per-order ETA swap: "cuánto se demora la entrega?" is policy by
    # default, but if the customer has a placed order with a meaningful
    # remaining time, the per-order answer is more useful than a generic
    # "40 a 50 minutos". Returns the order_status path so the existing
    # ETA-aware response template handles it.
    if field == "delivery_time":
        latest = order_lookup_service.get_latest_order(wa_id, business_id)
        if latest:
            from ..services.order_eta import estimate_remaining_minutes
            if estimate_remaining_minutes({
                "status": latest.get("status"),
                "confirmed_at": latest.get("confirmed_at"),
            }) is not None:
                return _base_result(
                    wa_id, business_id,
                    RESULT_KIND_ORDER_STATUS,
                    order=_clean_order_for_response(latest),
                )

    value = business_info_service.get_business_info(business_context, field)
    if value is None:
        return _base_result(
            wa_id, business_id,
            RESULT_KIND_INFO_MISSING,
            field=field,
            available_fields=business_info_service.supported_fields(),
        )

    return _base_result(
        wa_id, business_id,
        RESULT_KIND_BUSINESS_INFO,
        field=field,
        value=value,
    )


def _handle_order_status(
    wa_id: str,
    business_id: str,
    session: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    # Ambiguity guard: "qué tengo en mi pedido?" / "dónde está mi pedido?"
    # might have been routed here by the language-only classifier, but if
    # the user has an active in-progress cart the correct interpretation
    # is VIEW_CART on the order agent. Emit a handoff instead of answering.
    order_context = (session or {}).get("order_context") or {}
    active_cart_items = order_context.get("items") or []
    if active_cart_items:
        logger.info(
            "[CS_FLOW] order_status routed to CS but user has active cart (n=%d) "
            "— handing off to order/VIEW_CART",
            len(active_cart_items),
        )
        return _base_result(
            wa_id, business_id,
            RESULT_KIND_HANDOFF,
            handoff={
                "to": "order",
                "segment": "qué tengo en mi pedido",
                "context": {"reason": "mi_pedido_active_cart"},
            },
        )

    order = order_lookup_service.get_latest_order(wa_id, business_id)
    if not order:
        return _base_result(
            wa_id, business_id,
            RESULT_KIND_NO_ORDER,
        )
    return _base_result(
        wa_id, business_id,
        RESULT_KIND_ORDER_STATUS,
        order=_clean_order_for_response(order),
    )


def _handle_order_history(
    wa_id: str,
    business_id: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    limit_raw = params.get("limit") or 5
    try:
        limit = max(1, min(int(limit_raw), 20))
    except (TypeError, ValueError):
        limit = 5

    orders = order_lookup_service.get_order_history(wa_id, business_id, limit=limit)
    if not orders:
        return _base_result(
            wa_id, business_id,
            RESULT_KIND_NO_ORDER,
        )

    return _base_result(
        wa_id, business_id,
        RESULT_KIND_ORDER_HISTORY,
        orders=[_clean_order_for_response(o) for o in orders],
    )


def _handle_cancel_order(
    wa_id: str,
    business_id: str,
) -> Dict[str, Any]:
    """
    Cancel the customer's most recent order, gated by the customer
    modification policy. We only cancel the *latest* order — if the
    customer has multiple in-flight (rare on WhatsApp), they'd need
    to specify, but for the MVP we don't model that.
    """
    order = order_lookup_service.get_latest_order(wa_id, business_id)
    if not order:
        return _base_result(wa_id, business_id, RESULT_KIND_NO_ORDER)

    current_status = order.get("status")
    if not can_customer_cancel(current_status):
        return _base_result(
            wa_id, business_id,
            RESULT_KIND_CANCEL_NOT_ALLOWED,
            order=_clean_order_for_response(order),
        )

    try:
        updated = order_modification_service.cancel_order(
            order_id=order["id"],
            reason=CANCEL_REASON_CUSTOMER_WHATSAPP,
        )
    except InvalidStatusTransition:
        # Race: status changed between read and write (admin moved it).
        # Re-read to give the customer an accurate explanation.
        latest = order_lookup_service.get_order_by_id(order["id"]) or order
        return _base_result(
            wa_id, business_id,
            RESULT_KIND_CANCEL_NOT_ALLOWED,
            order=_clean_order_for_response(latest),
        )

    return _base_result(
        wa_id, business_id,
        RESULT_KIND_ORDER_CANCELLED,
        order=_clean_order_for_response(updated),
    )


# ── Promo handlers ───────────────────────────────────────────────────


def _summarize_promo_for_listing(promo: Dict[str, Any]) -> Dict[str, Any]:
    """
    Trim a Promotion.to_dict() down to what the response generator needs
    to render a one-line summary, plus what the agent needs to remember
    in `last_listed_promos` for ordinal/anaphora resolution next turn.
    """
    if promo.get("fixed_price") is not None:
        price_label = f"${int(float(promo['fixed_price'])):,}".replace(",", ".")
        price_kind = f"precio promo {price_label}"
    elif promo.get("discount_amount") is not None:
        amt = f"${int(float(promo['discount_amount'])):,}".replace(",", ".")
        price_kind = f"descuento de {amt}"
    elif promo.get("discount_pct") is not None:
        price_kind = f"descuento del {int(promo['discount_pct'])}%"
    else:
        price_kind = ""

    days = promo.get("days_of_week") or []
    schedule_label = _spanish_days_label(days) if days else None

    return {
        "id": promo.get("id"),
        "name": promo.get("name"),
        "description": promo.get("description"),
        "price_kind": price_kind,
        "schedule_label": schedule_label,
    }


_DAY_NAMES_ES = {
    1: "lunes", 2: "martes", 3: "miércoles", 4: "jueves",
    5: "viernes", 6: "sábado", 7: "domingo",
}


def _spanish_days_label(days: List[int]) -> str:
    """[1,5] → 'lunes y viernes'. Sorted, oxford-style join."""
    names = [_DAY_NAMES_ES[d] for d in sorted(set(days)) if d in _DAY_NAMES_ES]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " y " + names[-1]


def _handle_get_promos(
    wa_id: str,
    business_id: str,
    business_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    List promos in two buckets — active right now and upcoming this
    week — so the response can say "hoy no tenemos, pero el viernes
    sale X". Schedule math runs in the business's local timezone.

    Persists the active set into `customer_service_context.
    last_listed_promos` so a follow-up "dame esa" / "la primera" can
    resolve back to a concrete promo_id. Upcoming promos aren't
    persisted because the customer can't add a promo that isn't active.
    """
    tz_name = promotion_service.timezone_from_business_context(business_context)
    buckets = promotion_service.list_promos_for_listing(
        business_id, timezone_name=tz_name,
    )
    active = buckets.get("active_now") or []
    upcoming = buckets.get("upcoming") or []

    if not active and not upcoming:
        return _base_result(wa_id, business_id, RESULT_KIND_NO_PROMOS)

    return _base_result(
        wa_id, business_id,
        RESULT_KIND_PROMOS_LIST,
        promos=[_summarize_promo_for_listing(p) for p in active],
        upcoming_promos=[_summarize_promo_for_listing(p) for p in upcoming],
    )


def _handle_select_listed_promo(
    wa_id: str,
    business_id: str,
    params: Dict[str, Any],
    session: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Resolve a promo reference into a concrete `promo_id` and hand off
    to the order agent for cart-add. Two surfaces feed in here:

    A) Anaphora after a list: the planner saw "dame esa" / "la primera"
       / "la del honey" and we lean on `last_listed_promos`.
    B) Cold ask: planner ambiguates a fresh "dame una promo de honey"
       as SELECT_LISTED_PROMO too. There's no list to select from, so
       fall through to a query against ALL active promos via the shared
       matcher.

    Resolution priority:
      1. params.promo_id — exact id (rare; planner could pass it).
      2. params.selector — ordinal against `last_listed_promos`.
      3. params.query against `last_listed_promos` (anaphora pass).
      4. params.query against ALL active promos (cold-ask pass).

    Outcomes:
      - 1 match → RESULT_KIND_HANDOFF with concrete promo_id.
      - 2+ matches → RESULT_KIND_PROMO_AMBIGUOUS (asks the customer
        to be more specific).
      - 0 matches → RESULT_KIND_PROMO_NOT_RESOLVED.
    """
    cs_ctx = (session or {}).get("customer_service_context") or {}
    listed = cs_ctx.get("last_listed_promos") or []

    promo_id: Optional[str] = None

    # 1) explicit id
    raw_id = (params.get("promo_id") or "").strip()
    if raw_id:
        if any(p.get("id") == raw_id for p in listed):
            promo_id = raw_id

    # 2) ordinal selector against the listed set
    if promo_id is None:
        selector = (params.get("selector") or "").strip().lower()
        idx = _ordinal_to_index(selector) if selector else None
        if idx is not None and 0 <= idx < len(listed):
            promo_id = listed[idx].get("id")

    # 3) fuzzy query against the listed set (anaphora pass).
    raw_query = (params.get("query") or "").strip()
    if promo_id is None and raw_query and listed:
        q = raw_query.lower()
        matches = [p for p in listed if q in (p.get("name") or "").lower()]
        if len(matches) == 1:
            promo_id = matches[0].get("id")

    # 4) Cold-ask pass: query against ALL active promos via shared matcher.
    # Triggered when the planner picked SELECT_LISTED_PROMO but there's no
    # listed set (or the anaphora pass missed). This is the bridge that
    # lets a fresh "me das una promo de honey" still resolve correctly.
    if promo_id is None and raw_query:
        all_matches = promotion_service.find_promo_by_query(business_id, raw_query)
        if len(all_matches) == 1:
            promo_id = all_matches[0].get("id")
        elif len(all_matches) >= 2:
            return _base_result(
                wa_id, business_id,
                RESULT_KIND_PROMO_AMBIGUOUS,
                query=raw_query,
                candidates=[
                    {"id": p.get("id"), "name": p.get("name")}
                    for p in all_matches[:5]
                ],
            )

    if promo_id is None:
        return _base_result(
            wa_id, business_id,
            RESULT_KIND_PROMO_NOT_RESOLVED,
            listed_count=len(listed),
            query=raw_query or None,
        )

    # Hand off to the order agent. The order agent's fast-path consumes
    # context.promo_id and synthesizes ADD_PROMO_TO_CART directly.
    return _base_result(
        wa_id, business_id,
        RESULT_KIND_HANDOFF,
        handoff={
            "to": "order",
            "segment": "agregar promo seleccionada",
            "context": {"promo_id": promo_id},
        },
    )


_ORDINAL_WORDS_ES = {
    "primer": 0, "primero": 0, "primera": 0, "1": 0, "uno": 0, "una": 0,
    "segundo": 1, "segunda": 1, "2": 1, "dos": 1,
    "tercero": 2, "tercera": 2, "3": 2, "tres": 2,
    "cuarto": 3, "cuarta": 3, "4": 3, "cuatro": 3,
    "quinto": 4, "quinta": 4, "5": 4, "cinco": 4,
}


def _ordinal_to_index(selector: str) -> Optional[int]:
    """'la primera' → 0, '2' → 1, 'tercero' → 2. Returns None on miss."""
    if not selector:
        return None
    s = selector.lower().strip()
    # Strip leading articles / common framing tokens.
    for prefix in ("la ", "el ", "los ", "las ", "esa ", "ese ", "esos ", "esas "):
        if s.startswith(prefix):
            s = s[len(prefix):]
    for token in s.split():
        if token in _ORDINAL_WORDS_ES:
            return _ORDINAL_WORDS_ES[token]
    return _ORDINAL_WORDS_ES.get(s)
