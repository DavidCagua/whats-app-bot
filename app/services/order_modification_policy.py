"""
Customer-facing modification policy for orders.

`order_status_machine` describes every status transition the system
allows — including admin-only ones (e.g. courier marks an order
cancelled because the customer wasn't home, which is a legitimate
out_for_delivery → cancelled). This module is narrower: it answers
"which modifications can THE CUSTOMER trigger from WhatsApp?"

Today: cancellation only. Item edits and address changes are deferred.
"""

from __future__ import annotations

from typing import FrozenSet, Optional

from .order_status_machine import (
    STATUS_PENDING,
    STATUS_CONFIRMED,
)


# Statuses from which the customer is allowed to cancel via the bot.
# `out_for_delivery` is intentionally excluded — once the courier has
# the order, cancellation must go through the business by phone.
CUSTOMER_CANCELLABLE_STATUSES: FrozenSet[str] = frozenset({
    STATUS_PENDING,
    STATUS_CONFIRMED,
})


def can_customer_cancel(status: Optional[str]) -> bool:
    return status in CUSTOMER_CANCELLABLE_STATUSES


# Reason strings that get persisted on the order. Keeping these as
# constants so analytics queries against `cancellation_reason` aren't
# parsing free text.
CANCEL_REASON_CUSTOMER_WHATSAPP = "Cancelado por el cliente vía WhatsApp"
