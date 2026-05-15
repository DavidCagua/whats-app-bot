"""orders: add analytics fields (created_via, cancelled_by, out_for_delivery_at)

Revision ID: p1k3l6m8n0j4
Revises: o0j2k5l7m9i3
Create Date: 2026-05-12 00:00:00.000000

Adds three columns the admin dashboard needs for KPIs that the current
schema can't express:

  created_via         — distinguishes bot-created orders from admin /
                        manual entries (KPI: "pedidos automáticos vs
                        totales"). Default 'bot' because every existing
                        order came in through the WhatsApp agent.
  cancelled_by        — who triggered the cancellation. NULL for
                        non-cancelled orders. Existing cancelled rows
                        stay NULL — we can't infer who pressed the
                        button retroactively. New cancellations start
                        populating this from the next deploy.
  out_for_delivery_at — timestamp the order moved into the courier
                        state. Existing rows that already passed through
                        this state stay NULL; new ones get stamped by
                        the status machine.

Delivery fee and SLA-for-demoras are not stored — the dashboard treats
them as constants (7000 COP, 50 min) in the KPI layer for now.

No data backfill — the dashboard treats pre-migration rows as "data
not yet captured" and shows the new KPIs from the cutover date forward.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "p1k3l6m8n0j4"
down_revision: Union[str, Sequence[str], None] = "o0j2k5l7m9i3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS created_via         VARCHAR(20) NOT NULL DEFAULT 'bot',
            ADD COLUMN IF NOT EXISTS cancelled_by        VARCHAR(20) NULL,
            ADD COLUMN IF NOT EXISTS out_for_delivery_at TIMESTAMPTZ NULL
        """
    )

    op.execute(
        "COMMENT ON COLUMN orders.created_via IS "
        "'How the order was created: bot (WhatsApp agent), admin (staff via console), manual (import).'"
    )
    op.execute(
        "COMMENT ON COLUMN orders.cancelled_by IS "
        "'Who triggered the cancellation: customer, business, or bot (automated).'"
    )
    op.execute(
        "COMMENT ON COLUMN orders.out_for_delivery_at IS "
        "'Set when status moves to out_for_delivery. Combined with confirmed_at / completed_at gives prep vs dispatch times.'"
    )

    op.execute("CREATE INDEX IF NOT EXISTS idx_orders_created_via ON orders (created_via)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_orders_cancelled_by ON orders (cancelled_by)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_orders_cancelled_by")
    op.execute("DROP INDEX IF EXISTS idx_orders_created_via")
    op.execute(
        """
        ALTER TABLE orders
            DROP COLUMN IF EXISTS out_for_delivery_at,
            DROP COLUMN IF EXISTS cancelled_by,
            DROP COLUMN IF EXISTS created_via
        """
    )
