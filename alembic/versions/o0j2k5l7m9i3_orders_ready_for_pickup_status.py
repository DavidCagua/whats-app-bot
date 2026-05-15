"""orders: add 'ready_for_pickup' status and ready_at timestamp

Revision ID: o0j2k5l7m9i3
Revises: n9i1j4k6l8h2
Create Date: 2026-05-12 00:00:00.000000

Pickup orders had no intermediate state between 'confirmed' (kitchen is
preparing it) and 'completed' (customer picked it up), so the CS agent
could not tell a customer "your order is ready, come get it". The
operator UI also had no status to mark a pickup order as ready, forcing
operators to either skip straight to 'completed' (lies — the customer
hasn't arrived) or to mis-use 'out_for_delivery' (semantically wrong
for pickup).

State machine after this revision (enforced in
app/services/order_status_machine.py):

    delivery: pending → confirmed → out_for_delivery → completed
    pickup:   pending → confirmed → ready_for_pickup  → completed
    cancelled is reachable from any non-terminal state.

No backfill needed — existing pickup orders never transitioned through
this state, so nothing to rewrite.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "o0j2k5l7m9i3"
down_revision: Union[str, Sequence[str], None] = "n9i1j4k6l8h2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE must run outside a transaction on
    # PostgreSQL <= 11. Use alembic's autocommit_block so this works
    # against both old and new server versions.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE order_status "
            "ADD VALUE IF NOT EXISTS 'ready_for_pickup' BEFORE 'completed'"
        )

    op.execute(
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS "
        "ready_at TIMESTAMPTZ NULL"
    )


def downgrade() -> None:
    # Drop the column cleanly.
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS ready_at")

    # PostgreSQL does not support removing a value from an enum type.
    # Anyone running a real downgrade needs to:
    #   1. Confirm no rows hold orders.status = 'ready_for_pickup'
    #      (UPDATE them to 'confirmed' or 'cancelled' first).
    #   2. Rename the enum, create a new one without the value, swap
    #      the column type over, and drop the old enum.
    # Out of scope for the auto-downgrade path — flag for manual work.
    pass
