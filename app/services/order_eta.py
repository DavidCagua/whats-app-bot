"""
Single source of truth for order timing estimates.

The order agent quotes a delivery range at order-placement time
(NOMINAL_RANGE_TEXT). Later, when the customer asks "cuánto se demora?",
the customer service agent must report a number that lines up with that
original promise — otherwise the customer reads two different ETAs in
the same conversation.

Both agents must read from this module. Numbers tuned for a small
restaurant with in-house delivery; the operator can override the
delivery ETA from the orders page (businesses.settings.delivery_eta_minutes
+ delivery_eta_until). Pickup ETA is fixed — pickup wait depends on the
kitchen, not delivery load, so it doesn't move with the override.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


# Customer-facing range quoted at order placement when no operator
# override is active. Keep NOMINAL_TOTAL_MINUTES the midpoint of the
# range. Range width (10 min) is also what the override uses when the
# operator picks a lower bound.
NOMINAL_RANGE_TEXT = "40 a 50 minutos"
NOMINAL_TOTAL_MINUTES = 45
NOMINAL_LOWER_MINUTES = 40
NOMINAL_UPPER_MINUTES = 50
ETA_RANGE_WIDTH_MINUTES = 10

# Pickup is faster than delivery — no last-mile leg, just kitchen prep.
# Surfaced on pickup receipts and on "¿cuánto se demora?" follow-ups
# for pickup orders. Keep PICKUP_TOTAL_MINUTES the midpoint of the
# range so the two numbers don't drift.
PICKUP_RANGE_TEXT = "15 a 20 minutos"
PICKUP_TOTAL_MINUTES = 17

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

# Pickup orders never reach 'out_for_delivery' (they go confirmed →
# completed when the customer walks in). Both pre-completion states
# share the smaller pickup budget so a customer who asks "cuánto se
# demora?" on a pickup order doesn't get the 45-min delivery answer.
_PICKUP_BUDGETS_MINUTES: Dict[str, int] = {
    "pending": PICKUP_TOTAL_MINUTES,
    "confirmed": PICKUP_TOTAL_MINUTES,
}

# Floor so the bot never says "0 min" while the order is still in-flight.
_FLOOR_MINUTES = 5


def resolve_delivery_eta(
    business_settings: Optional[Dict[str, Any]],
    now_utc: Optional[datetime] = None,
) -> Tuple[int, int, str, bool]:
    """
    Resolve the active customer-facing delivery ETA for this business.

    Returns ``(lower_minutes, upper_minutes, range_text, is_override)``:
      - ``(40, 50, "40 a 50 minutos", False)`` when no override is active.
      - ``(70, 80, "1 hora 10 minutos a 1 hora 20 minutos", True)`` when
        the operator set delivery_eta_minutes=70 on the orders page.

    Override sources (businesses.settings):
      - ``delivery_eta_minutes``: int lower bound (60..240). Anything
        outside that range, or <= NOMINAL_LOWER_MINUTES, is treated as
        unset — we don't promise faster than the nominal.
      - ``delivery_eta_until``: ISO timestamp. Past that moment the
        override is ignored. Lets the dashboard auto-expire at end of
        business day without a cron.

    Affects delivery only. Pickup keeps PICKUP_RANGE_TEXT — that
    distinction is enforced by callers; this function doesn't know
    fulfillment_type.
    """
    if not business_settings or not isinstance(business_settings, dict):
        return (
            NOMINAL_LOWER_MINUTES,
            NOMINAL_UPPER_MINUTES,
            NOMINAL_RANGE_TEXT,
            False,
        )

    raw_minutes = business_settings.get("delivery_eta_minutes")
    try:
        lower = int(raw_minutes) if raw_minutes is not None else 0
    except (TypeError, ValueError):
        lower = 0

    if lower <= NOMINAL_LOWER_MINUTES or lower > 240:
        return (
            NOMINAL_LOWER_MINUTES,
            NOMINAL_UPPER_MINUTES,
            NOMINAL_RANGE_TEXT,
            False,
        )

    until = _parse_iso(business_settings.get("delivery_eta_until"))
    if until is not None:
        now = now_utc or datetime.now(timezone.utc)
        if now >= until:
            return (
                NOMINAL_LOWER_MINUTES,
                NOMINAL_UPPER_MINUTES,
                NOMINAL_RANGE_TEXT,
                False,
            )

    upper = lower + ETA_RANGE_WIDTH_MINUTES
    return (lower, upper, format_eta_range_spanish(lower, upper), True)


def format_eta_range_spanish(lower: int, upper: int) -> str:
    """
    Render a delivery-ETA range for customer-facing copy.

    Up to 59 min: "40 a 50 minutos" (matches the nominal phrasing).
    60+ min: "1 hora 10 minutos a 1 hora 20 minutos" — operator and
    customer see the same numbers the dashboard dropdown displays.
    """
    if upper < 60:
        return f"{lower} a {upper} minutos"
    return f"{_format_minutes_spanish(lower)} a {_format_minutes_spanish(upper)}"


def _format_minutes_spanish(minutes: int) -> str:
    """45 → '45 minutos'; 70 → '1 hora 10 minutos'; 60 → '1 hora'; 120 → '2 horas'."""
    if minutes < 60:
        return f"{minutes} minutos"
    hours, rem = divmod(minutes, 60)
    hour_word = "hora" if hours == 1 else "horas"
    if rem == 0:
        return f"{hours} {hour_word}"
    return f"{hours} {hour_word} {rem} minutos"


def estimate_remaining_minutes(
    order: Dict[str, Any],
    business_settings: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """
    Approximate remaining wait time, in minutes, for a non-terminal order.

    - `confirmed`: budget minus elapsed since `confirmed_at`, floored.
    - `pending` / `out_for_delivery`: flat budget (no reliable per-state
      timestamp to subtract from).
    - terminal (`completed`, `cancelled`) or unknown: None.

    Pickup orders use the smaller pickup budget on every status so the
    answer matches what was promised on the receipt. Delivery orders
    honor the operator override when ``business_settings`` is passed in
    (delivery_eta_minutes / delivery_eta_until); otherwise fall back to
    the nominal 45-min budget.
    """
    status = order.get("status")
    is_pickup = (order.get("fulfillment_type") or "delivery") == "pickup"
    if is_pickup:
        budget = _PICKUP_BUDGETS_MINUTES.get(status)
    else:
        lower, _, _, is_override = resolve_delivery_eta(business_settings)
        # Override only inflates the pre-dispatch budgets (pending /
        # confirmed). out_for_delivery is a courier-leg flat budget —
        # the kitchen delay isn't relevant once they're on the road.
        if status in ("pending", "confirmed"):
            budget = lower if is_override else _BUDGETS_MINUTES.get(status)
        else:
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
