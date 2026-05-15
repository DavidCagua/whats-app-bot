"""orders.fulfillment_type — pickup-vs-delivery flag

Revision ID: h3c5d8e0f2b6
Revises: g2b4c7d9e1a5
Create Date: 2026-05-09

Adds a single column to ``orders`` to record whether the order is for
delivery (the historical default) or pickup at the store. The order
flow's collection step branches on this: delivery still requires
name/address/phone/payment_method, pickup requires only name (the
WhatsApp ID covers phone, all payment methods are accepted on site).

Backfill is implicit — every existing row gets ``'delivery'`` via the
column default. CHECK constraint guards against typos slipping into
production. Index supports the eventual admin-console "pickup-only"
filter.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "h3c5d8e0f2b6"
down_revision: Union[str, Sequence[str], None] = "g2b4c7d9e1a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "fulfillment_type",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'delivery'"),
        ),
    )
    op.create_check_constraint(
        "orders_fulfillment_type_check",
        "orders",
        "fulfillment_type IN ('delivery', 'pickup')",
    )
    op.create_index(
        "idx_orders_fulfillment_type",
        "orders",
        ["fulfillment_type"],
    )


def downgrade() -> None:
    op.drop_index("idx_orders_fulfillment_type", table_name="orders")
    op.drop_constraint(
        "orders_fulfillment_type_check", "orders", type_="check"
    )
    op.drop_column("orders", "fulfillment_type")
