UPDATE businesses
SET settings = settings || '{"order_agent_mode":"tool_calling"}'::jsonb
WHERE id = '44488756-473b-46d2-a907-9f579e98ecfd';
