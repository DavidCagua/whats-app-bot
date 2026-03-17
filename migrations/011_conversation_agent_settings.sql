-- Migration 011: Per-conversation agent enable/disable

CREATE TABLE IF NOT EXISTS conversation_agent_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    whatsapp_id VARCHAR(50) NOT NULL,
    agent_enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(business_id, whatsapp_id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_agent_settings_business_id
    ON conversation_agent_settings(business_id);

CREATE INDEX IF NOT EXISTS idx_conversation_agent_settings_whatsapp_id
    ON conversation_agent_settings(whatsapp_id);

CREATE INDEX IF NOT EXISTS idx_conversation_agent_settings_agent_enabled
    ON conversation_agent_settings(agent_enabled);

