-- Migration 016: Services catalog + booking service reference
-- Alchemy-owned migration: Prisma consumes resulting schema.

-- ============================================================================
-- 1. CREATE services
-- ============================================================================
CREATE TABLE IF NOT EXISTS services (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    price DECIMAL(12, 2) NOT NULL,
    currency VARCHAR(10) DEFAULT 'COP',
    duration_minutes INT NOT NULL DEFAULT 60 CHECK (duration_minutes > 0),
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_services_business_id ON services(business_id);
CREATE INDEX IF NOT EXISTS idx_services_is_active ON services(is_active);
CREATE UNIQUE INDEX IF NOT EXISTS idx_services_business_name ON services(business_id, name);

-- ============================================================================
-- 2. ADD bookings.service_id
-- ============================================================================
ALTER TABLE bookings
ADD COLUMN IF NOT EXISTS service_id UUID REFERENCES services(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_bookings_service_id ON bookings(service_id);
