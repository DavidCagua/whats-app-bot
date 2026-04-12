-- Local dev: Biela business + Twilio WhatsApp row (phone_number_id NULL).
-- business_id must match scripts/seed_biela_menu.sql.
-- Idempotent: safe to re-run; clears Biela products so menu seed can re-apply.

-- ============================================================================
-- Business (same UUID as seed_biela_menu.sql)
-- ============================================================================
INSERT INTO businesses (id, name, business_type, settings, is_active, created_at, updated_at)
VALUES (
    '44488756-473b-46d2-a907-9f579e98ecfd',
    'Biela',
    'restaurant',
    $biela_settings${
  "city": "PASTO",
  "phone": "+573177000722",
  "state": "Nariño",
  "address": "calle 18 # 28 - 33",
  "country": "Colombia",
  "language": "es-CO",
  "menu_url": "https://gixlink.com/Biela/menu.html",
  "timezone": "America/Bogota",
  "ai_prompt": "Biela hamburguesas",
  "promotions": [],
  "payment_link": "",
  "agent_enabled": true,
  "payment_methods": [],
  "products_enabled": true,
  "conversation_primary_agent": ""
}$biela_settings$::jsonb,
    true,
    NOW(),
    NOW()
)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name,
    business_type = EXCLUDED.business_type,
    settings = EXCLUDED.settings,
    is_active = EXCLUDED.is_active,
    updated_at = NOW();

-- ============================================================================
-- WhatsApp number: Twilio sandbox (+14155238886), no Meta phone_number_id
-- Unique (business_id, phone_number) — see migration 005
-- ============================================================================
INSERT INTO whatsapp_numbers (
    business_id,
    phone_number_id,
    phone_number,
    is_active,
    created_at,
    updated_at
)
VALUES (
    '44488756-473b-46d2-a907-9f579e98ecfd',
    NULL,
    '+14155238886',
    true,
    NOW(),
    NOW()
)
ON CONFLICT (business_id, phone_number) DO UPDATE SET
    phone_number_id = EXCLUDED.phone_number_id,
    is_active = EXCLUDED.is_active,
    updated_at = NOW();

-- ============================================================================
-- Agents (business is created after migration 006 backfill — register explicitly)
-- Order first for this restaurant; booking kept enabled at lower priority.
-- ============================================================================
INSERT INTO business_agents (business_id, agent_type, enabled, priority, config, created_at, updated_at)
VALUES
    ('44488756-473b-46d2-a907-9f579e98ecfd', 'order', true, 1, '{}', NOW(), NOW()),
    ('44488756-473b-46d2-a907-9f579e98ecfd', 'booking', true, 2, '{}', NOW(), NOW())
ON CONFLICT (business_id, agent_type) DO UPDATE SET
    enabled = EXCLUDED.enabled,
    priority = EXCLUDED.priority,
    updated_at = NOW();

-- ============================================================================
-- Clear Biela orders + catalog so seed_biela_menu.sql can re-run (local dev)
-- ============================================================================
DELETE FROM order_items
WHERE order_id IN (SELECT id FROM orders WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd');

DELETE FROM orders WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd';

DELETE FROM products WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd';
