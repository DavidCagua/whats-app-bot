"""
Order flow: state machine and deterministic executor.
Backend is the single source of truth; cart state changes only via tools.
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

# Cart-mutating intents (log cart_before / cart_after)
CART_MUTATING_INTENTS = (
    INTENT_ADD_TO_CART,
    INTENT_REMOVE_FROM_CART,
    INTENT_UPDATE_CART_ITEM,
)


def _cart_summary_from_session(wa_id: str, business_id: str) -> str:
    """Build a short cart summary from session for response generator."""
    result = session_state_service.load(wa_id, business_id)
    oc = result.get("session", {}).get("order_context") or {}
    items = oc.get("items") or []
    total = oc.get("total") or 0
    if not items:
        return "Carrito vacío."
    lines = [f"{it.get('quantity', 0)}x {it.get('name', '')}" for it in items]
    total_str = f"${int(total):,}".replace(",", ".")
    return "Pedido actual: " + "; ".join(lines) + f". Total: {total_str}"


def _normalize_product_name(s: str) -> str:
    """Normalize for fuzzy match: lower, strip, collapse spaces, treat hyphen as space."""
    if not s:
        return ""
    return " ".join((s or "").lower().strip().replace("-", " ").split())


def _resolve_product_id_by_name(wa_id: str, business_id: str, product_name: str) -> Optional[str]:
    """Resolve product_name to product_id from current cart items. Returns None if not found."""
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
    """Return total quantity for this product_id in the cart (single line)."""
    cart = order_tools._cart_from_session(wa_id, business_id)
    for it in (cart.get("items") or []):
        if it.get("product_id") == product_id:
            return int(it.get("quantity") or 0)
    return 0


def _log_cart_debug(wa_id: str, business_id: str, tool_name: str, params: Dict, before: Dict, after: Optional[Dict] = None) -> None:
    """Log cart_before, tool_called, cart_after for debugging desync."""
    items_before = before.get("items") or []
    total_before = before.get("total") or 0
    before_str = "; ".join([f"{it.get('quantity')}x {it.get('name')}" for it in items_before]) or "empty"
    logger.info(
        "[ORDER_FLOW] cart_debug | tool=%s | params=%s | before: %s (total=%s)",
        tool_name,
        params,
        before_str,
        total_before,
    )
    if after is not None:
        items_after = after.get("items") or []
        total_after = after.get("total") or 0
        after_str = "; ".join([f"{it.get('quantity')}x {it.get('name')}" for it in items_after]) or "empty"
        logger.info(
            "[ORDER_FLOW] cart_debug | after: %s (total=%s)",
            after_str,
            total_after,
        )


def _get_cart_for_logging(wa_id: str, business_id: str) -> Dict:
    """Load cart dict for debug logging (items + total)."""
    result = session_state_service.load(wa_id, business_id)
    oc = result.get("session", {}).get("order_context") or {}
    return {"items": oc.get("items") or [], "total": oc.get("total") or 0}


def execute_order_intent(
    wa_id: str,
    business_id: str,
    business_context: Optional[Dict],
    session: Dict,
    intent: str,
    params: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Execute one order intent: validate state, run one tool (or state transition), update state.
    Returns: success, tool_result, state_after, error, cart_summary.
    Cart state changes only through this executor; never trust LLM belief.
    """
    params = params or {}
    ctx = {**(business_context or {}), "wa_id": wa_id, "business_id": business_id}
    order_context = session.get("order_context") or {}
    current_state = order_context.get("state") or derive_order_state(order_context)

    allowed = ALLOWED_INTENTS_BY_STATE.get(current_state, ())
    if intent not in allowed:
        logger.warning(
            "[ORDER_FLOW] Intent rejected: intent=%s state=%s allowed=%s",
            intent,
            current_state,
            list(allowed),
        )
        return {
            "success": False,
            "tool_result": None,
            "state_after": current_state,
            "error": f"Intent {intent} no permitido en estado {current_state}. Permitidos: {allowed}",
            "cart_summary": _cart_summary_from_session(wa_id, business_id),
        }

    # PROCEED_TO_CHECKOUT: state transition + get delivery status so response can confirm or ask for missing
    if intent == INTENT_PROCEED_TO_CHECKOUT:
        cart_before = _get_cart_for_logging(wa_id, business_id)
        if not (cart_before.get("items")):
            return {
                "success": False,
                "tool_result": None,
                "state_after": current_state,
                "error": "El carrito está vacío. Agrega productos antes de continuar.",
                "cart_summary": "Carrito vacío.",
            }
        session_state_service.save(
            wa_id,
            business_id,
            {"order_context": {**order_context, "state": ORDER_STATE_COLLECTING_DELIVERY}},
        )
        # Return DELIVERY_STATUS so response generator can show "Tengo esta dirección..." or ask only missing
        delivery_status_str = "OK_COLLECTING_DELIVERY"
        for t in order_tools.order_tools:
            if t.name == "get_customer_info":
                try:
                    delivery_status_str = t.invoke({"injected_business_context": ctx})
                except Exception as e:
                    logger.warning("[ORDER_FLOW] get_customer_info after PROCEED_TO_CHECKOUT failed: %s", e)
                break
        state_after = ORDER_STATE_COLLECTING_DELIVERY
        # If we already have all delivery data (e.g. from DB), persist to session and set READY_TO_PLACE so "si" -> PLACE_ORDER is allowed
        if "all_present=true" in (delivery_status_str or ""):
            cart_now = order_tools._cart_from_session(wa_id, business_id)
            session_di = cart_now.get("delivery_info") or {}
            cust = customer_service.get_customer(wa_id) if wa_id else None
            delivery_info = {
                "name": (session_di.get("name") or "").strip() or (cust.get("name") or "").strip() or "",
                "address": (session_di.get("address") or "").strip() or (cust.get("address") or "").strip() or "",
                "phone": (session_di.get("phone") or "").strip() or (cust.get("phone") or "").strip() or "",
                "payment_method": (session_di.get("payment_method") or "").strip() or (cust.get("payment_method") or "").strip() or "",
            }
            if delivery_info.get("name") and delivery_info.get("address") and delivery_info.get("phone") and delivery_info.get("payment_method"):
                session_state_service.save(
                    wa_id,
                    business_id,
                    {"order_context": {**cart_now, "delivery_info": delivery_info, "state": ORDER_STATE_READY_TO_PLACE}},
                )
                state_after = ORDER_STATE_READY_TO_PLACE
        return {
            "success": True,
            "tool_result": delivery_status_str,
            "state_after": state_after,
            "error": None,
            "cart_summary": _cart_summary_from_session(wa_id, business_id),
        }

    # CHAT: no tool, no state change
    if intent == INTENT_CHAT:
        return {
            "success": True,
            "tool_result": None,
            "state_after": current_state,
            "error": None,
            "cart_summary": _cart_summary_from_session(wa_id, business_id),
        }

    # GREET: no tool, no state change
    if intent == INTENT_GREET:
        return {
            "success": True,
            "tool_result": "GREET",
            "state_after": current_state,
            "error": None,
            "cart_summary": _cart_summary_from_session(wa_id, business_id),
        }

    # Map intent to tool and build args
    tool_name = None
    tool_args: Dict[str, Any] = {"injected_business_context": ctx}

    if intent == INTENT_GET_MENU_CATEGORIES:
        tool_name = "get_menu_categories"
    elif intent == INTENT_LIST_PRODUCTS:
        tool_name = "list_category_products"
        tool_args["category"] = params.get("category") or ""
    elif intent == INTENT_SEARCH_PRODUCTS:
        tool_name = "search_products"
        tool_args["query"] = params.get("query") or ""
    elif intent == INTENT_GET_PRODUCT:
        tool_name = "get_product_details"
        tool_args["product_id"] = params.get("product_id") or ""
        tool_args["product_name"] = params.get("product_name") or ""
    elif intent == INTENT_ADD_TO_CART:
        tool_name = "add_to_cart"
        tool_args["product_id"] = params.get("product_id") or ""
        tool_args["product_name"] = params.get("product_name") or ""
        tool_args["quantity"] = int(params.get("quantity") or 1)
    elif intent == INTENT_VIEW_CART:
        tool_name = "view_cart"
    elif intent == INTENT_UPDATE_CART_ITEM:
        tool_name = "update_cart_item"
        tool_args["product_id"] = params.get("product_id") or ""
        tool_args["quantity"] = int(params.get("quantity") or 0)
        # Resolve product_id from product_name or first item if missing
        if not tool_args["product_id"] and wa_id and business_id:
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
    elif intent == INTENT_REMOVE_FROM_CART:
        tool_name = "remove_from_cart"
        tool_args["product_id"] = params.get("product_id") or ""
    elif intent == INTENT_GET_CUSTOMER_INFO:
        tool_name = "get_customer_info"
    elif intent == INTENT_SUBMIT_DELIVERY_INFO:
        tool_name = "submit_delivery_info"
        tool_args["address"] = params.get("address") or ""
        tool_args["payment_method"] = params.get("payment_method") or ""
        tool_args["phone"] = params.get("phone") or ""
        tool_args["name"] = params.get("name") or ""
    elif intent == INTENT_PLACE_ORDER:
        tool_name = "place_order"

    if not tool_name:
        return {
            "success": False,
            "tool_result": None,
            "state_after": current_state,
            "error": f"Intent {intent} no mapeado a herramienta",
            "cart_summary": _cart_summary_from_session(wa_id, business_id),
        }

    # Cart debug logging for cart-mutating intents
    cart_before_log = _get_cart_for_logging(wa_id, business_id) if intent in CART_MUTATING_INTENTS else None

    # Find and invoke tool
    tool_fn = None
    for t in order_tools.order_tools:
        if t.name == tool_name:
            tool_fn = t
            break
    if not tool_fn:
        return {
            "success": False,
            "tool_result": None,
            "state_after": current_state,
            "error": f"Tool {tool_name} no encontrada",
            "cart_summary": _cart_summary_from_session(wa_id, business_id),
        }

    # Log intent -> tool + args (exclude injected_business_context) so we can see what was called
    log_params = {k: v for k, v in tool_args.items() if k != "injected_business_context"}
    logger.warning(
        "[ORDER_FLOW] Executing intent=%s -> tool=%s args=%s",
        intent,
        tool_name,
        log_params,
    )

    result_str = None
    if intent == INTENT_ADD_TO_CART and isinstance(params.get("items"), list) and len(params["items"]) > 0:
        # Multi-item add: invoke add_to_cart for each item (backend remains single source of truth)
        result_parts = []
        for item in params["items"]:
            if not isinstance(item, dict):
                continue
            args = {
                "injected_business_context": ctx,
                "product_id": item.get("product_id") or "",
                "product_name": item.get("product_name") or "",
                "quantity": int(item.get("quantity") or 1),
            }
            try:
                r = tool_fn.invoke(args)
                result_parts.append(r if isinstance(r, str) else str(r))
            except Exception as e:
                logger.exception("[ORDER_FLOW] add_to_cart item failed: %s", e)
                result_parts.append(f"❌ Error: {e}")
        result_str = "\n".join(result_parts) if result_parts else ""
    elif intent == INTENT_UPDATE_CART_ITEM and isinstance(params.get("items"), list) and len(params["items"]) == 2:
        # Replace A with B: reduce/remove first product, add second
        first = params["items"][0] if isinstance(params["items"][0], dict) else {}
        second = params["items"][1] if isinstance(params["items"][1], dict) else {}
        first_name = (first.get("product_name") or first.get("name") or "").strip()
        second_name = (second.get("product_name") or second.get("name") or "").strip()
        remove_qty = int(first.get("quantity") or 1)
        add_qty = int(second.get("quantity") or 1)
        pid = _resolve_product_id_by_name(wa_id, business_id, first_name) if first_name else None
        if not pid:
            result_str = f"❌ No encontré '{first_name}' en tu carrito. Indica el producto a cambiar o revisa el carrito."
        else:
            current_qty = _get_cart_item_quantity(wa_id, business_id, pid)
            new_qty = max(0, current_qty - remove_qty)
            try:
                r1 = tool_fn.invoke({
                    "injected_business_context": ctx,
                    "product_id": pid,
                    "quantity": new_qty,
                })
                result_str = r1 if isinstance(r1, str) else str(r1)
            except Exception as e:
                logger.exception("[ORDER_FLOW] update_cart_item (replace) failed: %s", e)
                result_str = f"❌ Error al actualizar: {e}"
            if result_str and "❌" not in result_str and second_name:
                add_tool = next((t for t in order_tools.order_tools if t.name == "add_to_cart"), None)
                if add_tool:
                    try:
                        r2 = add_tool.invoke({
                            "injected_business_context": ctx,
                            "product_id": "",
                            "product_name": second_name,
                            "quantity": add_qty,
                        })
                        result_str = result_str + "\n" + (r2 if isinstance(r2, str) else str(r2))
                    except Exception as e:
                        logger.exception("[ORDER_FLOW] add_to_cart (replace) failed: %s", e)
                        result_str = result_str + "\n" + f"❌ Error al agregar: {e}"
    else:
        try:
            result = tool_fn.invoke(tool_args)
            result_str = result if isinstance(result, str) else str(result)
        except Exception as e:
            logger.exception("[ORDER_FLOW] Tool %s failed: %s", tool_name, e)
            return {
                "success": False,
                "tool_result": str(e),
                "state_after": current_state,
                "error": str(e),
                "cart_summary": _cart_summary_from_session(wa_id, business_id),
            }

    # Log tool result summary (length; first 80 chars if short) for debugging
    result_preview = (result_str[:80] + "…") if len(result_str) > 80 else result_str
    logger.warning(
        "[ORDER_FLOW] Tool %s completed | result_len=%s | preview=%s",
        tool_name,
        len(result_str),
        result_preview.replace("\n", " "),
    )

    # Cart debug: log before and after for cart-mutating intents
    if intent in CART_MUTATING_INTENTS and cart_before_log is not None:
        cart_after_log = _get_cart_for_logging(wa_id, business_id)
        _log_cart_debug(wa_id, business_id, tool_name, params, cart_before_log, cart_after_log)

    # State transitions after successful tool run
    state_after = current_state
    if intent == INTENT_ADD_TO_CART and current_state == ORDER_STATE_GREETING and "✅" in result_str:
        session_state_service.save(
            wa_id,
            business_id,
            {"order_context": {**order_tools._cart_from_session(wa_id, business_id), "state": ORDER_STATE_ORDERING}},
        )
        state_after = ORDER_STATE_ORDERING
    elif intent == INTENT_SUBMIT_DELIVERY_INFO and "✅" in result_str:
        cart_after = order_tools._cart_from_session(wa_id, business_id)
        di = cart_after.get("delivery_info") or {}
        cust = customer_service.get_customer(wa_id) if wa_id else None
        # Merged view: session overrides, then DB (so we see "all present" when user just filled the last gap)
        merged_name = (di.get("name") or "").strip() or (cust.get("name") or "").strip() if cust else (di.get("name") or "").strip()
        merged_address = (di.get("address") or "").strip() or (cust.get("address") or "").strip() if cust else (di.get("address") or "").strip()
        merged_phone = (di.get("phone") or "").strip() or (cust.get("phone") or "").strip() if cust else (di.get("phone") or "").strip()
        merged_payment = (di.get("payment_method") or "").strip() or (cust.get("payment_method") or "").strip() if cust else (di.get("payment_method") or "").strip()
        has_all = bool(merged_name) and bool(merged_address) and bool(merged_phone) and bool(merged_payment)
        if has_all:
            delivery_info = {"name": merged_name, "address": merged_address, "phone": merged_phone, "payment_method": merged_payment}
            session_state_service.save(
                wa_id,
                business_id,
                {"order_context": {**cart_after, "delivery_info": delivery_info, "state": ORDER_STATE_READY_TO_PLACE}},
            )
            state_after = ORDER_STATE_READY_TO_PLACE
            # So response generator says "Tengo esta dirección... ¿Procedemos?" instead of generic "He guardado..."
            result_str = (
                result_str
                + "\nDELIVERY_STATUS|name=" + merged_name + "|address=" + merged_address
                + "|phone=" + merged_phone + "|payment=" + merged_payment
                + "|all_present=true|missing="
            )
        else:
            session_state_service.save(
                wa_id,
                business_id,
                {"order_context": {**cart_after, "state": ORDER_STATE_COLLECTING_DELIVERY}},
            )
            state_after = ORDER_STATE_COLLECTING_DELIVERY
            # So response generator asks only for missing fields instead of "¿proceder o agregar más?"
            missing = []
            if not merged_name:
                missing.append("name")
            if not merged_address:
                missing.append("address")
            if not merged_phone:
                missing.append("phone")
            if not merged_payment:
                missing.append("payment")
            name_d = merged_name or order_tools.NO_REGISTRADO
            addr_d = merged_address or order_tools.NO_REGISTRADA
            phone_d = merged_phone or order_tools.NO_REGISTRADO
            pay_d = merged_payment or order_tools.NO_REGISTRADO
            result_str = (
                result_str
                + "\nDELIVERY_STATUS|name=" + name_d + "|address=" + addr_d
                + "|phone=" + phone_d + "|payment=" + pay_d
                + "|all_present=false|missing=" + ",".join(missing)
            )
    elif intent == INTENT_PLACE_ORDER and ("✅" in result_str or "confirmado" in result_str.lower()):
        state_after = ORDER_STATE_GREETING  # context cleared by place_order tool

    return {
        "success": "❌" not in result_str and "Error" not in result_str[:10],
        "tool_result": result_str,
        "state_after": state_after,
        "error": None if "❌" not in result_str else result_str,
        "cart_summary": _cart_summary_from_session(wa_id, business_id),
    }
