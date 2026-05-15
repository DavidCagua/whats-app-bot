-- Delete all conversation history and session state for one WhatsApp number.
-- Target: +573159280840
-- NOTE: This script is destructive. Review before executing.

BEGIN;

-- Optional pre-check counts
SELECT 'conversations_before' AS label, COUNT(*) AS total
FROM conversations
WHERE whatsapp_id IN ('+573159280840', '573159280840');

SELECT 'conversation_sessions_before' AS label, COUNT(*) AS total
FROM conversation_sessions
WHERE wa_id IN ('+573159280840', '573159280840');

-- Delete message history first (attachments cascade via FK on conversation_id)
DELETE FROM conversations
WHERE whatsapp_id IN ('+573159280840', '573159280840');

-- Delete per-conversation multi-turn session state
DELETE FROM conversation_sessions
WHERE wa_id IN ('+573159280840', '573159280840');

-- Optional post-check counts
SELECT 'conversations_after' AS label, COUNT(*) AS total
FROM conversations
WHERE whatsapp_id IN ('+573159280840', '573159280840');

SELECT 'conversation_sessions_after' AS label, COUNT(*) AS total
FROM conversation_sessions
WHERE wa_id IN ('+573159280840', '573159280840');

COMMIT;
