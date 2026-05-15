-- RLS Security Migration (Part 2)
-- Version: 021
-- Description: Enable RLS on tables missed in migration 020: conversations, customers, processed_messages.
--              These tables were later added to 020 itself, so the ALTER TABLE
--              lines are kept with IF NOT EXISTS-style idempotency (ENABLE RLS
--              is naturally idempotent in Postgres). The policies use
--              CREATE POLICY IF NOT EXISTS to avoid duplicate errors.

ALTER TABLE conversations       ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers           ENABLE ROW LEVEL SECURITY;
ALTER TABLE processed_messages  ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_all_conversations' AND tablename = 'conversations') THEN
    CREATE POLICY "service_role_all_conversations" ON conversations
      FOR ALL TO service_role USING (true) WITH CHECK (true);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_all_customers' AND tablename = 'customers') THEN
    CREATE POLICY "service_role_all_customers" ON customers
      FOR ALL TO service_role USING (true) WITH CHECK (true);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_all_processed_messages' AND tablename = 'processed_messages') THEN
    CREATE POLICY "service_role_all_processed_messages" ON processed_messages
      FOR ALL TO service_role USING (true) WITH CHECK (true);
  END IF;
END
$$;
