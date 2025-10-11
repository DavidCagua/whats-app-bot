-- Multi-Tenant WhatsApp Bot Schema Migration
-- Version: 001
-- Description: Add support for multiple businesses, users, and WhatsApp numbers

-- ============================================================================
-- 1. CREATE NEW TABLES
-- ============================================================================

-- Businesses table (organizations/barbershops)
CREATE TABLE IF NOT EXISTS businesses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    business_type VARCHAR(50) DEFAULT 'barberia',
    settings JSONB DEFAULT '{}',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- WhatsApp numbers table (can receive messages)
-- Note: All numbers use same Meta App (access_token, app_id, app_secret from .env)
-- Only phone_number_id differs per business
CREATE TABLE IF NOT EXISTS whatsapp_numbers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    phone_number_id VARCHAR(255) NOT NULL UNIQUE, -- Meta's phone number ID for webhook routing
    phone_number VARCHAR(50) NOT NULL, -- Display number (e.g., +15556738752)
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Users table (people who can manage businesses)
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name VARCHAR(255),
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- User-Business relationships (many-to-many)
CREATE TABLE IF NOT EXISTS user_businesses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    role VARCHAR(50) DEFAULT 'staff', -- 'owner', 'admin', 'staff'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(user_id, business_id)
);

-- ============================================================================
-- 2. UPDATE EXISTING TABLES
-- ============================================================================

-- Note: customers table does NOT need business_id
-- Customer data is business-agnostic (a person is a person)
-- Business relationship is tracked through conversations

-- Add business_id and whatsapp_number_id to conversations table
ALTER TABLE conversations
ADD COLUMN IF NOT EXISTS business_id UUID REFERENCES businesses(id) ON DELETE CASCADE,
ADD COLUMN IF NOT EXISTS whatsapp_number_id UUID REFERENCES whatsapp_numbers(id) ON DELETE SET NULL;

-- Add indexes for conversations
CREATE INDEX IF NOT EXISTS idx_conversations_business_id ON conversations(business_id);
CREATE INDEX IF NOT EXISTS idx_conversations_whatsapp_number_id ON conversations(whatsapp_number_id);

-- ============================================================================
-- 3. CREATE INDEXES FOR PERFORMANCE
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_businesses_is_active ON businesses(is_active);
CREATE INDEX IF NOT EXISTS idx_whatsapp_numbers_business_id ON whatsapp_numbers(business_id);
CREATE INDEX IF NOT EXISTS idx_whatsapp_numbers_phone_number_id ON whatsapp_numbers(phone_number_id);
CREATE INDEX IF NOT EXISTS idx_whatsapp_numbers_is_active ON whatsapp_numbers(is_active);
CREATE INDEX IF NOT EXISTS idx_user_businesses_user_id ON user_businesses(user_id);
CREATE INDEX IF NOT EXISTS idx_user_businesses_business_id ON user_businesses(business_id);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- ============================================================================
-- 4. CREATE UPDATED_AT TRIGGER FUNCTION
-- ============================================================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply trigger to businesses table
DROP TRIGGER IF EXISTS update_businesses_updated_at ON businesses;
CREATE TRIGGER update_businesses_updated_at
    BEFORE UPDATE ON businesses
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Apply trigger to whatsapp_numbers table
DROP TRIGGER IF EXISTS update_whatsapp_numbers_updated_at ON whatsapp_numbers;
CREATE TRIGGER update_whatsapp_numbers_updated_at
    BEFORE UPDATE ON whatsapp_numbers
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Apply trigger to users table
DROP TRIGGER IF EXISTS update_users_updated_at ON users;
CREATE TRIGGER update_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- 5. INSERT DEFAULT BUSINESS (FOR MIGRATION)
-- ============================================================================

-- Insert default business for existing data
INSERT INTO businesses (id, name, business_type, settings, is_active, created_at, updated_at)
VALUES (
    '00000000-0000-0000-0000-000000000001', -- Fixed UUID for easy reference
    'Jorgito Barber - Pasto',
    'barberia',
    '{
        "address": "Calle 18 #25-30, Centro, Pasto",
        "phone": "+57 300 123 4567",
        "city": "Pasto",
        "state": "Nariño",
        "country": "Colombia",
        "timezone": "America/Bogota",
        "business_hours": {
            "monday": {"open": "09:00", "close": "19:00"},
            "tuesday": {"open": "09:00", "close": "19:00"},
            "wednesday": {"open": "09:00", "close": "19:00"},
            "thursday": {"open": "09:00", "close": "19:00"},
            "friday": {"open": "09:00", "close": "19:00"},
            "saturday": {"open": "09:00", "close": "18:00"},
            "sunday": {"open": "closed", "close": "closed"}
        },
        "services": [
            {"name": "Corte de cabello", "price": 20000, "duration": 60},
            {"name": "Barba", "price": 10000, "duration": 30},
            {"name": "Cejas", "price": 10000, "duration": 15},
            {"name": "Combo corte + barba", "price": 30000, "duration": 90},
            {"name": "Combo full estilo", "price": 35000, "duration": 105}
        ],
        "payment_methods": ["Efectivo", "Tarjeta", "Nequi", "DaviPlata"],
        "promotions": [
            "Cumpleañero feliz: 10% de descuento si cumples este mes",
            "Corte con parcero: 2 cortes por $34.000",
            "Combo full estilo: Corte + barba + cejas por $35.000"
        ],
        "staff": [
            {"name": "Luis Gómez", "specialties": ["Cortes clásicos", "Fade"]},
            {"name": "Alejandro Caicedo", "specialties": ["Diseños", "Barba"]},
            {"name": "Camilo Martínez", "specialties": ["Cortes modernos", "Color"]}
        ],
        "language": "es-CO",
        "appointment_settings": {
            "max_concurrent": 2,
            "min_advance_hours": 1,
            "default_duration_minutes": 60
        },
        "ai_prompt": "Tú eres {business_name}, un asistente virtual de IA para una barbería ubicada en {city}, {state}, {country}. Tu función es atender con carisma y eficiencia a los clientes a través de WhatsApp. Usas un estilo juvenil, cercano y profesional, como si fueras un barbero de confianza.\\n\\nObjetivo principal:\\n- Resolver dudas comunes (precios, servicios, horarios, ubicación, formas de pago)\\n- Guiar al cliente para que agende una cita\\n- Transmitir la personalidad del negocio: juvenil, confiable y con buen estilo\\n- Recolectar información clave sin ser invasivo\\n\\nEstilo de comunicación:\\nUsa un tono cercano, relajado y respetuoso, típico de la región. Utiliza frases como: ''Hola parce'', ''¿Te agendo de una?'', ''¿Qué más pues?''. Personaliza siempre que sea posible. Usa emojis con moderación 💈\\n\\nREGLAS DE CITAS:\\n- IMPORTANTE: Máximo {max_concurrent} citas simultáneas al mismo tiempo\\n- Si ya hay {max_concurrent} citas en el mismo horario, NO crees otra. Informa al cliente que ese horario está completo y ofrece alternativas\\n- Siempre confirma citas con este formato EXACTO: ''✅ Tu cita está agendada para el **[fecha completa]** a las **[hora]** para [servicio], [nombre]. ¡Nos vemos y prepárate para salir renovado! 💇🔥''\\n\\nCuándo usar herramientas de calendario:\\n1. Cliente dice ''quiero agendar'' o pide hora → usa schedule_appointment()\\n2. Cliente pregunta ''qué horarios tienes'' → usa get_available_slots()\\n3. Cliente dice ''cambiar mi cita'' → usa reschedule_appointment()\\n4. Cliente dice ''cancelar mi cita'' → usa cancel_appointment()\\n\\nRecolección de datos:\\nNecesitas: nombre completo, fecha, hora, tipo de servicio. Pregunta de forma natural: ''¿Cómo te llamás y para cuándo querés la cita?''\\n\\nContexto actual:\\n- Cliente: {name} (WhatsApp ID: {wa_id})\\n- Fecha de hoy: {current_date}\\n- Año actual: {current_year}\\n- Zona horaria: {timezone}"
    }'::jsonb,
    true,
    NOW(),
    NOW()
)
ON CONFLICT (id) DO NOTHING;

-- Insert default WhatsApp number for Jorgito Barber
-- Credentials (access_token, app_id, app_secret, verify_token) are in .env
INSERT INTO whatsapp_numbers (
    id,
    business_id,
    phone_number_id,
    phone_number,
    is_active,
    created_at,
    updated_at
)
VALUES (
    '00000000-0000-0000-0000-000000000002',
    '00000000-0000-0000-0000-000000000001',
    '717510114781982',
    '+15556738752',
    true,
    NOW(),
    NOW()
)
ON CONFLICT (phone_number_id) DO UPDATE
SET phone_number = EXCLUDED.phone_number,
    is_active = EXCLUDED.is_active,
    updated_at = NOW();

-- ============================================================================
-- 6. UPDATE EXISTING DATA TO REFERENCE DEFAULT BUSINESS
-- ============================================================================

-- Update existing conversations to reference default business
UPDATE conversations
SET business_id = '00000000-0000-0000-0000-000000000001'
WHERE business_id IS NULL;

-- ============================================================================
-- 7. MAKE BUSINESS_ID NOT NULL (AFTER MIGRATION)
-- ============================================================================

-- Now make business_id required for conversations
ALTER TABLE conversations
ALTER COLUMN business_id SET NOT NULL;

-- ============================================================================
-- 8. CREATE VIEWS FOR EASY QUERYING
-- ============================================================================

-- View to see business with their WhatsApp numbers
CREATE OR REPLACE VIEW business_whatsapp_numbers AS
SELECT
    b.id as business_id,
    b.name as business_name,
    b.business_type,
    b.is_active as business_active,
    w.id as whatsapp_number_id,
    w.phone_number,
    w.phone_number_id,
    w.is_active as number_active,
    w.created_at as number_created_at
FROM businesses b
LEFT JOIN whatsapp_numbers w ON b.id = w.business_id
ORDER BY b.created_at DESC, w.created_at DESC;

-- View to see user-business relationships with details
CREATE OR REPLACE VIEW user_business_access AS
SELECT
    u.id as user_id,
    u.email,
    u.full_name,
    ub.role,
    b.id as business_id,
    b.name as business_name,
    b.business_type,
    b.is_active as business_active
FROM users u
INNER JOIN user_businesses ub ON u.id = ub.user_id
INNER JOIN businesses b ON ub.business_id = b.id
ORDER BY u.email, b.name;

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

-- Add comment to track migration version
COMMENT ON TABLE businesses IS 'Multi-tenant businesses table - Migration 001';
COMMENT ON TABLE whatsapp_numbers IS 'WhatsApp Business API numbers - Migration 001';
COMMENT ON TABLE users IS 'System users who can manage businesses - Migration 001';
COMMENT ON TABLE user_businesses IS 'User-business access control - Migration 001';
