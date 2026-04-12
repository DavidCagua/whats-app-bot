-- Seed Biela menu products for business 44488756-473b-46d2-a907-9f579e98ecfd
-- Run after migration 008 (adds category column)
-- Supabase uses PostgreSQL

-- BURGERS
INSERT INTO products (business_id, name, description, price, currency, category, sku, is_active) VALUES
('44488756-473b-46d2-a907-9f579e98ecfd', 'BARRACUDA', 'Pan artesanal, 150gr de carne, tocineta, queso mozzarella, queso cheddar, cebolla caramelizada, mayonesa de cilantro, salsa BBQ, mostaza dulce y papas fritas', 28000, 'COP', 'BURGERS', 'BL-BUR-01', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'BIELA', 'Pan artesanal, 150gr de carne, jamón, queso mozzarella, tomate, lechuga, mayonesa de cilantro, salsa de ajo, salsa BBQ y papas fritas', 28000, 'COP', 'BURGERS', 'BL-BUR-02', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'BETA', 'Pan artesanal, 150gr de carne, tocineta, queso mozzarella, queso cheddar, queso parmesano, queso crema, pepinillos caramelizados, mayonesa de cilantro, salsa BBQ y papas fritas', 28000, 'COP', 'BURGERS', 'BL-BUR-03', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'ARRABBIATA', 'Pan artesanal, 150gr de carne, tocineta, queso cheddar, crema griega, cebolla caramelizada en reducción de guayaba, mayonesa de cilantro, salsa BBQ y papas fritas', 27000, 'COP', 'BURGERS', 'BL-BUR-04', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'AMERICANA', 'Pan artesanal, 150gr de carne, queso cheddar, tomate, lechuga, pepinillos caramelizados, mayonesa de cilantro, salsa BBQ, mostaza artesanal y papas fritas', 22000, 'COP', 'BURGERS', 'BL-BUR-05', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'BIMOTA', 'Pan artesanal, 150gr de carne, tocineta, queso crema, chimichurri, aros de cebolla morados, tomate, mayonesa de cilantro, salsa chipotle, reducción de maracuyá y papas fritas', 27000, 'COP', 'BURGERS', 'BL-BUR-06', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'MONTESA', 'Pan artesanal, 150gr de carne, tocineta, cebolla caramelizada, queso azul, tomate asado, aros de cebolla apanados, salsa chipotle, salsa BBQ y papas fritas', 30000, 'COP', 'BURGERS', 'BL-BUR-07', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'MANHATTAN', 'Pan artesanal, 150gr de carne, tocineta, queso mozzarella, pepinillos caramelizados, cebolla crispy, salsa tártara, mostaza americana, salsa chipotle y papas fritas', 28000, 'COP', 'BURGERS', 'BL-BUR-08', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'LA VUELTA', 'Pan artesanal, 150gr de carne, tocineta crispy de cebolla, caramelizado de chilacuan, queso quajada, salsa tártara, salsa chipotle, mostaza americana y papas fritas', 29000, 'COP', 'BURGERS', 'BL-BUR-09', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'HONEY BURGER', 'Pan artesanal, 150gr de carne, tocineta, queso cheddar, cebolla caramelizada, cebolla crispy, salsa BBQ, salsa chipotle y papas fritas', 28000, 'COP', 'BURGERS', 'BL-BUR-10', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'MONTANA', 'Pan artesanal, 150gr de carne, queso mozzarella, mermelada de tomate cherry, albahaca, salsa tatemada con concho de frito, salsa tártara, cebolla crispy y papas fritas', 28000, 'COP', 'BURGERS', 'BL-BUR-11', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'AL PASTOR', 'Pan artesanal, 150gr de carne, queso mozzarella, carne de cerdo al pastor con piña asada, cebolla crispy, salsa chipotle, crema agria, mayonesa de cilantro y papas fritas', 27000, 'COP', 'BURGERS', 'BL-BUR-12', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'MEXICAN BURGER', 'Pan artesanal, 150gr de carne, queso mozzarella, tocineta, pico de gallo, jalapeño, crema agria, salsa de tamarindo y papas fritas', 27000, 'COP', 'BURGERS', 'BL-BUR-13', true);

-- HOT DOGS
INSERT INTO products (business_id, name, description, price, currency, category, sku, is_active) VALUES
('44488756-473b-46d2-a907-9f579e98ecfd', 'PEGORETTI', 'Pan artesanal, salchicha americana, queso mozzarella, trozos de pollo apanado, tomate cherry caramelizado, cebolla crispy, salsa tártara, salsa BBQ, mostaza y papas fritas', 27000, 'COP', 'HOT DOGS', 'BL-HD-01', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'DENVER', 'Pan artesanal, salchicha americana, queso mozzarella, queso cheddar, tocineta, cebolla caramelizada, mayonesa de cilantro, cebolla crispy y papas fritas', 27000, 'COP', 'HOT DOGS', 'BL-HD-02', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'SPECIAL DOG', 'Pan artesanal, salchicha americana, trozos de costilla en salsa maracuyá, papas trituradas, crema griega, salsa chipotle, mayonesa de cilantro y papas fritas', 27000, 'COP', 'HOT DOGS', 'BL-HD-03', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'NAIROBI', 'Pan artesanal, salchicha americana, queso mozzarella, costilla en salsa BBQ, mayonesa de cilantro, cebolla morada encurtida, ripio triturado y papas fritas', 27000, 'COP', 'HOT DOGS', 'BL-HD-04', true);

-- FRIES
INSERT INTO products (business_id, name, description, price, currency, category, sku, is_active) VALUES
('44488756-473b-46d2-a907-9f579e98ecfd', 'SPECIAL FRIES', 'Papas fritas, salchicha americana, chorizo artesanal, plátano maduro, albahaca, queso parmesano y pico de gallo', 30000, 'COP', 'FRIES', 'BL-FR-01', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'SALCHIPAPA', 'Papas fritas y salchicha americana, acompañadas de tu salsa favorita', 18000, 'COP', 'FRIES', 'BL-FR-02', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'BIELA FRIES', 'Papas fritas con queso crema, queso parmesano, salchicha americana, tocineta caramelizada, mayonesa de cilantro, mermelada de tomate cherry y albahaca', 28000, 'COP', 'FRIES', 'BL-FR-03', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'CHEESE FRIES', 'Papas fritas con queso cheddar, tocineta caramelizada y queso parmesano', 27000, 'COP', 'FRIES', 'BL-FR-04', true);

-- CHICKEN BURGERS
INSERT INTO products (business_id, name, description, price, currency, category, sku, is_active) VALUES
('44488756-473b-46d2-a907-9f579e98ecfd', 'BOOSTER', 'Pan artesanal, filete de pollo apanado, cebolla caramelizada, tomate, lechuga, salsa tártara, salsa BBQ, mostaza y papas fritas', 28000, 'COP', 'CHICKEN BURGERS', 'BL-CB-01', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'VITTORIA', 'Pan artesanal, filete de pollo apanado, albahaca, mermelada de tomate cherry, cebolla crispy, salsa tártara, mostaza y papas fritas', 28000, 'COP', 'CHICKEN BURGERS', 'BL-CB-02', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'ARIZONA', 'Pan artesanal, filete de pollo apanado, tocineta, pepinillos caramelizados, salsa chipotle, salsa tártara, cebolla crispy y papas fritas', 28000, 'COP', 'CHICKEN BURGERS', 'BL-CB-03', true);

-- MENÚ INFANTIL
INSERT INTO products (business_id, name, description, price, currency, category, sku, is_active) VALUES
('44488756-473b-46d2-a907-9f579e98ecfd', 'MINI BURGER', 'Mini burger clásica, pops de pollo apanado, papas fritas, pastel de brownie, mermelada de frutos rojos y helado', 40000, 'COP', 'MENÚ INFANTIL', 'BL-MI-01', true);

-- STEAK & RIBS
INSERT INTO products (business_id, name, description, price, currency, category, sku, is_active) VALUES
('44488756-473b-46d2-a907-9f579e98ecfd', 'COSTILLAS DE CERDO EN SALSA BBQ', 'Costilla de cerdo acompañada de papas fritas, cebolla encurtida y guacamole', 38000, 'COP', 'STEAK & RIBS', 'BL-SR-01', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'PICADA', 'Papas fritas, carne de cerdo con chimichurri, chorizo artesanal, crispetas de pollo, costillas de cerdo en salsa BBQ, salchicha americana, aborrajado de plátano maduro con queso y bocadillo', 55000, 'COP', 'STEAK & RIBS', 'BL-SR-02', true);

-- BEBIDAS
INSERT INTO products (business_id, name, description, price, currency, category, sku, is_active) VALUES
('44488756-473b-46d2-a907-9f579e98ecfd', 'Limonada de cereza', 'Limonada natural preparada con jarabe de cereza y hielo', 12000, 'COP', 'BEBIDAS', 'BL-BEB-01', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Limonada de fresa', 'Limonada natural con fresas frescas licuadas y hielo', 10000, 'COP', 'BEBIDAS', 'BL-BEB-02', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Limonada de hierba buena', 'Limonada natural con hojas de hierba buena fresca, refrescante y ligeramente mentolada', 9000, 'COP', 'BEBIDAS', 'BL-BEB-03', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Limonada natural', 'Limonada clásica preparada con limón fresco exprimido al momento y hielo', 6500, 'COP', 'BEBIDAS', 'BL-BEB-04', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Hervido Maracuyá', 'Bebida caliente preparada con maracuyá, especias y aguardiente opcional. Tradicional colombiana para clima frío', 9500, 'COP', 'BEBIDAS', 'BL-BEB-05', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Hervido Mora', 'Bebida caliente preparada con mora, especias y aguardiente opcional. Tradicional colombiana para clima frío', 9500, 'COP', 'BEBIDAS', 'BL-BEB-06', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Malteada Maracuyá y uvilla', 'Malteada cremosa de maracuyá y uvilla con helado y leche, decorada con crema', 15000, 'COP', 'BEBIDAS', 'BL-BEB-07', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Malteada Brownie', 'Malteada de chocolate con trozos de brownie, helado y crema batida', 15000, 'COP', 'BEBIDAS', 'BL-BEB-08', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Malteada Frutos rojos', 'Malteada cremosa de frutos rojos (fresa, mora, arándano) con helado y crema', 15000, 'COP', 'BEBIDAS', 'BL-BEB-09', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Jugos en agua', 'Jugo natural de la fruta del día preparado en agua', 7500, 'COP', 'BEBIDAS', 'BL-BEB-10', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Jugos en leche', 'Jugo natural de la fruta del día preparado en leche', 7500, 'COP', 'BEBIDAS', 'BL-BEB-11', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Coca-Cola', 'Gaseosa Coca-Cola tradicional en botella personal', 5500, 'COP', 'BEBIDAS', 'BL-BEB-12', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Coca-Cola Zero', 'Gaseosa Coca-Cola Zero sin azúcar en botella personal', 5500, 'COP', 'BEBIDAS', 'BL-BEB-13', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Soda', 'Agua con gas (club soda) en botella personal', 4500, 'COP', 'BEBIDAS', 'BL-BEB-14', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Agua', 'Agua sin gas en botella personal', 4000, 'COP', 'BEBIDAS', 'BL-BEB-15', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Club Colombia', 'Cerveza colombiana tipo lager rubia, en botella de 330ml', 7500, 'COP', 'BEBIDAS', 'BL-BEB-16', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Poker', 'Cerveza colombiana tipo lager rubia, en botella de 330ml', 7500, 'COP', 'BEBIDAS', 'BL-BEB-17', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Corona 355ml', 'Cerveza mexicana tipo lager importada en botella de 355ml', 12000, 'COP', 'BEBIDAS', 'BL-BEB-18', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Corona michelada', 'Corona preparada al estilo michelada con limón, sal y salsas. Servida en copa escarchada', 14500, 'COP', 'BEBIDAS', 'BL-BEB-19', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Michelada', 'Cerveza nacional (Club Colombia o Poker) preparada con limón, sal y salsas. Servida en copa escarchada', 12000, 'COP', 'BEBIDAS', 'BL-BEB-20', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Soda Uvilla y maracuyá', 'Soda saborizada con pulpa natural de uvilla y maracuyá, servida con hielo', 15000, 'COP', 'BEBIDAS', 'BL-BEB-21', true),
('44488756-473b-46d2-a907-9f579e98ecfd', 'Soda Frutos rojos', 'Soda saborizada con mezcla natural de frutos rojos (fresa, mora, arándano), servida con hielo', 15000, 'COP', 'BEBIDAS', 'BL-BEB-22', true);
