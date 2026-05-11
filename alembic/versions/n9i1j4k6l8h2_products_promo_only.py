"""products.promo_only — flag products that only exist inside promotions

Revision ID: n9i1j4k6l8h2
Revises: m8h0i3j5k7g1
Create Date: 2026-05-11

Some products (e.g. Biela's "Oregon burger") are only meant to be sold
as part of a configured Promotion — never offered individually. Setting
``is_active=false`` previously hid the product from the bot's catalog,
but it also hid it from the admin promo picker AND made the order-creation
path reject orders containing it. Both broke the promo flow.

New boolean lets us keep the row ``is_active=true`` (so lookups by ID in
the cart-add and order-creation paths succeed) while filtering the
product out of every bot-facing *discovery* surface — search, catalog
listing, category listing. Defaults to FALSE so existing rows are
unaffected.

The 6 discovery queries in app/services/product_search.py and
app/database/product_order_service.py add ``AND promo_only = FALSE`` in
the same migration as the column flip. Order-creation and add-to-cart
paths intentionally do NOT filter on this — by the time a promo-bound
item is in the cart, it's already been vetted by the agent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "n9i1j4k6l8h2"
down_revision: Union[str, Sequence[str], None] = "m8h0i3j5k7g1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "promo_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Partial index — most products are not promo-only, so a small
    # partial index on the TRUE rows is cheaper than a full one and
    # serves the admin "list promo-only items" query if/when it lands.
    op.create_index(
        "idx_products_promo_only_true",
        "products",
        ["business_id"],
        postgresql_where=sa.text("promo_only = TRUE"),
    )


def downgrade() -> None:
    op.drop_index("idx_products_promo_only_true", table_name="products")
    op.drop_column("products", "promo_only")
