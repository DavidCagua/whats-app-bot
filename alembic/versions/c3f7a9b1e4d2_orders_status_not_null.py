"""orders: enforce NOT NULL on status column

Revision ID: c3f7a9b1e4d2
Revises: b2d4e6f8a0c1
Create Date: 2026-04-26 22:00:00.000000

The Order SQLAlchemy model declares `status` as nullable=False, but the
prior migration (a1c2d3e4f5b6) only converted the column type from
VARCHAR(20) to the order_status ENUM via ALTER COLUMN ... TYPE. It
forgot to add SET NOT NULL alongside, leaving a model/DB drift the
alembic-drift CI check now flags.

Defensive backfill before the constraint: any null status defaults to
'pending'. In practice this is a no-op (every Order row was inserted
with status='pending' or later transitioned to a real state via the
admin console / customer cancel path), but guards manual SQL inserts
that bypassed the model's default.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c3f7a9b1e4d2"
down_revision: Union[str, Sequence[str], None] = "b2d4e6f8a0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ORDER_STATUS_ENUM = postgresql.ENUM(
    "pending", "confirmed", "out_for_delivery", "completed", "cancelled",
    name="order_status",
    create_type=False,
)


def upgrade() -> None:
    op.execute("UPDATE orders SET status = 'pending' WHERE status IS NULL")
    op.alter_column(
        "orders",
        "status",
        existing_type=_ORDER_STATUS_ENUM,
        nullable=False,
        existing_server_default=sa.text("'pending'::order_status"),
    )


def downgrade() -> None:
    op.alter_column(
        "orders",
        "status",
        existing_type=_ORDER_STATUS_ENUM,
        nullable=True,
        existing_server_default=sa.text("'pending'::order_status"),
    )
