INSERT INTO business_agents (id, business_id, agent_type, enabled, priority, config)
VALUES (
  gen_random_uuid(),
  (SELECT id FROM businesses WHERE name ILIKE 'biela%' LIMIT 1),
  'customer_service',
  true,
  2,
  '{}'::jsonb
);
