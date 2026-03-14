-- Migration 006: Multi-agent architecture - agent_types, business_agents, conversation_sessions
-- Part of Multi-Agent Architecture Migration Plan

-- ============================================================================
-- 1. CREATE agent_types (Reference Table)
-- ============================================================================
CREATE TABLE IF NOT EXISTS agent_types (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Seed agent types
INSERT INTO agent_types (type, name, description) VALUES
    ('booking', 'Booking Agent', 'Handles appointment scheduling and calendar operations'),
    ('order', 'Order Agent', 'Handles restaurant/retail orders'),
    ('sales', 'Sales Agent', 'Handles product sales and checkout'),
    ('support', 'Support Agent', 'Handles customer support and tickets')
ON CONFLICT (type) DO NOTHING;

-- ============================================================================
-- 2. CREATE business_agents
-- ============================================================================
CREATE TABLE IF NOT EXISTS business_agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    agent_type VARCHAR(50) NOT NULL,
    enabled BOOLEAN DEFAULT true,
    priority INTEGER DEFAULT 100,
    config JSONB DEFAULT '{}',
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(business_id, agent_type)
);

CREATE INDEX IF NOT EXISTS idx_business_agents_business_id ON business_agents(business_id);
CREATE INDEX IF NOT EXISTS idx_business_agents_enabled ON business_agents(enabled);

-- Backfill: enable booking_agent for all existing businesses
INSERT INTO business_agents (business_id, agent_type, enabled, priority, config)
SELECT id, 'booking', true, 1, '{}'
FROM businesses
ON CONFLICT (business_id, agent_type) DO NOTHING;

-- ============================================================================
-- 3. CREATE conversation_sessions (Session State)
-- ============================================================================
CREATE TABLE IF NOT EXISTS conversation_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wa_id VARCHAR(50) NOT NULL,
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    active_agents JSONB DEFAULT '[]',
    order_context JSONB DEFAULT '{}',
    booking_context JSONB DEFAULT '{}',
    agent_contexts JSONB DEFAULT '{}',
    last_activity_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(wa_id, business_id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_sessions_wa_business ON conversation_sessions(wa_id, business_id);
CREATE INDEX IF NOT EXISTS idx_conversation_sessions_last_activity ON conversation_sessions(last_activity_at);

-- ============================================================================
-- 4. ADD agent_type to conversations (Future-proofing)
-- ============================================================================
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS agent_type VARCHAR(50) NULL;
