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
INTENT_CUSTOMER_SERVICE_CHAT = "CUSTOMER_SERVICE_CHAT"

VALID_INTENTS = {
    INTENT_GET_BUSINESS_INFO,
    INTENT_GET_ORDER_STATUS,
    INTENT_GET_ORDER_HISTORY,
    INTENT_CANCEL_ORDER,
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
            return _handle_business_info(wa_id, business_id, business_context, params)

        if intent == INTENT_GET_ORDER_STATUS:
            return _handle_order_status(wa_id, business_id, session)

        if intent == INTENT_GET_ORDER_HISTORY:
            return _handle_order_history(wa_id, business_id, params)

        if intent == INTENT_CANCEL_ORDER:
            return _handle_cancel_order(wa_id, business_id)

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
) -> Dict[str, Any]:
    field = (params.get("field") or "").strip().lower()
    if not field:
        return _base_result(
            wa_id, business_id,
            RESULT_KIND_INFO_MISSING,
            field=None,
            available_fields=business_info_service.supported_fields(),
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
