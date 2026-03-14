-- Seed Biela menu products for business aacb60a5-03b0-4a09-ab6f-9aa10eabdc13
-- Run after migration 008 (adds category column)
-- Supabase uses PostgreSQL

-- BURGERS
INSERT INTO products (business_id, name, description, price, currency, category, sku, is_active) VALUES
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'BARRACUDA', 'Pan artesanal, 150gr de carne, tocineta, queso mozzarella, queso cheddar, cebolla caramelizada, mayonesa de cilantro, salsa BBQ, mostaza dulce y papas fritas', 28000, 'COP', 'BURGERS', 'BL-BUR-01', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'BIELA', 'Pan artesanal, 150gr de carne, jamón, queso mozzarella, tomate, lechuga, mayonesa de cilantro, salsa de ajo, salsa BBQ y papas fritas', 28000, 'COP', 'BURGERS', 'BL-BUR-02', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'BETA', 'Pan artesanal, 150gr de carne, tocineta, queso mozzarella, queso cheddar, queso parmesano, queso crema, pepinillos caramelizados, mayonesa de cilantro, salsa BBQ y papas fritas', 28000, 'COP', 'BURGERS', 'BL-BUR-03', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'ARRABBIATA', 'Pan artesanal, 150gr de carne, tocineta, queso cheddar, crema griega, cebolla caramelizada en reducción de guayaba, mayonesa de cilantro, salsa BBQ y papas fritas', 27000, 'COP', 'BURGERS', 'BL-BUR-04', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'AMERICANA', 'Pan artesanal, 150gr de carne, queso cheddar, tomate, lechuga, pepinillos caramelizados, mayonesa de cilantro, salsa BBQ, mostaza artesanal y papas fritas', 22000, 'COP', 'BURGERS', 'BL-BUR-05', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'BIMOTA', 'Pan artesanal, 150gr de carne, tocineta, queso crema, chimichurri, aros de cebolla morados, tomate, mayonesa de cilantro, salsa chipotle, reducción de maracuyá y papas fritas', 27000, 'COP', 'BURGERS', 'BL-BUR-06', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'MONTESA', 'Pan artesanal, 150gr de carne, tocineta, cebolla caramelizada, queso azul, tomate asado, aros de cebolla apanados, salsa chipotle, salsa BBQ y papas fritas', 30000, 'COP', 'BURGERS', 'BL-BUR-07', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'MANHATTAN', 'Pan artesanal, 150gr de carne, tocineta, queso mozzarella, pepinillos caramelizados, cebolla crispy, salsa tártara, mostaza americana, salsa chipotle y papas fritas', 28000, 'COP', 'BURGERS', 'BL-BUR-08', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'LA VUELTA', 'Pan artesanal, 150gr de carne, tocineta crispy de cebolla, caramelizado de chilacuan, queso quajada, salsa tártara, salsa chipotle, mostaza americana y papas fritas', 29000, 'COP', 'BURGERS', 'BL-BUR-09', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'HONEY BURGER', 'Pan artesanal, 150gr de carne, tocineta, queso cheddar, cebolla caramelizada, cebolla crispy, salsa BBQ, salsa chipotle y papas fritas', 28000, 'COP', 'BURGERS', 'BL-BUR-10', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'MONTANA', 'Pan artesanal, 150gr de carne, queso mozzarella, mermelada de tomate cherry, albahaca, salsa tatemada con concho de frito, salsa tártara, cebolla crispy y papas fritas', 28000, 'COP', 'BURGERS', 'BL-BUR-11', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'AL PASTOR', 'Pan artesanal, 150gr de carne, queso mozzarella, carne de cerdo al pastor con piña asada, cebolla crispy, salsa chipotle, crema agria, mayonesa de cilantro y papas fritas', 27000, 'COP', 'BURGERS', 'BL-BUR-12', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'MEXICAN BURGER', 'Pan artesanal, 150gr de carne, queso mozzarella, tocineta, pico de gallo, jalapeño, crema agria, salsa de tamarindo y papas fritas', 27000, 'COP', 'BURGERS', 'BL-BUR-13', true);

-- HOT DOGS
INSERT INTO products (business_id, name, description, price, currency, category, sku, is_active) VALUES
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'PEGORETTI', 'Pan artesanal, salchicha americana, queso mozzarella, trozos de pollo apanado, tomate cherry caramelizado, cebolla crispy, salsa tártara, salsa BBQ, mostaza y papas fritas', 27000, 'COP', 'HOT DOGS', 'BL-HD-01', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'DENVER', 'Pan artesanal, salchicha americana, queso mozzarella, queso cheddar, tocineta, cebolla caramelizada, mayonesa de cilantro, cebolla crispy y papas fritas', 27000, 'COP', 'HOT DOGS', 'BL-HD-02', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'SPECIAL DOG', 'Pan artesanal, salchicha americana, trozos de costilla en salsa maracuyá, papas trituradas, crema griega, salsa chipotle, mayonesa de cilantro y papas fritas', 27000, 'COP', 'HOT DOGS', 'BL-HD-03', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'NAIROBI', 'Pan artesanal, salchicha americana, queso mozzarella, costilla en salsa BBQ, mayonesa de cilantro, cebolla morada encurtida, ripio triturado y papas fritas', 27000, 'COP', 'HOT DOGS', 'BL-HD-04', true);

-- FRIES
INSERT INTO products (business_id, name, description, price, currency, category, sku, is_active) VALUES
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'SPECIAL FRIES', 'Papas fritas, salchicha americana, chorizo artesanal, plátano maduro, albahaca, queso parmesano y pico de gallo', 30000, 'COP', 'FRIES', 'BL-FR-01', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'SALCHIPAPA', 'Papas fritas y salchicha americana, acompañadas de tu salsa favorita', 18000, 'COP', 'FRIES', 'BL-FR-02', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'BIELA FRIES', 'Papas fritas con queso crema, queso parmesano, salchicha americana, tocineta caramelizada, mayonesa de cilantro, mermelada de tomate cherry y albahaca', 28000, 'COP', 'FRIES', 'BL-FR-03', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'CHEESE FRIES', 'Papas fritas con queso cheddar, tocineta caramelizada y queso parmesano', 27000, 'COP', 'FRIES', 'BL-FR-04', true);

-- CHICKEN BURGERS
INSERT INTO products (business_id, name, description, price, currency, category, sku, is_active) VALUES
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'BOOSTER', 'Pan artesanal, filete de pollo apanado, cebolla caramelizada, tomate, lechuga, salsa tártara, salsa BBQ, mostaza y papas fritas', 28000, 'COP', 'CHICKEN BURGERS', 'BL-CB-01', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'VITTORIA', 'Pan artesanal, filete de pollo apanado, albahaca, mermelada de tomate cherry, cebolla crispy, salsa tártara, mostaza y papas fritas', 28000, 'COP', 'CHICKEN BURGERS', 'BL-CB-02', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'ARIZONA', 'Pan artesanal, filete de pollo apanado, tocineta, pepinillos caramelizados, salsa chipotle, salsa tártara, cebolla crispy y papas fritas', 28000, 'COP', 'CHICKEN BURGERS', 'BL-CB-03', true);

-- MENÚ INFANTIL
INSERT INTO products (business_id, name, description, price, currency, category, sku, is_active) VALUES
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'MINI BURGER', 'Mini burger clásica, pops de pollo apanado, papas fritas, pastel de brownie, mermelada de frutos rojos y helado', 40000, 'COP', 'MENÚ INFANTIL', 'BL-MI-01', true);

-- STEAK & RIBS
INSERT INTO products (business_id, name, description, price, currency, category, sku, is_active) VALUES
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'COSTILLAS DE CERDO EN SALSA BBQ', 'Costilla de cerdo acompañada de papas fritas, cebolla encurtida y guacamole', 38000, 'COP', 'STEAK & RIBS', 'BL-SR-01', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'PICADA', 'Papas fritas, carne de cerdo con chimichurri, chorizo artesanal, crispetas de pollo, costillas de cerdo en salsa BBQ, salchicha americana, aborrajado de plátano maduro con queso y bocadillo', 55000, 'COP', 'STEAK & RIBS', 'BL-SR-02', true);

-- BEBIDAS
INSERT INTO products (business_id, name, description, price, currency, category, sku, is_active) VALUES
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Limonada de cereza', NULL, 12000, 'COP', 'BEBIDAS', 'BL-BEB-01', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Limonada de fresa', NULL, 10000, 'COP', 'BEBIDAS', 'BL-BEB-02', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Limonada de hierba buena', NULL, 9000, 'COP', 'BEBIDAS', 'BL-BEB-03', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Limonada natural', NULL, 6500, 'COP', 'BEBIDAS', 'BL-BEB-04', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Hervido Maracuyá', NULL, 9500, 'COP', 'BEBIDAS', 'BL-BEB-05', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Hervido Mora', NULL, 9500, 'COP', 'BEBIDAS', 'BL-BEB-06', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Malteada Maracuyá y uvilla', NULL, 15000, 'COP', 'BEBIDAS', 'BL-BEB-07', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Malteada Brownie', NULL, 15000, 'COP', 'BEBIDAS', 'BL-BEB-08', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Malteada Frutos rojos', NULL, 15000, 'COP', 'BEBIDAS', 'BL-BEB-09', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Jugos en agua', NULL, 7500, 'COP', 'BEBIDAS', 'BL-BEB-10', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Jugos en leche', NULL, 7500, 'COP', 'BEBIDAS', 'BL-BEB-11', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Coca-Cola', NULL, 5500, 'COP', 'BEBIDAS', 'BL-BEB-12', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Coca-Cola Zero', NULL, 5500, 'COP', 'BEBIDAS', 'BL-BEB-13', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Soda', NULL, 4500, 'COP', 'BEBIDAS', 'BL-BEB-14', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Agua', NULL, 4000, 'COP', 'BEBIDAS', 'BL-BEB-15', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Club Colombia', NULL, 7500, 'COP', 'BEBIDAS', 'BL-BEB-16', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Poker', NULL, 7500, 'COP', 'BEBIDAS', 'BL-BEB-17', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Corona 355ml', NULL, 12000, 'COP', 'BEBIDAS', 'BL-BEB-18', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Corona michelada', NULL, 14500, 'COP', 'BEBIDAS', 'BL-BEB-19', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Michelada', NULL, 12000, 'COP', 'BEBIDAS', 'BL-BEB-20', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Soda Uvilla y maracuyá', NULL, 15000, 'COP', 'BEBIDAS', 'BL-BEB-21', true),
('aacb60a5-03b0-4a09-ab6f-9aa10eabdc13', 'Soda Frutos rojos', NULL, 15000, 'COP', 'BEBIDAS', 'BL-BEB-22', true);
