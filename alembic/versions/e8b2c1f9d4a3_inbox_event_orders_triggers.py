"""inbox_event NOTIFY triggers for orders + order_items

Revision ID: e8b2c1f9d4a3
Revises: d4f8a2b1c5e7
Create Date: 2026-05-03

Extends the inbox_event LISTEN channel established in d4f8a2b1c5e7 with
order lifecycle events so the admin-console orders page can drop SSR
re-fetching in favour of SSE pushes. Reusing the same channel keeps the
admin to a single LISTEN connection regardless of how many panes are
open.

Triggers fire on:
  - orders INSERT / UPDATE OF status, total_amount, delivery_address,
    contact_phone, payment_method
  - order_items INSERT / UPDATE / DELETE (re-resolves business_id via
    the parent order; no-ops on cascade-delete races)

Payloads share the inbox_event JSON shape but use type='order' with
order_id instead of message_id, so the existing subscriber filter logic
can route them by business_id alone.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "e8b2c1f9d4a3"
down_revision: Union[str, Sequence[str], None] = "d4f8a2b1c5e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION notify_inbox_order() RETURNS trigger AS $$
        BEGIN
            PERFORM pg_notify(
                'inbox_event',
                json_build_object(
                    'type',        'order',
                    'business_id', NEW.business_id,
                    'order_id',    NEW.id,
                    'status',      NEW.status,
                    'op',          TG_OP
                )::text
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION notify_inbox_order_item() RETURNS trigger AS $$
        DECLARE
            parent_order_id    uuid;
            parent_business_id uuid;
        BEGIN
            parent_order_id := COALESCE(NEW.order_id, OLD.order_id);
            SELECT business_id
              INTO parent_business_id
              FROM orders
              WHERE id = parent_order_id;

            IF parent_business_id IS NULL THEN
                RETURN COALESCE(NEW, OLD);
            END IF;

            PERFORM pg_notify(
                'inbox_event',
                json_build_object(
                    'type',        'order',
                    'business_id', parent_business_id,
                    'order_id',    parent_order_id,
                    'op',          'ITEM_' || TG_OP
                )::text
            );
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        DROP TRIGGER IF EXISTS orders_notify_inbox ON orders;
        CREATE TRIGGER orders_notify_inbox
            AFTER INSERT OR UPDATE OF status, total_amount, delivery_address, contact_phone, payment_method
            ON orders
            FOR EACH ROW EXECUTE FUNCTION notify_inbox_order();
        """
    )

    op.execute(
        """
        DROP TRIGGER IF EXISTS order_items_notify_inbox ON order_items;
        CREATE TRIGGER order_items_notify_inbox
            AFTER INSERT OR UPDATE OR DELETE ON order_items
            FOR EACH ROW EXECUTE FUNCTION notify_inbox_order_item();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS order_items_notify_inbox ON order_items")
    op.execute("DROP TRIGGER IF EXISTS orders_notify_inbox ON orders")
    op.execute("DROP FUNCTION IF EXISTS notify_inbox_order_item()")
    op.execute("DROP FUNCTION IF EXISTS notify_inbox_order()")
