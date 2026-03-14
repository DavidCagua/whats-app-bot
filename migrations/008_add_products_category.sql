-- Migration 008: Add category column to products

ALTER TABLE products ADD COLUMN IF NOT EXISTS category VARCHAR(50);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
