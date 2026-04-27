"""
Single source of truth for order timing estimates.

The order agent quotes a delivery range at order-placement time
(NOMINAL_RANGE_TEXT). Later, when the customer asks "cuánto se demora?",
the customer service agent must report a number that lines up with that
original promise — otherwise the customer reads two different ETAs in
the same conversation.

Both agents must read from this module. Numbers tuned for a small
restaurant with in-house delivery; override per-business via settings
when that becomes necessary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


# Customer-facing range quoted at order placement. Keep this in sync
# with NOMINAL_TOTAL_MINUTES — the midpoint of the range should equal
# (or be close to) NOMINAL_TOTAL_MINUTES.
NOMINAL_RANGE_TEXT = "40 a 50 minutos"
NOMINAL_TOTAL_MINUTES = 45

# Per-state remaining-wait budgets (minutes). Anchored to the original
# 40-50 min promise so the second answer never undercuts the first.
_BUDGETS_MINUTES: Dict[str, int] = {
    # Just placed, business hasn't acted yet — quote the original promise.
    "pending": NOMINAL_TOTAL_MINUTES,
    # Business accepted; subtract elapsed since confirmation.
    "confirmed": NOMINAL_TOTAL_MINUTES,
    # Courier dispatched; the typical last-leg.
    "out_for_delivery": 10,
}

# Floor so the bot never says "0 min" while the order is still in-flight.
_FLOOR_MINUTES = 5


def estimate_remaining_minutes(order: Dict[str, Any]) -> Optional[int]:
    """
    Approximate remaining wait time, in minutes, for a non-terminal order.

    - `confirmed`: budget minus elapsed since `confirmed_at`, floored.
    - `pending` / `out_for_delivery`: flat budget (no reliable per-state
      timestamp to subtract from).
    - terminal (`completed`, `cancelled`) or unknown: None.
    """
    status = order.get("status")
    budget = _BUDGETS_MINUTES.get(status)
    if budget is None:
        return None

    if status == "confirmed":
        confirmed_at = _parse_iso(order.get("confirmed_at"))
        if confirmed_at is not None:
            elapsed_min = int((datetime.now(timezone.utc) - confirmed_at).total_seconds() // 60)
            return max(_FLOOR_MINUTES, budget - max(0, elapsed_min))

    return budget


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
