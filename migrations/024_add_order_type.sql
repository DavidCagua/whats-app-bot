-- Migration 024: Add order_type to orders (delivery | pickup).
--
-- Phase 0 of the pickup-on-site flow. Pure schema addition: every existing
-- order is backfilled to 'delivery' (current behavior), and every new
-- order writes 'delivery' explicitly until the planner / executor learns
-- to detect pickup. No code path branches on this column yet.
--
-- The status machine already supports the pickup path (confirmed →
-- completed without out_for_delivery) — see app/services/order_status_machine.py.

-- 1. Add the column with a server-side default and backfill.
ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS order_type TEXT
    NOT NULL DEFAULT 'delivery';

-- 2. Constrain values so a typo can't slip into the column.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'orders_order_type_check'
    ) THEN
        ALTER TABLE orders
            ADD CONSTRAINT orders_order_type_check
            CHECK (order_type IN ('delivery', 'pickup'));
    END IF;
END$$;

-- 3. Index for admin queries that filter by type. Light cost (single
-- character values, mostly 'delivery') but useful once the operator
-- console adds a "pickup only" filter.
CREATE INDEX IF NOT EXISTS idx_orders_order_type ON orders(order_type);
