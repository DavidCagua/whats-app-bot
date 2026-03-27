-- Migration 017: One-time seed of services from businesses.settings.services
-- Initial-load only. No booking backfill required.

INSERT INTO services (
    business_id,
    name,
    description,
    price,
    currency,
    duration_minutes,
    is_active
)
SELECT
    b.id AS business_id,
    svc.name,
    NULLIF(svc.description, '') AS description,
    svc.price,
    COALESCE(NULLIF(svc.currency, ''), 'COP') AS currency,
    COALESCE(svc.duration_minutes, 60) AS duration_minutes,
    COALESCE(svc.is_active, true) AS is_active
FROM businesses b
CROSS JOIN LATERAL (
    SELECT
        NULLIF(item->>'name', '') AS name,
        item->>'description' AS description,
        CASE
            WHEN (item->>'price') ~ '^-?[0-9]+(\.[0-9]+)?$' THEN (item->>'price')::numeric
            ELSE 0::numeric
        END AS price,
        item->>'currency' AS currency,
        CASE
            WHEN COALESCE(item->>'duration_minutes', item->>'duration') ~ '^[0-9]+$'
            THEN COALESCE(item->>'duration_minutes', item->>'duration')::int
            ELSE 60
        END AS duration_minutes,
        CASE
            WHEN item ? 'is_active' THEN (item->>'is_active')::boolean
            ELSE true
        END AS is_active
    FROM jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(b.settings->'services') = 'array' THEN b.settings->'services'
            ELSE '[]'::jsonb
        END
    ) AS item
) AS svc
WHERE svc.name IS NOT NULL
  AND svc.price >= 0
ON CONFLICT (business_id, name) DO NOTHING;
