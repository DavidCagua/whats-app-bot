-- Idempotent UPDATE: fill in descriptions for Biela beverages.
-- Use this on an existing DB instead of re-running the full seed
-- (which would duplicate products).
--
-- Run with:
--   psql $LOCAL_DB_URL -f scripts/biela/biela_beverage_descriptions_update.sql
--
-- Safe to re-run: uses UPDATE ... WHERE, idempotent by SKU.

BEGIN;

UPDATE products SET description = 'Limonada natural preparada con jarabe de cereza y hielo'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-01';

UPDATE products SET description = 'Limonada natural con fresas frescas licuadas y hielo'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-02';

UPDATE products SET description = 'Limonada natural con hojas de hierba buena fresca, refrescante y ligeramente mentolada'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-03';

UPDATE products SET description = 'Limonada clásica preparada con limón fresco exprimido al momento y hielo'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-04';

UPDATE products SET description = 'Bebida caliente preparada con maracuyá, especias y aguardiente opcional. Tradicional colombiana para clima frío'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-05';

UPDATE products SET description = 'Bebida caliente preparada con mora, especias y aguardiente opcional. Tradicional colombiana para clima frío'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-06';

UPDATE products SET description = 'Malteada cremosa de maracuyá y uvilla con helado y leche, decorada con crema'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-07';

UPDATE products SET description = 'Malteada de chocolate con trozos de brownie, helado y crema batida'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-08';

UPDATE products SET description = 'Malteada cremosa de frutos rojos (fresa, mora, arándano) con helado y crema'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-09';

UPDATE products SET description = 'Jugo natural de la fruta del día preparado en agua'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-10';

UPDATE products SET description = 'Jugo natural de la fruta del día preparado en leche'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-11';

UPDATE products SET description = 'Gaseosa Coca-Cola tradicional en botella personal'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-12';

UPDATE products SET description = 'Gaseosa Coca-Cola Zero sin azúcar en botella personal'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-13';

UPDATE products SET description = 'Agua con gas (club soda) en botella personal'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-14';

UPDATE products SET description = 'Agua sin gas en botella personal'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-15';

UPDATE products SET description = 'Cerveza colombiana tipo lager rubia, en botella de 330ml'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-16';

UPDATE products SET description = 'Cerveza colombiana tipo lager rubia, en botella de 330ml'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-17';

UPDATE products SET description = 'Cerveza mexicana tipo lager importada en botella de 355ml'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-18';

UPDATE products SET description = 'Corona preparada al estilo michelada con limón, sal y salsas. Servida en copa escarchada'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-19';

UPDATE products SET description = 'Cerveza nacional (Club Colombia o Poker) preparada con limón, sal y salsas. Servida en copa escarchada'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-20';

UPDATE products SET description = 'Soda saborizada con pulpa natural de uvilla y maracuyá, servida con hielo'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-21';

UPDATE products SET description = 'Soda saborizada con mezcla natural de frutos rojos (fresa, mora, arándano), servida con hielo'
  WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd' AND sku = 'BL-BEB-22';

COMMIT;

DO $$
DECLARE
    cnt integer;
BEGIN
    SELECT COUNT(*) INTO cnt FROM products
    WHERE business_id = '44488756-473b-46d2-a907-9f579e98ecfd'
      AND category = 'BEBIDAS'
      AND description IS NOT NULL;
    RAISE NOTICE 'Biela beverages with descriptions: %', cnt;
END $$;
