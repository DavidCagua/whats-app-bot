-- Rollback for Multi-Tenant WhatsApp Bot Schema Migration
-- Version: 001
-- Description: Rollback multi-tenant changes

-- ============================================================================
-- WARNING: This will delete all multi-tenant data!
-- Make sure you have a backup before running this rollback.
-- ============================================================================

-- Drop views
DROP VIEW IF EXISTS user_business_access;
DROP VIEW IF EXISTS business_whatsapp_numbers;

-- Remove foreign key constraints from existing tables
ALTER TABLE conversations DROP COLUMN IF EXISTS whatsapp_number_id;
ALTER TABLE conversations DROP COLUMN IF EXISTS business_id;
ALTER TABLE customers DROP COLUMN IF EXISTS business_id;

-- Drop triggers
DROP TRIGGER IF EXISTS update_users_updated_at ON users;
DROP TRIGGER IF EXISTS update_whatsapp_numbers_updated_at ON whatsapp_numbers;
DROP TRIGGER IF EXISTS update_businesses_updated_at ON businesses;

-- Drop new tables (cascade will handle foreign keys)
DROP TABLE IF EXISTS user_businesses CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS whatsapp_numbers CASCADE;
DROP TABLE IF EXISTS businesses CASCADE;

-- Drop trigger function
DROP FUNCTION IF EXISTS update_updated_at_column();

-- ============================================================================
-- ROLLBACK COMPLETE
-- ============================================================================
