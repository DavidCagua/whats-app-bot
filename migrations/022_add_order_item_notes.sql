-- Migration 022: Add per-item notes to order_items
-- Stores customization notes captured by the WhatsApp bot
-- (e.g. "sin cebolla crispy", "extra salsa") at the line-item level
-- so kitchen / fulfillment can see them alongside each product.

ALTER TABLE order_items
    ADD COLUMN IF NOT EXISTS notes TEXT;
