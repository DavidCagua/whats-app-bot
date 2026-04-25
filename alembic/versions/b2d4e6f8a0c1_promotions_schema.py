"""promotions: structured promos, components, order bindings + audit

Revision ID: b2d4e6f8a0c1
Revises: a1c2d3e4f5b6
Create Date: 2026-04-25 00:00:00.000000

Phase 1 of structured promotions.

Tables:
- promotions:           the rule. Pricing mode (fixed_price | discount_amount
                        | discount_pct, exactly one), schedule (days,
                        time window, date window), is_active.
- promotion_components: which products + qty define the promo. A promo
                        with zero components matches any cart (e.g.
                        "10% off everything Mondays").
- order_promotions:     audit row per applied promo on an order. Snapshots
                        promo_name so analytics survive rename/delete.

Column adds:
- orders.promo_discount_amount   — total $ saved on this order. Receipts
                                   read this directly instead of joining.
- order_items.promotion_id       — which promo this line "belongs to".
                                   NULL for unbundled items.
- order_items.promo_group_id     — random UUID per promo application.
                                   Distinguishes two applications of the
                                   same promo on the same order.

The matcher logic lives in app/services/promotion_service.py. This
revision is schema only.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b2d4e6f8a0c1"
down_revision: Union[str, Sequence[str], None] = "a1c2d3e4f5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── promotions ────────────────────────────────────────────────
    op.create_table(
        "promotions",
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
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        # Pricing mode — exactly one is non-null (CHECK below).
        sa.Column("fixed_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("discount_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("discount_pct", sa.SmallInteger(), nullable=True),
        # Schedule. NULL means "no constraint on this dimension".
        sa.Column("days_of_week", postgresql.ARRAY(sa.SmallInteger()), nullable=True),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=True),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "(fixed_price IS NOT NULL)::int "
            "+ (discount_amount IS NOT NULL)::int "
            "+ (discount_pct IS NOT NULL)::int = 1",
            name="promotions_one_pricing_mode",
        ),
        sa.CheckConstraint(
            "discount_pct IS NULL OR (discount_pct > 0 AND discount_pct <= 100)",
            name="promotions_pct_in_range",
        ),
    )
    op.create_index(
        "idx_promotions_business_active",
        "promotions",
        ["business_id", "is_active"],
    )

    # ── promotion_components ──────────────────────────────────────
    op.create_table(
        "promotion_components",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "promotion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("promotions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "quantity",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.CheckConstraint(
            "quantity > 0", name="promotion_components_qty_positive"
        ),
        sa.UniqueConstraint(
            "promotion_id", "product_id", name="promotion_components_unique_product"
        ),
    )
    op.create_index(
        "idx_promotion_components_promotion",
        "promotion_components",
        ["promotion_id"],
    )

    # ── order_promotions (audit) ──────────────────────────────────
    op.create_table(
        "order_promotions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "promotion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("promotions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # Snapshots so analytics survive a promo rename/delete.
        sa.Column("promotion_name", sa.String(length=120), nullable=False),
        sa.Column("pricing_mode", sa.String(length=20), nullable=False),
        sa.Column("discount_applied", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("idx_order_promotions_order", "order_promotions", ["order_id"])
    op.create_index("idx_order_promotions_promotion", "order_promotions", ["promotion_id"])

    # ── orders / order_items columns ──────────────────────────────
    op.add_column(
        "orders",
        sa.Column(
            "promo_discount_amount",
            sa.Numeric(12, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "order_items",
        sa.Column(
            "promotion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("promotions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "order_items",
        sa.Column("promo_group_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "idx_order_items_promotion_id",
        "order_items",
        ["promotion_id"],
    )
    op.create_index(
        "idx_order_items_promo_group_id",
        "order_items",
        ["promo_group_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_order_items_promo_group_id", table_name="order_items")
    op.drop_index("idx_order_items_promotion_id", table_name="order_items")
    op.drop_column("order_items", "promo_group_id")
    op.drop_column("order_items", "promotion_id")
    op.drop_column("orders", "promo_discount_amount")

    op.drop_index("idx_order_promotions_promotion", table_name="order_promotions")
    op.drop_index("idx_order_promotions_order", table_name="order_promotions")
    op.drop_table("order_promotions")

    op.drop_index("idx_promotion_components_promotion", table_name="promotion_components")
    op.drop_table("promotion_components")

    op.drop_index("idx_promotions_business_active", table_name="promotions")
    op.drop_table("promotions")
