-- Rollback for migration 003
-- Remove display_name column from whatsapp_numbers table

ALTER TABLE whatsapp_numbers
DROP COLUMN IF EXISTS display_name;
