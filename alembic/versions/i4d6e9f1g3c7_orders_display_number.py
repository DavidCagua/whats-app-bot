"""orders.display_number — per-business, per-day human-facing counter

Revision ID: i4d6e9f1g3c7
Revises: h3c5d8e0f2b6
Create Date: 2026-05-09

The UUID PK stays as-is (it's load-bearing — referenced by order_items,
order_promotions, the bot's tool calls, the print URL, SSE payloads,
audit logs). This adds a parallel "display number" the cashier and
customer can actually say out loud: pedido #001 the first one of the
day, #002 the second, etc., resetting at Bogotá midnight.

Two rows are added to the orders table plus a small per-business+day
counter table that serializes concurrent allocations:

  orders.display_number INT — 1, 2, 3, … resets per (business, day)
  orders.display_date   DATE — Bogotá-local date this counter belongs to
  UNIQUE (business_id, display_date, display_number) — race safety net

  order_counters (business_id, display_date, last_value)
  ON CONFLICT DO UPDATE SET last_value = last_value + 1 RETURNING last_value
  — the row-level lock during the UPSERT serializes concurrent inserts
  with no application-level retry logic and no SERIALIZABLE isolation.

Backfill computes display_number for every existing row using
ROW_NUMBER() partitioned by (business, Bogotá-day, ordered by created_at)
and seeds order_counters from the per-day max so new orders pick up
where the historical numbering left off.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "i4d6e9f1g3c7"
down_revision: Union[str, Sequence[str], None] = "h3c5d8e0f2b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add the two new columns as nullable; backfill; flip to NOT NULL.
    op.add_column(
        "orders",
        sa.Column("display_number", sa.Integer(), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("display_date", sa.Date(), nullable=True),
    )

    # 2. Per-business+day counter table that owns the next-value lock.
    op.create_table(
        "order_counters",
        sa.Column("business_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_date", sa.Date(), nullable=False),
        sa.Column("last_value", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("business_id", "display_date"),
    )

    # 3. Backfill display_date and display_number on existing rows. Bogotá
    #    is UTC-5 year-round (no DST), so the offset arithmetic is stable.
    op.execute(
        """
        UPDATE orders SET
          display_date = (created_at AT TIME ZONE 'America/Bogota')::date,
          display_number = sub.rn
        FROM (
          SELECT
            id,
            ROW_NUMBER() OVER (
              PARTITION BY business_id, (created_at AT TIME ZONE 'America/Bogota')::date
              ORDER BY created_at, id
            ) AS rn
          FROM orders
        ) AS sub
        WHERE orders.id = sub.id;
        """
    )

    # 4. Seed order_counters from the historical per-day max so the next
    #    bot- or admin-created order picks up at MAX + 1.
    op.execute(
        """
        INSERT INTO order_counters (business_id, display_date, last_value)
        SELECT business_id, display_date, MAX(display_number)
        FROM orders
        WHERE display_number IS NOT NULL
        GROUP BY business_id, display_date;
        """
    )

    # 5. Flip both columns to NOT NULL now that every row has a value.
    op.alter_column("orders", "display_number", nullable=False)
    op.alter_column("orders", "display_date", nullable=False)

    # 6. Uniqueness guarantee — the safety net behind the counter UPSERT.
    op.create_unique_constraint(
        "orders_business_display_unique",
        "orders",
        ["business_id", "display_date", "display_number"],
    )

    # 7. Index on (business_id, display_date) for the daily-counter lookup
    #    path (count today's orders, list today's orders, etc.).
    op.create_index(
        "idx_orders_business_display_date",
        "orders",
        ["business_id", "display_date"],
    )


def downgrade() -> None:
    op.drop_index("idx_orders_business_display_date", table_name="orders")
    op.drop_constraint("orders_business_display_unique", "orders", type_="unique")
    op.drop_column("orders", "display_date")
    op.drop_column("orders", "display_number")
    op.drop_table("order_counters")
