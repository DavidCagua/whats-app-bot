-- Migration 010: Add last_order_id and last_booking_id for session continuity after task completion

ALTER TABLE conversation_sessions ADD COLUMN IF NOT EXISTS last_order_id VARCHAR(50);
ALTER TABLE conversation_sessions ADD COLUMN IF NOT EXISTS last_booking_id VARCHAR(50);
