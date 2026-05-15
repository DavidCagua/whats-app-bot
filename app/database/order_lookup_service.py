"""
Read-only order lookups for the customer service agent.

Separate from product_order_service.py because:
- product_order_service handles WRITES (create_order, etc.) and the
  customer service agent must not be coupled to that surface.
- Reads here are narrow: "latest order for wa_id", "last N orders".
  Full order details come from the existing Order.to_dict() shape.
"""

import logging
from typing import List, Optional, Dict, Any

from sqlalchemy.orm import joinedload

from .models import Order, get_db_session


logger = logging.getLogger(__name__)


def get_latest_order(
    wa_id: str,
    business_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Return the user's most recent order at this business, or None.

    Result dict shape (from Order.to_dict()) includes:
      id, business_id, customer_id, whatsapp_id, status, total_amount,
      notes, delivery_address, contact_phone, payment_method,
      created_at, updated_at.
    Plus an "items" list of order line items.
    """
    if not wa_id or not business_id:
        return None

    session = get_db_session()
    try:
        order = (
            session.query(Order)
            .options(joinedload(Order.order_items))
            .filter(
                Order.whatsapp_id == wa_id,
                Order.business_id == business_id,
            )
            .order_by(Order.created_at.desc())
            .first()
        )
        if not order:
            return None
        return _order_to_dict(order)
    except Exception as exc:
        logger.error("[ORDER_LOOKUP] get_latest_order failed: %s", exc, exc_info=True)
        return None
    finally:
        session.close()


def get_order_history(
    wa_id: str,
    business_id: str,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """
    Return the user's last N orders at this business, newest first.
    Empty list when the user has no orders.
    """
    if not wa_id or not business_id:
        return []

    session = get_db_session()
    try:
        orders = (
            session.query(Order)
            .options(joinedload(Order.order_items))
            .filter(
                Order.whatsapp_id == wa_id,
                Order.business_id == business_id,
            )
            .order_by(Order.created_at.desc())
            .limit(limit)
            .all()
        )
        return [_order_to_dict(o) for o in orders]
    except Exception as exc:
        logger.error("[ORDER_LOOKUP] get_order_history failed: %s", exc, exc_info=True)
        return []
    finally:
        session.close()


def get_order_by_id(order_id: str) -> Optional[Dict[str, Any]]:
    """Fetch one order by UUID. Returns None if not found."""
    if not order_id:
        return None

    session = get_db_session()
    try:
        order = (
            session.query(Order)
            .options(joinedload(Order.order_items))
            .filter(Order.id == order_id)
            .first()
        )
        if not order:
            return None
        return _order_to_dict(order)
    except Exception as exc:
        logger.error("[ORDER_LOOKUP] get_order_by_id failed: %s", exc, exc_info=True)
        return None
    finally:
        session.close()


def _order_to_dict(order: Order) -> Dict[str, Any]:
    """Serialize an Order + its items into a narrow dict for the customer service agent."""
    base = order.to_dict() if hasattr(order, "to_dict") else {}
    items = []
    for item in (order.order_items or []):
        # Surface the product name so CS replies can render
        # "1x BARRACUDA — $28.000" when the customer asks for the
        # per-item breakdown of a placed order. Lazy-loaded from
        # the OrderItem.product relationship; on rare detached-session
        # paths fall back to product_id-only.
        try:
            product_name = item.product.name if item.product else None
        except Exception:
            product_name = None
        items.append({
            "product_id": str(item.product_id) if item.product_id else None,
            "name": product_name,
            "quantity": int(item.quantity or 0),
            "unit_price": float(item.unit_price or 0),
            "line_total": float(item.line_total or 0),
            "notes": (item.notes or "").strip() or None,
        })
    base["items"] = items
    return base
