"""
Service for product catalog and order creation.
Includes category normalization and hybrid lookup (category + semantic fallback).
"""

import logging
import unicodedata
import uuid
from typing import Dict, List, Optional, Any

from .models import Product, Order, OrderItem, get_db_session
from .customer_service import customer_service

logger = logging.getLogger(__name__)

# Map natural-language category terms to canonical DB category values
CATEGORY_MAP = {
    "hamburguesa": "BURGERS",
    "hamburguesas": "BURGERS",
    "burger": "BURGERS",
    "burgers": "BURGERS",
    "bebida": "BEBIDAS",
    "bebidas": "BEBIDAS",
    "drink": "BEBIDAS",
    "drinks": "BEBIDAS",
    "postre": "DESSERTS",
    "postres": "DESSERTS",
    "dessert": "DESSERTS",
    "desserts": "DESSERTS",
    "papas": "FRIES",
    "fries": "FRIES",
    "hot dog": "HOT DOGS",
    "hot dogs": "HOT DOGS",
    "perro": "HOT DOGS",
    "perros": "HOT DOGS",
    "pollo": "CHICKEN BURGERS",
    "chicken": "CHICKEN BURGERS",
    "infantil": "MENÚ INFANTIL",
    "niños": "MENÚ INFANTIL",
    "niñas": "MENÚ INFANTIL",
    "steak": "STEAK & RIBS",
    "ribs": "STEAK & RIBS",
    "costilla": "STEAK & RIBS",
    "costillas": "STEAK & RIBS",
}


def normalize_category(input_category: str) -> str:
    """
    Map natural-language category to canonical DB category.
    Lowercases and strips; optional accent stripping; lookup in CATEGORY_MAP; else return original.
    """
    if not input_category or not input_category.strip():
        return input_category or ""
    raw = input_category.strip().lower()
    # Strip accents for lookup (e.g. "bebidas" vs "bebídas")
    normalized_key = unicodedata.normalize("NFD", raw)
    normalized_key = "".join(c for c in normalized_key if unicodedata.category(c) != "Mn")
    canonical = CATEGORY_MAP.get(normalized_key) or CATEGORY_MAP.get(raw)
    if canonical:
        logger.warning(
            "[CATEGORY_NORMALIZATION] input=%s normalized=%s",
            input_category.strip(),
            canonical,
        )
        return canonical
    return input_category.strip()


# Common words to skip when searching by tokens (Spanish)
_SEARCH_STOPWORDS = frozenset(
    {"una", "un", "la", "el", "de", "con", "para", "por", "y", "e", "o", "u", "del", "al", "los", "las", "unos", "unas",
     "que", "en", "lo", "le", "se", "da", "al", "algo", "uno", "como", "mas", "pero", "sus", "este", "esta", "este",
     "hamburguesa", "burger", "bebida", "gaseosa", "refresco"}  # generic product terms - search by distinctive part
)


def _search_tokens(query: str) -> list:
    """Extract significant search tokens from query, normalized."""
    if not query or not query.strip():
        return []
    words = query.strip().lower().replace("-", " ").replace(",", " ").split()
    return [w for w in words if len(w) > 1 and w not in _SEARCH_STOPWORDS]


class ProductOrderService:
    """Service for products and orders."""

    def list_products(
        self,
        business_id: str,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List active products for a business, optionally filtered by category.

        Args:
            business_id: Business UUID
            category: Optional category filter (case-insensitive)

        Returns:
            List of product dicts with id, name, description, price, currency, category
        """
        try:
            db_session = get_db_session()
            query = (
                db_session.query(Product)
                .filter(
                    Product.business_id == uuid.UUID(business_id),
                    Product.is_active == True,
                )
                .order_by(Product.category, Product.name)
            )
            if category and category.strip():
                query = query.filter(
                    Product.category.ilike(f"%{category.strip()}%")
                )
            products = query.all()
            result = [p.to_dict() for p in products]
            db_session.close()
            return result
        except Exception as e:
            logger.error(f"[PRODUCT_ORDER] Error listing products: {e}")
            return []

    def list_categories(self, business_id: str) -> List[str]:
        """
        Return distinct category names for active products of a business, ordered by name.

        Args:
            business_id: Business UUID

        Returns:
            List of non-null category strings (e.g. ["BEBIDAS", "HAMBURGUESAS", ...])
        """
        try:
            db_session = get_db_session()
            rows = (
                db_session.query(Product.category)
                .filter(
                    Product.business_id == uuid.UUID(business_id),
                    Product.is_active == True,
                    Product.category.isnot(None),
                    Product.category != "",
                )
                .distinct()
                .order_by(Product.category)
                .all()
            )
            result = [r[0] for r in rows if r[0]]
            db_session.close()
            return result
        except Exception as e:
            logger.error(f"[PRODUCT_ORDER] Error listing categories: {e}")
            return []

    def get_product(
        self,
        product_id: Optional[str] = None,
        product_name: Optional[str] = None,
        business_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get a single product by ID or by name (fuzzy match).

        Args:
            product_id: Product UUID (takes precedence)
            product_name: Product name to search (used if product_id not provided)
            business_id: Business UUID (required for name search)

        Returns:
            Product dict or None if not found
        """
        try:
            db_session = get_db_session()
            product = None

            if product_id:
                product = (
                    db_session.query(Product)
                    .filter(
                        Product.id == uuid.UUID(product_id),
                        Product.is_active == True,
                    )
                    .first()
                )
            elif product_name and business_id:
                product = self._find_product_by_name_or_desc(
                    db_session, business_id, product_name.strip()
                )

            result = product.to_dict() if product else None
            db_session.close()
            return result
        except Exception as e:
            logger.error(f"[PRODUCT_ORDER] Error getting product: {e}")
            return None

    def _find_product_by_name_or_desc(
        self, db_session, business_id: str, query: str
    ) -> Optional[Any]:
        """Find a product by name or description. Tries full query, then token-by-token."""
        from sqlalchemy import or_, func

        business_uuid = uuid.UUID(business_id)
        qnorm = query.strip().lower()

        def name_or_desc_contains(term: str):
            desc_col = func.coalesce(Product.description, "")
            return or_(
                Product.name.ilike(f"%{term}%"),
                desc_col.ilike(f"%{term}%"),
            )

        base = db_session.query(Product).filter(
            Product.business_id == business_uuid,
            Product.is_active == True,
        )

        # 1. Try full query in name
        product = base.filter(Product.name.ilike(f"%{qnorm}%")).first()
        if product:
            return product

        # 2. Try full query in name or description
        product = base.filter(name_or_desc_contains(qnorm)).first()
        if product:
            return product

        # 3. Try each significant token (e.g. "hamburguesa barracuda" -> "barracuda")
        tokens = _search_tokens(query)
        for tok in tokens:
            product = base.filter(name_or_desc_contains(tok)).first()
            if product:
                return product

        return None

    def search_products(
        self,
        business_id: str,
        query: str,
    ) -> List[Dict[str, Any]]:
        """
        Search products by name, description, or category. Handles multi-word queries:
        - "hamburguesa barracuda" -> matches BARRACUDA
        - "bebidas" -> matches products in category BEBIDAS
        - "coca zero" -> matches Coca-Cola Zero
        """
        try:
            if not query or not query.strip():
                return []
            from sqlalchemy import or_, func

            db_session = get_db_session()
            business_uuid = uuid.UUID(business_id)
            qnorm = query.strip().lower()

            def name_desc_or_category_contains(term: str):
                desc_col = func.coalesce(Product.description, "")
                cat_col = func.coalesce(Product.category, "")
                return or_(
                    Product.name.ilike(f"%{term}%"),
                    desc_col.ilike(f"%{term}%"),
                    cat_col.ilike(f"%{term}%"),
                )

            base = (
                db_session.query(Product)
                .filter(
                    Product.business_id == business_uuid,
                    Product.is_active == True,
                )
            )

            # Build OR of: full query + each significant token
            conditions = [name_desc_or_category_contains(qnorm)]
            for tok in _search_tokens(query):
                if tok != qnorm:
                    conditions.append(name_desc_or_category_contains(tok))

            combined = or_(*conditions)
            products = base.filter(combined).order_by(Product.name).all()
            result = [p.to_dict() for p in products]
            db_session.close()
            return result
        except Exception as e:
            logger.error(f"[PRODUCT_ORDER] Error searching products: {e}")
            return []

    def search_products_semantic(
        self,
        business_id: str,
        query: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Search products by name and description (and ingredients if column exists) using ILIKE.
        No embeddings; for small menus. Used as fallback when category lookup returns no rows.
        """
        try:
            if not query or not query.strip():
                return []
            from sqlalchemy import or_, func

            db_session = get_db_session()
            business_uuid = uuid.UUID(business_id)
            q = query.strip()
            desc_col = func.coalesce(Product.description, "")
            # name OR description (ingredients not in schema; description often holds ingredients)
            condition = or_(
                Product.name.ilike(f"%{q}%"),
                desc_col.ilike(f"%{q}%"),
            )
            products = (
                db_session.query(Product)
                .filter(
                    Product.business_id == business_uuid,
                    Product.is_active == True,
                    condition,
                )
                .order_by(Product.name)
                .limit(limit)
                .all()
            )
            result = [p.to_dict() for p in products]
            db_session.close()
            logger.warning(
                "[SEMANTIC_SEARCH] query=%s results=%s",
                q,
                len(result),
            )
            return result
        except Exception as e:
            logger.error(f"[PRODUCT_ORDER] Error in search_products_semantic: {e}")
            return []

    def list_products_with_fallback(
        self,
        business_id: str,
        category: str,
    ) -> List[Dict[str, Any]]:
        """
        List products by category with normalization and semantic fallback.
        1) Normalize category (e.g. hamburguesas -> BURGERS), list by category.
        2) If no rows, run semantic search on the original category/query string.
        """
        raw = (category or "").strip()
        normalized = normalize_category(category) if raw else ""
        products = self.list_products(business_id=business_id, category=normalized or None)
        if not products and raw:
            logger.warning(
                "[LOOKUP_FALLBACK] category_lookup_empty → semantic_search_used category=%s",
                raw,
            )
            products = self.search_products_semantic(business_id=business_id, query=raw)
        return products

    def create_order(
        self,
        business_id: str,
        whatsapp_id: str,
        items: List[Dict[str, Any]],
        customer_id: Optional[int] = None,
        notes: Optional[str] = None,
        delivery_address: Optional[str] = None,
        contact_phone: Optional[str] = None,
        payment_method: Optional[str] = None,
        customer_name: Optional[str] = None,
        delivery_fee: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Create an order with line items and delivery info.

        Args:
            business_id: Business UUID
            whatsapp_id: Customer WhatsApp ID
            items: List of {product_id, name, price, quantity}
            customer_id: Optional customer ID
            notes: Optional order notes
            delivery_address: Delivery address for this order
            contact_phone: Contact phone (optional, for delivery)
            payment_method: Payment method for this order
            customer_name: Customer name for order; used when creating/updating customer
            delivery_fee: Delivery fee to add to the order total (default 0)

        Returns:
            {"success": True, "order_id": "uuid", "subtotal": float, "total": float} or {"success": False, "error": str}
        """
        try:
            if not items:
                return {"success": False, "error": "El pedido está vacío"}

            db_session = get_db_session()
            total = 0.0
            order_items_data = []

            for item in items:
                product_id = item.get("product_id")
                price = float(item.get("price", 0))
                quantity = int(item.get("quantity", 1))

                if not product_id or price <= 0 or quantity <= 0:
                    db_session.close()
                    return {"success": False, "error": f"Item inválido: {item}"}

                product = (
                    db_session.query(Product)
                    .filter(
                        Product.id == uuid.UUID(product_id),
                        Product.business_id == uuid.UUID(business_id),
                        Product.is_active == True,
                    )
                    .first()
                )
                if not product:
                    db_session.close()
                    return {"success": False, "error": f"Producto no encontrado: {product_id}"}

                line_total = price * quantity
                total += line_total
                order_items_data.append({
                    "product_id": product_id,
                    "quantity": quantity,
                    "unit_price": price,
                    "line_total": line_total,
                })

            # Create or update customer with delivery info
            cust = customer_service.get_customer(whatsapp_id)
            name_to_use = (customer_name or "").strip() or (cust.get("name") if cust else None) or "Cliente"
            if cust:
                customer_service.update_customer(
                    whatsapp_id,
                    name=name_to_use,
                    address=delivery_address or cust.get("address"),
                    phone=contact_phone if contact_phone is not None else cust.get("phone"),
                    payment_method=payment_method or cust.get("payment_method"),
                )
                customer_id = cust.get("id")
            else:
                new_cust = customer_service.create_customer(
                    whatsapp_id=whatsapp_id,
                    name=name_to_use,
                    address=delivery_address,
                    phone=contact_phone,
                    payment_method=payment_method,
                )
                customer_id = new_cust.get("id") if new_cust else None

            subtotal = total
            grand_total = subtotal + float(delivery_fee)

            order = Order(
                business_id=uuid.UUID(business_id),
                customer_id=customer_id,
                whatsapp_id=whatsapp_id,
                status="pending",
                total_amount=grand_total,
                notes=notes,
                delivery_address=delivery_address,
                contact_phone=contact_phone,
                payment_method=payment_method,
            )
            db_session.add(order)
            db_session.flush()

            for oi in order_items_data:
                order_item = OrderItem(
                    order_id=order.id,
                    product_id=uuid.UUID(oi["product_id"]),
                    quantity=oi["quantity"],
                    unit_price=oi["unit_price"],
                    line_total=oi["line_total"],
                )
                db_session.add(order_item)

            db_session.commit()
            order_id = str(order.id)
            db_session.close()

            logger.info(
                f"[PRODUCT_ORDER] Created order {order_id} for {whatsapp_id}, subtotal={subtotal}, "
                f"delivery_fee={delivery_fee}, total={grand_total}, address={bool(delivery_address)}"
            )
            return {"success": True, "order_id": order_id, "subtotal": subtotal, "total": grand_total}
        except Exception as e:
            logger.error(f"[PRODUCT_ORDER] Error creating order: {e}")
            try:
                db_session.rollback()
            except Exception:
                pass
            try:
                db_session.close()
            except Exception:
                pass
            return {"success": False, "error": str(e)}


# Global instance
product_order_service = ProductOrderService()
