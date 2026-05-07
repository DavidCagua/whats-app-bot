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
import re
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
from ..database.product_order_service import (
    product_order_service,
    AmbiguousProductError,
    ProductNotFoundError,
)
from ..services import order_tools
from ..services import catalog_cache
from ..services import catalog_service
from ..services import promotion_service
from ..services.cancel_keywords import has_explicit_cancel_keyword
from . import turn_cache


def _save_session_and_invalidate(wa_id: str, business_id: str, state_update: Dict) -> None:
    """
    Persist a session state update and drop the per-turn cached copy
    so subsequent reads in this turn see the merged state. Every save
    in order_flow goes through this wrapper because the service does a
    server-side shallow merge the in-memory cache can't observe.
    """
    session_state_service.save(wa_id, business_id, state_update)
    turn_cache.current().invalidate_session(wa_id, business_id)

logger = logging.getLogger(__name__)

# Intent names (planner output)
# Note: GREET was removed when the router's greeting fast-path took over
# pure-greeting handling (see app/services/business_greeting.py).
INTENT_GET_MENU_CATEGORIES = "GET_MENU_CATEGORIES"
INTENT_LIST_PRODUCTS = "LIST_PRODUCTS"
INTENT_SEARCH_PRODUCTS = "SEARCH_PRODUCTS"
INTENT_GET_PRODUCT = "GET_PRODUCT"
INTENT_ADD_TO_CART = "ADD_TO_CART"
INTENT_ADD_PROMO_TO_CART = "ADD_PROMO_TO_CART"
INTENT_VIEW_CART = "VIEW_CART"
INTENT_UPDATE_CART_ITEM = "UPDATE_CART_ITEM"
INTENT_REMOVE_FROM_CART = "REMOVE_FROM_CART"
INTENT_PROCEED_TO_CHECKOUT = "PROCEED_TO_CHECKOUT"
INTENT_GET_CUSTOMER_INFO = "GET_CUSTOMER_INFO"
INTENT_SUBMIT_DELIVERY_INFO = "SUBMIT_DELIVERY_INFO"
INTENT_PLACE_ORDER = "PLACE_ORDER"
INTENT_CHAT = "CHAT"
# Semantic intent: user expressed confirmation ("listo", "procedamos", "sí",
# "dale", "ok"). Not a transition — the executor resolves it to the concrete
# action that makes sense for the current state. This keeps the planner out
# of state-machine decisions it can't see.
INTENT_CONFIRM = "CONFIRM"
# Customer wants to abandon the in-progress cart entirely ("cancela el pedido",
# "déjalo así", "ya no quiero pedir"). Distinct from REMOVE_FROM_CART, which
# drops a single product. ABANDON_CART clears the cart and resets state to
# GREETING. Cancellation of a PLACED order belongs to the customer service
# agent — see customer_service_flow.INTENT_CANCEL_ORDER.
INTENT_ABANDON_CART = "ABANDON_CART"

# Result kinds — routing signal for the response generator.
# Every execute_order_intent return carries exactly one of these so the
# response generator can pick the right branch without string-sniffing tool output.
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
RESULT_KIND_CART_ABANDONED = "cart_abandoned"

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
        INTENT_GET_MENU_CATEGORIES,
        INTENT_LIST_PRODUCTS,
        INTENT_SEARCH_PRODUCTS,
        INTENT_GET_PRODUCT,
        INTENT_ADD_TO_CART,
        INTENT_ADD_PROMO_TO_CART,
        # Allowed so multi-intent dispatch can capture delivery PII when
        # the customer dumps everything in their first message (product +
        # name + address + phone). Executor stays in GREETING when the
        # cart is empty — purely additive data capture, no forced state move.
        INTENT_SUBMIT_DELIVERY_INFO,
        INTENT_CONFIRM,
        INTENT_CHAT,
    ),
    ORDER_STATE_ORDERING: (
        INTENT_GET_MENU_CATEGORIES,
        INTENT_LIST_PRODUCTS,
        INTENT_SEARCH_PRODUCTS,
        INTENT_GET_PRODUCT,
        INTENT_ADD_TO_CART,
        INTENT_ADD_PROMO_TO_CART,
        INTENT_VIEW_CART,
        INTENT_UPDATE_CART_ITEM,
        INTENT_REMOVE_FROM_CART,
        INTENT_PROCEED_TO_CHECKOUT,
        # Same rationale as GREETING — multi-intent capture path.
        INTENT_SUBMIT_DELIVERY_INFO,
        INTENT_CONFIRM,
        INTENT_ABANDON_CART,
        INTENT_CHAT,
    ),
    ORDER_STATE_COLLECTING_DELIVERY: (
        INTENT_GET_CUSTOMER_INFO,
        INTENT_SUBMIT_DELIVERY_INFO,
        INTENT_PROCEED_TO_CHECKOUT,
        INTENT_CONFIRM,
        INTENT_ABANDON_CART,
        INTENT_CHAT,
    ),
    ORDER_STATE_READY_TO_PLACE: (
        INTENT_PLACE_ORDER,
        INTENT_VIEW_CART,
        INTENT_GET_CUSTOMER_INFO,
        INTENT_SUBMIT_DELIVERY_INFO,
        INTENT_CONFIRM,
        INTENT_ABANDON_CART,
        INTENT_CHAT,
    ),
}


# Semantic-intent resolution: CONFIRM is the only semantic (non-transitional)
# intent today. Keeping this table small and explicit — the executor, not the
# planner, owns state-machine decisions.
CONFIRM_RESOLUTION: Dict[str, str] = {
    ORDER_STATE_GREETING: INTENT_CHAT,
    ORDER_STATE_ORDERING: INTENT_PROCEED_TO_CHECKOUT,
    ORDER_STATE_COLLECTING_DELIVERY: INTENT_GET_CUSTOMER_INFO,
    ORDER_STATE_READY_TO_PLACE: INTENT_PLACE_ORDER,
}

CART_MUTATING_INTENTS = (
    INTENT_ADD_TO_CART,
    INTENT_ADD_PROMO_TO_CART,
    INTENT_REMOVE_FROM_CART,
    INTENT_UPDATE_CART_ITEM,
)

# Browse intents that should re-open the cart when emitted during
# COLLECTING_DELIVERY / READY_TO_PLACE. The user changed their mind
# mid-checkout and wants to explore the menu or add more items.
# Delivery info persists in the session — CONFIRM picks it back up.
BROWSE_INTENTS = (
    INTENT_LIST_PRODUCTS,
    INTENT_SEARCH_PRODUCTS,
    INTENT_GET_PRODUCT,
    INTENT_GET_MENU_CATEGORIES,
)


# ---------- helpers ----------

def _cart_summary_from_session(wa_id: str, business_id: str) -> str:
    """Short human cart summary for logging (never shown to user as-is anymore)."""
    result = turn_cache.current().get_session(
        wa_id, business_id,
        loader=lambda: session_state_service.load(wa_id, business_id),
    )
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
    """
    Resolve a planner-emitted product_name to a product_id in the cart.

    Handles three name shapes the planner can produce:
      1. Exact base name:  "Jugos en leche"      → matches item.name
      2. Name with notes:  "Jugos en leche (mango)" → strip parens, match name + notes
      3. Qualifier phrase:  "jugo de mango"        → extract qualifier, match item.notes

    When multiple cart items share the same base name (e.g. two Jugos en
    leche with different notes), the qualifier/parenthetical is used to
    disambiguate. Returns the first match or None.
    """
    if not product_name or not wa_id or not business_id:
        return None
    cart = order_tools._cart_from_session(wa_id, business_id)
    items = cart.get("items") or []
    if not items:
        return None

    raw = (product_name or "").strip()

    # Extract parenthetical notes if present: "Jugos en leche (mango)" → base="Jugos en leche", paren_notes="mango"
    paren_match = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", raw)
    if paren_match:
        base_raw = paren_match.group(1).strip()
        paren_notes = paren_match.group(2).strip().lower()
    else:
        base_raw = raw
        paren_notes = ""

    want = _normalize_product_name(base_raw)

    # Pass 1: exact base-name match. Collect all matches.
    matches = []
    for it in items:
        item_name = _normalize_product_name(it.get("name") or "")
        if item_name and item_name == want:
            matches.append(it)

    if len(matches) == 1:
        return matches[0].get("product_id")

    if len(matches) > 1 and paren_notes:
        # Multiple items share the base name — use parenthetical to disambiguate.
        for it in matches:
            if (it.get("notes") or "").strip().lower() == paren_notes:
                return it.get("product_id")

    if matches:
        # Multiple matches, no disambiguating notes → return first (best effort)
        return matches[0].get("product_id")

    # Pass 2: the planner may have emitted a qualifier phrase instead of
    # the catalog name, e.g. "jugo de mango" for item "Jugos en leche"
    # with notes="mango". Try matching any item whose notes contain a
    # word from the query that ISN'T in the item's base name.
    want_tokens = set(want.split())
    for it in items:
        item_name_norm = _normalize_product_name(it.get("name") or "")
        item_notes = (it.get("notes") or "").strip().lower()
        if not item_notes:
            continue
        name_tokens = set(item_name_norm.split())
        # Qualifier tokens = words the user said that aren't in the product name
        qualifier_tokens = want_tokens - name_tokens
        if qualifier_tokens and item_notes in qualifier_tokens:
            return it.get("product_id")

    # Pass 3: partial / substring match on base name (existing fallback)
    for it in items:
        item_name_norm = _normalize_product_name(it.get("name") or "")
        if want and want in item_name_norm:
            return it.get("product_id")

    return None


def _normalize_for_match(s: str) -> str:
    """
    Accent-insensitive + case-insensitive product-name normalization used by
    the disambiguation bypass. The planner is instructed to echo exact option
    names, but users may type without accents and models occasionally drop or
    add them — we shouldn't re-trigger disambiguation over diacritics alone.
    """
    import unicodedata as _ud
    base = _normalize_product_name(s)
    if not base:
        return ""
    nfkd = _ud.normalize("NFD", base)
    return "".join(c for c in nfkd if _ud.category(c) != "Mn")


def _resolve_from_pending_disambiguation(
    pending: Optional[Dict[str, Any]],
    product_name: str,
) -> Optional[str]:
    """
    If a disambiguation is pending from the previous turn and `product_name`
    matches exactly one of the saved options by normalized name, return that
    option's product_id. Otherwise return None. The caller can then bypass
    search_products and use the product_id directly — breaking the infinite
    disambiguation loop that would otherwise fire when a query word (e.g.
    "soda") is both an exact match and a prefix of other variants.
    """
    if not pending or not product_name:
        return None
    options = pending.get("options") or []
    if not options:
        return None
    want = _normalize_for_match(product_name)
    if not want:
        return None
    for opt in options:
        if not isinstance(opt, dict):
            continue
        opt_name = _normalize_for_match(opt.get("name") or "")
        if opt_name and opt_name == want:
            pid = opt.get("product_id") or opt.get("id")
            if pid:
                return str(pid)
    return None


def _get_cart_item_quantity(wa_id: str, business_id: str, product_id: str) -> int:
    cart = order_tools._cart_from_session(wa_id, business_id)
    for it in (cart.get("items") or []):
        if it.get("product_id") == product_id:
            return int(it.get("quantity") or 0)
    return 0


def _get_cart_for_logging(wa_id: str, business_id: str) -> Dict:
    result = turn_cache.current().get_session(
        wa_id, business_id,
        loader=lambda: session_state_service.load(wa_id, business_id),
    )
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
        # "exact" | "lexical" | "embedding" — which retrieval lane fired.
        # Response generator uses this to decide whether to present results
        # as authoritative matches or as "related products you might like".
        # Absent = direct DB lookup (LIST_PRODUCTS by category), treat as
        # authoritative.
        "matched_by": p.get("matched_by"),
    }


def _clean_cart_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Trim cart items for the response generator. Keeps product_id +
    promotion_id + promo_group_id so the response side can re-run the
    matcher and group bundle items in display."""
    return [
        {
            "product_id": it.get("product_id"),
            "name": it.get("name") or "",
            "quantity": int(it.get("quantity") or 0),
            "price": float(it.get("price") or 0),
            "notes": (it.get("notes") or "").strip() or None,
            "promotion_id": it.get("promotion_id"),
            "promo_group_id": it.get("promo_group_id"),
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
    """Falls back to the shared DELIVERY_FEE_DEFAULT so order receipts
    and CS info answers agree on the same number."""
    from ..services.business_info_service import DELIVERY_FEE_DEFAULT
    if not business_context:
        return float(DELIVERY_FEE_DEFAULT)
    settings = (business_context.get("business") or {}).get("settings") or {}
    return float(settings.get("delivery_fee", DELIVERY_FEE_DEFAULT))


def _get_allowed_payment_methods(business_context: Optional[Dict]) -> List[str]:
    """
    Return the list of payment methods configured by the business in
    settings.payment_methods. Empty list means "no enforcement" — every
    payment string is accepted (legacy behaviour).
    """
    if not business_context:
        return []
    settings = (business_context.get("business") or {}).get("settings") or {}
    raw = settings.get("payment_methods") or []
    if not isinstance(raw, list):
        return []
    return [str(m).strip() for m in raw if str(m).strip()]


def _normalize_payment_method(value: str, allowed: List[str]) -> Optional[str]:
    """
    Match `value` against the configured payment-method list (case-
    insensitive, trim, partial substring). Returns the canonical entry
    from `allowed` or None if no match.

    If `allowed` is empty, returns the original value (no enforcement).
    """
    if not value:
        return None
    v = value.strip().lower()
    if not v:
        return None
    if not allowed:
        return value.strip()
    for canonical in allowed:
        c = canonical.strip().lower()
        if not c:
            continue
        if v == c or v in c or c in v:
            return canonical
    return None


def _build_delivery_status(
    wa_id: str,
    business_id: str,
    business_context: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Build structured delivery status from session + DB customer.
    Session delivery_info overrides DB customer. When the business
    configures an allowed payment-method list, any resolved value that
    doesn't match is treated as missing so the bot re-asks instead of
    silently accepting an unsupported method.
    """
    cart = order_tools._cart_from_session(wa_id, business_id) if wa_id and business_id else {}
    session_di = cart.get("delivery_info") or {}
    cust = (
        turn_cache.current().get_customer(
            wa_id, loader=lambda: customer_service.get_customer(wa_id)
        )
        if wa_id
        else None
    )
    cust = cust or {}

    name = (session_di.get("name") or "").strip() or (cust.get("name") or "").strip()
    address = (session_di.get("address") or "").strip() or (cust.get("address") or "").strip()
    phone = (session_di.get("phone") or "").strip() or (cust.get("phone") or "").strip()
    payment_raw = (session_di.get("payment_method") or "").strip() or (cust.get("payment_method") or "").strip()

    allowed = _get_allowed_payment_methods(business_context)
    payment_canonical: Optional[str] = None
    if payment_raw:
        payment_canonical = _normalize_payment_method(payment_raw, allowed)

    missing = []
    if not name:
        missing.append("name")
    if not address:
        missing.append("address")
    if not phone:
        missing.append("phone")
    if not payment_canonical:
        missing.append("payment")

    return {
        "all_present": len(missing) == 0,
        "missing": missing,
        "name": name or None,
        "address": address or None,
        "phone": phone or None,
        "payment_method": payment_canonical,
        "payment_methods_allowed": allowed,
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


def _save_pending_disambiguation(
    wa_id: str,
    business_id: str,
    requested_name: str,
    options: List[Dict[str, Any]],
    pending_replacement_product_id: Optional[str] = None,
    requested_quantity: Optional[int] = None,
    requested_notes: Optional[str] = None,
) -> None:
    """
    Save the options we just offered the customer so the NEXT turn's planner
    can resolve replies like 'la normal', 'la primera', 'la Corona'.

    When pending_replacement_product_id is set, the disambiguation was
    triggered by a product swap (UPDATE_CART_ITEM.new_product_name) — the
    bypass resolver in the next turn will remove that cart item after
    successfully adding the chosen replacement.

    ``requested_quantity`` and ``requested_notes`` carry the customer's
    original ask through the disambiguation roundtrip. Without them,
    "dos limonadas" → "limonada natural" lands as 1x natural because the
    planner's default quantity is 1 (production 2026-05-07,
    Biela / 3177000722: customer ordered 2 limonadas + barracuda + vimota,
    only 1 limonada was kept after disamb).
    """
    try:
        result = turn_cache.current().get_session(
            wa_id, business_id,
            loader=lambda: session_state_service.load(wa_id, business_id),
        )
        oc = dict((result.get("session", {}).get("order_context") or {}))
        pending_entry: Dict[str, Any] = {
            "requested_name": requested_name,
            "options": options,
        }
        if pending_replacement_product_id:
            pending_entry["pending_replacement_product_id"] = pending_replacement_product_id
        if requested_quantity and requested_quantity > 0:
            pending_entry["requested_quantity"] = int(requested_quantity)
        if requested_notes:
            pending_entry["requested_notes"] = requested_notes.strip()
        oc["pending_disambiguation"] = pending_entry
        _save_session_and_invalidate(wa_id, business_id, {"order_context": oc})
        turn_cache.current().invalidate_session(wa_id, business_id)
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


def _recovery_result(
    state_after: str,
    wa_id: str,
    business_id: str,
    business_context: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Build a 'soft' result when the planner emits a state-illegal intent.
    The allowlist rejection used to surface as a user-facing USER_ERROR
    ("En este momento no podemos proceder con eso."), which dead-ends the
    customer — we saw Biela orders abandoned this way. Instead, re-render
    a state-appropriate prompt so the conversation keeps moving.

    Tagged with ``is_recovery=True`` so the multi-intent dispatcher can
    prefer a real prior result over a recovery when picking which result
    drives the customer-facing reply.
    """
    if state_after in (ORDER_STATE_COLLECTING_DELIVERY, ORDER_STATE_READY_TO_PLACE):
        # Re-emit the confirmation/collection prompt the user was responding to.
        delivery_status = _build_delivery_status(wa_id, business_id, business_context)
        result = _base_result(
            state_after, wa_id, business_id,
            RESULT_KIND_DELIVERY_STATUS,
            delivery_status=delivery_status,
        )
    else:
        # ORDERING / GREETING: a neutral CHAT turn is safer than a USER_ERROR.
        result = _base_result(state_after, wa_id, business_id, RESULT_KIND_CHAT)
    result["is_recovery"] = True
    return result


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
    pending_replacement_product_id: Optional[str] = None,
) -> Dict[str, Any]:
    options = [
        {
            "name": m.get("name"),
            "price": float(m.get("price") or 0),
            "product_id": m.get("id") or m.get("product_id"),
        }
        for m in matches
    ]
    logger.warning(
        "[ORDER_FLOW] Ambiguous product '%s' — %d options; blocking action, asking user",
        requested_name, len(matches),
    )
    # Persist the options so the NEXT turn's planner can resolve replies
    # like "la normal", "la primera", "el más barato" against them.
    _save_pending_disambiguation(
        wa_id, business_id, requested_name, options,
        pending_replacement_product_id=pending_replacement_product_id,
    )
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


# ---------- context-aware disambiguation filter ----------

def _extract_product_names_from_history(
    conversation_history: Optional[List[Dict[str, str]]],
) -> set:
    """
    Extract product names mentioned in recent assistant messages.

    Scans the last few assistant messages for product name patterns like
    "SPECIAL DOG ($27.000)" or "• VITTORIA ($28.000)" — the format used
    by the response generator when listing products.

    Returns a set of lowercased product names found.
    """
    if not conversation_history:
        return set()

    names: set = set()
    # Look at the last 4 assistant messages (covers typical list + follow-up)
    assistant_msgs = [
        m for m in conversation_history
        if (m.get("role") or "") == "assistant"
    ][-4:]

    for msg in assistant_msgs:
        content = msg.get("content") or msg.get("message") or ""
        # Pattern 1: "PRODUCT_NAME ($XX.XXX)" — standard product listing
        for match in re.finditer(r"([A-ZÁÉÍÓÚÑa-záéíóúñ][\w\s]*?)\s*\(\$[\d.,]+\)", content):
            name = match.group(1).strip()
            if len(name) >= 3:  # skip noise like "de" or "a"
                names.add(name.lower())
        # Pattern 2: "• NAME" or "- NAME" at start of line — bullet lists
        for match in re.finditer(r"(?:^|[\n•\-])\s*\**([A-ZÁÉÍÓÚÑ][\w\s]+?)\**(?:\s*[\($\-—:])", content):
            name = match.group(1).strip()
            if len(name) >= 3:
                names.add(name.lower())

    return names


def _filter_ambiguous_by_history(
    matches: List[Dict[str, Any]],
    conversation_history: Optional[List[Dict[str, str]]],
) -> Optional[Dict[str, Any]]:
    """
    When AmbiguousProductError fires, check if exactly one candidate
    was recently listed by the bot. If so, the user is referencing
    that product by abbreviation — return it as the winner.

    Returns the winning product dict, or None if filtering can't resolve.
    """
    recent_names = _extract_product_names_from_history(conversation_history)
    if not recent_names:
        return None

    # Find which ambiguous candidates appeared in recent bot messages
    in_history = [
        m for m in matches
        if (m.get("name") or "").lower() in recent_names
    ]

    if len(in_history) == 1:
        winner = in_history[0]
        logger.info(
            "[ORDER_FLOW] disambig resolved by history context: %r "
            "(was in recent listing, other candidates were not)",
            winner.get("name"),
        )
        return winner

    return None


# ---------- main executor ----------

def execute_order_intent(
    wa_id: str,
    business_id: str,
    business_context: Optional[Dict],
    session: Dict,
    intent: str,
    params: Optional[Dict] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    message_body: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute one order intent: validate state, run one tool (or compute state),
    return a structured result the response generator can consume by result_kind.
    """
    params = params or {}
    ctx = {**(business_context or {}), "wa_id": wa_id, "business_id": business_id}
    order_context = session.get("order_context") or {}
    current_state = order_context.get("state") or derive_order_state(order_context)
    # Capture any pending disambiguation from the previous turn BEFORE we clear
    # it — used by the add/swap bypass so replies like "soda" can be resolved
    # against the saved options without re-triggering ambiguity.
    pending_disamb: Optional[Dict[str, Any]] = order_context.get("pending_disambiguation") or None

    # --- Semantic intent resolution ---------------------------------------
    # CONFIRM is a user-intent, not a transition. Translate it here so the
    # rest of the executor only ever sees concrete intents.
    if intent == INTENT_CONFIRM:
        resolved = CONFIRM_RESOLUTION.get(current_state, INTENT_CHAT)
        logger.warning(
            "[ORDER_FLOW] CONFIRM resolved: state=%s -> intent=%s",
            current_state, resolved,
        )
        intent = resolved

    # --- Hard guard for ABANDON_CART --------------------------------------
    # ABANDON_CART wipes the in-progress cart and resets state to GREETING.
    # The planner has hallucinated this intent on bare clarifying nouns
    # mid-checkout (production 2026-05-06, Biela / 3137112249: customer
    # said "Hamburguesa" in COLLECTING_DELIVERY → ABANDON_CART → cart wiped,
    # sale lost). Mirrors the CANCEL_ORDER guard in customer_service_agent;
    # without an explicit cancel verb, downgrade to CHAT so the response
    # generator just re-prompts the customer instead of acting destructively.
    if intent == INTENT_ABANDON_CART and not has_explicit_cancel_keyword(message_body):
        logger.warning(
            "[ORDER_FLOW] ABANDON_CART refused: no explicit cancel keyword in "
            "message=%r (state=%s) — downgrading to CHAT",
            (message_body or "")[:120], current_state,
        )
        intent = INTENT_CHAT

    # --- Safety coercion --------------------------------------------------
    # If the planner still emits PROCEED_TO_CHECKOUT while in READY_TO_PLACE
    # (it shouldn't, now that CONFIRM exists — but we saw this in prod), the
    # user meant "place the order," not "re-enter checkout." Coerce instead
    # of rejecting. Logged as [COERCE] so we can watch for planner drift.
    if current_state == ORDER_STATE_READY_TO_PLACE and intent == INTENT_PROCEED_TO_CHECKOUT:
        logger.warning(
            "[ORDER_FLOW] [COERCE] PROCEED_TO_CHECKOUT in READY_TO_PLACE -> PLACE_ORDER",
        )
        intent = INTENT_PLACE_ORDER

    allowed = ALLOWED_INTENTS_BY_STATE.get(current_state, ())
    if intent not in allowed:
        if (
            current_state in (ORDER_STATE_READY_TO_PLACE, ORDER_STATE_COLLECTING_DELIVERY)
            and intent in (*CART_MUTATING_INTENTS, *BROWSE_INTENTS)
        ):
            logger.warning(
                "[ORDER_FLOW] Re-opening cart: intent=%s state=%s -> %s",
                intent, current_state, ORDER_STATE_ORDERING,
            )
            _save_session_and_invalidate(
                wa_id, business_id,
                {"order_context": {**order_context, "state": ORDER_STATE_ORDERING}},
            )
            turn_cache.current().invalidate_session(wa_id, business_id)
            order_context = {**order_context, "state": ORDER_STATE_ORDERING}
            current_state = ORDER_STATE_ORDERING
            session = {**session, "order_context": order_context}
        else:
            # INVARIANT: a planner-emitted intent should never fail the
            # allowlist now that (a) CONFIRM handles confirmation verbs and
            # (b) cart mutations auto re-open. If we land here it's planner
            # drift; log loudly but recover instead of dead-ending the user.
            logger.error(
                "[ORDER_FLOW] [INVARIANT] Intent rejected: intent=%s state=%s allowed=%s",
                intent, current_state, list(allowed),
            )
            return _recovery_result(current_state, wa_id, business_id, business_context)

    # Pending disambiguation is one-shot: the planner already consumed it
    # (via the prompt context). Clear it now; if THIS intent raises another
    # AmbiguousProductError, _disambig_result will save a fresh one.
    _clear_pending_disambiguation(wa_id, business_id)

    try:
        # --- state-transition / no-tool intents ---
        if intent == INTENT_CHAT:
            return _base_result(current_state, wa_id, business_id, RESULT_KIND_CHAT)

        if intent == INTENT_PROCEED_TO_CHECKOUT:
            cart_before = _get_cart_for_logging(wa_id, business_id)
            if not cart_before.get("items"):
                return _user_error_result(
                    current_state, wa_id, business_id,
                    "Tu pedido está vacío. Agrega productos antes de continuar.",
                )
            _save_session_and_invalidate(
                wa_id, business_id,
                {"order_context": {**order_context, "state": ORDER_STATE_COLLECTING_DELIVERY}},
            )
            turn_cache.current().invalidate_session(wa_id, business_id)
            delivery_status = _build_delivery_status(wa_id, business_id, business_context)
            state_after = ORDER_STATE_COLLECTING_DELIVERY
            if delivery_status["all_present"]:
                cart_now = order_tools._cart_from_session(wa_id, business_id)
                _save_session_and_invalidate(
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

        # --- read intents (delegated to catalog_service) ---
        # catalog_service is the shared read-only capability for menu/search.
        # Response generator still receives _clean_product_dict-shaped payloads
        # so the prompt/eval contract is unchanged.
        if intent == INTENT_GET_MENU_CATEGORIES:
            categories = catalog_service.list_categories(business_id)
            if categories:
                return _base_result(
                    current_state, wa_id, business_id,
                    RESULT_KIND_MENU_CATEGORIES,
                    categories=list(categories),
                )
            all_products = catalog_service.list_products(business_id)
            return _base_result(
                current_state, wa_id, business_id,
                RESULT_KIND_PRODUCTS_LIST,
                products=[_clean_product_dict(p) for p in all_products],
                query_label=None,
                category_label=None,
            )

        if intent == INTENT_LIST_PRODUCTS:
            category = (params.get("category") or "").strip()
            products = catalog_service.list_products(
                business_id=business_id, category=category or None,
            )
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
            products = catalog_service.search_products(
                business_id=business_id, query=query,
            )
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
            product = catalog_service.get_product(
                business_id=business_id,
                product_id=product_id or None,
                product_name=product_name or None,
            )
            if not product:
                return _user_error_result(
                    current_state, wa_id, business_id,
                    f"No encontré {product_name or 'ese producto'} en el menú.",
                )
            # Surface how many of THIS product the user already has in
            # their active cart. The response generator uses this to
            # avoid the "¿agregarla al pedido?" upsell when the product
            # is already there — that prompt drove "Si" → duplicate
            # cart line in production (Biela 2026-05-06 +573159280840).
            try:
                cart = order_tools._cart_from_session(wa_id, business_id)
                pid = str(product.get("id") or "")
                in_cart_qty = sum(
                    int(it.get("quantity") or 0)
                    for it in (cart.get("items") or [])
                    if str(it.get("product_id") or "") == pid
                )
            except Exception:
                in_cart_qty = 0
            return _base_result(
                current_state, wa_id, business_id,
                RESULT_KIND_PRODUCT_DETAILS,
                product=_clean_product_dict(product),
                in_cart_quantity=in_cart_qty,
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
            delivery_status = _build_delivery_status(wa_id, business_id, business_context)
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

            # Track per-item failures in a multi-item batch so we can
            # ask the user to clarify / flag missing items after processing
            # the rest. Both lists survive the loop and are consumed by
            # the post-loop branching below.
            multi_ambiguity: Optional[AmbiguousProductError] = None
            multi_ambiguity_query: str = ""
            # Carry the original requested quantity/notes through the disamb
            # roundtrip so the customer's "dos limonadas" doesn't collapse to
            # 1x when they reply with the variant choice.
            multi_ambiguity_quantity: Optional[int] = None
            multi_ambiguity_notes: Optional[str] = None
            multi_not_found: List[str] = []

            if isinstance(params.get("items"), list) and len(params["items"]) > 0:
                # Multi-item add: skip duplicates, invoke once per item.
                # We explicitly DO NOT re-raise AmbiguousProductError here —
                # one ambiguous item in a batch shouldn't black-hole the
                # rest. Instead we capture the first ambiguity, keep
                # processing the remaining items, and at the end either
                # ask for clarification (if nothing succeeded) or return a
                # cart_change result carrying a pending_clarification field
                # so the response generator can mention both: "agregué la
                # soda; del jugo tengo estas opciones …".
                existing_names = {_normalize_product_name(it.get("name") or "") for it in (cart_before.get("items") or [])}
                # Duplicate-add guard: only skip items already in cart when
                # the batch has 2+ items. This catches the planner
                # hallucination pattern (user asks for 1 product, planner
                # re-emits the whole cart), but allows single-item batches
                # through — when there's only one item, the user explicitly
                # asked for it (e.g. "dame otra barracuda") and skipping
                # would silently drop their request. (fix: 984e1b2 was too
                # aggressive, blocked all re-adds including intentional ones)
                skip_existing = len(params["items"]) > 1
                for item in params["items"]:
                    if not isinstance(item, dict):
                        continue
                    # Strip trailing parenthetical notes from the planner's
                    # product_name before comparing — the planner sometimes
                    # includes cart-summary notes like "(mango)" in the name
                    # (e.g. "Jugos en leche (mango)") which won't match the
                    # base name "Jugos en leche" in the cart.
                    raw_name = re.sub(r"\s*\(.*?\)\s*$", "", item.get("product_name") or "")
                    item_name = _normalize_product_name(raw_name)
                    if skip_existing and item_name and item_name in existing_names:
                        logger.warning(
                            "[ORDER_FLOW] Skipping duplicate add: '%s' already in cart",
                            item.get("product_name"),
                        )
                        continue
                    bypass_pid = _resolve_from_pending_disambiguation(
                        pending_disamb, item.get("product_name") or ""
                    )
                    args = {
                        "injected_business_context": ctx,
                        "product_id": item.get("product_id") or bypass_pid or "",
                        "product_name": "" if bypass_pid else (item.get("product_name") or ""),
                        "quantity": int(item.get("quantity") or 1),
                        "notes": (item.get("notes") or "").strip(),
                    }
                    try:
                        tool_fn.invoke(args)
                    except AmbiguousProductError as amb_err:
                        # Try to resolve via conversation history context
                        history_winner = _filter_ambiguous_by_history(
                            amb_err.matches or [], conversation_history,
                        )
                        if history_winner:
                            # Retry with the resolved product
                            retry_args = {
                                "injected_business_context": ctx,
                                "product_id": history_winner.get("id") or "",
                                "product_name": "",
                                "quantity": int(item.get("quantity") or 1),
                                "notes": (item.get("notes") or "").strip(),
                            }
                            try:
                                tool_fn.invoke(retry_args)
                            except Exception as retry_e:
                                logger.warning(
                                    "[ORDER_FLOW] multi-item: history-resolved retry failed: %s",
                                    retry_e,
                                )
                                if multi_ambiguity is None:
                                    multi_ambiguity = amb_err
                                    multi_ambiguity_query = (
                                        (item.get("product_name") or "").strip()
                                        or (amb_err.query or "").strip()
                                    )
                                    multi_ambiguity_quantity = (
                                        int(item.get("quantity") or 1)
                                    )
                                    multi_ambiguity_notes = (
                                        (item.get("notes") or "").strip() or None
                                    )
                        else:
                            if multi_ambiguity is None:
                                multi_ambiguity = amb_err
                                multi_ambiguity_query = (
                                    (item.get("product_name") or "").strip()
                                    or (amb_err.query or "").strip()
                                )
                                multi_ambiguity_quantity = (
                                    int(item.get("quantity") or 1)
                                )
                                multi_ambiguity_notes = (
                                    (item.get("notes") or "").strip() or None
                                )
                            logger.warning(
                                "[ORDER_FLOW] multi-item: ambiguous item '%s' (qty=%s) "
                                "(%d matches); continuing with remaining items",
                                item.get("product_name"),
                                item.get("quantity") or 1,
                                len(amb_err.matches or []),
                            )
                    except ProductNotFoundError as nf_err:
                        label = (item.get("product_name") or "").strip() or (nf_err.query or "")
                        if label:
                            multi_not_found.append(label)
                        logger.warning(
                            "[ORDER_FLOW] multi-item: item '%s' not found; continuing",
                            item.get("product_name"),
                        )
                    except Exception as e:
                        logger.exception("[ORDER_FLOW] add_to_cart item failed: %s", e)
            else:
                bypass_pid = _resolve_from_pending_disambiguation(
                    pending_disamb, params.get("product_name") or ""
                )
                if bypass_pid:
                    logger.warning(
                        "[ORDER_FLOW] bypass: resolved '%s' via pending_disambiguation → %s",
                        params.get("product_name"), bypass_pid,
                    )
                # Quantity / notes resolution on a bypass: prefer the
                # planner's params when explicitly > 1 (customer revised the
                # number when picking the variant — "dame 3 limonadas
                # natural"). Otherwise fall back to the original quantity
                # captured when we first asked them to disambiguate, so a
                # plain "limonada natural" reply preserves the "dos
                # limonadas" they originally asked for. Same for notes.
                planner_qty = int(params.get("quantity") or 1)
                pending_qty = int((pending_disamb or {}).get("requested_quantity") or 0)
                resolved_qty = planner_qty
                if bypass_pid and planner_qty <= 1 and pending_qty > 1:
                    resolved_qty = pending_qty
                    logger.warning(
                        "[ORDER_FLOW] bypass: preserving original quantity %d "
                        "(planner emitted %d)", pending_qty, planner_qty,
                    )
                planner_notes = (params.get("notes") or "").strip()
                pending_notes = ((pending_disamb or {}).get("requested_notes") or "").strip()
                resolved_notes = planner_notes or (pending_notes if bypass_pid else "")
                args = {
                    "injected_business_context": ctx,
                    "product_id": params.get("product_id") or bypass_pid or "",
                    "product_name": "" if bypass_pid else (params.get("product_name") or ""),
                    "quantity": resolved_qty,
                    "notes": resolved_notes,
                }
                tool_fn.invoke(args)

                # Swap completion: if the pending disambiguation was triggered
                # by a product swap (UPDATE_CART_ITEM.new_product_name), the
                # old item still needs to be removed now that the replacement
                # has been added successfully.
                replacement_pid = (pending_disamb or {}).get("pending_replacement_product_id")
                if replacement_pid and bypass_pid:
                    rm_tool = _find_tool("remove_from_cart")
                    if rm_tool:
                        try:
                            rm_tool.invoke({
                                "injected_business_context": ctx,
                                "product_id": replacement_pid,
                                "product_name": "",
                            })
                            logger.warning(
                                "[ORDER_FLOW] swap complete: removed old product_id=%s",
                                replacement_pid,
                            )
                        except Exception as e:
                            logger.exception(
                                "[ORDER_FLOW] swap removal failed: %s", e,
                            )

            cart_after = _get_cart_for_logging(wa_id, business_id)
            _log_cart_debug(wa_id, business_id, "add_to_cart", params, cart_before, cart_after)
            cart_change = _build_cart_change(cart_before, cart_after)

            # State transition on first add
            state_after = current_state
            if current_state == ORDER_STATE_GREETING and cart_change["action"] != CART_ACTION_NOOP:
                _save_session_and_invalidate(
                    wa_id, business_id,
                    {"order_context": {**order_tools._cart_from_session(wa_id, business_id), "state": ORDER_STATE_ORDERING}},
                )
                state_after = ORDER_STATE_ORDERING

            # Multi-item batch: resolve captured per-item failures.
            # Cases (precedence top → bottom):
            #   1. Nothing succeeded + ambiguity captured → raise the
            #      ambiguity so the outer handler builds a full
            #      needs_clarification result (same UX as single-item).
            #      If ``multi_not_found`` is ALSO populated, we mention
            #      it in the disamb result via the new not_found extra.
            #   2. Nothing succeeded + only not-found captured → raise
            #      a ProductNotFoundError (with a combined query) so
            #      the outer handler builds a user_error result telling
            #      the user which items weren't found.
            #   3. Some items succeeded + ambiguity captured → return a
            #      cart_change result carrying pending_clarification so
            #      the response mentions both the partial success and
            #      the open question. Pending disamb is persisted so the
            #      next turn's planner can resolve the reply. Any
            #      not_found items ride along as a sibling extra.
            #   4. Some items succeeded + only not-found captured →
            #      return a cart_change result carrying not_found so
            #      the response confirms what landed and flags what
            #      didn't.
            #   5. No failures → normal cart_change result.
            batch_failed = cart_change["action"] == CART_ACTION_NOOP
            if multi_ambiguity is not None:
                if batch_failed:
                    # Nothing to confirm — fall through to the outer
                    # handler which builds a clean needs_clarification
                    # result. (not_found items are dropped in this case
                    # because disamb is the blocking question.)
                    raise multi_ambiguity
                options = [
                    {
                        "name": m.get("name"),
                        "price": float(m.get("price") or 0),
                        "product_id": m.get("id") or m.get("product_id"),
                    }
                    for m in (multi_ambiguity.matches or [])
                ]
                _save_pending_disambiguation(
                    wa_id, business_id, multi_ambiguity_query, options,
                    requested_quantity=multi_ambiguity_quantity,
                    requested_notes=multi_ambiguity_notes,
                )
                extras: Dict[str, Any] = {
                    "cart_change": cart_change,
                    "pending_clarification": {
                        "requested_name": multi_ambiguity_query,
                        "options": options,
                    },
                }
                if multi_not_found:
                    extras["not_found"] = list(multi_not_found)
                return _base_result(
                    state_after, wa_id, business_id,
                    RESULT_KIND_CART_CHANGE,
                    **extras,
                )

            if multi_not_found:
                if batch_failed:
                    # Nothing succeeded and every failure was "not found".
                    # Raise so the outer handler turns it into a user_error
                    # result with a clear "no encontré …" message.
                    raise ProductNotFoundError(
                        query=", ".join(multi_not_found)
                    )
                return _base_result(
                    state_after, wa_id, business_id,
                    RESULT_KIND_CART_CHANGE,
                    cart_change=cart_change,
                    not_found=list(multi_not_found),
                )

            return _base_result(
                state_after, wa_id, business_id,
                RESULT_KIND_CART_CHANGE,
                cart_change=cart_change,
            )

        if intent == INTENT_ADD_PROMO_TO_CART:
            tool_fn = _find_tool("add_promo_to_cart")
            if not tool_fn:
                return _internal_error_result(current_state, wa_id, business_id, "Tool add_promo_to_cart no encontrada")
            cart_before = _get_cart_for_logging(wa_id, business_id)
            args = {
                "injected_business_context": ctx,
                "promo_id": (params.get("promo_id") or "").strip(),
                "promo_query": (params.get("promo_query") or "").strip(),
            }
            tool_msg = tool_fn.invoke(args)
            cart_after = _get_cart_for_logging(wa_id, business_id)
            _log_cart_debug(wa_id, business_id, "add_promo_to_cart", params, cart_before, cart_after)

            if tool_msg.startswith("❌"):
                # Tool refused (no match, ambiguous, schedule miss, etc.).
                # Surface as user_error so the response generator phrases it
                # for the customer instead of dumping the raw tool string.
                return _user_error_result(
                    current_state, wa_id, business_id, tool_msg.lstrip("❌").strip(),
                )

            cart_change = _build_cart_change(cart_before, cart_after)

            # First-add transition out of GREETING, mirroring INTENT_ADD_TO_CART.
            state_after = current_state
            if current_state == ORDER_STATE_GREETING and cart_change["action"] != CART_ACTION_NOOP:
                _save_session_and_invalidate(
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

            # ---- Variant swap path: user chose a different variant of an
            # existing cart item (e.g. "la soda que sea de frutos rojos").
            # Resolve the new product FIRST — if it's ambiguous, the old item
            # stays in the cart untouched and we show disambiguation with the
            # old product_id stashed as `pending_replacement_product_id` so
            # the next-turn bypass can complete the swap atomically.
            new_product_name = (params.get("new_product_name") or "").strip()
            if new_product_name:
                old_name = (params.get("product_name") or "").strip()
                old_pid = _resolve_product_id_by_name(wa_id, business_id, old_name) if old_name else None
                if not old_pid:
                    return _user_error_result(
                        current_state, wa_id, business_id,
                        f"No encontré '{old_name or 'ese producto'}' en tu pedido para reemplazar.",
                    )

                add_tool = _find_tool("add_to_cart")
                if not add_tool:
                    return _internal_error_result(current_state, wa_id, business_id, "Tool add_to_cart no encontrada")
                try:
                    add_tool.invoke({
                        "injected_business_context": ctx,
                        "product_id": "",
                        "product_name": new_product_name,
                        "quantity": 1,
                    })
                except AmbiguousProductError as amb_e:
                    return _disambig_result(
                        current_state, wa_id, business_id,
                        new_product_name, list(getattr(amb_e, "matches", []) or []),
                        pending_replacement_product_id=old_pid,
                    )

                rm_tool = _find_tool("remove_from_cart")
                if rm_tool:
                    try:
                        rm_tool.invoke({
                            "injected_business_context": ctx,
                            "product_id": old_pid,
                            "product_name": "",
                        })
                    except Exception as e:
                        logger.exception("[ORDER_FLOW] swap removal failed: %s", e)

                cart_after = _get_cart_for_logging(wa_id, business_id)
                _log_cart_debug(wa_id, business_id, "update_cart_item_swap", params, cart_before, cart_after)
                cart_change = _build_cart_change(cart_before, cart_after)
                return _base_result(
                    current_state, wa_id, business_id,
                    RESULT_KIND_CART_CHANGE,
                    cart_change=cart_change,
                )

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

        if intent == INTENT_ABANDON_CART:
            cart_before = _get_cart_for_logging(wa_id, business_id)
            had_items = bool((cart_before.get("items") or []))
            _save_session_and_invalidate(
                wa_id, business_id,
                {"order_context": {
                    "items": [],
                    "total": 0,
                    "delivery_info": {},
                    "state": ORDER_STATE_GREETING,
                    "pending_disambiguation": None,
                }},
            )
            logger.warning(
                "[ORDER_FLOW] cart abandoned: state=%s -> %s had_items=%s",
                current_state, ORDER_STATE_GREETING, had_items,
            )
            return _base_result(
                ORDER_STATE_GREETING, wa_id, business_id,
                RESULT_KIND_CART_ABANDONED,
                had_items=had_items,
            )

        # --- delivery / place order ---

        if intent == INTENT_SUBMIT_DELIVERY_INFO:
            tool_fn = _find_tool("submit_delivery_info")
            if not tool_fn:
                return _internal_error_result(current_state, wa_id, business_id, "Tool submit_delivery_info no encontrada")
            phone_param = (params.get("phone") or "").strip()
            if phone_param == "<SENDER>":
                phone_param = _format_phone_from_wa_id(wa_id)

            # Normalize payment_method against the business's allowed list.
            # If the user/planner proposed something not configured (e.g. Nequi
            # when only Efectivo + Transferencia are allowed), strip it before
            # persisting and surface the rejected input to the response
            # generator so it can tell the customer what we accept.
            allowed_methods = _get_allowed_payment_methods(business_context)
            raw_payment_param = (params.get("payment_method") or "").strip()
            payment_param_canonical = _normalize_payment_method(
                raw_payment_param, allowed_methods
            ) if raw_payment_param else ""
            payment_rejected_input: Optional[str] = None
            if raw_payment_param and payment_param_canonical is None:
                payment_rejected_input = raw_payment_param
                payment_param_canonical = ""

            tool_fn.invoke({
                "injected_business_context": ctx,
                "address": params.get("address") or "",
                "payment_method": payment_param_canonical or "",
                "phone": phone_param,
                "name": params.get("name") or "",
            })
            delivery_status = _build_delivery_status(wa_id, business_id, business_context)
            if payment_rejected_input:
                delivery_status["payment_rejected_input"] = payment_rejected_input

            state_after = current_state
            cart_now = order_tools._cart_from_session(wa_id, business_id)
            cart_has_items = bool((cart_now.get("items") or []))
            if delivery_status["all_present"] and cart_has_items:
                _save_session_and_invalidate(
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
            elif cart_has_items:
                # Cart has items but data is partial → standard checkout flow.
                _save_session_and_invalidate(
                    wa_id, business_id,
                    {"order_context": {**cart_now, "state": ORDER_STATE_COLLECTING_DELIVERY}},
                )
                state_after = ORDER_STATE_COLLECTING_DELIVERY
            else:
                # Cart is empty — multi-intent capture path. The customer
                # provided delivery data in the same turn as a product
                # mention, but the ADD_TO_CART intent hasn't run yet (or
                # was rejected). Persist the data silently and stay in
                # whatever state we were in. The cart-mutating intent in
                # the same turn (or a future turn) advances state when
                # appropriate. Forcing COLLECTING_DELIVERY here would
                # confuse the response generator into asking about
                # delivery for an empty cart.
                logger.warning(
                    "[ORDER_FLOW] SUBMIT_DELIVERY_INFO with empty cart — "
                    "persisting profile, state stays %s", current_state,
                )
                state_after = current_state

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
                    delivery_status = _build_delivery_status(wa_id, business_id, business_context)
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

            # Re-run the matcher on the placed cart to surface promo
            # info to the response generator. create_order ran the same
            # math against the DB; we duplicate the (cheap) read here so
            # the receipt can show "Promo: -$X" without a DB roundtrip.
            # Use cart_before's raw items (with product_id + bindings),
            # not items_snapshot which strips them for display.
            promo_subtotal = subtotal_snapshot
            promo_discount = 0
            applied_promos: List[str] = []
            try:
                pricing = promotion_service.match_and_apply(
                    business_id=business_id,
                    cart_items=[
                        {
                            "product_id": it.get("product_id"),
                            "quantity": int(it.get("quantity") or 0),
                            "unit_price": float(it.get("price") or 0),
                            "promotion_id": it.get("promotion_id"),
                            "promo_group_id": it.get("promo_group_id"),
                        }
                        for it in (cart_before.get("items") or [])
                    ],
                )
                promo_subtotal = int(pricing.get("subtotal_after_promos") or subtotal_snapshot)
                promo_discount = int(pricing.get("promo_discount_total") or 0)
                applied_promos = [a["promotion_name"] for a in (pricing.get("applications") or [])]
            except Exception as exc:
                logger.warning("[ORDER_FLOW] promo display recompute failed: %s", exc)

            return _base_result(
                ORDER_STATE_GREETING, wa_id, business_id,
                RESULT_KIND_ORDER_PLACED,
                order_placed={
                    "order_id_display": order_id_display or None,
                    "items": items_snapshot,
                    "subtotal": promo_subtotal,
                    "promo_discount": promo_discount,
                    "applied_promos": applied_promos,
                    "delivery_fee": delivery_fee,
                    "total": promo_subtotal + delivery_fee,
                },
            )

        # Unknown intent
        return _user_error_result(current_state, wa_id, business_id, f"Intent {intent} no soportado")

    except AmbiguousProductError as e:
        # Try to resolve via conversation history context before asking user
        history_winner = _filter_ambiguous_by_history(
            e.matches or [], conversation_history,
        )
        if history_winner and intent == INTENT_ADD_TO_CART:
            # Retry the add with the resolved product
            try:
                tool_fn = _find_tool("add_to_cart")
                if tool_fn:
                    retry_args = {
                        "injected_business_context": ctx,
                        "product_id": history_winner.get("id") or "",
                        "product_name": "",
                        "quantity": int(params.get("quantity") or 1),
                        "notes": (params.get("notes") or "").strip(),
                    }
                    tool_fn.invoke(retry_args)
                    # Success — return a normal cart_change result
                    cart_after = _get_cart_for_logging(wa_id, business_id)
                    return _base_result(
                        ORDER_STATE_ORDERING, wa_id, business_id,
                        RESULT_KIND_CART_CHANGE,
                        cart_action=CART_ACTION_ADDED,
                        cart_items=cart_after.get("items") or [],
                        cart_total=cart_after.get("total") or 0,
                    )
            except Exception as retry_e:
                logger.warning(
                    "[ORDER_FLOW] history-resolved retry failed: %s", retry_e,
                )

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
    except ProductNotFoundError as nf_e:
        # Single-item (or all-not-found multi-item re-raise) path:
        # render as a user_error with a clear "no encontré X" message.
        # The response generator's USER_ERROR branch picks this up and
        # invites the customer to try another product or see the menu.
        missing = (nf_e.query or "").strip() or "ese producto"
        logger.warning("[ORDER_FLOW] product not found: %r", missing)
        return _user_error_result(
            current_state, wa_id, business_id,
            f"No encontré '{missing}' en el menú. ¿Quieres ver las categorías o intentar con otro nombre?",
        )
    except Exception as e:
        logger.exception("[ORDER_FLOW] Intent %s failed: %s", intent, e)
        return _internal_error_result(current_state, wa_id, business_id, str(e))
