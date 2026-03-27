-- RLS Security Migration (Part 2)
-- Version: 021
-- Description: Enable RLS on tables missed in migration 020: conversations, customers, processed_messages.

ALTER TABLE conversations       ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers           ENABLE ROW LEVEL SECURITY;
ALTER TABLE processed_messages  ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_all_conversations" ON conversations
  FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "service_role_all_customers" ON customers
  FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "service_role_all_processed_messages" ON processed_messages
  FOR ALL TO service_role USING (true) WITH CHECK (true);
