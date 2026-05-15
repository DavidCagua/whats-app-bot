"""business_customers join table — per-business customer profiles

Revision ID: g2b4c7d9e1a5
Revises: f1a3b6c2d8e9
Create Date: 2026-05-07

The `customers` table is global (one row per WhatsApp ID worldwide) which
breaks down as soon as a second business onboards: a number that orders
from two businesses currently shares one row, so name / address / phone
collide across tenants.

This revision introduces a `business_customers` join row keyed by
`(business_id, customer_id)`. Per-business overrides on
name/phone/address/payment_method live on the join row; the global
`customers` row stays as a fallback / canonical identity by `whatsapp_id`.

`source` distinguishes auto-linked rows (created by the order or booking
agents on first transaction) from manually created ones (the new admin-
console "Crear cliente" button), so future flows can treat them
differently if needed.

Backfill: every existing `(orders.business_id, orders.customer_id)` and
`(bookings.business_id, bookings.customer_id)` pair is materialized as a
join row with `source='auto'`. After this revision, the admin-console
customers query reads exclusively from `business_customers`, so the
backfill is what keeps Biela's existing customers visible on rollout.

Provider-side: purely additive. Safe to deploy ahead of consumer code.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "g2b4c7d9e1a5"
down_revision: Union[str, Sequence[str], None] = "f1a3b6c2d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "business_customers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "business_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("payment_method", sa.String(length=100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'auto'"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "business_id", "customer_id", name="uq_business_customers_pair"
        ),
    )
    op.create_index(
        "idx_business_customers_business_id",
        "business_customers",
        ["business_id"],
    )
    op.create_index(
        "idx_business_customers_customer_id",
        "business_customers",
        ["customer_id"],
    )

    # Backfill from orders and bookings. ON CONFLICT keeps the second
    # source-of-truth pass idempotent if both tables reference the same
    # (business, customer) pair.
    op.execute(
        """
        INSERT INTO business_customers (business_id, customer_id, source)
        SELECT DISTINCT o.business_id, o.customer_id, 'auto'
        FROM orders o
        WHERE o.customer_id IS NOT NULL
        ON CONFLICT (business_id, customer_id) DO NOTHING;
        """
    )
    op.execute(
        """
        INSERT INTO business_customers (business_id, customer_id, source)
        SELECT DISTINCT b.business_id, b.customer_id, 'auto'
        FROM bookings b
        WHERE b.customer_id IS NOT NULL
        ON CONFLICT (business_id, customer_id) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.drop_index(
        "idx_business_customers_customer_id", table_name="business_customers"
    )
    op.drop_index(
        "idx_business_customers_business_id", table_name="business_customers"
    )
    op.drop_table("business_customers")
