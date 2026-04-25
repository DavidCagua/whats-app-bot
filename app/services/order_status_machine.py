"""
Order status state machine.

Single source of truth for legal status transitions on the `orders`
table. Both the bot and the admin console must go through `transition()`
so we never end up with, e.g., a 'completed' order moved back to
'pending'.

State graph:
    pending ──────► confirmed ──────► out_for_delivery ──────► completed
       │              │                      │
       └────► cancelled ◄────────────────────┘

Pickup vs delivery: pickup orders skip 'out_for_delivery' and go
confirmed → completed directly. Same machine handles both.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Optional, Tuple


STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_OUT_FOR_DELIVERY = "out_for_delivery"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"

ALL_STATUSES: FrozenSet[str] = frozenset({
    STATUS_PENDING,
    STATUS_CONFIRMED,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_COMPLETED,
    STATUS_CANCELLED,
})

TERMINAL_STATUSES: FrozenSet[str] = frozenset({
    STATUS_COMPLETED,
    STATUS_CANCELLED,
})

_ALLOWED_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    STATUS_PENDING: frozenset({STATUS_CONFIRMED, STATUS_CANCELLED}),
    STATUS_CONFIRMED: frozenset({
        STATUS_OUT_FOR_DELIVERY,
        STATUS_COMPLETED,
        STATUS_CANCELLED,
    }),
    STATUS_OUT_FOR_DELIVERY: frozenset({STATUS_COMPLETED, STATUS_CANCELLED}),
    STATUS_COMPLETED: frozenset(),
    STATUS_CANCELLED: frozenset(),
}


class InvalidStatusTransition(ValueError):
    """Raised when a caller tries to move an order to a non-allowed state."""


def is_valid_status(status: Optional[str]) -> bool:
    return status in ALL_STATUSES


def is_terminal(status: Optional[str]) -> bool:
    return status in TERMINAL_STATUSES


def allowed_next(status: Optional[str]) -> FrozenSet[str]:
    """Return the set of states reachable from `status` in one step."""
    if status not in _ALLOWED_TRANSITIONS:
        return frozenset()
    return _ALLOWED_TRANSITIONS[status]


def can_transition(from_status: Optional[str], to_status: str) -> bool:
    return to_status in allowed_next(from_status)


def assert_transition(from_status: Optional[str], to_status: str) -> None:
    """Raise InvalidStatusTransition if the move isn't legal."""
    if not is_valid_status(to_status):
        raise InvalidStatusTransition(
            f"unknown target status: {to_status!r}"
        )
    if not can_transition(from_status, to_status):
        raise InvalidStatusTransition(
            f"cannot transition from {from_status!r} to {to_status!r}; "
            f"allowed: {sorted(allowed_next(from_status))}"
        )


def timestamp_field_for(status: str) -> Optional[str]:
    """
    Return the column name that should be set to NOW() when the order
    enters this status. None means no dedicated timestamp.

    `out_for_delivery` intentionally has no dedicated timestamp — it's
    a transient state and `confirmed_at`/`completed_at` bracket it.
    """
    return {
        STATUS_CONFIRMED: "confirmed_at",
        STATUS_COMPLETED: "completed_at",
        STATUS_CANCELLED: "cancelled_at",
    }.get(status)
