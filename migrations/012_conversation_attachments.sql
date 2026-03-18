-- Migration 012: Voice and media - conversation_attachments and optional message_type

-- Optional: distinguish message type for UI (text | audio | image | document)
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS message_type VARCHAR(20) DEFAULT 'text';

CREATE TABLE IF NOT EXISTS conversation_attachments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    type VARCHAR(20) NOT NULL,
    content_type VARCHAR(255),
    provider_media_url TEXT,
    provider_media_id VARCHAR(255),
    url TEXT,
    size_bytes BIGINT,
    duration_sec NUMERIC(10, 2),
    transcript TEXT,
    provider_metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversation_attachments_conversation_id
    ON conversation_attachments(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversation_attachments_type
    ON conversation_attachments(type);
