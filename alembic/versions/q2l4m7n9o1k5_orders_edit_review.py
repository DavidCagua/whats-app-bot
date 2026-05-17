"""orders: add edit-review tracking (last_edited_at, last_edit_acknowledged_at)

Revision ID: q2l4m7n9o1k5
Revises: p1k3l6m8n0j4
Create Date: 2026-05-17 00:00:00.000000

Adds two timestamps the admin console uses to surface "this order was
edited by an operator — somebody should double-check it" warnings on the
orders table.

  last_edited_at              — stamped by the admin "edit order" server
                                action every time the row is saved.
                                Customer-initiated cart changes (via the
                                bot) DO NOT bump this — only operator
                                edits do.
  last_edit_acknowledged_at   — stamped when an operator clicks the
                                "Marcar revisado" inline button. Global,
                                not per-user — first acknowledger clears
                                the warning for everyone.

An order has an unacknowledged edit when:
  last_edited_at IS NOT NULL
  AND (last_edit_acknowledged_at IS NULL
       OR last_edit_acknowledged_at < last_edited_at)
  AND status NOT IN ('completed', 'cancelled')

Re-arm semantics: a subsequent edit bumps last_edited_at; the comparison
naturally flips back to "unacknowledged" without needing to null out the
acknowledgement column. The edit action also clears the ack timestamp on
write so a stale ack from a prior edit cycle doesn't accidentally cover
the new one if the clocks drift.

No data backfill — historical orders are treated as "no edits to review".

Index covers the banner-count query, which filters on status + the two
timestamps.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "q2l4m7n9o1k5"
down_revision: Union[str, Sequence[str], None] = "p1k3l6m8n0j4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS last_edited_at            TIMESTAMPTZ NULL,
            ADD COLUMN IF NOT EXISTS last_edit_acknowledged_at TIMESTAMPTZ NULL
        """
    )

    op.execute(
        "COMMENT ON COLUMN orders.last_edited_at IS "
        "'Stamped by the admin edit-order action. Customer-side cart "
        "modifications do not bump this — only operator edits do.'"
    )
    op.execute(
        "COMMENT ON COLUMN orders.last_edit_acknowledged_at IS "
        "'Stamped when an operator clicks Marcar revisado. Compared to "
        "last_edited_at to determine whether the warning still applies. "
        "Global ack (not per-user).'"
    )

    # Partial index for the unacknowledged-edits count query — only
    # active orders with a recorded edit can have an outstanding ack,
    # so the index stays small even on businesses with long history.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_orders_unacked_edits
        ON orders (business_id, last_edited_at, last_edit_acknowledged_at)
        WHERE last_edited_at IS NOT NULL
          AND status NOT IN ('completed', 'cancelled')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_orders_unacked_edits")
    op.execute(
        """
        ALTER TABLE orders
            DROP COLUMN IF EXISTS last_edit_acknowledged_at,
            DROP COLUMN IF EXISTS last_edited_at
        """
    )
