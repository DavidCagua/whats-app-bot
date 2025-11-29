-- Add display_name column to whatsapp_numbers table
-- Version: 003
-- Description: Add optional display_name field for friendly identification of WhatsApp numbers

-- Add display_name column
ALTER TABLE whatsapp_numbers
ADD COLUMN IF NOT EXISTS display_name VARCHAR(255);

-- Add comment explaining the column
COMMENT ON COLUMN whatsapp_numbers.display_name IS 'Optional friendly name to identify this WhatsApp number (e.g., "Main Line", "Support Line")';
