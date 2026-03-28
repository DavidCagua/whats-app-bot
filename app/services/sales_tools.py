"""
Sales tools for the Sales agent.
Reuses product browsing tools from order_tools; adds purchase contact tool.
"""

import logging
from typing import Optional, Dict
from langchain.tools import tool

from .order_tools import (
    get_menu_categories,
    list_category_products,
    search_products,
    get_product_details,
)

logger = logging.getLogger(__name__)

_PURCHASE_POLICY = (
    "No se crean pedidos en sistema por este chat. La venta queda lista cuando el cliente envía "
    "por aquí su nombre completo, dirección, ciudad y teléfono, y realiza el pago con el enlace "
    "que le envías."
)

_SHIPPING_NOTE = (
    "Los envíos suelen tardar aproximadamente 2 días hábiles en llegar. "
    "Se le informará cuando su pedido sea despachado."
)


def _purchase_instructions_body(
    payment_link: str,
    purchase_contact: str,
    phone: str,
    business_name: str,
) -> str:
    lines = [
        "Usa esto en tu respuesta al cliente (puedes redactarlo con naturalidad, sin omitir datos):",
        "",
        "1) Solicita: nombre completo, dirección, ciudad y teléfono.",
        f"2) {_PURCHASE_POLICY}",
        f"3) {_SHIPPING_NOTE}",
        "",
    ]
    if payment_link:
        lines.append(f"Enlace de pago (cópialo tal cual): {payment_link}")
    elif purchase_contact:
        lines.append(
            f"No hay enlace de pago configurado. Indica que coordine el pago con: {purchase_contact}"
        )
    elif phone:
        lines.append(
            f"No hay enlace de pago configurado. Indica que coordine el pago o la compra al {phone}."
        )
    else:
        lines.append(
            f"No hay enlace de pago ni contacto en configuración. Pide los datos y di que {business_name} "
            "le confirmará cómo pagar por este mismo chat."
        )
    return "\n".join(lines)


@tool
def get_purchase_contact(injected_business_context: dict = None) -> str:
    """
    Devuelve la política de compra (datos a pedir, enlace de pago, plazos de envío).
    Úsala cuando el usuario quiera comprar, pagar o cerrar la venta.
    """
    logger.info("[SALES_TOOL] get_purchase_contact")
    try:
        ctx = injected_business_context or {}
        settings = (ctx.get("business") or {}).get("settings") or {}
        business_name = (ctx.get("business") or {}).get("name") or "el negocio"

        payment_link = (settings.get("payment_link") or "").strip()
        purchase_contact = (settings.get("purchase_contact") or "").strip()
        phone = (settings.get("phone") or "").strip()

        return _purchase_instructions_body(
            payment_link, purchase_contact, phone, business_name
        )
    except Exception as e:
        logger.error(f"[SALES_TOOL] get_purchase_contact error: {e}")
        return (
            "Pide nombre completo, dirección, ciudad y teléfono. "
            "Indica plazo de envío aproximado de 2 días hábiles y que avisarán al despachar. "
            "Coordina el pago por este chat."
        )


# Tools exposed to the sales agent
sales_tools = [
    get_menu_categories,
    list_category_products,
    search_products,
    get_product_details,
    get_purchase_contact,
]
