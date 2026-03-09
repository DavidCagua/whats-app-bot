-- Migration: Make phone_number_id optional in whatsapp_numbers table
-- This allows businesses to use WhatsApp without Meta's phone_number_id
-- (useful for Twilio or other providers that don't use this identifier)

-- Make phone_number_id nullable and remove unique constraint
ALTER TABLE whatsapp_numbers
ALTER COLUMN phone_number_id DROP NOT NULL;

-- Drop the unique constraint on phone_number_id
-- First, find and drop the constraint
DO $$
DECLARE
    constraint_name TEXT;
BEGIN
    -- Find the unique constraint name
    SELECT conname INTO constraint_name
    FROM pg_constraint
    WHERE conrelid = 'whatsapp_numbers'::regclass
    AND contype = 'u'
    AND array_length(conkey, 1) = 1
    AND conkey[1] = (
        SELECT attnum FROM pg_attribute
        WHERE attrelid = 'whatsapp_numbers'::regclass
        AND attname = 'phone_number_id'
    );
    
    -- Drop the constraint if found
    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE whatsapp_numbers DROP CONSTRAINT %I', constraint_name);
    END IF;
END $$;

-- Add a unique constraint that only applies when phone_number_id is not null
CREATE UNIQUE INDEX IF NOT EXISTS whatsapp_numbers_phone_number_id_key 
ON whatsapp_numbers(phone_number_id) 
WHERE phone_number_id IS NOT NULL;

-- Make phone_number unique per business (to prevent duplicate entries)
CREATE UNIQUE INDEX IF NOT EXISTS whatsapp_numbers_business_phone_unique
ON whatsapp_numbers(business_id, phone_number);
