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
from ..services import business_info_service


logger = logging.getLogger(__name__)


# Intents (planner output)
INTENT_GET_BUSINESS_INFO = "GET_BUSINESS_INFO"
INTENT_GET_ORDER_STATUS = "GET_ORDER_STATUS"
INTENT_GET_ORDER_HISTORY = "GET_ORDER_HISTORY"
INTENT_CUSTOMER_SERVICE_CHAT = "CUSTOMER_SERVICE_CHAT"

VALID_INTENTS = {
    INTENT_GET_BUSINESS_INFO,
    INTENT_GET_ORDER_STATUS,
    INTENT_GET_ORDER_HISTORY,
    INTENT_CUSTOMER_SERVICE_CHAT,
}


# Result kinds — drives response generator branching
RESULT_KIND_BUSINESS_INFO = "business_info"
RESULT_KIND_INFO_MISSING = "info_missing"
RESULT_KIND_ORDER_STATUS = "order_status"
RESULT_KIND_NO_ORDER = "no_order"
RESULT_KIND_ORDER_HISTORY = "order_history"
RESULT_KIND_CHAT_FALLBACK = "cs_chat_fallback"
RESULT_KIND_INTERNAL_ERROR = "cs_internal_error"


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
    """Strip internal-only fields before passing to the response generator."""
    return {
        "id": order.get("id"),
        "status": order.get("status"),
        "total_amount": order.get("total_amount"),
        "delivery_address": order.get("delivery_address"),
        "payment_method": order.get("payment_method"),
        "notes": order.get("notes"),
        "created_at": order.get("created_at"),
        "items": order.get("items") or [],
    }


def execute_customer_service_intent(
    *,
    wa_id: str,
    business_id: str,
    business_context: Optional[Dict[str, Any]],
    intent: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Deterministic executor for customer service intents.

    Backend is single source of truth; response generator receives
    structured data and does not re-parse tool strings.
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
            return _handle_order_status(wa_id, business_id)

        if intent == INTENT_GET_ORDER_HISTORY:
            return _handle_order_history(wa_id, business_id, params)

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


def _handle_order_status(wa_id: str, business_id: str) -> Dict[str, Any]:
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
