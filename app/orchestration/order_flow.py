"""
Order flow: state machine and deterministic executor.
Backend is the single source of truth; cart state changes only via tools.

This module is the structured-data layer between the planner LLM and the
response-generator LLM. Tools return display strings for LangChain, but this
executor reads the session/services directly and builds structured payloads
(result_kind + typed fields) so the response generator never has to parse
backend strings.
"""

import logging
from typing import Any, Dict, List, Optional

from ..database.session_state_service import (
    session_state_service,
    derive_order_state,
    ORDER_STATE_GREETING,
    ORDER_STATE_ORDERING,
    ORDER_STATE_COLLECTING_DELIVERY,
    ORDER_STATE_READY_TO_PLACE,
)
from ..database.customer_service import customer_service
from ..database.product_order_service import product_order_service, AmbiguousProductError
from ..services import order_tools

logger = logging.getLogger(__name__)

# Intent names (planner output)
INTENT_GREET = "GREET"
INTENT_GET_MENU_CATEGORIES = "GET_MENU_CATEGORIES"
INTENT_LIST_PRODUCTS = "LIST_PRODUCTS"
INTENT_SEARCH_PRODUCTS = "SEARCH_PRODUCTS"
INTENT_GET_PRODUCT = "GET_PRODUCT"
INTENT_ADD_TO_CART = "ADD_TO_CART"
INTENT_VIEW_CART = "VIEW_CART"
INTENT_UPDATE_CART_ITEM = "UPDATE_CART_ITEM"
INTENT_REMOVE_FROM_CART = "REMOVE_FROM_CART"
INTENT_PROCEED_TO_CHECKOUT = "PROCEED_TO_CHECKOUT"
INTENT_GET_CUSTOMER_INFO = "GET_CUSTOMER_INFO"
INTENT_SUBMIT_DELIVERY_INFO = "SUBMIT_DELIVERY_INFO"
INTENT_PLACE_ORDER = "PLACE_ORDER"
INTENT_CHAT = "CHAT"

# Result kinds — routing signal for the response generator.
# Every execute_order_intent return carries exactly one of these so the
# response generator can pick the right branch without string-sniffing tool output.
RESULT_KIND_GREET = "greet"
RESULT_KIND_CHAT = "chat"
RESULT_KIND_MENU_CATEGORIES = "menu_categories"
RESULT_KIND_PRODUCTS_LIST = "products_list"
RESULT_KIND_PRODUCT_DETAILS = "product_details"
RESULT_KIND_CART_CHANGE = "cart_change"
RESULT_KIND_CART_VIEW = "cart_view"
RESULT_KIND_DELIVERY_STATUS = "delivery_status"
RESULT_KIND_ORDER_PLACED = "order_placed"
RESULT_KIND_NEEDS_CLARIFICATION = "needs_clarification"
RESULT_KIND_USER_ERROR = "user_error"
RESULT_KIND_INTERNAL_ERROR = "internal_error"

# Cart-change actions
CART_ACTION_ADDED = "added"
CART_ACTION_REMOVED = "removed"
CART_ACTION_UPDATED_QUANTITY = "updated_quantity"
CART_ACTION_UPDATED_NOTES = "updated_notes"
CART_ACTION_REPLACED = "replaced"
CART_ACTION_NOOP = "noop"

ORDER_STATES = (
    ORDER_STATE_GREETING,
    ORDER_STATE_ORDERING,
    ORDER_STATE_COLLECTING_DELIVERY,
    ORDER_STATE_READY_TO_PLACE,
)

ALLOWED_INTENTS_BY_STATE: Dict[str, tuple] = {
    ORDER_STATE_GREETING: (
        INTENT_GREET,
        INTENT_GET_MENU_CATEGORIES,
        INTENT_LIST_PRODUCTS,
        INTENT_SEARCH_PRODUCTS,
        INTENT_GET_PRODUCT,
        INTENT_ADD_TO_CART,
        INTENT_CHAT,
    ),
    ORDER_STATE_ORDERING: (
        INTENT_GET_MENU_CATEGORIES,
        INTENT_LIST_PRODUCTS,
        INTENT_SEARCH_PRODUCTS,
        INTENT_GET_PRODUCT,
        INTENT_ADD_TO_CART,
        INTENT_VIEW_CART,
        INTENT_UPDATE_CART_ITEM,
        INTENT_REMOVE_FROM_CART,
        INTENT_PROCEED_TO_CHECKOUT,
        INTENT_CHAT,
    ),
    ORDER_STATE_COLLECTING_DELIVERY: (
        INTENT_GET_CUSTOMER_INFO,
        INTENT_SUBMIT_DELIVERY_INFO,
        INTENT_PROCEED_TO_CHECKOUT,
        INTENT_CHAT,
    ),
    ORDER_STATE_READY_TO_PLACE: (
        INTENT_PLACE_ORDER,
        INTENT_VIEW_CART,
        INTENT_GET_CUSTOMER_INFO,
        INTENT_SUBMIT_DELIVERY_INFO,
        INTENT_CHAT,
    ),
}

CART_MUTATING_INTENTS = (
    INTENT_ADD_TO_CART,
    INTENT_REMOVE_FROM_CART,
    INTENT_UPDATE_CART_ITEM,
)


# ---------- helpers ----------

def _cart_summary_from_session(wa_id: str, business_id: str) -> str:
    """Short human cart summary for logging (never shown to user as-is anymore)."""
    result = session_state_service.load(wa_id, business_id)
    oc = result.get("session", {}).get("order_context") or {}
    items = oc.get("items") or []
    total = oc.get("total") or 0
    if not items:
        return "Pedido vacío."
    lines = [f"{it.get('quantity', 0)}x {it.get('name', '')}" for it in items]
    total_str = f"${int(total):,}".replace(",", ".")
    return "Pedido actual: " + "; ".join(lines) + f". Subtotal: {total_str}"


def _normalize_product_name(s: str) -> str:
    if not s:
        return ""
    return " ".join((s or "").lower().strip().replace("-", " ").split())


def _resolve_product_id_by_name(wa_id: str, business_id: str, product_name: str) -> Optional[str]:
    if not product_name or not wa_id or not business_id:
        return None
    cart = order_tools._cart_from_session(wa_id, business_id)
    items = cart.get("items") or []
    want = _normalize_product_name(product_name)
    for it in items:
        name = (it.get("name") or "").strip()
        if not name:
            continue
        if _normalize_product_name(name) == want:
            return it.get("product_id")
    return None


def _get_cart_item_quantity(wa_id: str, business_id: str, product_id: str) -> int:
    cart = order_tools._cart_from_session(wa_id, business_id)
    for it in (cart.get("items") or []):
        if it.get("product_id") == product_id:
            return int(it.get("quantity") or 0)
    return 0


def _get_cart_for_logging(wa_id: str, business_id: str) -> Dict:
    result = session_state_service.load(wa_id, business_id)
    oc = result.get("session", {}).get("order_context") or {}
    return {"items": oc.get("items") or [], "total": oc.get("total") or 0}


def _log_cart_debug(wa_id: str, business_id: str, tool_name: str, params: Dict, before: Dict, after: Optional[Dict] = None) -> None:
    items_before = before.get("items") or []
    total_before = before.get("total") or 0
    before_str = "; ".join([f"{it.get('quantity')}x {it.get('name')}" for it in items_before]) or "empty"
    logger.info(
        "[ORDER_FLOW] cart_debug | tool=%s | params=%s | before: %s (total=%s)",
        tool_name, params, before_str, total_before,
    )
    if after is not None:
        items_after = after.get("items") or []
        total_after = after.get("total") or 0
        after_str = "; ".join([f"{it.get('quantity')}x {it.get('name')}" for it in items_after]) or "empty"
        logger.info("[ORDER_FLOW] cart_debug | after: %s (total=%s)", after_str, total_after)


def _clean_product_dict(p: Dict[str, Any]) -> Dict[str, Any]:
    """Strip internal fields (UUID, business_id, etc.) from a product dict before passing to the response generator."""
    return {
        "name": p.get("name") or "",
        "price": float(p.get("price") or 0),
        "currency": p.get("currency") or "COP",
        "description": (p.get("description") or "").strip() or None,
        "category": p.get("category") or None,
    }


def _clean_cart_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Strip UUIDs from cart items for the response generator."""
    return [
        {
            "name": it.get("name") or "",
            "quantity": int(it.get("quantity") or 0),
            "price": float(it.get("price") or 0),
            "notes": (it.get("notes") or "").strip() or None,
        }
        for it in (items or [])
    ]


def _format_phone_from_wa_id(wa_id: str) -> str:
    """
    Format a wa_id (raw WhatsApp sender ID) as an E.164 phone number.
    Twilio wa_ids already include a leading '+'; Meta wa_ids are digits only.
    """
    if not wa_id:
        return ""
    raw = str(wa_id).strip()
    if raw.startswith("whatsapp:"):
        raw = raw[len("whatsapp:"):].strip()
    import re as _re
    digits = _re.sub(r"[^\d]", "", raw)
    if not digits:
        return ""
    return "+" + digits


def _get_delivery_fee(business_context: Optional[Dict]) -> float:
    if not business_context:
        return 5000.0
    settings = (business_context.get("business") or {}).get("settings") or {}
    return float(settings.get("delivery_fee", 5000))


def _build_delivery_status(wa_id: str, business_id: str) -> Dict[str, Any]:
    """
    Build structured delivery status from session + DB customer.
    Session delivery_info overrides DB customer.
    """
    cart = order_tools._cart_from_session(wa_id, business_id) if wa_id and business_id else {}
    session_di = cart.get("delivery_info") or {}
    cust = customer_service.get_customer(wa_id) if wa_id else None
    cust = cust or {}

    name = (session_di.get("name") or "").strip() or (cust.get("name") or "").strip()
    address = (session_di.get("address") or "").strip() or (cust.get("address") or "").strip()
    phone = (session_di.get("phone") or "").strip() or (cust.get("phone") or "").strip()
    payment = (session_di.get("payment_method") or "").strip() or (cust.get("payment_method") or "").strip()

    missing = []
    if not name:
        missing.append("name")
    if not address:
        missing.append("address")
    if not phone:
        missing.append("phone")
    if not payment:
        missing.append("payment")

    return {
        "all_present": len(missing) == 0,
        "missing": missing,
        "name": name or None,
        "address": address or None,
        "phone": phone or None,
        "payment_method": payment or None,
    }


def _diff_cart_items(before_items: List[Dict], after_items: List[Dict]) -> Dict[str, List[Dict]]:
    """
    Diff cart items by (product_id, notes) → returns added, removed, updated.
    """
    def key(it: Dict) -> tuple:
        return (it.get("product_id") or "", (it.get("notes") or "").strip())

    before_map: Dict[tuple, Dict] = {key(it): it for it in (before_items or [])}
    after_map: Dict[tuple, Dict] = {key(it): it for it in (after_items or [])}

    added: List[Dict] = []
    removed: List[Dict] = []
    updated: List[Dict] = []

    for k, aft in after_map.items():
        bef = before_map.get(k)
        if not bef:
            added.append(aft)
        elif int(bef.get("quantity") or 0) != int(aft.get("quantity") or 0):
            updated.append({
                **aft,
                "previous_quantity": int(bef.get("quantity") or 0),
            })
    for k, bef in before_map.items():
        if k not in after_map:
            removed.append(bef)

    return {"added": added, "removed": removed, "updated": updated}


def _infer_cart_action(diff: Dict[str, List[Dict]]) -> str:
    if diff["added"] and not diff["removed"] and not diff["updated"]:
        return CART_ACTION_ADDED
    if diff["removed"] and not diff["added"] and not diff["updated"]:
        return CART_ACTION_REMOVED
    if diff["updated"] and not diff["added"] and not diff["removed"]:
        return CART_ACTION_UPDATED_QUANTITY
    if diff["added"] and diff["removed"]:
        return CART_ACTION_REPLACED
    if not diff["added"] and not diff["removed"] and not diff["updated"]:
        return CART_ACTION_NOOP
    return CART_ACTION_UPDATED_QUANTITY


def _build_cart_change(before: Dict, after: Dict) -> Dict[str, Any]:
    diff = _diff_cart_items(before.get("items") or [], after.get("items") or [])
    action = _infer_cart_action(diff)
    return {
        "action": action,
        "added": _clean_cart_items(diff["added"]),
        "removed": _clean_cart_items(diff["removed"]),
        "updated": _clean_cart_items(diff["updated"]),
        "cart_after": _clean_cart_items(after.get("items") or []),
        "total_after": int(after.get("total") or 0),
    }


def _save_pending_disambiguation(wa_id: str, business_id: str, requested_name: str, options: List[Dict[str, Any]]) -> None:
    """
    Save the options we just offered the customer so the NEXT turn's planner
    can resolve replies like 'la normal', 'la primera', 'la Corona'.
    """
    try:
        result = session_state_service.load(wa_id, business_id)
        oc = dict((result.get("session", {}).get("order_context") or {}))
        oc["pending_disambiguation"] = {
            "requested_name": requested_name,
            "options": options,
        }
        session_state_service.save(wa_id, business_id, {"order_context": oc})
    except Exception as e:
        logger.warning("[ORDER_FLOW] Failed to save pending_disambiguation: %s", e)


def _clear_pending_disambiguation(wa_id: str, business_id: str) -> None:
    """
    Remove pending_disambiguation from the session order_context.

    Uses a direct SQL UPDATE with the postgres jsonb `-` operator because
    session_state_service.save does a shallow MERGE of order_context and
    can't delete a nested key: merging {oc_without_key} into {oc_with_key}
    keeps the key (merge only updates/adds, never removes).
    """
    try:
        from ..database.models import get_db_session as _get_session
        from sqlalchemy import text as _sql
        db_session = _get_session()
        try:
            import uuid as _uuid
            db_session.execute(
                _sql(
                    "UPDATE conversation_sessions "
                    "SET order_context = order_context - 'pending_disambiguation', "
                    "    updated_at = NOW(), last_activity_at = NOW() "
                    "WHERE wa_id = :wa_id "
                    "  AND business_id = :business_id "
                    "  AND order_context ? 'pending_disambiguation'"
                ),
                {"wa_id": wa_id, "business_id": _uuid.UUID(business_id)},
            )
            db_session.commit()
        finally:
            db_session.close()
    except Exception as e:
        logger.warning("[ORDER_FLOW] Failed to clear pending_disambiguation: %s", e)


def _base_result(state_after: str, wa_id: str, business_id: str, kind: str, **extra) -> Dict[str, Any]:
    """Build a result dict with required fields + structured extras."""
    return {
        "success": True,
        "result_kind": kind,
        "state_after": state_after,
        "error": None,
        "error_kind": None,
        "cart_summary": _cart_summary_from_session(wa_id, business_id),
        "tool_result": "",
        **extra,
    }


def _user_error_result(state_after: str, wa_id: str, business_id: str, message: str) -> Dict[str, Any]:
    return {
        "success": False,
        "result_kind": RESULT_KIND_USER_ERROR,
        "state_after": state_after,
        "error": message,
        "error_kind": "user_visible",
        "error_message": message,
        "cart_summary": _cart_summary_from_session(wa_id, business_id),
        "tool_result": message,
    }


def _internal_error_result(state_after: str, wa_id: str, business_id: str, message: str) -> Dict[str, Any]:
    return {
        "success": False,
        "result_kind": RESULT_KIND_INTERNAL_ERROR,
        "state_after": state_after,
        "error": message,
        "error_kind": "internal",
        "error_message": message,
        "cart_summary": _cart_summary_from_session(wa_id, business_id),
        "tool_result": "",
    }


def _disambig_result(
    state_after: str,
    wa_id: str,
    business_id: str,
    requested_name: str,
    matches: List[Dict[str, Any]],
) -> Dict[str, Any]:
    options = [
        {"name": m.get("name"), "price": float(m.get("price") or 0)}
        for m in matches
    ]
    logger.warning(
        "[ORDER_FLOW] Ambiguous product '%s' — %d options; blocking action, asking user",
        requested_name, len(matches),
    )
    # Persist the options so the NEXT turn's planner can resolve replies
    # like "la normal", "la primera", "el más barato" against them.
    _save_pending_disambiguation(wa_id, business_id, requested_name, options)
    return {
        "success": False,
        "result_kind": RESULT_KIND_NEEDS_CLARIFICATION,
        "needs_clarification": True,
        "state_after": state_after,
        "error": None,
        "error_kind": None,
        "requested_name": requested_name or "ese producto",
        "options": options,
        "cart_summary": _cart_summary_from_session(wa_id, business_id),
        "tool_result": "",
    }


def _find_tool(tool_name: str):
    for t in order_tools.order_tools:
        if t.name == tool_name:
            return t
    return None


# ---------- main executor ----------

def execute_order_intent(
    wa_id: str,
    business_id: str,
    business_context: Optional[Dict],
    session: Dict,
    intent: str,
    params: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Execute one order intent: validate state, run one tool (or compute state),
    return a structured result the response generator can consume by result_kind.
    """
    params = params or {}
    ctx = {**(business_context or {}), "wa_id": wa_id, "business_id": business_id}
    order_context = session.get("order_context") or {}
    current_state = order_context.get("state") or derive_order_state(order_context)

    allowed = ALLOWED_INTENTS_BY_STATE.get(current_state, ())
    if intent not in allowed:
        if (
            current_state in (ORDER_STATE_READY_TO_PLACE, ORDER_STATE_COLLECTING_DELIVERY)
            and intent in CART_MUTATING_INTENTS
        ):
            logger.warning(
                "[ORDER_FLOW] Re-opening cart: intent=%s state=%s -> %s",
                intent, current_state, ORDER_STATE_ORDERING,
            )
            session_state_service.save(
                wa_id, business_id,
                {"order_context": {**order_context, "state": ORDER_STATE_ORDERING}},
            )
            order_context = {**order_context, "state": ORDER_STATE_ORDERING}
            current_state = ORDER_STATE_ORDERING
            session = {**session, "order_context": order_context}
        else:
            logger.warning(
                "[ORDER_FLOW] Intent rejected: intent=%s state=%s allowed=%s",
                intent, current_state, list(allowed),
            )
            return _user_error_result(
                current_state, wa_id, business_id,
                f"Esa acción no se puede hacer en este momento ({current_state}).",
            )

    # Pending disambiguation is one-shot: the planner already consumed it
    # (via the prompt context). Clear it now; if THIS intent raises another
    # AmbiguousProductError, _disambig_result will save a fresh one.
    _clear_pending_disambiguation(wa_id, business_id)

    try:
        # --- state-transition / no-tool intents ---
        if intent == INTENT_GREET:
            return _base_result(current_state, wa_id, business_id, RESULT_KIND_GREET)

        if intent == INTENT_CHAT:
            return _base_result(current_state, wa_id, business_id, RESULT_KIND_CHAT)

        if intent == INTENT_PROCEED_TO_CHECKOUT:
            cart_before = _get_cart_for_logging(wa_id, business_id)
            if not cart_before.get("items"):
                return _user_error_result(
                    current_state, wa_id, business_id,
                    "Tu pedido está vacío. Agrega productos antes de continuar.",
                )
            session_state_service.save(
                wa_id, business_id,
                {"order_context": {**order_context, "state": ORDER_STATE_COLLECTING_DELIVERY}},
            )
            delivery_status = _build_delivery_status(wa_id, business_id)
            state_after = ORDER_STATE_COLLECTING_DELIVERY
            if delivery_status["all_present"]:
                cart_now = order_tools._cart_from_session(wa_id, business_id)
                session_state_service.save(
                    wa_id, business_id,
                    {"order_context": {
                        **cart_now,
                        "delivery_info": {
                            "name": delivery_status["name"],
                            "address": delivery_status["address"],
                            "phone": delivery_status["phone"],
                            "payment_method": delivery_status["payment_method"],
                        },
                        "state": ORDER_STATE_READY_TO_PLACE,
                    }},
                )
                state_after = ORDER_STATE_READY_TO_PLACE
            return _base_result(
                state_after, wa_id, business_id,
                RESULT_KIND_DELIVERY_STATUS,
                delivery_status=delivery_status,
            )

        # --- read intents (call service directly, no @tool wrapper) ---
        if intent == INTENT_GET_MENU_CATEGORIES:
            categories = product_order_service.list_categories(business_id=business_id) or []
            if categories:
                return _base_result(
                    current_state, wa_id, business_id,
                    RESULT_KIND_MENU_CATEGORIES,
                    categories=list(categories),
                )
            all_products = product_order_service.list_products(business_id=business_id) or []
            return _base_result(
                current_state, wa_id, business_id,
                RESULT_KIND_PRODUCTS_LIST,
                products=[_clean_product_dict(p) for p in all_products],
                query_label=None,
                category_label=None,
            )

        if intent == INTENT_LIST_PRODUCTS:
            category = (params.get("category") or "").strip()
            products = product_order_service.list_products_with_fallback(
                business_id=business_id, category=category,
            ) or []
            return _base_result(
                current_state, wa_id, business_id,
                RESULT_KIND_PRODUCTS_LIST,
                products=[_clean_product_dict(p) for p in products],
                category_label=category or None,
                query_label=None,
            )

        if intent == INTENT_SEARCH_PRODUCTS:
            query = (params.get("query") or "").strip()
            if not query:
                return _user_error_result(
                    current_state, wa_id, business_id,
                    "Indica qué producto estás buscando.",
                )
            products = product_order_service.search_products(
                business_id=business_id, query=query,
            ) or []
            return _base_result(
                current_state, wa_id, business_id,
                RESULT_KIND_PRODUCTS_LIST,
                products=[_clean_product_dict(p) for p in products],
                query_label=query,
                category_label=None,
            )

        if intent == INTENT_GET_PRODUCT:
            product_id = (params.get("product_id") or "").strip()
            product_name = (params.get("product_name") or "").strip()
            if not product_id and not product_name:
                return _user_error_result(
                    current_state, wa_id, business_id,
                    "Indica el nombre del producto que quieres conocer.",
                )
            product = product_order_service.get_product(
                product_id=product_id or None,
                product_name=product_name or None,
                business_id=business_id,
            )
            if not product:
                return _user_error_result(
                    current_state, wa_id, business_id,
                    f"No encontré {product_name or 'ese producto'} en el menú.",
                )
            return _base_result(
                current_state, wa_id, business_id,
                RESULT_KIND_PRODUCT_DETAILS,
                product=_clean_product_dict(product),
            )

        if intent == INTENT_VIEW_CART:
            cart = order_tools._cart_from_session(wa_id, business_id)
            items = cart.get("items") or []
            subtotal = int(cart.get("total") or 0)
            delivery_fee = int(_get_delivery_fee(business_context))
            return _base_result(
                current_state, wa_id, business_id,
                RESULT_KIND_CART_VIEW,
                cart_view={
                    "items": _clean_cart_items(items),
                    "subtotal": subtotal,
                    "delivery_fee": delivery_fee,
                    "total": subtotal + delivery_fee if items else 0,
                    "is_empty": not items,
                },
            )

        if intent == INTENT_GET_CUSTOMER_INFO:
            delivery_status = _build_delivery_status(wa_id, business_id)
            return _base_result(
                current_state, wa_id, business_id,
                RESULT_KIND_DELIVERY_STATUS,
                delivery_status=delivery_status,
            )

        # --- write intents (invoke @tool wrapper, then diff session) ---

        if intent == INTENT_ADD_TO_CART:
            tool_fn = _find_tool("add_to_cart")
            if not tool_fn:
                return _internal_error_result(current_state, wa_id, business_id, "Tool add_to_cart no encontrada")
            cart_before = _get_cart_for_logging(wa_id, business_id)

            if isinstance(params.get("items"), list) and len(params["items"]) > 0:
                # Multi-item add: skip duplicates, invoke once per item
                existing_names = {_normalize_product_name(it.get("name") or "") for it in (cart_before.get("items") or [])}
                for item in params["items"]:
                    if not isinstance(item, dict):
                        continue
                    item_name = _normalize_product_name(item.get("product_name") or "")
                    if item_name and item_name in existing_names:
                        logger.warning(
                            "[ORDER_FLOW] Skipping duplicate add: '%s' already in cart",
                            item.get("product_name"),
                        )
                        continue
                    args = {
                        "injected_business_context": ctx,
                        "product_id": item.get("product_id") or "",
                        "product_name": item.get("product_name") or "",
                        "quantity": int(item.get("quantity") or 1),
                    }
                    try:
                        tool_fn.invoke(args)
                    except AmbiguousProductError:
                        raise
                    except Exception as e:
                        logger.exception("[ORDER_FLOW] add_to_cart item failed: %s", e)
            else:
                args = {
                    "injected_business_context": ctx,
                    "product_id": params.get("product_id") or "",
                    "product_name": params.get("product_name") or "",
                    "quantity": int(params.get("quantity") or 1),
                    "notes": (params.get("notes") or "").strip(),
                }
                tool_fn.invoke(args)

            cart_after = _get_cart_for_logging(wa_id, business_id)
            _log_cart_debug(wa_id, business_id, "add_to_cart", params, cart_before, cart_after)
            cart_change = _build_cart_change(cart_before, cart_after)

            # State transition on first add
            state_after = current_state
            if current_state == ORDER_STATE_GREETING and cart_change["action"] != CART_ACTION_NOOP:
                session_state_service.save(
                    wa_id, business_id,
                    {"order_context": {**order_tools._cart_from_session(wa_id, business_id), "state": ORDER_STATE_ORDERING}},
                )
                state_after = ORDER_STATE_ORDERING

            return _base_result(
                state_after, wa_id, business_id,
                RESULT_KIND_CART_CHANGE,
                cart_change=cart_change,
            )

        if intent == INTENT_UPDATE_CART_ITEM:
            tool_fn = _find_tool("update_cart_item")
            if not tool_fn:
                return _internal_error_result(current_state, wa_id, business_id, "Tool update_cart_item no encontrada")
            cart_before = _get_cart_for_logging(wa_id, business_id)

            tool_args = {
                "injected_business_context": ctx,
                "product_id": (params.get("product_id") or "").strip(),
                "quantity": int(params.get("quantity") or 0),
                "notes": (params.get("notes") or "").strip(),
            }

            # Resolve product_id from product_name or first item if missing
            if not tool_args["product_id"]:
                name = (params.get("product_name") or "").strip()
                items_list = params.get("items") if isinstance(params.get("items"), list) else []
                if not name and len(items_list) > 0 and isinstance(items_list[0], dict):
                    name = (items_list[0].get("product_name") or items_list[0].get("name") or "").strip()
                if name:
                    resolved = _resolve_product_id_by_name(wa_id, business_id, name)
                    if resolved:
                        tool_args["product_id"] = resolved
                if tool_args["quantity"] == 0 and len(items_list) > 0 and isinstance(items_list[0], dict):
                    tool_args["quantity"] = int(items_list[0].get("quantity") or 0)

            if isinstance(params.get("items"), list) and len(params["items"]) == 2:
                # Replace A with B
                first = params["items"][0] if isinstance(params["items"][0], dict) else {}
                second = params["items"][1] if isinstance(params["items"][1], dict) else {}
                first_name = (first.get("product_name") or first.get("name") or "").strip()
                second_name = (second.get("product_name") or second.get("name") or "").strip()
                remove_qty = int(first.get("quantity") or 1)
                add_qty = int(second.get("quantity") or 1)
                pid = _resolve_product_id_by_name(wa_id, business_id, first_name) if first_name else None
                if not pid:
                    return _user_error_result(
                        current_state, wa_id, business_id,
                        f"No encontré '{first_name}' en tu pedido.",
                    )
                current_qty = _get_cart_item_quantity(wa_id, business_id, pid)
                new_qty = max(0, current_qty - remove_qty)
                tool_fn.invoke({
                    "injected_business_context": ctx,
                    "product_id": pid,
                    "quantity": new_qty,
                })
                if second_name:
                    add_tool = _find_tool("add_to_cart")
                    if add_tool:
                        add_tool.invoke({
                            "injected_business_context": ctx,
                            "product_id": "",
                            "product_name": second_name,
                            "quantity": add_qty,
                        })
            else:
                if not tool_args["product_id"]:
                    return _user_error_result(
                        current_state, wa_id, business_id,
                        "No pude identificar qué producto modificar en tu pedido.",
                    )
                tool_fn.invoke(tool_args)

            cart_after = _get_cart_for_logging(wa_id, business_id)
            _log_cart_debug(wa_id, business_id, "update_cart_item", params, cart_before, cart_after)
            cart_change = _build_cart_change(cart_before, cart_after)

            # Determine whether this was a notes-only update (quantity unchanged)
            if cart_change["action"] == CART_ACTION_UPDATED_QUANTITY and not cart_change["added"] and not cart_change["removed"]:
                # Check if notes differ while quantity stayed: emit updated_notes
                # We already marked updated_quantity when diff only has quantity changes;
                # but notes-only changes produce a pure removed+added pair (different notes keys).
                pass
            # Notes-only edits show up as removed+added with same product_id (different notes key) → replaced.
            # For UX purposes, treat replaced with same product_id as updated_notes.
            if cart_change["action"] == CART_ACTION_REPLACED:
                removed_pids = {(it.get("name"), it.get("price")) for it in cart_change["removed"]}
                added_pids = {(it.get("name"), it.get("price")) for it in cart_change["added"]}
                if removed_pids == added_pids and removed_pids:
                    cart_change["action"] = CART_ACTION_UPDATED_NOTES

            return _base_result(
                current_state, wa_id, business_id,
                RESULT_KIND_CART_CHANGE,
                cart_change=cart_change,
            )

        if intent == INTENT_REMOVE_FROM_CART:
            tool_fn = _find_tool("remove_from_cart")
            if not tool_fn:
                return _internal_error_result(current_state, wa_id, business_id, "Tool remove_from_cart no encontrada")
            cart_before = _get_cart_for_logging(wa_id, business_id)

            tool_args = {
                "injected_business_context": ctx,
                "product_id": params.get("product_id") or "",
                "product_name": (params.get("product_name") or "").strip(),
            }
            if not tool_args["product_id"] and tool_args["product_name"]:
                resolved = _resolve_product_id_by_name(wa_id, business_id, tool_args["product_name"])
                if resolved:
                    tool_args["product_id"] = resolved

            tool_fn.invoke(tool_args)
            cart_after = _get_cart_for_logging(wa_id, business_id)
            _log_cart_debug(wa_id, business_id, "remove_from_cart", params, cart_before, cart_after)
            cart_change = _build_cart_change(cart_before, cart_after)

            if cart_change["action"] == CART_ACTION_NOOP:
                return _user_error_result(
                    current_state, wa_id, business_id,
                    f"No encontré '{tool_args['product_name'] or 'ese producto'}' en tu pedido.",
                )

            return _base_result(
                current_state, wa_id, business_id,
                RESULT_KIND_CART_CHANGE,
                cart_change=cart_change,
            )

        # --- delivery / place order ---

        if intent == INTENT_SUBMIT_DELIVERY_INFO:
            tool_fn = _find_tool("submit_delivery_info")
            if not tool_fn:
                return _internal_error_result(current_state, wa_id, business_id, "Tool submit_delivery_info no encontrada")
            phone_param = (params.get("phone") or "").strip()
            if phone_param == "<SENDER>":
                phone_param = _format_phone_from_wa_id(wa_id)
            tool_fn.invoke({
                "injected_business_context": ctx,
                "address": params.get("address") or "",
                "payment_method": params.get("payment_method") or "",
                "phone": phone_param,
                "name": params.get("name") or "",
            })
            delivery_status = _build_delivery_status(wa_id, business_id)

            state_after = current_state
            if delivery_status["all_present"]:
                cart_now = order_tools._cart_from_session(wa_id, business_id)
                session_state_service.save(
                    wa_id, business_id,
                    {"order_context": {
                        **cart_now,
                        "delivery_info": {
                            "name": delivery_status["name"],
                            "address": delivery_status["address"],
                            "phone": delivery_status["phone"],
                            "payment_method": delivery_status["payment_method"],
                        },
                        "state": ORDER_STATE_READY_TO_PLACE,
                    }},
                )
                state_after = ORDER_STATE_READY_TO_PLACE
            else:
                cart_now = order_tools._cart_from_session(wa_id, business_id)
                session_state_service.save(
                    wa_id, business_id,
                    {"order_context": {**cart_now, "state": ORDER_STATE_COLLECTING_DELIVERY}},
                )
                state_after = ORDER_STATE_COLLECTING_DELIVERY

            return _base_result(
                state_after, wa_id, business_id,
                RESULT_KIND_DELIVERY_STATUS,
                delivery_status=delivery_status,
            )

        if intent == INTENT_PLACE_ORDER:
            tool_fn = _find_tool("place_order")
            if not tool_fn:
                return _internal_error_result(current_state, wa_id, business_id, "Tool place_order no encontrada")
            cart_before = _get_cart_for_logging(wa_id, business_id)
            items_snapshot = _clean_cart_items(cart_before.get("items") or [])
            subtotal_snapshot = int(cart_before.get("total") or 0)
            delivery_fee = int(_get_delivery_fee(business_context))

            result_str = tool_fn.invoke({"injected_business_context": ctx})
            result_str = result_str if isinstance(result_str, str) else str(result_str)

            if "✅" not in result_str and "confirmado" not in result_str.lower():
                if "MISSING_DELIVERY_INFO" in result_str:
                    delivery_status = _build_delivery_status(wa_id, business_id)
                    return _base_result(
                        ORDER_STATE_COLLECTING_DELIVERY, wa_id, business_id,
                        RESULT_KIND_DELIVERY_STATUS,
                        delivery_status=delivery_status,
                    )
                return _user_error_result(current_state, wa_id, business_id, result_str.replace("❌", "").strip())

            # Parse order id from "#XXXXXXXX"
            order_id_display = ""
            try:
                import re as _re
                m = _re.search(r"#([0-9A-Fa-f]{6,})", result_str)
                if m:
                    order_id_display = m.group(1).upper()
            except Exception:
                pass

            return _base_result(
                ORDER_STATE_GREETING, wa_id, business_id,
                RESULT_KIND_ORDER_PLACED,
                order_placed={
                    "order_id_display": order_id_display or None,
                    "items": items_snapshot,
                    "subtotal": subtotal_snapshot,
                    "delivery_fee": delivery_fee,
                    "total": subtotal_snapshot + delivery_fee,
                },
            )

        # Unknown intent
        return _user_error_result(current_state, wa_id, business_id, f"Intent {intent} no soportado")

    except AmbiguousProductError as e:
        requested_name = (params.get("product_name") or "").strip()
        if not requested_name and isinstance(params.get("items"), list):
            for it in params["items"]:
                if isinstance(it, dict) and (it.get("product_name") or "").strip():
                    requested_name = it["product_name"].strip()
                    break
        return _disambig_result(
            current_state, wa_id, business_id,
            requested_name or "ese producto",
            e.matches or [],
        )
    except Exception as e:
        logger.exception("[ORDER_FLOW] Intent %s failed: %s", intent, e)
        return _internal_error_result(current_state, wa_id, business_id, str(e))
