-- Version: 004
-- Unified routing: look up by phone_number for both Meta and Twilio.
-- No provider column - Twilio numbers use phone_number_id = 'twilio:+123...'.
-- Index for phone_number lookups.
CREATE INDEX IF NOT EXISTS idx_whatsapp_numbers_phone_number
ON whatsapp_numbers(phone_number)
WHERE is_active = true;
