"""orders: native enum status, lifecycle timestamps, cancellation_reason

Revision ID: a1c2d3e4f5b6
Revises: 03d26bf9ed13
Create Date: 2026-04-25 00:00:00.000000

Post-venta flow needs richer order state.

Changes:
- Convert orders.status from VARCHAR(20) to a native Postgres ENUM
  `order_status` with values: pending, confirmed, out_for_delivery,
  completed, cancelled. Native enum gives us DB-level validation —
  invalid statuses can't sneak in via raw SQL or a buggy admin client.
- Add lifecycle timestamps: confirmed_at, completed_at, cancelled_at,
  so the customer service agent can answer "¿dónde está mi pedido?"
  with timing context.
- Add cancellation_reason TEXT — populated when status moves to
  'cancelled', so post-venta queries can explain why.

State machine (enforced in app/services/order_status_machine.py):
    pending → confirmed | cancelled
    confirmed → out_for_delivery | completed | cancelled
    out_for_delivery → completed | cancelled
    completed → (terminal)
    cancelled → (terminal)

Backfill: existing rows past 'pending' must have been confirmed at some
point; copy updated_at into confirmed_at. Same for completed/cancelled.

Note: bookings.status is left as VARCHAR — different lifecycle, out of
scope for this revision.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a1c2d3e4f5b6"
down_revision: Union[str, Sequence[str], None] = "03d26bf9ed13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ORDER_STATUS_VALUES = (
    "pending",
    "confirmed",
    "out_for_delivery",
    "completed",
    "cancelled",
)


def upgrade() -> None:
    # 1. Create the enum type. create_type=False on the column references
    #    below because we're managing the type explicitly here.
    order_status = postgresql.ENUM(*ORDER_STATUS_VALUES, name="order_status")
    order_status.create(op.get_bind(), checkfirst=True)

    # 2. Drop the old default before the type swap; Postgres can't cast
    #    a VARCHAR default into the new enum type implicitly.
    op.execute("ALTER TABLE orders ALTER COLUMN status DROP DEFAULT")

    # 3. Defensive: any legacy rows with an unknown status get coerced
    #    to 'pending'. Should be a no-op in practice.
    op.execute(
        "UPDATE orders SET status = 'pending' "
        f"WHERE status IS NULL OR status NOT IN ({', '.join(repr(v) for v in ORDER_STATUS_VALUES)})"
    )

    # 4. Convert the column type and re-apply the default.
    op.execute(
        "ALTER TABLE orders "
        "ALTER COLUMN status TYPE order_status USING status::order_status"
    )
    op.execute(
        "ALTER TABLE orders "
        "ALTER COLUMN status SET DEFAULT 'pending'::order_status"
    )

    # 5. Add new lifecycle columns.
    op.add_column("orders", sa.Column("cancellation_reason", sa.Text(), nullable=True))
    op.add_column("orders", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))

    # 6. Backfill timestamps from the prior coarse state.
    op.execute(
        "UPDATE orders SET completed_at = updated_at "
        "WHERE status = 'completed' AND completed_at IS NULL"
    )
    op.execute(
        "UPDATE orders SET cancelled_at = updated_at "
        "WHERE status = 'cancelled' AND cancelled_at IS NULL"
    )
    # Anything past 'pending' must have been confirmed at some point.
    op.execute(
        "UPDATE orders SET confirmed_at = updated_at "
        "WHERE status IN ('confirmed', 'out_for_delivery', 'completed') "
        "AND confirmed_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("orders", "cancelled_at")
    op.drop_column("orders", "completed_at")
    op.drop_column("orders", "confirmed_at")
    op.drop_column("orders", "cancellation_reason")

    op.execute("ALTER TABLE orders ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE orders "
        "ALTER COLUMN status TYPE VARCHAR(20) USING status::text"
    )
    op.execute(
        "ALTER TABLE orders "
        "ALTER COLUMN status SET DEFAULT 'pending'"
    )

    order_status = postgresql.ENUM(*ORDER_STATUS_VALUES, name="order_status")
    order_status.drop(op.get_bind(), checkfirst=True)
