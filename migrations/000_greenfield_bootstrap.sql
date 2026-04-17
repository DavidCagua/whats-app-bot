-- Greenfield bootstrap (run before 001)
-- Version: 000
-- Description: Tables that existed in legacy prod before migration 001 was written (001 only ALTERs
--              conversations; customers / processed_messages are never CREATE'd in 001–021).
--              On existing databases every statement is a no-op (IF NOT EXISTS).
--              Apply in the same order as other migrations: 000, then 001, …

-- ============================================================================
-- conversations (pre–001 shape: no business_id yet; 001 adds columns + NOT NULL)
-- ============================================================================
CREATE TABLE IF NOT EXISTS conversations (
    id SERIAL PRIMARY KEY,
    whatsapp_id VARCHAR(50) NOT NULL,
    message TEXT NOT NULL,
    role VARCHAR(20) NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_whatsapp_id ON conversations(whatsapp_id);
CREATE INDEX IF NOT EXISTS idx_conversations_timestamp ON conversations(timestamp);

-- ============================================================================
-- customers (required by 007 orders.customer_id FK)
-- ============================================================================
CREATE TABLE IF NOT EXISTS customers (
    id SERIAL PRIMARY KEY,
    whatsapp_id VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL,
    age INTEGER,
    address TEXT,
    phone VARCHAR(50),
    payment_method VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT customers_whatsapp_id_key UNIQUE (whatsapp_id)
);

CREATE INDEX IF NOT EXISTS idx_customers_whatsapp_id ON customers(whatsapp_id);

-- ============================================================================
-- processed_messages (020 enables RLS; table was only created ad hoc via SQLAlchemy before)
-- ============================================================================
CREATE TABLE IF NOT EXISTS processed_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id VARCHAR(255) NOT NULL,
    processed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT processed_messages_message_id_key UNIQUE (message_id)
);

CREATE INDEX IF NOT EXISTS idx_processed_messages_message_id ON processed_messages(message_id);
CREATE INDEX IF NOT EXISTS idx_processed_at ON processed_messages(processed_at);
