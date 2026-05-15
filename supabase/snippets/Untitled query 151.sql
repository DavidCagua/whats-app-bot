-- Delete all conversation history and session state for one WhatsApp number.
-- Target: +573177000722
-- NOTE: This script is destructive. Review before executing.

BEGIN;

-- Optional pre-check counts
SELECT 'conversations_before' AS label, COUNT(*) AS total
FROM conversations
WHERE whatsapp_id IN ('+573177000722', '573177000722');

SELECT 'conversation_sessions_before' AS label, COUNT(*) AS total
FROM conversation_sessions
WHERE wa_id IN ('+573177000722', '573177000722');

-- Delete message history first (attachments cascade via FK on conversation_id)
DELETE FROM conversations
WHERE whatsapp_id IN ('+573177000722', '573177000722');

-- Delete per-conversation multi-turn session state
DELETE FROM conversation_sessions
WHERE wa_id IN ('+573177000722', '573177000722');

-- Optional post-check counts
SELECT 'conversations_after' AS label, COUNT(*) AS total
FROM conversations
WHERE whatsapp_id IN ('+573177000722', '573177000722');

SELECT 'conversation_sessions_after' AS label, COUNT(*) AS total
FROM conversation_sessions
WHERE wa_id IN ('+573177000722', '573177000722');
-- DELETE FROM customers
-- WHERE whatsapp_id IN ('+573177000722', '573177000722');
COMMIT;


-- UPDATE businesses
--    SET settings = settings || jsonb_build_object('daily_analysis_enabled', true)
--  WHERE name ILIKE '%biela%';


--  UPDATE businesses
-- SET settings = jsonb_set(
--   coalesce(settings, '{}'::jsonb),
--   '{closed_day_alt_contact}',
--   '{"name":"Sede Las Cuadras","phone":"+573026722877"}'::jsonb
-- )
-- WHERE name = 'Biela';

