"""
Customer-initiated mutations on existing orders.

Sibling to order_lookup_service.py (which is strictly read-only). Today
this only handles cancellation; address/payment/item edits will land
here when needed.

Every write goes through `order_status_machine.assert_transition()` so
illegal moves are rejected at the DB layer too — defence in depth on
top of the customer-policy gate at the agent layer.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .models import Order, get_db_session
from ..services.order_status_machine import (
    STATUS_CANCELLED,
    InvalidStatusTransition,
    assert_transition,
)


logger = logging.getLogger(__name__)


class OrderNotFound(Exception):
    """Raised when the target order doesn't exist."""


def cancel_order(
    order_id: str,
    reason: str,
    cancelled_by: str = "customer",
) -> Dict[str, Any]:
    """
    Move an order to `cancelled`. Sets `cancelled_at`, `cancelled_by`,
    and `cancellation_reason`. Rejects illegal transitions.

    Returns the updated order dict (`Order.to_dict()` shape) on success.
    Raises `InvalidStatusTransition` if the current status doesn't
    allow cancellation. Raises `OrderNotFound` if the id is unknown.

    `cancelled_by` defaults to 'customer' because this code path is
    invoked from the WhatsApp customer-service flow. The admin console
    cancels through its own server action and passes 'business'.

    Caller is responsible for the customer-vs-admin policy gate
    (see `order_modification_policy.can_customer_cancel`).
    """
    if not order_id:
        raise OrderNotFound("order_id is required")

    session = get_db_session()
    try:
        order = (
            session.query(Order)
            .filter(Order.id == uuid.UUID(order_id))
            .first()
        )
        if order is None:
            raise OrderNotFound(f"order {order_id} not found")

        # Defensive: enforce the state machine even though the agent
        # already checked the customer policy.
        assert_transition(order.status, STATUS_CANCELLED)

        now = datetime.now(timezone.utc)
        order.status = STATUS_CANCELLED
        order.cancelled_at = now
        order.cancelled_by = cancelled_by
        order.cancellation_reason = reason
        order.updated_at = now

        session.commit()
        result = order.to_dict()
        logger.info(
            "[ORDER_MOD] cancelled order=%s reason=%r prev_status=%s",
            order_id, reason, result.get("status"),
        )
        return result
    except InvalidStatusTransition:
        session.rollback()
        raise
    except OrderNotFound:
        session.rollback()
        raise
    except Exception as exc:
        logger.error("[ORDER_MOD] cancel_order failed: %s", exc, exc_info=True)
        session.rollback()
        raise
    finally:
        session.close()
