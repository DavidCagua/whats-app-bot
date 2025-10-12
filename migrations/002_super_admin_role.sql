-- Add Super Admin Role Support
-- Version: 002
-- Description: Add role column to users table for super admin access control

-- ============================================================================
-- 1. ADD ROLE COLUMN TO USERS TABLE
-- ============================================================================

-- Add role column with default 'staff'
ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(50) DEFAULT 'staff';

-- ============================================================================
-- 2. CREATE INDEX FOR ROLE LOOKUPS
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

-- ============================================================================
-- 3. CREATE FIRST SUPER ADMIN USER
-- ============================================================================

-- Insert super admin user (password: 'admin123')
-- Password hash generated with bcrypt rounds=12
INSERT INTO users (email, password_hash, full_name, role, is_active)
VALUES (
    'admin@console.com',
    '$2a$12$ybPPvQjcJfgS9GD3ruq2geJwLv4hd3iautaL4cJnb1X2w5/z3LG/y',
    'Super Admin',
    'super_admin',
    true
)
ON CONFLICT (email) DO UPDATE
SET role = 'super_admin',
    is_active = true,
    updated_at = NOW();

-- ============================================================================
-- 4. ADD COMMENT
-- ============================================================================

COMMENT ON COLUMN users.role IS 'User role: super_admin (full access), admin (org admin), staff (read-only)';

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================
