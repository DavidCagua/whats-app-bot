-- Migration 013: Bookings & Business Availability
-- In-house booking system to replace Google Calendar dependency.
-- bookings link to customers (existing table) and businesses.
-- business_availability defines open hours and slot config per business.

-- ============================================================================
-- 1. CREATE business_availability
-- ============================================================================
CREATE TABLE IF NOT EXISTS business_availability (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    day_of_week SMALLINT NOT NULL CHECK (day_of_week BETWEEN 0 AND 6), -- 0=Sunday, 6=Saturday
    open_time TIME NOT NULL,        -- e.g. 09:00
    close_time TIME NOT NULL,       -- e.g. 18:00
    slot_duration_minutes INT NOT NULL DEFAULT 60 CHECK (slot_duration_minutes > 0),
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_business_availability_business_day
    ON business_availability(business_id, day_of_week);
CREATE INDEX IF NOT EXISTS idx_business_availability_business_id
    ON business_availability(business_id);

-- ============================================================================
-- 2. CREATE bookings
-- ============================================================================
CREATE TABLE IF NOT EXISTS bookings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    customer_id INT REFERENCES customers(id) ON DELETE SET NULL,
    service_name VARCHAR(255),          -- e.g. "Corte de cabello", "Consulta"
    start_at TIMESTAMP WITH TIME ZONE NOT NULL,
    end_at TIMESTAMP WITH TIME ZONE NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'confirmed'
        CHECK (status IN ('pending', 'confirmed', 'cancelled', 'no_show', 'completed')),
    notes TEXT,
    created_via VARCHAR(20) DEFAULT 'whatsapp'
        CHECK (created_via IN ('whatsapp', 'admin', 'api')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bookings_business_id ON bookings(business_id);
CREATE INDEX IF NOT EXISTS idx_bookings_customer_id ON bookings(customer_id);
CREATE INDEX IF NOT EXISTS idx_bookings_status ON bookings(status);
CREATE INDEX IF NOT EXISTS idx_bookings_start_at ON bookings(start_at);
CREATE INDEX IF NOT EXISTS idx_bookings_business_start ON bookings(business_id, start_at);
