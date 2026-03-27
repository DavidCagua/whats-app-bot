-- RLS Security Migration
-- Version: 020
-- Description: Enable Row Level Security on all tables and restrict access to service_role only.
--              The Flask backend uses the service_role key and is unaffected.
--              Anonymous and authenticated (JWT) users will have no direct table access.

-- ============================================================================
-- ENABLE RLS
-- ============================================================================

ALTER TABLE businesses               ENABLE ROW LEVEL SECURITY;
ALTER TABLE whatsapp_numbers         ENABLE ROW LEVEL SECURITY;
ALTER TABLE users                    ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_businesses          ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_types              ENABLE ROW LEVEL SECURITY;
ALTER TABLE business_agents          ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_sessions    ENABLE ROW LEVEL SECURITY;
ALTER TABLE products                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_items              ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_agent_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_attachments ENABLE ROW LEVEL SECURITY;
ALTER TABLE business_availability    ENABLE ROW LEVEL SECURITY;
ALTER TABLE bookings                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE staff_members            ENABLE ROW LEVEL SECURITY;
ALTER TABLE services                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations            ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers                ENABLE ROW LEVEL SECURITY;
ALTER TABLE processed_messages       ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- SERVICE ROLE POLICIES (full access for backend)
-- service_role bypasses RLS by default in Supabase, but explicit policies
-- are added here for clarity and future-proofing.
-- ============================================================================

-- businesses
CREATE POLICY "service_role_all_businesses" ON businesses
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- whatsapp_numbers
CREATE POLICY "service_role_all_whatsapp_numbers" ON whatsapp_numbers
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- users
CREATE POLICY "service_role_all_users" ON users
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- user_businesses
CREATE POLICY "service_role_all_user_businesses" ON user_businesses
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- agent_types
CREATE POLICY "service_role_all_agent_types" ON agent_types
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- business_agents
CREATE POLICY "service_role_all_business_agents" ON business_agents
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- conversation_sessions
CREATE POLICY "service_role_all_conversation_sessions" ON conversation_sessions
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- products
CREATE POLICY "service_role_all_products" ON products
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- orders
CREATE POLICY "service_role_all_orders" ON orders
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- order_items
CREATE POLICY "service_role_all_order_items" ON order_items
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- conversation_agent_settings
CREATE POLICY "service_role_all_conversation_agent_settings" ON conversation_agent_settings
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- conversation_attachments
CREATE POLICY "service_role_all_conversation_attachments" ON conversation_attachments
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- business_availability
CREATE POLICY "service_role_all_business_availability" ON business_availability
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- bookings
CREATE POLICY "service_role_all_bookings" ON bookings
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- staff_members
CREATE POLICY "service_role_all_staff_members" ON staff_members
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- services
CREATE POLICY "service_role_all_services" ON services
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- conversations
CREATE POLICY "service_role_all_conversations" ON conversations
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- customers
CREATE POLICY "service_role_all_customers" ON customers
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- processed_messages
CREATE POLICY "service_role_all_processed_messages" ON processed_messages
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ============================================================================
-- NOTE: business_whatsapp_numbers and user_business_access are VIEWS.
-- Views inherit security from their underlying tables (whatsapp_numbers,
-- user_businesses, businesses). No RLS needed on views directly.
-- ============================================================================
