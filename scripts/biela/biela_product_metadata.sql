-- Pre-generated product search tags for Biela.
--
-- Tags are hand-curated (not LLM-generated) based on the Biela menu. They cover
-- generic terms a customer would say that DON'T appear in the product name
-- or description — the whole point of tags is to bridge that gap.
--
-- The search pipeline already stems the query (Snowball Spanish) and matches
-- substring variants, so we don't include morphological variants here
-- (no "hervidito" — just "hervido" is enough; the stemmer handles "-ito").
--
-- Embeddings are NOT populated here — run scripts/generate_product_metadata.py
-- with an OPENAI_API_KEY to populate them. The search falls back to
-- lexical + tag matching when embeddings are missing.
--
-- Idempotent: keyed by SKU so re-running is safe.

\set business_id '44488756-473b-46d2-a907-9f579e98ecfd'

-- ============================================================================
-- BURGERS — names are unique codenames; no customer will search by code.
--            tag each with generic "hamburguesa" terms + distinctive ingredients.
-- ============================================================================

UPDATE products SET tags = ARRAY['hamburguesa', 'burger', 'carne', 'res', 'queso', 'papas'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BUR-01';
UPDATE products SET tags = ARRAY['hamburguesa', 'burger', 'carne', 'res', 'clasica', 'papas'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BUR-02';
UPDATE products SET tags = ARRAY['hamburguesa', 'burger', 'carne', 'res', 'quesos', 'tocineta'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BUR-03';
UPDATE products SET tags = ARRAY['hamburguesa', 'burger', 'carne', 'res', 'chimichurri', 'picante'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BUR-04';
UPDATE products SET tags = ARRAY['hamburguesa', 'burger', 'carne', 'res', 'clasica', 'sencilla'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BUR-05';
UPDATE products SET tags = ARRAY['hamburguesa', 'burger', 'carne', 'res', 'cebolla morada', 'chipotle'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BUR-06';
UPDATE products SET tags = ARRAY['hamburguesa', 'burger', 'carne', 'res', 'queso azul', 'gourmet'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BUR-07';
UPDATE products SET tags = ARRAY['hamburguesa', 'burger', 'carne', 'res', 'crispy', 'mostaza'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BUR-08';
UPDATE products SET tags = ARRAY['hamburguesa', 'burger', 'carne', 'res', 'chilacuan', 'cuajada'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BUR-09';
UPDATE products SET tags = ARRAY['hamburguesa', 'burger', 'carne', 'res', 'miel', 'dulce', 'bbq'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BUR-10';
UPDATE products SET tags = ARRAY['hamburguesa', 'burger', 'carne', 'res', 'tomate cherry', 'albahaca'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BUR-11';
UPDATE products SET tags = ARRAY['hamburguesa', 'burger', 'carne', 'res', 'cerdo', 'pastor', 'pina'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BUR-12';
UPDATE products SET tags = ARRAY['hamburguesa', 'burger', 'carne', 'res', 'mexicana', 'jalapeno', 'picante'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BUR-13';

-- ============================================================================
-- HOT DOGS — "perro caliente" / "perro" is the regional term
-- ============================================================================

UPDATE products SET tags = ARRAY['hot dog', 'perro', 'perro caliente', 'salchicha', 'pollo'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-HD-01';
UPDATE products SET tags = ARRAY['hot dog', 'perro', 'perro caliente', 'salchicha', 'tocineta', 'queso'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-HD-02';
UPDATE products SET tags = ARRAY['hot dog', 'perro', 'perro caliente', 'salchicha', 'costilla', 'maracuya'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-HD-03';
UPDATE products SET tags = ARRAY['hot dog', 'perro', 'perro caliente', 'salchicha', 'costilla', 'bbq'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-HD-04';

-- ============================================================================
-- FRIES — "salchipapas" is the regional generic for fries-with-toppings
-- ============================================================================

UPDATE products SET tags = ARRAY['papas', 'fritas', 'salchipapas', 'especial', 'completa'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-FR-01';
UPDATE products SET tags = ARRAY['papas', 'fritas', 'salchipapas', 'salchicha', 'clasica'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-FR-02';
UPDATE products SET tags = ARRAY['papas', 'fritas', 'salchipapas', 'queso', 'completa'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-FR-03';
UPDATE products SET tags = ARRAY['papas', 'fritas', 'salchipapas', 'queso', 'cheddar'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-FR-04';

-- ============================================================================
-- CHICKEN BURGERS
-- ============================================================================

UPDATE products SET tags = ARRAY['hamburguesa', 'pollo', 'chicken burger', 'apanado', 'crispy'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-CB-01';
UPDATE products SET tags = ARRAY['hamburguesa', 'pollo', 'chicken burger', 'apanado', 'tomate cherry'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-CB-02';
UPDATE products SET tags = ARRAY['hamburguesa', 'pollo', 'chicken burger', 'apanado', 'chipotle'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-CB-03';

-- ============================================================================
-- MENÚ INFANTIL
-- ============================================================================

UPDATE products SET tags = ARRAY['infantil', 'nino', 'nina', 'kids', 'menu infantil', 'combo nino'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-MI-01';

-- ============================================================================
-- STEAK & RIBS
-- ============================================================================

UPDATE products SET tags = ARRAY['costillas', 'ribs', 'cerdo', 'bbq', 'carne'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-SR-01';
UPDATE products SET tags = ARRAY['picada', 'surtida', 'combo', 'para compartir', 'carne', 'cerdo'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-SR-02';

-- ============================================================================
-- BEBIDAS — THIS is the critical category.
--            All descriptions are NULL → tags are the only way to find them.
-- ============================================================================

-- Limonadas
UPDATE products SET tags = ARRAY['limonada', 'jugo', 'bebida fria', 'cereza'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-01';
UPDATE products SET tags = ARRAY['limonada', 'jugo', 'bebida fria', 'fresa'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-02';
UPDATE products SET tags = ARRAY['limonada', 'jugo', 'bebida fria', 'hierba buena', 'mentolada'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-03';
UPDATE products SET tags = ARRAY['limonada', 'jugo', 'bebida fria', 'natural', 'limon'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-04';

-- Hervidos (the "hervidito" case)
UPDATE products SET tags = ARRAY['hervido', 'caliente', 'bebida caliente', 'maracuya'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-05';
UPDATE products SET tags = ARRAY['hervido', 'caliente', 'bebida caliente', 'mora'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-06';

-- Malteadas
UPDATE products SET tags = ARRAY['malteada', 'milkshake', 'batido', 'maracuya', 'uvilla'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-07';
UPDATE products SET tags = ARRAY['malteada', 'milkshake', 'batido', 'brownie', 'chocolate'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-08';
UPDATE products SET tags = ARRAY['malteada', 'milkshake', 'batido', 'frutos rojos', 'fresa'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-09';

-- Jugos
UPDATE products SET tags = ARRAY['jugo', 'natural', 'agua', 'bebida fria'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-10';
UPDATE products SET tags = ARRAY['jugo', 'natural', 'leche', 'bebida fria'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-11';

-- Gaseosas / sodas
UPDATE products SET tags = ARRAY['gaseosa', 'soda', 'refresco', 'coca', 'cola', 'bebida fria'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-12';
UPDATE products SET tags = ARRAY['gaseosa', 'soda', 'refresco', 'coca', 'cola', 'zero', 'light', 'sin azucar'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-13';
UPDATE products SET tags = ARRAY['gaseosa', 'soda', 'refresco', 'club soda'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-14';
UPDATE products SET tags = ARRAY['agua', 'sin gas', 'botella'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-15';

-- Cervezas (THE critical tag — the word "cerveza" appears nowhere in the menu data)
UPDATE products SET tags = ARRAY['cerveza', 'beer', 'chela', 'pola', 'rubia', 'club colombia', 'nacional'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-16';
UPDATE products SET tags = ARRAY['cerveza', 'beer', 'chela', 'pola', 'rubia', 'poker', 'nacional'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-17';
UPDATE products SET tags = ARRAY['cerveza', 'beer', 'chela', 'pola', 'mexicana', 'corona', 'importada'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-18';
UPDATE products SET tags = ARRAY['cerveza', 'beer', 'chela', 'michelada', 'mexicana', 'corona'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-19';

-- Micheladas y sodas especiales
UPDATE products SET tags = ARRAY['michelada', 'cerveza', 'limon', 'preparada'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-20';
UPDATE products SET tags = ARRAY['soda', 'saborizada', 'uvilla', 'maracuya', 'bebida fria'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-21';
UPDATE products SET tags = ARRAY['soda', 'saborizada', 'frutos rojos', 'bebida fria'] WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-22';

-- ============================================================================
-- Summary
-- ============================================================================
DO $$
DECLARE
    cnt integer;
BEGIN
    SELECT COUNT(*) INTO cnt FROM products WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND array_length(tags, 1) > 0;
    RAISE NOTICE 'Biela products with tags: %', cnt;
END $$;
