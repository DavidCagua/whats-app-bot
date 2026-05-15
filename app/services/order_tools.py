"""
Order tools for the Order agent.
Browse products, manage cart, place orders.
"""

import contextvars
import logging
from typing import Annotated, Dict, List, Optional
from langchain.tools import tool
from langchain_core.tools import InjectedToolArg

import uuid


# Per-turn context var for the tool-calling agent. Set by the agent
# before invoking any tool; read by _get_context() when the legacy
# ``injected_business_context`` kwarg isn't passed. Lets the same tool
# functions serve both the legacy executor (which passes context
# explicitly) and the new tool-calling agent (which sets it via
# context var so the model's tool schema stays clean).
_tool_business_context: contextvars.ContextVar[Optional[Dict]] = contextvars.ContextVar(
    "_tool_business_context", default=None,
)

from ..database.product_order_service import (
    product_order_service,
    AmbiguousProductError,
    ProductNotFoundError,
)
from ..database.session_state_service import session_state_service
from ..database.customer_service import customer_service
from .order_eta import NOMINAL_RANGE_TEXT, PICKUP_RANGE_TEXT, resolve_delivery_eta
from . import catalog_cache
from . import promotion_service


def _turn_cache():
    """
    Lazy import of the per-turn cache. ``order_tools`` is loaded early
    by the agent module; a top-level ``from ..orchestration import
    turn_cache`` would trigger the orchestration package __init__ before
    it's ready. Defer the import to first call; Python caches the module
    so it's effectively free.
    """
    from ..orchestration import turn_cache as tc
    return tc.current()

logger = logging.getLogger(__name__)


def _format_price(price: float, currency: str = "COP") -> str:
    """Format price for display."""
    return f"${int(price):,}".replace(",", ".")


# Used by the add_promo_to_cart miss path to name upcoming-promo days
# in Spanish ("X aplica el viernes"). Mirrors the dict in
# customer_service_flow but duplicated here to avoid a cross-module
# private-name import.
_DAY_NAMES_ES = {
    1: "lunes", 2: "martes", 3: "miércoles", 4: "jueves",
    5: "viernes", 6: "sábado", 7: "domingo",
}


def _product_availability(product: Dict) -> str:
    """
    Classify a product row by availability. Returns one of:
      - "available"   — sellable directly (default)
      - "promo_only"  — exists but only via a promo bundle
      - "inactive"    — currently disabled by the operator
    """
    if not product:
        return "available"
    if product.get("is_active") is False:
        return "inactive"
    if product.get("promo_only") is True:
        return "promo_only"
    return "available"


def _search_listing_marker(product: Dict) -> str:
    """
    Compact suffix used when listing multiple search hits. Kept short so
    a search result of 5 items stays readable.
    """
    state = _product_availability(product)
    if state == "promo_only":
        return " (solo en promo)"
    if state == "inactive":
        return " (no disponible por ahora)"
    return ""


def _detail_availability_note(
    product: Dict,
    business_id: str,
    timezone_name: Optional[str],
) -> str:
    """
    Longer explanatory marker used by get_product_details (single
    product view). For promo_only products it names the active promo
    (or the upcoming day) so the customer knows the path to obtain it.

    Returns empty string when the product is fully available.
    """
    state = _product_availability(product)
    if state == "available":
        return ""
    if state == "inactive":
        return "\n\nℹ️ Este producto no está disponible por ahora."
    # promo_only: look up the containing promo(s).
    try:
        buckets = promotion_service.find_promos_containing_product(
            business_id=business_id,
            product_id=str(product.get("id") or ""),
            timezone_name=timezone_name,
        )
    except Exception as exc:
        logger.warning(
            "[ORDER_TOOL] find_promos_containing_product failed for %s: %s",
            product.get("id"), exc,
        )
        return "\n\nℹ️ Este producto solo se vende como parte de una promo."
    active = buckets.get("active") or []
    upcoming = buckets.get("upcoming") or []
    if active:
        p = active[0]
        name = p.get("name") or "una promo"
        if p.get("fixed_price") is not None:
            price = _format_price(p["fixed_price"])
            return f"\n\nℹ️ Solo se vende como parte de la promo *{name}* ({price})."
        return f"\n\nℹ️ Solo se vende como parte de la promo *{name}*."
    if upcoming:
        p = upcoming[0]
        name = p.get("name") or "una promo"
        day = _DAY_NAMES_ES.get(int(p.get("next_active_day") or 0))
        if day:
            return (
                f"\n\nℹ️ Solo se vende como parte de la promo *{name}*, "
                f"que aplica el {day}."
            )
        return f"\n\nℹ️ Solo se vende como parte de la promo *{name}*."
    return (
        "\n\nℹ️ Este producto solo se vende en promos; "
        "por ahora ninguna está activa."
    )


def _format_unavailable_for_cart(
    product: Dict,
    business_id: str,
    timezone_name: Optional[str],
) -> str:
    """
    Compose the add_to_cart refusal message for an unavailable product.
    Mirrors `_detail_availability_note` but framed as a refusal +
    redirect rather than a description suffix.
    """
    name = product.get("name") or "Ese producto"
    state = _product_availability(product)
    if state == "inactive":
        return f"❌ *{name}* no está disponible por ahora."
    # promo_only
    try:
        buckets = promotion_service.find_promos_containing_product(
            business_id=business_id,
            product_id=str(product.get("id") or ""),
            timezone_name=timezone_name,
        )
    except Exception as exc:
        logger.warning(
            "[ORDER_TOOL] find_promos_containing_product failed for %s: %s",
            product.get("id"), exc,
        )
        return (
            f"❌ *{name}* solo se vende como parte de una promo. "
            "¿Quieres ver las promos disponibles?"
        )
    active = buckets.get("active") or []
    upcoming = buckets.get("upcoming") or []
    if active:
        p = active[0]
        promo_name = p.get("name") or "una promo"
        if p.get("fixed_price") is not None:
            price = _format_price(p["fixed_price"])
            return (
                f"❌ *{name}* solo se vende como parte de la promo "
                f"*{promo_name}* ({price}). ¿Te interesa la promo?"
            )
        return (
            f"❌ *{name}* solo se vende como parte de la promo "
            f"*{promo_name}*. ¿Te interesa la promo?"
        )
    if upcoming:
        p = upcoming[0]
        promo_name = p.get("name") or "una promo"
        day = _DAY_NAMES_ES.get(int(p.get("next_active_day") or 0))
        if day:
            return (
                f"❌ *{name}* solo va en promos. La próxima con ese "
                f"producto es *{promo_name}*, aplica el {day}."
            )
        return f"❌ *{name}* solo va en promos. La próxima es *{promo_name}*."
    return (
        f"❌ *{name}* solo se vende en promos y por ahora ninguna "
        "está activa con ese producto."
    )


def _format_promo_miss_message(
    business_id: str,
    query: str,
    timezone_name: Optional[str],
) -> str:
    """
    Compose the no-match reply for ``add_promo_to_cart``. Surfaces the
    business's current promos (and upcoming this week) so the customer
    learns what IS available in one turn instead of having to ask again.

    Three shapes by availability:
      - active promos exist → list them and invite the customer to pick.
      - only upcoming this week → name them with the day they apply.
      - nothing at all → offer the menu instead.
    """
    try:
        buckets = promotion_service.list_promos_for_listing(
            business_id, timezone_name=timezone_name,
        )
    except Exception as exc:
        logger.warning(
            "[ORDER_TOOL] list_promos_for_listing failed in promo-miss path: %s", exc,
        )
        return f"❌ No encontré una promo activa que coincida con '{query}'."

    active = buckets.get("active_now") or []
    upcoming = buckets.get("upcoming") or []

    # First branch: the customer specifically named an UPCOMING promo
    # (substring match on the promo's name). Honor that — saying
    # "no encontré" while listing other active promos is misleading
    # because the requested promo exists, just not today. Production
    # 2026-05-11 / Biela: query "Misuri" on Monday was answered with the
    # Oregon list, hiding that Misuri applies on Wednesday.
    q_lower = (query or "").lower().strip()
    if q_lower and upcoming:
        for p in upcoming:
            name = (p.get("name") or "").lower()
            if not name:
                continue
            if q_lower in name or any(
                tok in name for tok in q_lower.split() if len(tok) >= 4
            ):
                day = _DAY_NAMES_ES.get(int(p.get("next_active_day") or 0))
                promo_name = p.get("name") or "esa promo"
                if day:
                    return (
                        f"❌ La promo *{promo_name}* aplica los {day}, hoy no. "
                        "¿Quieres ver las promos disponibles hoy?"
                    )
                return (
                    f"❌ La promo *{promo_name}* no aplica hoy. "
                    "¿Quieres ver las disponibles?"
                )

    def _line(p: Dict, idx: int) -> str:
        name = p.get("name") or ""
        if p.get("fixed_price") is not None:
            return f"{idx}. {name} — {_format_price(p['fixed_price'])}"
        if p.get("discount_amount") is not None:
            return f"{idx}. {name} — descuento {_format_price(p['discount_amount'])}"
        if p.get("discount_pct") is not None:
            return f"{idx}. {name} — {int(p['discount_pct'])}% off"
        return f"{idx}. {name}"

    if active:
        lines = "\n".join(_line(p, i) for i, p in enumerate(active, start=1))
        return (
            f"❌ No encontré una promo de '{query}'. Hoy tenemos:\n"
            f"{lines}\n\n¿Te interesa alguna?"
        )

    if upcoming:
        parts: List[str] = []
        for p in upcoming[:5]:
            name = p.get("name") or ""
            day = _DAY_NAMES_ES.get(int(p.get("next_active_day") or 0))
            parts.append(f"{name} ({day})" if day else name)
        return (
            f"❌ No tenemos una promo de '{query}' activa hoy. "
            f"Esta semana viene: {', '.join(parts)}."
        )

    return (
        f"❌ No encontré una promo de '{query}'. Por ahora no tenemos "
        "promos activas. ¿Te ayudo con el menú?"
    )

# Terms that suggest the user is asking by ingredient (include description in search result for LLM).
_INGREDIENT_LIKE_WORDS = frozenset({
    "queso", "pollo", "carne", "tocineta", "cebolla", "salsa", "jamón", "jamón",
    "pepinillo", "tomate", "lechuga", "aguacate", "guacamole", "champiñón",
    "jalapeño", "chipotle", "bbq", "mostaza", "mayonesa", "crema", "hongo",
    "azul", "cheddar", "mozzarella", "parmesano", "pastor", "costilla",
    "con", "algo", "alguna", "algún", "tienen", "tienes", "con",
})

# Max length for description snippet when including in search result (ingredient-like queries).
_SEARCH_DESC_SNIPPET_LEN = 140


def _is_ingredient_like_query(query: str) -> bool:
    """True if query looks like an ingredient or multi-item request (include description in result)."""
    if not query or not query.strip():
        return False
    q = query.strip().lower()
    words = set(w for w in q.split() if len(w) > 1)
    if len(words) >= 2:
        return True
    return bool(words & _INGREDIENT_LIKE_WORDS)


def _get_context(injected_business_context: Optional[Dict]) -> tuple:
    """
    Extract business_id and wa_id from injected context.

    Falls back to the context var when the kwarg isn't provided (the
    tool-calling agent path — see ``_tool_business_context``). Legacy
    executor callers still pass the kwarg explicitly so behaviour is
    unchanged for them.
    """
    ctx = injected_business_context or _tool_business_context.get() or {}
    business_id = ctx.get("business_id") or ""
    wa_id = ctx.get("wa_id") or ""
    return business_id, wa_id


def set_tool_context(business_context: Dict) -> contextvars.Token:
    """
    Set the per-turn business context for tools that read it via the
    context var. Returns a token the caller can pass to
    ``reset_tool_context`` after the turn completes.

    Used by the tool-calling agent to make wa_id / business_id available
    to tools without exposing them in the model's tool schema.
    """
    return _tool_business_context.set(business_context)


def reset_tool_context(token: contextvars.Token) -> None:
    """Restore the previous context-var value. Call after the turn ends."""
    _tool_business_context.reset(token)


def _products_enabled(ctx: Optional[Dict]) -> bool:
    """Check if products/orders are enabled for the business."""
    ctx = ctx or _tool_business_context.get()
    if not ctx:
        return True
    settings = (ctx.get("business") or {}).get("settings") or {}
    return settings.get("products_enabled", True)


def _get_delivery_fee(ctx: Optional[Dict]) -> float:
    """Get delivery fee from business settings. Falls back to the same
    default the customer service info lookup uses (so receipts and the
    'cuánto cobran de domicilio' answer agree on the same number)."""
    from .business_info_service import DELIVERY_FEE_DEFAULT
    ctx = ctx or _tool_business_context.get()
    if not ctx:
        return float(DELIVERY_FEE_DEFAULT)
    settings = (ctx.get("business") or {}).get("settings") or {}
    return float(settings.get("delivery_fee", DELIVERY_FEE_DEFAULT))


def _allowed_payment_methods(injected_business_context: Optional[Dict]) -> List[str]:
    """Read the business's configured payment-method allowlist.

    Returns ``[]`` when the business has no list configured — caller
    treats that as "no enforcement" (accept any string verbatim).
    """
    ctx = injected_business_context or _tool_business_context.get() or {}
    settings = (ctx.get("business") or {}).get("settings") or {}
    raw = settings.get("payment_methods") or []
    if not isinstance(raw, list):
        return []
    return [str(m).strip() for m in raw if str(m).strip()]


def _match_payment_method(value: str, allowed: List[str]) -> Optional[str]:
    """Fuzzy-match ``value`` against the allowed list.

    Case-insensitive, substring-tolerant — handles fragments,
    abbreviations, and stray casing. Returns the canonical entry from
    ``allowed`` on a hit, ``None`` on no match. Empty ``allowed``
    means "no enforcement" → returns ``None`` so the caller falls
    back to the raw value.
    """
    if not value or not allowed:
        return None
    v = value.strip().lower()
    if not v:
        return None
    for canonical in allowed:
        c = canonical.strip().lower()
        if not c:
            continue
        if v == c or v in c or c in v:
            return canonical
    return None


def _read_fulfillment_type(wa_id: str, business_id: str) -> str:
    """Read ``order_context.fulfillment_type`` from the session.

    Returns ``'delivery'`` when unset (the historical default) or
    ``'pickup'`` when the agent recorded an explicit pickup signal.
    Centralized so place_order, the renderer's confirm-text path, and
    the CTA card all read the same source of truth.
    """
    try:
        from ..database.session_state_service import session_state_service
        result = session_state_service.load(wa_id, business_id) or {}
        oc = (result.get("session") or {}).get("order_context") or {}
        ftype = (oc.get("fulfillment_type") or "delivery").strip().lower()
        return ftype if ftype in ("delivery", "pickup") else "delivery"
    except Exception:
        return "delivery"


def _read_awaiting_confirmation(wa_id: str, business_id: str) -> bool:
    """Read the ``order_context.awaiting_confirmation`` flag from session.

    Set by the v2 agent in the turn that dispatches ``ready_to_confirm``.
    Cleared once ``place_order`` succeeds. Acts as the state-machine
    interlock that prevents the agent from skipping the confirmation
    prompt and going straight to place_order.
    """
    try:
        from ..database.session_state_service import session_state_service
        result = session_state_service.load(wa_id, business_id) or {}
        oc = (result.get("session") or {}).get("order_context") or {}
        return bool(oc.get("awaiting_confirmation"))
    except Exception:
        return False


def set_awaiting_confirmation(wa_id: str, business_id: str, value: bool) -> None:
    """Persist the awaiting_confirmation flag onto the session order_context.

    Used by the v2 agent: ``True`` after a successful ``ready_to_confirm``
    dispatch (CTA or text), ``False`` after place_order succeeds (also
    cleared inside ``place_order`` itself when it resets the cart).
    """
    try:
        from ..database.session_state_service import session_state_service
        result = session_state_service.load(wa_id, business_id) or {}
        oc = (result.get("session") or {}).get("order_context") or {}
        oc = {**oc, "awaiting_confirmation": bool(value)}
        session_state_service.save(wa_id, business_id, {"order_context": oc})
        # Drop the per-turn cache so the next read sees the new flag.
        try:
            _turn_cache().invalidate_session(wa_id, business_id)
        except Exception:
            pass
    except Exception as exc:
        logger.warning("[ORDER_TOOL] set_awaiting_confirmation failed: %s", exc)


def _cart_from_session(wa_id: str, business_id: str) -> Dict:
    """
    Load order_context (cart + delivery_info + state) from session.
    In-progress cart lives only in session; no separate DB cart.
    """
    if not wa_id or not business_id:
        return {"items": [], "total": 0, "delivery_info": None, "state": None}
    # Pass an explicit loader that uses this module's (patchable)
    # session_state_service reference so unit tests that
    # ``patch("app.services.order_tools.session_state_service", ...)``
    # still intercept the DB hit.
    result = _turn_cache().get_session(
        wa_id,
        business_id,
        loader=lambda: session_state_service.load(wa_id, business_id),
    )
    order_context = result.get("session", {}).get("order_context") or {}
    items = order_context.get("items") or []
    total = order_context.get("total") or 0
    delivery_info = order_context.get("delivery_info")
    state = order_context.get("state")
    raw_ftype = (order_context.get("fulfillment_type") or "").strip().lower()
    fulfillment_type = raw_ftype if raw_ftype in ("delivery", "pickup") else "delivery"
    notes = (order_context.get("notes") or "").strip()
    return {
        "items": items,
        "total": total,
        "delivery_info": delivery_info,
        "state": state,
        "fulfillment_type": fulfillment_type,
        "notes": notes,
    }


def _save_cart(wa_id: str, business_id: str, cart: Dict) -> None:
    """
    Save cart to session order_context. State is always derived from
    the merged cart contents (items + delivery_info + fulfillment_type).

    Single source of truth: state is a function of cart contents, not
    an independent field. Every mutation re-derives it via
    ``_compute_order_state`` so it cannot drift away from what the cart
    actually holds. Any ``state`` value the caller passes in is
    overwritten — there is no longer a legacy executor with its own
    state machine to defer to.
    """
    if not wa_id or not business_id:
        return
    existing = _cart_from_session(wa_id, business_id)
    merged = {**existing, **cart}
    merged["state"] = _compute_order_state(
        merged.get("items") or [],
        merged.get("delivery_info") or {},
        merged.get("fulfillment_type") or "delivery",
    )
    session_state_service.save(wa_id, business_id, {"order_context": merged})
    # Drop the per-turn cached session so the next _cart_from_session in
    # this turn refetches and sees the merged state.
    _turn_cache().invalidate_session(wa_id, business_id)


def _compute_order_state(
    items: List[Dict],
    delivery_info: Dict,
    fulfillment_type: str = "delivery",
) -> str:
    """Map cart contents → order state (the v2 derivation).

    - empty cart                                          → GREETING
    - items + complete delivery info (delivery mode)      → READY_TO_PLACE
    - items + name (pickup mode)                          → READY_TO_PLACE
    - items + any other delivery state                    → ORDERING

    Pickup mode short-circuits the address/phone/payment requirement —
    the customer is paying at the register and the kitchen has the
    WhatsApp ID, so name is the only field we still need.

    COLLECTING_DELIVERY is not derivable from contents — it's a
    legacy-executor explicit-transition state ("user just hit
    checkout"). v2 doesn't need it because the agent reads delivery
    completeness directly via ``get_customer_info``.
    """
    from ..database.session_state_service import (
        ORDER_STATE_GREETING,
        ORDER_STATE_ORDERING,
        ORDER_STATE_READY_TO_PLACE,
    )
    if not items:
        return ORDER_STATE_GREETING
    name = (delivery_info.get("name") or "").strip()
    if (fulfillment_type or "delivery").strip().lower() == "pickup":
        return ORDER_STATE_READY_TO_PLACE if name else ORDER_STATE_ORDERING
    address = (delivery_info.get("address") or "").strip()
    phone = (delivery_info.get("phone") or "").strip()
    payment = (delivery_info.get("payment_method") or "").strip()
    if name and address and phone and payment:
        return ORDER_STATE_READY_TO_PLACE
    return ORDER_STATE_ORDERING


@tool
def get_menu_categories(injected_business_context: Annotated[dict, InjectedToolArg]) -> str:
    """
    Show menu categories. Use when the user asks what is on the menu, what categories exist,
    or what they can order in general (e.g. "qué tienes", "qué hay en el menú").
    """
    logger.info("[ORDER_TOOL] get_menu_categories")
    try:
        business_id, _ = _get_context(injected_business_context)
        if not _products_enabled(injected_business_context):
            return "❌ Los pedidos de productos no están habilitados en este momento."
        if not business_id:
            return "❌ No se pudo identificar el negocio. Intenta de nuevo."

        categories = catalog_cache.list_categories(business_id)
        if not categories:
            # No categories set — fall back to listing all products directly
            all_products = catalog_cache.list_products(business_id)
            if not all_products:
                return "No hay productos disponibles en el menú por ahora."
            lines = []
            for p in all_products:
                price_str = _format_price(p.get("price", 0), p.get("currency", "COP"))
                lines.append(f"• {p['name']} - {price_str}")
            return "Productos disponibles:\n\n" + "\n".join(lines)
        return "Categorías del menú: " + ", ".join(categories) + ". Pregunta por una categoría para ver los productos (ej. qué tienes de bebidas)."
    except Exception as e:
        logger.error(f"[ORDER_TOOL] get_menu_categories error: {e}")
        return f"❌ Error al listar categorías: {str(e)}"


@tool
def list_category_products(category: str = "", *, injected_business_context: Annotated[dict, InjectedToolArg]) -> str:
    """
    List items in a category. Use when the user asks what you have in a category
    (e.g. drinks, bebidas, hamburguesas). Pass category (e.g. BEBIDAS, HAMBURGUESAS).
    Leave category empty to list the full menu.

    Args:
        category: Category filter (e.g. BEBIDAS, HAMBURGUESAS, SALCHIPAPAS). Empty = full menu.
    """
    logger.info(f"[ORDER_TOOL] list_category_products category='{category}'")
    try:
        business_id, _ = _get_context(injected_business_context)
        if not _products_enabled(injected_business_context):
            return "❌ Los pedidos de productos no están habilitados en este momento."
        if not business_id:
            return "❌ No se pudo identificar el negocio. Intenta de nuevo."

        products = catalog_cache.list_products_with_fallback(
            business_id=business_id,
            category=category.strip() if category else "",
        )

        if not products:
            cat_msg = f" en la categoría {category}" if category and category.strip() else ""
            return f"❌ No hay productos disponibles{cat_msg}."

        lines = []
        for p in products:
            price_str = _format_price(p.get("price", 0), p.get("currency", "COP"))
            cat = p.get("category") or ""
            lines.append(f"• {p['name']} - {price_str}" + (f" ({cat})" if cat else ""))

        header = f"Menú{f' - {category}' if category and category.strip() else ''}:\n\n"
        return header + "\n".join(lines)
    except Exception as e:
        logger.error(f"[ORDER_TOOL] list_category_products error: {e}")
        return f"❌ Error al listar productos: {str(e)}"


@tool
def search_products(query: str, *, injected_business_context: Annotated[dict, InjectedToolArg]) -> str:
    """
    Find a specific product by name or ingredients. Use when the user names a product or
    ingredient (e.g. barracuda, coca cola, queso azul). Not for "what do you have in category X"
    — use list_category_products for that.

    Args:
        query: Search term - product name or ingredient/description
    """
    logger.info(f"[ORDER_TOOL] search_products query='{query}'")
    try:
        business_id, _ = _get_context(injected_business_context)
        if not _products_enabled(injected_business_context):
            return "❌ Los pedidos no están habilitados."
        if not business_id:
            return "❌ No se pudo identificar el negocio."

        if not query or not query.strip():
            return "❌ Indica el término de búsqueda."

        # include_unavailable=True so info questions still find promo_only
        # and inactive products. Each line gets a short marker the LLM
        # can echo so the customer learns the restriction. add_to_cart
        # is the layer that still refuses to add unavailable items.
        products = product_order_service.search_products(
            business_id=business_id, query=query.strip(),
            include_unavailable=True,
        )

        if not products:
            return f"❌ No hay productos que coincidan con '{query}'."

        include_desc = _is_ingredient_like_query(query)
        lines = []
        for p in products:
            price_str = _format_price(p.get("price", 0), p.get("currency", "COP"))
            line = f"• {p['name']} - {price_str}{_search_listing_marker(p)}"
            if include_desc:
                desc = (p.get("description") or "").strip()
                if desc:
                    snippet = desc if len(desc) <= _SEARCH_DESC_SNIPPET_LEN else desc[:_SEARCH_DESC_SNIPPET_LEN].rsplit(" ", 1)[0] + "…"
                    line += f"\n  {snippet}"
            lines.append(line)

        header = f"Productos que coinciden con '{query}':\n\n"
        return header + "\n".join(lines)
    except Exception as e:
        logger.error(f"[ORDER_TOOL] search_products error: {e}")
        return f"❌ Error al buscar: {str(e)}"


@tool
def get_product_details(product_id: str = "", product_name: str = "", *, injected_business_context: Annotated[dict, InjectedToolArg]) -> str:
    """
    Get details of one product (name, price, description/ingredients). Use when the user
    asks what a product contains or what it is (e.g. qué trae la barracuda).

    Args:
        product_id: Product UUID (preferred when known)
        product_name: Product name, ingredients, or partial description
    """
    logger.info(f"[ORDER_TOOL] get_product_details product_id='{product_id}' product_name='{product_name}'")
    try:
        business_id, _ = _get_context(injected_business_context)
        if not _products_enabled(injected_business_context):
            return "❌ Los pedidos de productos no están habilitados en este momento."
        if not business_id:
            return "❌ No se pudo identificar el negocio. Intenta de nuevo."

        if not product_id and not (product_name and product_name.strip()):
            return "❌ Indica el nombre o ID del producto que deseas."

        # include_unavailable=True so ingredient / "what's in X" questions
        # still answer for promo_only and inactive products. The
        # availability note below explains the path to obtain the product
        # (or that it's not available right now). add_to_cart refuses.
        product = product_order_service.get_product(
            product_id=product_id.strip() if product_id else None,
            product_name=product_name.strip() if product_name else None,
            business_id=business_id,
            include_unavailable=True,
        )

        if not product:
            return "❌ Producto no encontrado. Usa list_category_products para ver el menú."

        price_str = _format_price(product.get("price", 0), product.get("currency", "COP"))
        desc = product.get("description") or ""
        tz_name = promotion_service.timezone_from_business_context(injected_business_context)
        avail_note = _detail_availability_note(product, business_id, tz_name)
        body = f"**{product['name']}** - {price_str}\n" + (f"{desc}" if desc else "")
        return body + avail_note
    except AmbiguousProductError:
        raise
    except Exception as e:
        logger.error(f"[ORDER_TOOL] get_product_details error: {e}")
        return f"❌ Error al buscar producto: {str(e)}"


@tool
def add_to_cart(product_id: str = "", product_name: str = "", quantity: int = 1, notes: str = "", *, injected_business_context: Annotated[dict, InjectedToolArg]) -> str:
    """
    Add a product to the cart. product_name supports flexible lookup by name or ingredients
    (e.g. "barracuda", "hamburguesa con queso azul", "coca zero").

    Args:
        product_id: Product UUID (preferred when known)
        product_name: Product name or description (flexible search)
        quantity: Quantity to add (default 1)
        notes: Special instructions for the item (e.g. "sin cebolla", "sin morcilla", "extra salsa")
    """
    logger.info(f"[ORDER_TOOL] add_to_cart product_id='{product_id}' product_name='{product_name}' quantity={quantity} notes='{notes}'")
    try:
        business_id, wa_id = _get_context(injected_business_context)
        if not _products_enabled(injected_business_context):
            return "❌ Los pedidos de productos no están habilitados en este momento."
        if not business_id or not wa_id:
            return "❌ No se pudo identificar la sesión. Intenta de nuevo."

        if quantity < 1:
            return "❌ La cantidad debe ser al menos 1."

        # include_unavailable=True so we can return a precise refusal
        # ("only in promo X" / "currently unavailable") instead of the
        # generic ProductNotFoundError. The flags get checked right
        # after — unavailable products never reach the cart write.
        product = None
        if product_id:
            product = product_order_service.get_product(
                product_id=product_id, business_id=business_id,
                include_unavailable=True,
            )
        elif product_name and product_name.strip():
            product = product_order_service.get_product(
                product_name=product_name, business_id=business_id,
                include_unavailable=True,
            )

        if not product:
            # Raise instead of returning a string so the multi-item
            # executor loop can distinguish "this item failed because
            # nothing matched" from "this item succeeded" and from
            # "this item was ambiguous". The old string return was
            # swallowed silently in the multi-item path, dropping
            # unmatchable items without telling the user.
            raise ProductNotFoundError(query=(product_name or product_id or "").strip())

        # Block promo_only / inactive products from direct add. We found
        # the product so we can give a specific reason instead of a
        # generic not-found.
        if _product_availability(product) != "available":
            tz_name = promotion_service.timezone_from_business_context(injected_business_context)
            return _format_unavailable_for_cart(product, business_id, tz_name)

        price = float(product.get("price", 0))
        pid = product["id"]
        name = product["name"]
        notes = (notes or "").strip()

        # Search may have attached a derived flavor/qualifier for generic
        # products (e.g. "jugo de mora en leche" → product "Jugos en leche"
        # + derived_notes "mora"). Fold the derived note into the item's
        # notes so the human at the restaurant sees the user's flavor
        # request on the ticket.
        derived = str(product.get("_derived_notes") or "").strip()
        if derived:
            notes = f"{derived}; {notes}" if notes else derived

        cart = _cart_from_session(wa_id, business_id)
        items: List[Dict] = list(cart.get("items") or [])

        # Stack qty when product_id AND notes match (case-insensitive,
        # whitespace-trimmed). Two adds with identical notes ("sin bbq",
        # "sin bbq") used to create two lines because the previous
        # branch only stacked when both sides had empty notes — that
        # made later remove_from_cart / update_cart_item calls
        # awkward (one took out everything, the other only the first
        # line). Now identical-notes adds merge into one line at qty=2.
        notes_norm = (notes or "").strip().lower()
        found = False
        for it in items:
            if it.get("product_id") != pid:
                continue
            if (it.get("notes") or "").strip().lower() == notes_norm:
                it["quantity"] = it.get("quantity", 0) + quantity
                found = True
                break
        if not found:
            new_item: Dict = {
                "product_id": pid,
                "name": name,
                "price": price,
                "quantity": quantity,
            }
            if notes:
                new_item["notes"] = notes
            items.append(new_item)

        total = sum(it.get("price", 0) * it.get("quantity", 0) for it in items)
        new_cart = {"items": items, "total": total}
        _save_cart(wa_id, business_id, new_cart)

        # Show the matcher-aware subtotal so the customer sees what they
        # will actually pay if a promo is bound on the cart.
        preview = promotion_service.preview_cart(business_id, items)
        notes_str = f" ({notes})" if notes else ""
        return (
            f"✅ Agregado {quantity}x {name}{notes_str} a tu pedido. "
            f"Subtotal: {_format_price(preview['subtotal'])}"
        )
    except AmbiguousProductError:
        raise
    except ProductNotFoundError:
        # Let the executor layer decide how to report this — in a
        # multi-item batch the loop captures it per-item and surfaces
        # via cart_change.not_found; in a single-item call the outer
        # handler builds a user_error result.
        raise
    except Exception as e:
        logger.error(f"[ORDER_TOOL] add_to_cart error: {e}")
        return f"❌ Error al agregar a tu pedido: {str(e)}"


@tool
def view_cart(injected_business_context: Annotated[dict, InjectedToolArg]) -> str:
    """
    View the current cart. Use when the customer wants to see what they have ordered or check the cart.
    """
    logger.info("[ORDER_TOOL] view_cart")
    try:
        business_id, wa_id = _get_context(injected_business_context)
        if not business_id or not wa_id:
            return "❌ No se pudo identificar la sesión. Intenta de nuevo."

        cart = _cart_from_session(wa_id, business_id)
        items = cart.get("items") or []

        if not items:
            return "Tu pedido está vacío. ¿Qué te gustaría ordenar? Pregunta por el menú o una categoría (ej. qué tienes de bebidas)."

        # Run the matcher so totals + display reflect any active promo bindings.
        preview = promotion_service.preview_cart(business_id, items)
        delivery_fee = _get_delivery_fee(injected_business_context)
        subtotal = preview["subtotal"]
        promo_discount = preview["promo_discount_total"]
        grand_total = subtotal + delivery_fee

        lines = _format_cart_display_lines(preview["display_groups"])
        parts = ["Tu pedido:", "", *lines, "", f"Subtotal: {_format_price(subtotal)}"]
        if promo_discount > 0:
            parts.append(f"🏷 Ahorro con promo: -{_format_price(promo_discount)}")
        parts.append(f"🛵 Domicilio: {_format_price(delivery_fee)}")
        parts.append(f"**Total: {_format_price(grand_total)}**")
        return "\n".join(parts)
    except Exception as e:
        logger.error(f"[ORDER_TOOL] view_cart error: {e}")
        return f"❌ Error al ver el pedido: {str(e)}"


def _format_cart_display_lines(display_groups: List[Dict]) -> List[str]:
    """Render preview_cart's display_groups as bullet lines for tool replies."""
    lines: List[str] = []
    for g in display_groups:
        if g.get("kind") == "promo_bundle":
            comps = ", ".join(
                f"{c.get('quantity')}x {c.get('name')}"
                for c in (g.get("components") or [])
            )
            price_str = _format_price(g.get("promo_price") or 0)
            lines.append(f"• 🏷 Promo *{g.get('promotion_name')}* ({comps}) — {price_str}")
        else:
            qty = int(g.get("quantity") or 0)
            name = g.get("name") or ""
            notes = g.get("notes")
            notes_str = f" ({notes})" if notes else ""
            price_str = _format_price(float(g.get("line_total") or 0))
            lines.append(f"• {qty}x {name}{notes_str} - {price_str}")
    return lines


@tool
def update_cart_item(
    product_id: str = "",
    product_name: str = "",
    quantity: int = 0,
    notes: str = "",
    *,
    injected_business_context: Annotated[dict, InjectedToolArg],
) -> str:
    """
    Update the quantity or notes of an item ALREADY in the cart. Use when the customer
    wants to set an EXACT target quantity ("solo una X", "que sean 3", "déjame con dos")
    or change notes on an existing item. Setting quantity=0 (with no notes) removes it.

    REFUSES when the named product is not currently in the cart — this is for editing
    existing lines, NOT for adding new ones. To add a new product, use add_to_cart.
    To decrement an existing line by N (without targeting an exact qty), use remove_from_cart(quantity=N).

    Resolution: pass either product_id (preferred when known from cart context) OR
    product_name (the tool resolves it against current cart contents — exact, then
    substring match). When the cart has multiple lines for the same product (different
    notes), the tool targets the first matching line.

    Args:
        product_id: Product UUID of an item already in the cart.
        product_name: Product name (used when product_id isn't known). Resolved against cart.
        quantity: New target quantity for the line (0 to remove; leave 0 if only updating notes).
        notes: Special instructions (e.g. "sin cebolla", "extra salsa"). Pass empty string to clear.
    """
    logger.info(
        f"[ORDER_TOOL] update_cart_item product_id='{product_id}' "
        f"product_name='{product_name}' quantity={quantity} notes='{notes}'"
    )
    try:
        business_id, wa_id = _get_context(injected_business_context)
        if not business_id or not wa_id:
            return "❌ No se pudo identificar la sesión. Intenta de nuevo."

        if not product_id and not (product_name and product_name.strip()):
            return "❌ Indica el producto a modificar (product_id o product_name)."

        notes = (notes or "").strip()
        cart = _cart_from_session(wa_id, business_id)
        original_items = cart.get("items") or []

        # Resolve target product_id by trying:
        #   1) Exact product_id match (UUID provided by the caller)
        #   2) product_name → exact case-insensitive match against cart items
        #   3) product_name → substring match against cart items
        # If none match, refuse — never create phantom lines.
        # If MULTIPLE match (same product across several lines with
        # different notes), refuse with a redirect to remove_from_cart —
        # silently picking the first match was producing "tool lied"
        # bugs (Biela / 2026-05-09: two MONTESA lines, update_cart_item
        # set qty=1 on one of them, total stayed at 2, bot said "Listo
        # ajustado" anyway).
        target_item = None
        if product_id:
            target_item = next(
                (it for it in original_items if it.get("product_id") == product_id),
                None,
            )
        if target_item is None and product_name:
            pn = product_name.strip().lower()
            exact_matches = [
                it for it in original_items
                if (it.get("name") or "").strip().lower() == pn
            ]
            substring_matches = []
            if not exact_matches:
                substring_matches = [
                    it for it in original_items
                    if pn in (it.get("name") or "").strip().lower()
                ]
            matches = exact_matches or substring_matches
            if len(matches) == 1:
                target_item = matches[0]
            elif len(matches) > 1:
                product_label = (matches[0].get("name") or product_name).strip()
                # List the existing variants so the model (or downstream
                # renderer) can mention them explicitly when prompting.
                variants = []
                for it in matches:
                    n = (it.get("notes") or "").strip()
                    qty = int(it.get("quantity", 0) or 0)
                    if n:
                        variants.append(f"{qty}x con notas '{n}'")
                    else:
                        variants.append(f"{qty}x sin notas")
                variants_str = "; ".join(variants)
                return (
                    f"❌ Hay {len(matches)} líneas de '{product_label}' en el carrito "
                    f"({variants_str}). update_cart_item solo edita UNA línea a la vez "
                    "por nombre y no sabe cuál querés ajustar. "
                    "Para REDUCIR el total del producto (e.g. 'solo una X' cuando hay "
                    "2), usa remove_from_cart(product_name=..., quantity=N) — "
                    "decrementa por N unidades en cascada. "
                    "Para REMOVER todas las líneas, usa remove_from_cart sin quantity. "
                    "Si querés editar una línea específica, llama view_cart primero "
                    "para tener el product_id y las notas exactas."
                )
        if target_item is None:
            ref = product_name or product_id or ""
            return (
                f"❌ '{ref}' no está en el carrito; update_cart_item solo edita "
                "ítems existentes. Si quieres agregarlo nuevo, usa add_to_cart. "
                "Si quieres ver lo que hay, usa view_cart."
            )

        # Lock onto the resolved line's actual product_id for filtering.
        resolved_id = target_item.get("product_id", "")
        effective_quantity = quantity
        if effective_quantity == 0 and notes:
            # qty=0 + notes set → keep existing qty, just update notes.
            effective_quantity = target_item.get("quantity", 1)

        # Drop the target line; rebuild it (or omit it if qty=0 and no notes).
        items: List[Dict] = [it for it in original_items if it is not target_item]
        if effective_quantity > 0:
            updated: Dict = {
                "product_id": resolved_id,
                "name": target_item.get("name", ""),
                "price": target_item.get("price", 0),
                "quantity": effective_quantity,
            }
            if notes:
                updated["notes"] = notes
            elif target_item.get("notes"):
                # Preserve existing notes when caller didn't pass new ones.
                updated["notes"] = target_item["notes"]
            items.append(updated)

        total = sum(it.get("price", 0) * it.get("quantity", 0) for it in items)
        new_cart = {"items": items, "total": total}
        _save_cart(wa_id, business_id, new_cart)

        if effective_quantity == 0:
            return "✅ Producto quitado de tu pedido."
        preview = promotion_service.preview_cart(business_id, items)
        notes_str = f" ({notes})" if notes else ""
        return f"✅ Ítem actualizado{notes_str}. Subtotal: {_format_price(preview['subtotal'])}"
    except Exception as e:
        logger.error(f"[ORDER_TOOL] update_cart_item error: {e}")
        return f"❌ Error al actualizar tu pedido: {str(e)}"


@tool
def remove_from_cart(
    product_id: str = "",
    product_name: str = "",
    quantity: int = 0,
    *,
    injected_business_context: Annotated[dict, InjectedToolArg],
) -> str:
    """
    Remove a product (entirely, or by quantity) from the cart.

    Behavior depends on `quantity`:
    - quantity == 0 (default, legacy): remove the product ENTIRELY — every line
      with this product_id is dropped. Use for "quita la X", "elimínalo",
      "no quiero la X", "ya no quiero eso".
    - quantity > 0: DECREMENT by N units. The matching line's qty is reduced;
      if the decrement equals or exceeds the line's qty, the line is dropped.
      With multiple lines for the same product (e.g. different notes), the
      decrement cascades from the first matching line. Use for "quita una X",
      "menos una", "una menos por favor" (relative decrement, NOT a target qty).

    For setting an EXACT target quantity ("solo una X", "déjame con dos X",
    "que sean 3"), prefer ``update_cart_item(quantity=N)`` — it sets the
    line's qty to N directly.

    Args:
        product_id: Product UUID to remove (preferred when known)
        product_name: Product name to remove (used when product_id is not available)
        quantity: 0 = remove entirely; >0 = decrement by N units
    """
    logger.info(
        f"[ORDER_TOOL] remove_from_cart product_id='{product_id}' "
        f"product_name='{product_name}' quantity={quantity}"
    )
    try:
        business_id, wa_id = _get_context(injected_business_context)
        if not business_id or not wa_id:
            return "❌ No se pudo identificar la sesión. Intenta de nuevo."

        cart = _cart_from_session(wa_id, business_id)
        original_items = cart.get("items") or []

        # Resolve product_id by name if not provided.
        # Handles three planner name shapes:
        #   "Jugos en leche"          → base-name match
        #   "Jugos en leche (mango)"  → strip parens, match name + notes
        #   "jugo de mango"           → qualifier match against item notes
        resolved_id = product_id.strip() if product_id else ""
        if not resolved_id and product_name:
            import re as _re
            raw = product_name.strip()
            paren_match = _re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", raw)
            if paren_match:
                base_name = paren_match.group(1).strip().lower()
                paren_notes = paren_match.group(2).strip().lower()
            else:
                base_name = raw.lower()
                paren_notes = ""

            # Pass 1: exact base-name match, disambiguate by notes
            base_matches = [
                it for it in original_items
                if (it.get("name") or "").lower().strip() == base_name
            ]
            if len(base_matches) == 1:
                resolved_id = base_matches[0].get("product_id", "")
            elif len(base_matches) > 1 and paren_notes:
                for it in base_matches:
                    if (it.get("notes") or "").strip().lower() == paren_notes:
                        resolved_id = it.get("product_id", "")
                        break
            if not resolved_id and base_matches:
                resolved_id = base_matches[0].get("product_id", "")

            # Pass 2: qualifier phrase — "jugo de mango" matches item
            # "Jugos en leche" with notes="mango"
            if not resolved_id:
                name_tokens = set(base_name.split())
                for it in original_items:
                    item_name = (it.get("name") or "").lower().strip()
                    item_notes = (it.get("notes") or "").strip().lower()
                    if not item_notes:
                        continue
                    item_tokens = set(item_name.split())
                    qualifier = name_tokens - item_tokens
                    if qualifier and item_notes in qualifier:
                        resolved_id = it.get("product_id", "")
                        break

            # Pass 3: partial / substring fallback
            if not resolved_id:
                for it in original_items:
                    if base_name in (it.get("name") or "").lower():
                        resolved_id = it.get("product_id", "")
                        break

        if not resolved_id:
            return "❌ No encontré ese producto en tu pedido. ¿Puedes indicar el nombre exacto?"

        non_matching = [it for it in original_items if it.get("product_id") != resolved_id]
        matching = [it for it in original_items if it.get("product_id") == resolved_id]

        try:
            qty_param = int(quantity or 0)
        except (TypeError, ValueError):
            qty_param = 0

        if qty_param <= 0:
            # Legacy: remove the product entirely (drop every matching line).
            items = non_matching
            total = sum(it.get("price", 0) * it.get("quantity", 0) for it in items)
            _save_cart(wa_id, business_id, {"items": items, "total": total})
            return "✅ Producto quitado de tu pedido."

        # Decrement by N. Cascade across matching lines if N exceeds a single line.
        product_label = (matching[0].get("name") if matching else "el producto") or "el producto"
        remaining_to_remove = qty_param
        updated_matching: List[Dict] = []
        for it in matching:
            if remaining_to_remove <= 0:
                updated_matching.append(it)
                continue
            try:
                line_qty = int(it.get("quantity", 0))
            except (TypeError, ValueError):
                line_qty = 0
            if remaining_to_remove >= line_qty:
                # Drop this line entirely; eat its qty from the budget
                remaining_to_remove -= line_qty
            else:
                new_qty = line_qty - remaining_to_remove
                updated = {**it, "quantity": new_qty}
                updated_matching.append(updated)
                remaining_to_remove = 0

        items = non_matching + updated_matching
        total = sum(it.get("price", 0) * it.get("quantity", 0) for it in items)
        _save_cart(wa_id, business_id, {"items": items, "total": total})

        actual_removed = qty_param - remaining_to_remove
        if actual_removed == 0:
            # Nothing to remove (line was already at 0, shouldn't really
            # happen since we resolved a product_id, but defensive).
            return f"❌ No había {product_label} en tu pedido."
        if remaining_to_remove > 0:
            return (
                f"✅ Quitamos {actual_removed}x {product_label} "
                f"(no había más en tu pedido)."
            )
        return f"✅ Quitamos {actual_removed}x {product_label} de tu pedido."
    except Exception as e:
        logger.error(f"[ORDER_TOOL] remove_from_cart error: {e}")
        return f"❌ Error al quitar el producto de tu pedido: {str(e)}"


NO_REGISTRADO = "NO_REGISTRADO"
NO_REGISTRADA = "NO_REGISTRADA"


@tool
def get_customer_info(injected_business_context: Annotated[dict, InjectedToolArg]) -> str:
    """
    Get merged delivery/customer status: session delivery_info + DB customer.
    Call when in COLLECTING_DELIVERY to know what we have and what is missing (name, address, phone, payment).

    Returns DELIVERY_STATUS|name=...|address=...|phone=...|payment=...|all_present=true| or |missing=name,address,...
    Use the exact values; if all_present=true confirm with the customer; if missing=... ask only for those fields; if all missing ask for all.
    """
    logger.info("[ORDER_TOOL] get_customer_info")
    try:
        business_id, wa_id = _get_context(injected_business_context)
        if not wa_id:
            return "❌ No se pudo identificar al cliente."

        cart = _cart_from_session(wa_id, business_id) if business_id else {}
        session_delivery = cart.get("delivery_info") or {}
        ftype = (cart.get("fulfillment_type") or "delivery").strip().lower()

        cust = _turn_cache().get_customer(
            wa_id, loader=lambda: customer_service.get_customer(wa_id)
        )
        db_name = (cust.get("name") or "").strip() if cust else ""
        db_address = (cust.get("address") or "").strip() if cust else ""
        db_phone = (cust.get("phone") or "").strip() if cust else ""
        db_payment = (cust.get("payment_method") or "").strip() if cust else ""

        name_val = (session_delivery.get("name") or "").strip() or db_name
        address_val = (session_delivery.get("address") or "").strip() or db_address
        phone_val = (session_delivery.get("phone") or "").strip() or db_phone
        payment_val = (session_delivery.get("payment_method") or "").strip() or db_payment

        name_display = name_val if name_val else NO_REGISTRADO
        addr_display = address_val if address_val else NO_REGISTRADA
        phone_display = phone_val if phone_val else NO_REGISTRADO
        pay_display = payment_val if payment_val else NO_REGISTRADO

        missing: List[str] = []
        all_present: bool
        if ftype == "pickup":
            if not name_val:
                missing.append("name")
            all_present = not missing
        else:
            if not name_val:
                missing.append("name")
            if not address_val:
                missing.append("address")
            if not phone_val:
                missing.append("phone")
            if not payment_val:
                missing.append("payment")
            # Never report all_present=true if any value is still a placeholder
            all_present = (
                len(missing) == 0
                and addr_display != NO_REGISTRADA
                and phone_display != NO_REGISTRADO
                and pay_display != NO_REGISTRADO
            )
        missing_str = ",".join(missing) if missing else ""
        return (
            f"DELIVERY_STATUS|mode={ftype}|name={name_display}|address={addr_display}|phone={phone_display}|payment={pay_display}|"
            f"all_present={'true' if all_present else 'false'}|missing={missing_str}"
        )
    except Exception as e:
        logger.error(f"[ORDER_TOOL] get_customer_info error: {e}")
        return f"❌ Error al consultar datos: {str(e)}"


@tool
def submit_delivery_info(
    address: str = "",
    payment_method: str = "",
    phone: str = "",
    name: str = "",
    fulfillment_type: str = "",
    notes: str = "",
    *,
    injected_business_context: Annotated[dict, InjectedToolArg],
) -> str:
    """
    Save or update delivery info (merge with existing). Call when the user provides one or more of:
    address, phone, name, payment_method, order-level notes, OR explicitly switches to/from pickup mode.

    EXTRACT ALL IDENTIFIABLE FIELDS in a SINGLE call. When the user dumps a multi-line message
    with several pieces of info ("Nombre Apellido / Calle ... / 3104078032 / Efectivo / llamen al llegar"),
    pass EVERY field you can identify in one invocation — name, address, phone, payment_method, notes.
    Don't omit a field just because you're unsure; if it looks like a person's name, pass it
    as `name`. The system normalizes payment values against the business's allowed list, so
    pass the user's literal payment text and let the tool canonicalize.

    PICKUP MODE: pass `fulfillment_type='pickup'` when the message signals the *customer*
    moves toward the store (customer-to-store direction) — "lo recojo", "paso a recoger",
    "para recoger", "voy por él", "yo la recojo", "la voy a traer", "lo voy a traer",
    "yo paso y la traigo", "para llevar", "en sitio", "en el local". Do NOT confuse with
    delivery phrases where the *store* moves toward the customer ("tráeme", "envíame",
    "mándame", "a domicilio") — those stay delivery. Distinguish by direction of motion,
    not by the verb alone: "traer" is pickup when the subject is the customer
    ("yo la voy a traer" → the customer brings it from the store) and delivery when the
    subject is the store ("tráemela" → the store brings it to the customer).
    In pickup mode only `name` is required for the order to be ready to place — address /
    phone / payment_method are NOT collected (the WhatsApp number covers phone and payment
    is at the register). To switch back to delivery on an explicit signal ("no, mejor
    domicilio", "envíenmelo"), pass `fulfillment_type='delivery'`.

    ORDER-LEVEL NOTES: instructions about the WHOLE ORDER or the delivery/pickup experience —
    pickup time ("a las 8 pm"), payment requests ("traigan cambio de un billete de 100",
    "préstame factura"), arrival/contact instructions ("llámenme cuando estén afuera",
    "déjenlo en portería", "tocar al timbre del 4B"), allergy / dietary disclaimers, etc.
    These do NOT apply to a specific product (those go on `add_to_cart(notes=...)`). Pass the
    consolidated, current-state string as `notes` — the tool REPLACES whatever was saved (so
    when the user amends "no, mejor a las 9", you pass the new consolidated note, not just
    the diff). To clear notes entirely, pass a single space.

    Args:
        address: Delivery address (e.g. "Calle 18 #43-38 apto 208"). Pass when present. Ignored in pickup mode.
        payment_method: Payment method (e.g. "efectivo", "Nequi", "transferencia", "Llave BreB"). Pass the user's text verbatim — the tool fuzzy-matches. Ignored in pickup mode.
        phone: Contact phone (digits only, e.g. "3104078032"). Use the WhatsApp number if user says "mismo"/"este número". Ignored in pickup mode.
        name: Customer name for the order (e.g. "Francisco Figueroa"). Pass when ANY name-shaped string is in the message. Required in BOTH modes.
        fulfillment_type: 'delivery' (default) or 'pickup'. Pass ONLY when the customer explicitly signals pickup or switches back. Empty string means "leave the current mode unchanged".
        notes: Order-level instructions (NOT product modifications). Examples: "A las 8 pm", "Llámenme cuando estén afuera", "Traigan cambio de $100.000", "Déjenlo en portería". Pass the FULL consolidated string each time — the tool replaces the saved value. Empty string leaves notes unchanged.
    """
    logger.info(
        "[ORDER_TOOL] submit_delivery_info address=%s payment=%s phone=%s name=%s ftype=%s notes=%s",
        bool(address and address.strip()),
        bool(payment_method and payment_method.strip()),
        bool(phone and str(phone).strip()),
        bool(name and name.strip()),
        (fulfillment_type or "").strip().lower() or "(unset)",
        bool(notes and str(notes).strip()),
    )
    try:
        business_id, wa_id = _get_context(injected_business_context)
        if not business_id or not wa_id:
            return "❌ No se pudo identificar la sesión. Intenta de nuevo."

        cart = _cart_from_session(wa_id, business_id)
        existing = (cart.get("delivery_info") or {}).copy()

        if name and str(name).strip():
            existing["name"] = str(name).strip()
        if address and str(address).strip():
            existing["address"] = str(address).strip()
        if phone is not None and str(phone).strip():
            existing["phone"] = str(phone).strip()
        if payment_method and str(payment_method).strip():
            # Normalize against the business's configured payment methods
            # using the same fuzzy matcher legacy uses (case-insensitive
            # substring). The v2 prompt already instructs the model to
            # canonicalize, but this is defense in depth — short
            # fragments like "breb" still resolve to "Llave BreB" even
            # if the model passed them verbatim. Falls back to the raw
            # value when no business list is configured (no enforcement).
            raw_pm = str(payment_method).strip()
            allowed = _allowed_payment_methods(injected_business_context)
            canonical = _match_payment_method(raw_pm, allowed)
            existing["payment_method"] = canonical or raw_pm

        # Resolve effective fulfillment_type for THIS save. Empty input
        # leaves the current mode unchanged. Validation rejects unknown
        # values rather than silently defaulting (mirrors create_order).
        ftype_in = (fulfillment_type or "").strip().lower()
        current_ftype = (cart.get("fulfillment_type") or "delivery").strip().lower()
        if ftype_in and ftype_in not in ("delivery", "pickup"):
            return f"❌ fulfillment_type inválido: {fulfillment_type!r}. Usa 'delivery' o 'pickup'."
        new_ftype = ftype_in or current_ftype

        # Order-level notes: empty string leaves the saved value
        # untouched, a single space clears it (escape hatch), any other
        # non-empty value REPLACES (model passes consolidated state).
        current_notes = (cart.get("notes") or "").strip()
        notes_raw = notes if notes is not None else ""
        notes_changed = False
        if isinstance(notes_raw, str) and notes_raw != "":
            if notes_raw.strip() == "":
                # User sentinel: a single space clears the notes.
                new_notes = ""
            else:
                new_notes = notes_raw.strip()
            notes_changed = (new_notes != current_notes)
        else:
            new_notes = current_notes

        has_new = any(
            (
                name and str(name).strip(),
                address and str(address).strip(),
                str(phone).strip() if phone is not None else False,
                payment_method and str(payment_method).strip(),
                bool(ftype_in) and ftype_in != current_ftype,
                notes_changed,
            )
        )
        if not has_new:
            return "✅ Sin cambios. Indica los datos que faltan (dirección, teléfono, nombre, medio de pago) para continuar."

        updated = {
            "items": cart.get("items") or [],
            "total": cart.get("total") or 0,
            "delivery_info": existing,
            "fulfillment_type": new_ftype,
            "notes": new_notes,
        }
        _save_cart(wa_id, business_id, updated)

        # Post-save completeness check (session ∪ customer DB). The agent
        # uses this to decide whether to emit ready_to_confirm now or
        # delivery_info_collected with missing fields. Without this signal
        # the agent has to call get_customer_info as a second hop, and in
        # practice the model often skips that and emits the wrong kind.
        try:
            cust = _turn_cache().get_customer(
                wa_id, loader=lambda: customer_service.get_customer(wa_id)
            ) or {}
        except Exception:
            cust = {}
        merged_name = (existing.get("name") or "").strip() or (cust.get("name") or "").strip()
        merged_addr = (existing.get("address") or "").strip() or (cust.get("address") or "").strip()
        merged_phone = (existing.get("phone") or "").strip() or (cust.get("phone") or "").strip()
        merged_payment = (
            (existing.get("payment_method") or "").strip()
            or (cust.get("payment_method") or "").strip()
        )
        missing: List[str] = []
        if new_ftype == "pickup":
            if not merged_name:
                missing.append("name")
        else:
            if not merged_name:
                missing.append("name")
            if not merged_addr:
                missing.append("address")
            if not merged_phone:
                missing.append("phone")
            if not merged_payment:
                missing.append("payment")
        all_present = not missing
        missing_str = ",".join(missing) if missing else ""
        mode_note = (
            "modo=pickup (recoger en local — solo se requiere nombre)"
            if new_ftype == "pickup"
            else "modo=delivery"
        )
        notes_note = ""
        if notes_changed:
            if new_notes:
                notes_note = f" notas guardadas: \"{new_notes[:100]}\""
            else:
                notes_note = " notas borradas"
        # If the customer is already in the confirmation state
        # (awaiting_confirmation=true was set in a prior turn after a
        # ready_to_confirm dispatch), don't re-prompt — the user has
        # already seen the card and is responding to it. Tell the agent
        # to place the order directly. Otherwise keep the legacy two-
        # step "show card → wait → place" instruction.
        already_awaiting = _read_awaiting_confirmation(wa_id, business_id)
        if all_present and already_awaiting:
            tail = (
                "El cliente ya está en fase de confirmación "
                "(awaiting_confirmation=true). Llama place_order AHORA — "
                "NO emitas otra tarjeta de confirmación."
            )
        elif all_present:
            tail = (
                "Si el cliente ya indicó que terminó de pedir, llama "
                "respond(kind='ready_to_confirm') AHORA — NO llames place_order, "
                "el sistema enviará la tarjeta de confirmación."
            )
        else:
            tail = (
                "Pide al cliente los campos faltantes y llama "
                "respond(kind='delivery_info_collected', facts=[...campos faltantes...])."
            )
        return (
            f"✅ Datos guardados ({mode_note}).{notes_note} "
            f"all_present={'true' if all_present else 'false'}|missing={missing_str}. "
            + tail
        )
    except Exception as e:
        logger.error(f"[ORDER_TOOL] submit_delivery_info error: {e}")
        return f"❌ Error al guardar: {str(e)}"


@tool
def place_order(injected_business_context: Annotated[dict, InjectedToolArg]) -> str:
    """
    Place the order. Use ONLY when:
    1. The cart has items
    2. For DELIVERY: name, address, phone, and payment_method have been collected via submit_delivery_info.
       For PICKUP (fulfillment_type='pickup' set on the order context): only name is required —
       the WhatsApp ID covers phone, payment is at the register, no address.
    3. The customer has EXPLICITLY confirmed in response to a ready_to_confirm prompt (CTA card or text).
       If you have not yet sent ready_to_confirm in a prior turn, call respond(kind='ready_to_confirm') instead
       and wait for the customer's affirmative reply before calling place_order.
    Creates the order from the cart and clears the cart.
    """
    logger.info("[ORDER_TOOL] place_order")
    try:
        business_id, wa_id = _get_context(injected_business_context)
        if not business_id or not wa_id:
            return "❌ No se pudo identificar la sesión. Intenta de nuevo."

        if not _products_enabled(injected_business_context):
            return "❌ Los pedidos no están habilitados en este momento."

        cart = _cart_from_session(wa_id, business_id)
        items = cart.get("items") or []
        delivery_info = cart.get("delivery_info") or {}
        ftype = (cart.get("fulfillment_type") or "delivery").strip().lower()
        if ftype not in ("delivery", "pickup"):
            ftype = "delivery"
        order_notes = (cart.get("notes") or "").strip()

        if not items:
            return "❌ Tu pedido está vacío. Agrega productos antes de confirmar."

        # State-machine guard: the customer must have seen a confirmation
        # prompt (CTA card or text) before we let place_order run. The
        # agent sets ``order_context.awaiting_confirmation = true`` in
        # the *prior* turn when it emits respond(kind='ready_to_confirm').
        # On this turn the guard ensures the user is actually responding
        # to that prompt (not that the model jumped ahead).
        awaiting = bool(
            (cart.get("awaiting_confirmation")
             if isinstance(cart, dict) else False)
            or _read_awaiting_confirmation(wa_id, business_id)
        )
        if not awaiting:
            return (
                "❌ El cliente todavía no ha confirmado el pedido. "
                "Llama respond(kind='ready_to_confirm') en este turno "
                "para mostrar la tarjeta de confirmación; el cliente "
                "responderá en el próximo turno y entonces sí podrás "
                "llamar place_order."
            )

        # Validate cart: each item must have product_id, name, price, quantity (from session only)
        for i, it in enumerate(items):
            if not it.get("product_id") or not it.get("name"):
                return f"❌ Ítem en tu pedido sin producto válido. Por favor revisa tu pedido."
            try:
                q = int(it.get("quantity") or 0)
                p = float(it.get("price") or 0)
            except (TypeError, ValueError):
                return "❌ Cantidades o precios inválidos en tu pedido. Intenta de nuevo."
            if q < 1 or p < 0:
                return "❌ Cantidades o precios inválidos en tu pedido. Intenta de nuevo."

        address = (delivery_info.get("address") or "").strip()
        payment_method = (delivery_info.get("payment_method") or "").strip()
        phone = (delivery_info.get("phone") or "").strip() or wa_id
        delivery_name = (delivery_info.get("name") or "").strip()

        # Required-fields gate. Pickup needs only name; delivery needs
        # address + payment_method (name is enforced via the customer
        # record below by falling back to "Cliente"). Phone is never
        # blocking — wa_id is the fallback in both modes.
        if ftype == "pickup":
            if not delivery_name:
                return (
                    "MISSING_DELIVERY_INFO|Falta el nombre para el pedido de recogida. "
                    "Pídelo y guárdalo con submit_delivery_info(name=..., fulfillment_type='pickup')."
                )
        else:
            if not address or not payment_method:
                return (
                    "MISSING_DELIVERY_INFO|Falta información para confirmar el pedido. "
                    "Necesito: nombre, dirección, teléfono y medio de pago. "
                    "Usa submit_delivery_info cuando tengas los datos."
                )

        cust = _turn_cache().get_customer(
            wa_id, loader=lambda: customer_service.get_customer(wa_id)
        )
        customer_name = delivery_name or (cust.get("name") or "").strip() if cust else delivery_name
        if not customer_name:
            customer_name = "Cliente"

        order_items = [
            {
                "product_id": it.get("product_id"),
                "name": it.get("name"),
                "price": it.get("price", 0),
                "quantity": it.get("quantity", 1),
                "notes": (it.get("notes") or "").strip() or None,
            }
            for it in items
        ]

        # Pickup orders don't have a delivery fee. Don't read/charge one
        # even if the business has a fee configured — the customer is
        # walking in.
        delivery_fee = 0.0 if ftype == "pickup" else _get_delivery_fee(injected_business_context)

        # Build the cart breakdown BEFORE create_order — the same call
        # clears the session cart, so we'd lose the items list otherwise.
        # Mirrors view_cart's format so the receipt and the cart preview
        # the customer saw at confirmation time stay visually consistent.
        try:
            preview = promotion_service.preview_cart(business_id, items)
            display_lines = _format_cart_display_lines(preview.get("display_groups") or [])
            promo_discount_preview = float(preview.get("promo_discount_total") or 0)
        except Exception as _exc:
            logger.warning(
                "[ORDER_TOOL] place_order preview_cart failed, "
                "receipt will skip itemization: %s", _exc,
            )
            display_lines = []
            promo_discount_preview = 0.0

        result = product_order_service.create_order(
            business_id=business_id,
            whatsapp_id=wa_id,
            items=order_items,
            delivery_address=(None if ftype == "pickup" else address),
            contact_phone=phone,
            payment_method=(None if ftype == "pickup" else payment_method),
            customer_name=customer_name,
            delivery_fee=delivery_fee,
            fulfillment_type=ftype,
            notes=order_notes or None,
        )

        if not result.get("success"):
            return f"❌ No se pudo crear el pedido: {result.get('error', 'Error desconocido')}"

        order_id = result.get("order_id", "")

        # Human-facing #001-style id, allocated atomically by the
        # service. Falls back to the UUID prefix only if the service
        # ever returns without one (shouldn't happen post-migration).
        display_number = result.get("display_number")
        display_number_str = (
            f"{int(display_number):03d}"
            if display_number is not None
            else order_id[:8].upper()
        )

        # Reset session: clear order context, clear active agents, store last_order_id
        # Session stays alive so user can ask "¿cuánto demora?", "quiero otro pedido", etc.
        session_state_service.save(
            wa_id,
            business_id,
            {
                "order_context": None,
                "active_agents": [],
                "last_order_id": order_id,
            },
        )
        _turn_cache().invalidate_session(wa_id, business_id)
        subtotal = result.get("subtotal", 0)
        total = result.get("total", 0)
        items_block = ("\n".join(display_lines) + "\n\n") if display_lines else ""
        promo_line = (
            f"🏷 Ahorro con promo: -{_format_price(promo_discount_preview)}\n"
            if promo_discount_preview > 0 else ""
        )
        notes_line = f"📝 Notas: {order_notes}\n" if order_notes else ""
        if ftype == "pickup":
            return (
                f"✅ ¡Pedido confirmado! #{display_number_str}\n\n"
                f"{items_block}"
                f"Subtotal: {_format_price(subtotal)}\n"
                f"{promo_line}"
                f"Total: {_format_price(total)}\n"
                f"{notes_line}"
                f"🏃 Recoge en el local.\n"
                f"⏱ Tiempo estimado: {PICKUP_RANGE_TEXT}."
            )
        # Delivery confirmation honors the operator's ETA override
        # (businesses.settings.delivery_eta_minutes). Pickup branch
        # above intentionally stays on PICKUP_RANGE_TEXT — pickup
        # wait depends on the kitchen, not delivery load.
        _delivery_settings = ((injected_business_context or {}).get("business") or {}).get("settings") or {}
        _, _, delivery_eta_text, _ = resolve_delivery_eta(_delivery_settings)
        return (
            f"✅ ¡Pedido confirmado! #{order_id[:8].upper()}\n\n"
            f"{items_block}"
            f"Subtotal: {_format_price(subtotal)}\n"
            f"{promo_line}"
            f"🛵 Domicilio: {_format_price(delivery_fee)} (puede variar según la distancia)\n"
            f"Total: {_format_price(total)}\n"
            f"{notes_line}"
            f"Nos ponemos en contacto pronto para coordinar la entrega.\n"
            f"⏱ Tiempo estimado de entrega: {delivery_eta_text}."
        )
    except Exception as e:
        logger.error(f"[ORDER_TOOL] place_order error: {e}")
        return f"❌ Error al confirmar el pedido: {str(e)}"


@tool
def list_promos(
    *,
    injected_business_context: Annotated[dict, InjectedToolArg],
) -> str:
    """
    List the business's active promos right now (and upcoming this week
    if any). Use when the customer asks about promos / offers / combos
    without naming a specific one ("me das una promo", "qué promos
    tienen", "tienen ofertas", "qué combos manejan").

    Do NOT use when the customer already named a specific promo — call
    add_promo_to_cart(promo_query=...) instead.

    Returns text the LLM reads (active list + upcoming-this-week tail).
    The LLM should follow up with respond(kind='menu_info' or
    'disambiguation', summary=..., facts=[...promo names...]) — never
    call add_promo_to_cart unless the customer explicitly picks one.
    """
    logger.info("[ORDER_TOOL] list_promos")
    try:
        business_id, _ = _get_context(injected_business_context)
        if not _products_enabled(injected_business_context):
            return "❌ Los pedidos no están habilitados en este momento."
        if not business_id:
            return "❌ No se pudo identificar el negocio."

        tz_name = promotion_service.timezone_from_business_context(injected_business_context)
        try:
            buckets = promotion_service.list_promos_for_listing(
                business_id, timezone_name=tz_name,
            )
        except Exception as exc:
            logger.warning("[ORDER_TOOL] list_promos_for_listing failed: %s", exc)
            return "❌ No pude consultar las promos en este momento."

        active = buckets.get("active_now") or []
        upcoming = buckets.get("upcoming") or []

        if not active and not upcoming:
            return "Sin promos activas ni próximas esta semana."

        def _line(p: Dict, idx: int) -> str:
            name = p.get("name") or ""
            if p.get("fixed_price") is not None:
                return f"{idx}. {name} — {_format_price(p['fixed_price'])}"
            if p.get("discount_amount") is not None:
                return f"{idx}. {name} — descuento {_format_price(p['discount_amount'])}"
            if p.get("discount_pct") is not None:
                return f"{idx}. {name} — {int(p['discount_pct'])}% off"
            return f"{idx}. {name}"

        parts: List[str] = []
        if active:
            lines = "\n".join(_line(p, i) for i, p in enumerate(active, start=1))
            parts.append(f"Promos activas hoy:\n{lines}")
        if upcoming:
            up_parts: List[str] = []
            for p in upcoming[:5]:
                name = p.get("name") or ""
                day = _DAY_NAMES_ES.get(int(p.get("next_active_day") or 0))
                up_parts.append(f"{name} ({day})" if day else name)
            parts.append(f"Esta semana también viene: {', '.join(up_parts)}.")

        return "\n\n".join(parts)
    except Exception as e:
        logger.error(f"[ORDER_TOOL] list_promos error: {e}", exc_info=True)
        return f"❌ Error al listar promos: {str(e)}"


@tool
def add_promo_to_cart(
    promo_id: str = "",
    promo_query: str = "",
    *,
    injected_business_context: Annotated[dict, InjectedToolArg],
) -> str:
    """
    Add a promotion (and its component products) to the cart as a single
    bound bundle. Use when the customer asks for a promo by name or
    accepts a previously-listed one.

    Resolution: pass `promo_id` if known (e.g. from a customer-service
    handoff after the user said "dame esa"). Otherwise pass `promo_query`
    — the user's free text — and we'll match against active promo names.

    Args:
        promo_id: Promotion UUID (preferred when known)
        promo_query: Customer's free-text reference to a promo
    """
    logger.info(f"[ORDER_TOOL] add_promo_to_cart promo_id='{promo_id}' promo_query='{promo_query}'")
    try:
        business_id, wa_id = _get_context(injected_business_context)
        if not _products_enabled(injected_business_context):
            return "❌ Los pedidos de productos no están habilitados en este momento."
        if not business_id or not wa_id:
            return "❌ No se pudo identificar la sesión. Intenta de nuevo."

        # Resolve the promo. Schedule check happens here too — if it's not
        # active right now, refuse early so we don't bind a non-applicable
        # promo to the cart. Pass the business timezone so the schedule
        # filter evaluates day/time in the right wall clock.
        tz_name = promotion_service.timezone_from_business_context(injected_business_context)
        active_promos = promotion_service.list_active_promos(
            business_id, timezone_name=tz_name,
        )
        promo = None
        if promo_id:
            promo = next((p for p in active_promos if p["id"] == promo_id), None)
            if not promo:
                # Direct id miss: maybe inactive or out of schedule.
                full = promotion_service.get_promotion(business_id, promo_id)
                if full and not full.get("is_active"):
                    return "❌ Esa promo ya no está activa."
                if full:
                    return "❌ Esa promo no aplica en este horario."
                return "❌ No encontré esa promo."
        elif promo_query:
            matches = promotion_service.find_promo_by_query(
                business_id, promo_query, timezone_name=tz_name,
            )
            if not matches:
                # Surface active/upcoming so the customer learns what IS
                # available in this turn (single-turn miss path) instead
                # of having to ask "what promos do you have?" separately.
                return _format_promo_miss_message(
                    business_id, promo_query, tz_name,
                )
            if len(matches) > 1:
                names = ", ".join(p["name"] for p in matches[:5])
                return f"❌ Varias promos coinciden ({names}). Pídela por nombre exacto."
            promo = matches[0]
        else:
            return "❌ Faltan datos de la promo."

        components = promo.get("components") or []
        if not components:
            return "❌ Esa promo no tiene productos definidos. Avísale al negocio."

        # Hydrate component product names + prices for the cart line items.
        promo_group_id = str(uuid.uuid4())
        cart = _cart_from_session(wa_id, business_id)
        items: List[Dict] = list(cart.get("items") or [])

        added_lines: List[str] = []
        for c in components:
            product = product_order_service.get_product(
                product_id=c["product_id"], business_id=business_id,
            )
            if not product:
                return f"❌ Uno de los productos de la promo ya no está disponible."
            qty = int(c.get("quantity") or 1)
            new_item: Dict = {
                "product_id": product["id"],
                "name": product["name"],
                "price": float(product.get("price", 0)),
                "quantity": qty,
                "promotion_id": promo["id"],
                "promo_group_id": promo_group_id,
            }
            items.append(new_item)
            added_lines.append(f"{qty}x {product['name']}")

        # Recompute display total from base prices — the real promo math
        # runs at place_order via promotion_service.match_and_apply.
        total = sum(it.get("price", 0) * it.get("quantity", 0) for it in items)
        new_cart = {"items": items, "total": total}
        _save_cart(wa_id, business_id, new_cart)

        # Use the matcher to compute what this addition will actually cost
        # the customer (promo binding honored). Avoids the "you'll see $56k
        # then $30k at checkout" bait pattern.
        preview = promotion_service.preview_cart(business_id, items)
        items_str = ", ".join(added_lines)
        return (
            f"✅ Agregué la promo *{promo['name']}* ({items_str}). "
            f"Subtotal: {_format_price(preview['subtotal'])}"
        )
    except Exception as e:
        logger.error(f"[ORDER_TOOL] add_promo_to_cart error: {e}", exc_info=True)
        return f"❌ Error al agregar la promo: {str(e)}"


# List of all order tools
order_tools = [
    get_menu_categories,
    list_category_products,
    search_products,
    get_product_details,
    add_to_cart,
    list_promos,
    add_promo_to_cart,
    view_cart,
    update_cart_item,
    remove_from_cart,
    get_customer_info,
    submit_delivery_info,
    place_order,
]


# Tools that mutate cart / customer profile / order state. Used by the
# v2 availability gate: when the business is closed (no
# ``business_availability`` row covers the current Bogotá time), these
# tool calls are intercepted and the turn is handed off to
# customer_service. Browse / read-only tools (get_menu_categories,
# list_category_products, search_products, get_product_details,
# view_cart, get_customer_info) are intentionally NOT in this set —
# customers can still read the menu while the shop is closed.
MUTATING_TOOL_NAMES = frozenset({
    "add_to_cart",
    "add_promo_to_cart",
    "update_cart_item",
    "remove_from_cart",
    "submit_delivery_info",
    "place_order",
})
