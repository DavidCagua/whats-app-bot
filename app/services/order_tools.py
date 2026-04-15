"""
Order tools for the Order agent.
Browse products, manage cart, place orders.
"""

import logging
from typing import Dict, List, Optional
from langchain.tools import tool

from ..database.product_order_service import product_order_service, AmbiguousProductError
from ..database.session_state_service import session_state_service


def _turn_cache():
    """
    Lazy import of the per-turn cache. order_tools is imported from
    app.orchestration.order_flow at module-load time, which would make
    a top-level `from ..orchestration import turn_cache` trigger the
    orchestration package __init__ before it's ready. Defer the import
    to first call; Python caches the module so it's ~free.
    """
    from ..orchestration import turn_cache as tc
    return tc.current()

logger = logging.getLogger(__name__)


def _format_price(price: float, currency: str = "COP") -> str:
    """Format price for display."""
    return f"${int(price):,}".replace(",", ".")

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
    """Extract business_id and wa_id from injected context."""
    ctx = injected_business_context or {}
    business_id = ctx.get("business_id") or ""
    wa_id = ctx.get("wa_id") or ""
    return business_id, wa_id


def _products_enabled(ctx: Optional[Dict]) -> bool:
    """Check if products/orders are enabled for the business."""
    if not ctx:
        return True
    settings = (ctx.get("business") or {}).get("settings") or {}
    return settings.get("products_enabled", True)


def _get_delivery_fee(ctx: Optional[Dict]) -> float:
    """Get delivery fee from business settings. Defaults to 5000 COP."""
    if not ctx:
        return 5000.0
    settings = (ctx.get("business") or {}).get("settings") or {}
    return float(settings.get("delivery_fee", 5000))


def _cart_from_session(wa_id: str, business_id: str) -> Dict:
    """
    Load order_context (cart + delivery_info + state) from session.
    In-progress cart lives only in session; no separate DB cart.
    """
    if not wa_id or not business_id:
        return {"items": [], "total": 0, "delivery_info": None, "state": None}
    result = _turn_cache().get_session(wa_id, business_id)
    order_context = result.get("session", {}).get("order_context") or {}
    items = order_context.get("items") or []
    total = order_context.get("total") or 0
    delivery_info = order_context.get("delivery_info")
    state = order_context.get("state")
    return {"items": items, "total": total, "delivery_info": delivery_info, "state": state}


def _save_cart(wa_id: str, business_id: str, cart: Dict) -> None:
    """
    Save cart to session order_context. Preserves existing state if cart omits it.
    """
    if not wa_id or not business_id:
        return
    existing = _cart_from_session(wa_id, business_id) if cart.get("state") is None else {}
    merged = {**existing, **cart}
    if merged.get("state") is None and existing.get("state") is not None:
        merged["state"] = existing["state"]
    session_state_service.save(wa_id, business_id, {"order_context": merged})
    # Drop the per-turn cached session so the next _cart_from_session in
    # this turn refetches and sees the merged state.
    _turn_cache().invalidate_session(wa_id, business_id)


@tool
def get_menu_categories(injected_business_context: dict = None) -> str:
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

        categories = product_order_service.list_categories(business_id=business_id)
        if not categories:
            # No categories set — fall back to listing all products directly
            all_products = product_order_service.list_products(business_id=business_id)
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
def list_category_products(category: str = "", injected_business_context: dict = None) -> str:
    """
    List items in a category. Use when the user asks what you have in a category
    (e.g. drinks, bebidas, hamburguesas). Pass category (e.g. BEBIDAS, HAMBURGUESAS).
    Leave category empty to list the full menu.

    Args:
        category: Category filter (e.g. BEBIDAS, HAMBURGUESAS, FRIES). Empty = full menu.
    """
    logger.info(f"[ORDER_TOOL] list_category_products category='{category}'")
    try:
        business_id, _ = _get_context(injected_business_context)
        if not _products_enabled(injected_business_context):
            return "❌ Los pedidos de productos no están habilitados en este momento."
        if not business_id:
            return "❌ No se pudo identificar el negocio. Intenta de nuevo."

        products = product_order_service.list_products_with_fallback(
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
def search_products(query: str, injected_business_context: dict = None) -> str:
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

        products = product_order_service.search_products(business_id=business_id, query=query.strip())

        if not products:
            return f"❌ No hay productos que coincidan con '{query}'."

        include_desc = _is_ingredient_like_query(query)
        lines = []
        for p in products:
            price_str = _format_price(p.get("price", 0), p.get("currency", "COP"))
            line = f"• {p['name']} - {price_str}"
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
def get_product_details(product_id: str = "", product_name: str = "", injected_business_context: dict = None) -> str:
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

        product = product_order_service.get_product(
            product_id=product_id.strip() if product_id else None,
            product_name=product_name.strip() if product_name else None,
            business_id=business_id,
        )

        if not product:
            return "❌ Producto no encontrado. Usa list_category_products para ver el menú."

        price_str = _format_price(product.get("price", 0), product.get("currency", "COP"))
        desc = product.get("description") or ""
        return f"**{product['name']}** - {price_str}\n" + (f"{desc}" if desc else "")
    except AmbiguousProductError:
        raise
    except Exception as e:
        logger.error(f"[ORDER_TOOL] get_product_details error: {e}")
        return f"❌ Error al buscar producto: {str(e)}"


@tool
def add_to_cart(product_id: str = "", product_name: str = "", quantity: int = 1, notes: str = "", injected_business_context: dict = None) -> str:
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

        product = None
        if product_id:
            product = product_order_service.get_product(product_id=product_id, business_id=business_id)
        elif product_name and product_name.strip():
            product = product_order_service.get_product(product_name=product_name, business_id=business_id)

        if not product:
            return "❌ Producto no encontrado. Pregunta por el menú o una categoría para ver productos."

        price = float(product.get("price", 0))
        pid = product["id"]
        name = product["name"]
        notes = (notes or "").strip()

        cart = _cart_from_session(wa_id, business_id)
        items: List[Dict] = list(cart.get("items") or [])

        # Update or add item; if notes provided, always add as new line item
        found = False
        if not notes:
            for it in items:
                if it.get("product_id") == pid and not it.get("notes"):
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

        notes_str = f" ({notes})" if notes else ""
        return f"✅ Agregado {quantity}x {name}{notes_str} a tu pedido. Subtotal: {_format_price(total)}"
    except AmbiguousProductError:
        raise
    except Exception as e:
        logger.error(f"[ORDER_TOOL] add_to_cart error: {e}")
        return f"❌ Error al agregar a tu pedido: {str(e)}"


@tool
def view_cart(injected_business_context: dict = None) -> str:
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
        subtotal = cart.get("total") or 0

        if not items:
            return "Tu pedido está vacío. ¿Qué te gustaría ordenar? Pregunta por el menú o una categoría (ej. qué tienes de bebidas)."

        lines = []
        for it in items:
            price_str = _format_price(it.get("price", 0) * it.get("quantity", 0))
            notes_str = f" ({it['notes']})" if it.get("notes") else ""
            lines.append(f"• {it.get('quantity', 0)}x {it.get('name', '')}{notes_str} - {price_str}")

        delivery_fee = _get_delivery_fee(injected_business_context)
        grand_total = subtotal + delivery_fee
        summary = (
            "Tu pedido:\n\n"
            + "\n".join(lines)
            + f"\n\nSubtotal: {_format_price(subtotal)}"
            + f"\n🛵 Domicilio: {_format_price(delivery_fee)}"
            + f"\n**Total: {_format_price(grand_total)}**"
        )
        return summary
    except Exception as e:
        logger.error(f"[ORDER_TOOL] view_cart error: {e}")
        return f"❌ Error al ver el pedido: {str(e)}"


@tool
def update_cart_item(product_id: str = "", quantity: int = 0, notes: str = "", injected_business_context: dict = None) -> str:
    """
    Update the quantity or notes of an item in the cart. Use when the customer wants to change how many of something
    they want, or add special instructions (e.g. "sin cebolla", "sin morcilla"). If quantity is 0 and no notes are
    provided, the item is removed from the cart.

    Args:
        product_id: Product UUID to update
        quantity: New quantity (0 to remove; leave 0 if only updating notes)
        notes: Special instructions for the item (e.g. "sin cebolla", "extra salsa"). Pass empty string to clear.
    """
    logger.info(f"[ORDER_TOOL] update_cart_item product_id='{product_id}' quantity={quantity} notes='{notes}'")
    try:
        business_id, wa_id = _get_context(injected_business_context)
        if not business_id or not wa_id:
            return "❌ No se pudo identificar la sesión. Intenta de nuevo."

        if not product_id:
            return "❌ Indica el producto a modificar (product_id)."

        notes = (notes or "").strip()
        cart = _cart_from_session(wa_id, business_id)
        original_items = cart.get("items") or []

        # Find the item being updated
        target_item = next((it for it in original_items if it.get("product_id") == product_id), None)

        # Determine effective quantity: keep existing if caller passes 0 and there are notes to set
        effective_quantity = quantity
        if effective_quantity == 0 and target_item and notes:
            effective_quantity = target_item.get("quantity", 1)

        items: List[Dict] = [it for it in original_items if it.get("product_id") != product_id]

        if effective_quantity > 0:
            original = target_item or {}
            updated: Dict = {
                "product_id": product_id,
                "name": original.get("name", ""),
                "price": original.get("price", 0),
                "quantity": effective_quantity,
            }
            if notes:
                updated["notes"] = notes
            items.append(updated)

        total = sum(it.get("price", 0) * it.get("quantity", 0) for it in items)
        new_cart = {"items": items, "total": total}
        _save_cart(wa_id, business_id, new_cart)

        if effective_quantity == 0:
            return "✅ Producto quitado de tu pedido."
        notes_str = f" ({notes})" if notes else ""
        return f"✅ Ítem actualizado{notes_str}. Subtotal: {_format_price(total)}"
    except Exception as e:
        logger.error(f"[ORDER_TOOL] update_cart_item error: {e}")
        return f"❌ Error al actualizar tu pedido: {str(e)}"


@tool
def remove_from_cart(product_id: str = "", product_name: str = "", injected_business_context: dict = None) -> str:
    """
    Remove a product from the cart. Use when the customer corrects an item ("no de cereza", "quita eso",
    "elimina la malteada"). Accepts either product_id (UUID) or product_name (flexible name match).

    Args:
        product_id: Product UUID to remove (preferred when known)
        product_name: Product name to remove (used when product_id is not available)
    """
    logger.info(f"[ORDER_TOOL] remove_from_cart product_id='{product_id}' product_name='{product_name}'")
    try:
        business_id, wa_id = _get_context(injected_business_context)
        if not business_id or not wa_id:
            return "❌ No se pudo identificar la sesión. Intenta de nuevo."

        cart = _cart_from_session(wa_id, business_id)
        original_items = cart.get("items") or []

        # Resolve product_id by name if not provided
        resolved_id = product_id.strip() if product_id else ""
        if not resolved_id and product_name:
            name_lower = product_name.lower().strip()
            for it in original_items:
                if (it.get("name") or "").lower().strip() == name_lower:
                    resolved_id = it.get("product_id", "")
                    break
            # Fuzzy: partial match fallback
            if not resolved_id:
                for it in original_items:
                    if name_lower in (it.get("name") or "").lower():
                        resolved_id = it.get("product_id", "")
                        break

        if not resolved_id:
            return "❌ No encontré ese producto en tu pedido. ¿Puedes indicar el nombre exacto?"

        items = [it for it in original_items if it.get("product_id") != resolved_id]
        total = sum(it.get("price", 0) * it.get("quantity", 0) for it in items)
        new_cart = {"items": items, "total": total}
        _save_cart(wa_id, business_id, new_cart)
        return "✅ Producto quitado de tu pedido."
    except Exception as e:
        logger.error(f"[ORDER_TOOL] remove_from_cart error: {e}")
        return f"❌ Error al quitar el producto de tu pedido: {str(e)}"


NO_REGISTRADO = "NO_REGISTRADO"
NO_REGISTRADA = "NO_REGISTRADA"


@tool
def get_customer_info(injected_business_context: dict = None) -> str:
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

        cust = _turn_cache().get_customer(wa_id)
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

        missing = []
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
            f"DELIVERY_STATUS|name={name_display}|address={addr_display}|phone={phone_display}|payment={pay_display}|"
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
    injected_business_context: dict = None,
) -> str:
    """
    Save or update delivery info (merge with existing). Call when the user provides one or more of:
    address, phone, name, payment_method. Params are optional; only provided non-empty values are merged.

    Args:
        address: Delivery address (optional; merge if provided)
        payment_method: Payment method e.g. Efectivo, Nequi (optional; merge if provided)
        phone: Contact phone; use WhatsApp number if "mismo"/"este número" (optional; merge if provided)
        name: Customer name for the order (optional; merge if provided)
    """
    logger.info(
        "[ORDER_TOOL] submit_delivery_info address=%s payment=%s phone=%s name=%s",
        bool(address and address.strip()),
        bool(payment_method and payment_method.strip()),
        bool(phone and str(phone).strip()),
        bool(name and name.strip()),
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
            existing["payment_method"] = str(payment_method).strip()

        has_new = any(
            (
                name and str(name).strip(),
                address and str(address).strip(),
                str(phone).strip() if phone is not None else False,
                payment_method and str(payment_method).strip(),
            )
        )
        if not has_new:
            return "✅ Sin cambios. Indica los datos que faltan (dirección, teléfono, nombre, medio de pago) para continuar."

        updated = {
            "items": cart.get("items") or [],
            "total": cart.get("total") or 0,
            "delivery_info": existing,
        }
        _save_cart(wa_id, business_id, updated)
        return "✅ Datos de entrega guardados. Puedes confirmar el pedido con place_order cuando tengas todo."
    except Exception as e:
        logger.error(f"[ORDER_TOOL] submit_delivery_info error: {e}")
        return f"❌ Error al guardar: {str(e)}"


@tool
def place_order(injected_business_context: dict = None) -> str:
    """
    Place the order. Use ONLY when:
    1. The cart has items
    2. Delivery info has been collected (via submit_delivery_info) - address and payment_method are required.
    If delivery info is missing, tell the customer you need their address and payment method first, then call submit_delivery_info.
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

        if not items:
            return "❌ Tu pedido está vacío. Agrega productos antes de confirmar."

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

        if not address or not payment_method:
            return (
                "MISSING_DELIVERY_INFO|Falta información para confirmar el pedido. "
                "Necesito: nombre, dirección, teléfono y medio de pago. "
                "Usa submit_delivery_info cuando tengas los datos."
            )

        cust = _turn_cache().get_customer(wa_id)
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

        delivery_fee = _get_delivery_fee(injected_business_context)

        result = product_order_service.create_order(
            business_id=business_id,
            whatsapp_id=wa_id,
            items=order_items,
            delivery_address=address,
            contact_phone=phone,
            payment_method=payment_method,
            customer_name=customer_name,
            delivery_fee=delivery_fee,
        )

        if not result.get("success"):
            return f"❌ No se pudo crear el pedido: {result.get('error', 'Error desconocido')}"

        order_id = result.get("order_id", "")

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
        return (
            f"✅ ¡Pedido confirmado! #{order_id[:8].upper()}\n"
            f"Subtotal: {_format_price(subtotal)}\n"
            f"🛵 Domicilio: {_format_price(delivery_fee)}\n"
            f"Total: {_format_price(total)}\n"
            f"Nos ponemos en contacto pronto para coordinar la entrega."
        )
    except Exception as e:
        logger.error(f"[ORDER_TOOL] place_order error: {e}")
        return f"❌ Error al confirmar el pedido: {str(e)}"


# List of all order tools
order_tools = [
    get_menu_categories,
    list_category_products,
    search_products,
    get_product_details,
    add_to_cart,
    view_cart,
    update_cart_item,
    remove_from_cart,
    get_customer_info,
    submit_delivery_info,
    place_order,
]
