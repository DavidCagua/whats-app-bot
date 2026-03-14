"""
Order tools for the Order agent.
Browse products, manage cart, place orders.
"""

import logging
from typing import Dict, List, Optional
from langchain.tools import tool

from ..database.product_order_service import product_order_service
from ..database.session_state_service import session_state_service
from ..database.customer_service import customer_service

logger = logging.getLogger(__name__)


def _format_price(price: float, currency: str = "COP") -> str:
    """Format price for display."""
    return f"${int(price):,}".replace(",", ".")


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


def _cart_from_session(wa_id: str, business_id: str) -> Dict:
    """Load order_context (cart + delivery_info) from session."""
    if not wa_id or not business_id:
        return {"items": [], "total": 0, "delivery_info": None}
    result = session_state_service.load(wa_id, business_id)
    order_context = result.get("session", {}).get("order_context") or {}
    items = order_context.get("items") or []
    total = order_context.get("total") or 0
    delivery_info = order_context.get("delivery_info")
    return {"items": items, "total": total, "delivery_info": delivery_info}


def _save_cart(wa_id: str, business_id: str, cart: Dict) -> None:
    """Save cart to session order_context."""
    if not wa_id or not business_id:
        return
    session_state_service.save(wa_id, business_id, {"order_context": cart})


@tool
def list_products(category: str = "", injected_business_context: dict = None) -> str:
    """
    List products from the menu. Use when the customer wants to see the menu, browse products,
    or ask what is available. Optionally filter by category (e.g. BURGERS, BEBIDAS, FRIES).

    Args:
        category: Optional category filter (e.g. BURGERS, BEBIDAS, HOT DOGS, FRIES, MENU INFANTIL, STEAK AND RIBS)
    """
    logger.info(f"[ORDER_TOOL] list_products category='{category}'")
    try:
        business_id, _ = _get_context(injected_business_context)
        if not _products_enabled(injected_business_context):
            return "❌ Los pedidos de productos no están habilitados en este momento."
        if not business_id:
            return "❌ No se pudo identificar el negocio. Intenta de nuevo."

        products = product_order_service.list_products(
            business_id=business_id,
            category=category.strip() if category else None,
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
        logger.error(f"[ORDER_TOOL] list_products error: {e}")
        return f"❌ Error al listar productos: {str(e)}"


@tool
def search_products(query: str, injected_business_context: dict = None) -> str:
    """
    Search products by name OR ingredients/description. Use for flexible lookups.
    - "hamburguesa barracuda" -> finds Barracuda
    - "hamburguesa con queso azul" -> finds Montesa (ingredients in description)
    - "coca zero" -> finds Coca-Cola Zero
    Returns ALL matches. If multiple, list them and ASK which one. If single match, add to cart.

    Args:
        query: Search term - can be product name or ingredient/description
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

        lines = []
        for p in products:
            price_str = _format_price(p.get("price", 0), p.get("currency", "COP"))
            lines.append(f"• {p['name']} - {price_str} (ID: {p['id']})")

        header = f"Productos que coinciden con '{query}':\n\n"
        return header + "\n".join(lines)
    except Exception as e:
        logger.error(f"[ORDER_TOOL] search_products error: {e}")
        return f"❌ Error al buscar: {str(e)}"


@tool
def get_product(product_id: str = "", product_name: str = "", injected_business_context: dict = None) -> str:
    """
    Get a single product by ID or by name/description. Supports flexible lookup:
    - "barracuda", "hamburguesa barracuda" -> finds Barracuda
    - "queso azul", "coca zero" -> finds by ingredients or partial name

    Args:
        product_id: Product UUID (preferred when known)
        product_name: Product name, ingredients, or partial description
    """
    logger.info(f"[ORDER_TOOL] get_product product_id='{product_id}' product_name='{product_name}'")
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
            return "❌ Producto no encontrado. Usa list_products para ver el menú."

        price_str = _format_price(product.get("price", 0), product.get("currency", "COP"))
        desc = product.get("description") or ""
        return f"**{product['name']}** - {price_str}\n" + (f"{desc}\n" if desc else "") + f"ID: {product['id']}"
    except Exception as e:
        logger.error(f"[ORDER_TOOL] get_product error: {e}")
        return f"❌ Error al buscar producto: {str(e)}"


@tool
def add_to_cart(product_id: str = "", product_name: str = "", quantity: int = 1, injected_business_context: dict = None) -> str:
    """
    Add a product to the cart. product_name supports flexible lookup by name or ingredients
    (e.g. "barracuda", "hamburguesa con queso azul", "coca zero").

    Args:
        product_id: Product UUID (preferred when known)
        product_name: Product name or description (flexible search)
        quantity: Quantity to add (default 1)
    """
    logger.info(f"[ORDER_TOOL] add_to_cart product_id='{product_id}' product_name='{product_name}' quantity={quantity}")
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
            return "❌ Producto no encontrado. Usa list_products para ver el menú."

        price = float(product.get("price", 0))
        pid = product["id"]
        name = product["name"]

        cart = _cart_from_session(wa_id, business_id)
        items: List[Dict] = list(cart.get("items") or [])

        # Update or add item
        found = False
        for it in items:
            if it.get("product_id") == pid:
                it["quantity"] = it.get("quantity", 0) + quantity
                found = True
                break
        if not found:
            items.append({
                "product_id": pid,
                "name": name,
                "price": price,
                "quantity": quantity,
            })

        total = sum(it.get("price", 0) * it.get("quantity", 0) for it in items)
        new_cart = {"items": items, "total": total}
        _save_cart(wa_id, business_id, new_cart)

        return f"✅ Agregado {quantity}x {name} al carrito. Total: {_format_price(total)}"
    except Exception as e:
        logger.error(f"[ORDER_TOOL] add_to_cart error: {e}")
        return f"❌ Error al agregar al carrito: {str(e)}"


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
        total = cart.get("total") or 0

        if not items:
            return "Tu carrito está vacío. ¿Qué te gustaría ordenar? Usa list_products para ver el menú."

        lines = []
        for it in items:
            price_str = _format_price(it.get("price", 0) * it.get("quantity", 0))
            pid = it.get("product_id", "")
            lines.append(f"• {it.get('quantity', 0)}x {it.get('name', '')} - {price_str} (ID: {pid})")

        return "Tu carrito:\n\n" + "\n".join(lines) + f"\n\n**Total: {_format_price(total)}**"
    except Exception as e:
        logger.error(f"[ORDER_TOOL] view_cart error: {e}")
        return f"❌ Error al ver el carrito: {str(e)}"


@tool
def update_cart_item(product_id: str = "", quantity: int = 0, injected_business_context: dict = None) -> str:
    """
    Update the quantity of an item in the cart. Use when the customer wants to change how many of something they want.
    If quantity is 0, the item is removed from the cart.

    Args:
        product_id: Product UUID to update
        quantity: New quantity (0 to remove)
    """
    logger.info(f"[ORDER_TOOL] update_cart_item product_id='{product_id}' quantity={quantity}")
    try:
        business_id, wa_id = _get_context(injected_business_context)
        if not business_id or not wa_id:
            return "❌ No se pudo identificar la sesión. Intenta de nuevo."

        if not product_id:
            return "❌ Indica el producto a modificar (product_id)."

        cart = _cart_from_session(wa_id, business_id)
        items: List[Dict] = [it for it in (cart.get("items") or []) if it.get("product_id") != product_id]

        if quantity > 0:
            # Find original item to keep price/name
            for it in (cart.get("items") or []):
                if it.get("product_id") == product_id:
                    items.append({
                        "product_id": product_id,
                        "name": it.get("name", ""),
                        "price": it.get("price", 0),
                        "quantity": quantity,
                    })
                    break

        total = sum(it.get("price", 0) * it.get("quantity", 0) for it in items)
        new_cart = {"items": items, "total": total}
        _save_cart(wa_id, business_id, new_cart)

        if quantity == 0:
            return "✅ Producto eliminado del carrito."
        return f"✅ Cantidad actualizada. Total: {_format_price(total)}"
    except Exception as e:
        logger.error(f"[ORDER_TOOL] update_cart_item error: {e}")
        return f"❌ Error al actualizar el carrito: {str(e)}"


@tool
def remove_from_cart(product_id: str = "", injected_business_context: dict = None) -> str:
    """
    Remove a product from the cart. Use when the customer corrects an item ("no de cereza", "quita eso").
    Get product_id from view_cart output (each item shows ID).

    Args:
        product_id: Product UUID to remove
    """
    logger.info(f"[ORDER_TOOL] remove_from_cart product_id='{product_id}'")
    try:
        business_id, wa_id = _get_context(injected_business_context)
        if not business_id or not wa_id:
            return "❌ No se pudo identificar la sesión. Intenta de nuevo."

        if not product_id:
            return "❌ Indica el producto a eliminar (product_id)."

        cart = _cart_from_session(wa_id, business_id)
        items = [it for it in (cart.get("items") or []) if it.get("product_id") != product_id]
        total = sum(it.get("price", 0) * it.get("quantity", 0) for it in items)
        new_cart = {"items": items, "total": total}
        _save_cart(wa_id, business_id, new_cart)
        return "✅ Producto eliminado del carrito."
    except Exception as e:
        logger.error(f"[ORDER_TOOL] remove_from_cart error: {e}")
        return f"❌ Error al eliminar del carrito: {str(e)}"


@tool
def get_customer_info(injected_business_context: dict = None) -> str:
    """
    Get customer information from the database. Call this ONLY after the customer has finished
    adding items and wants to proceed/confirm. Do NOT call while still taking the order.

    Returns structured data. Use the EXACT values returned - never invent placeholders like [dirección].
    - If address shows NO_REGISTRADA: ask the customer for their address.
    - If address shows a real value: confirm with the customer "¿Deseas recibir en [that exact address] o en otra?"
    """
    logger.info("[ORDER_TOOL] get_customer_info")
    try:
        _, wa_id = _get_context(injected_business_context)
        if not wa_id:
            return "❌ No se pudo identificar al cliente."

        cust = customer_service.get_customer(wa_id)
        if not cust:
            return (
                "NEW_CUSTOMER|name=Cliente|address=NO_REGISTRADA|phone=NO_REGISTRADO|payment=NO_REGISTRADO|"
                "Debes recolectar: dirección (obligatoria), teléfono (obligatorio, puede ser el mismo WhatsApp), medio de pago (obligatorio)."
            )

        name = cust.get("name") or "Cliente"
        address = (cust.get("address") or "").strip()
        phone = (cust.get("phone") or "").strip()
        payment = (cust.get("payment_method") or "").strip()

        addr_val = address if address else "NO_REGISTRADA"
        phone_val = phone if phone else "NO_REGISTRADO"
        pay_val = payment if payment else "NO_REGISTRADO"

        return f"RETURNING_CUSTOMER|name={name}|address={addr_val}|phone={phone_val}|payment={pay_val}"
    except Exception as e:
        logger.error(f"[ORDER_TOOL] get_customer_info error: {e}")
        return f"❌ Error al consultar datos: {str(e)}"


@tool
def submit_delivery_info(
    address: str,
    payment_method: str,
    phone: str = "",
    injected_business_context: dict = None,
) -> str:
    """
    Save delivery info. Call AFTER collecting from customer, BEFORE place_order.
    Required: address, payment_method. Phone: required for order; if customer uses same WhatsApp, pass wa_id.

    Args:
        address: Delivery address (required)
        payment_method: Payment method (e.g. Efectivo, Nequi, Tarjeta)
        phone: Contact phone - use WhatsApp number if customer says "mismo"/"este número"
    """
    logger.info(f"[ORDER_TOOL] submit_delivery_info address={bool(address)} payment={payment_method}")
    try:
        business_id, wa_id = _get_context(injected_business_context)
        if not business_id or not wa_id:
            return "❌ No se pudo identificar la sesión. Intenta de nuevo."

        if not address or not address.strip():
            return "❌ La dirección es requerida."

        if not payment_method or not payment_method.strip():
            return "❌ El medio de pago es requerido."

        delivery_info = {
            "address": address.strip(),
            "payment_method": payment_method.strip(),
        }
        if phone and str(phone).strip():
            delivery_info["phone"] = str(phone).strip()

        # Merge into order_context
        cart = _cart_from_session(wa_id, business_id)
        updated = {
            "items": cart.get("items") or [],
            "total": cart.get("total") or 0,
            "delivery_info": delivery_info,
        }
        _save_cart(wa_id, business_id, updated)
        return "✅ Datos de entrega guardados. Puedes confirmar el pedido con place_order."
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
            return "❌ Tu carrito está vacío. Agrega productos antes de confirmar el pedido."

        address = (delivery_info.get("address") or "").strip()
        payment_method = (delivery_info.get("payment_method") or "").strip()

        if not address or not payment_method:
            return (
                "MISSING_DELIVERY_INFO|Falta información para confirmar el pedido. "
                "Necesito: dirección de entrega (obligatoria), medio de pago (obligatorio), y teléfono de contacto (obligatorio; si es el mismo WhatsApp, indícalo y lo usamos). "
                "Usa submit_delivery_info cuando tengas dirección, teléfono y medio de pago."
            )

        order_items = [
            {
                "product_id": it.get("product_id"),
                "name": it.get("name"),
                "price": it.get("price", 0),
                "quantity": it.get("quantity", 1),
            }
            for it in items
        ]

        # contact_phone: use provided phone or WhatsApp number
        contact_phone = (delivery_info.get("phone") or "").strip() or wa_id

        result = product_order_service.create_order(
            business_id=business_id,
            whatsapp_id=wa_id,
            items=order_items,
            delivery_address=address,
            contact_phone=contact_phone,
            payment_method=payment_method,
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
        total = result.get("total", 0)
        return f"✅ ¡Pedido confirmado! Número de orden: {order_id[:8].upper()} Total: {_format_price(total)}. Nos pondremos en contacto pronto."
    except Exception as e:
        logger.error(f"[ORDER_TOOL] place_order error: {e}")
        return f"❌ Error al confirmar el pedido: {str(e)}"


# List of all order tools
order_tools = [
    list_products,
    search_products,
    get_product,
    add_to_cart,
    view_cart,
    update_cart_item,
    remove_from_cart,
    get_customer_info,
    submit_delivery_info,
    place_order,
]
