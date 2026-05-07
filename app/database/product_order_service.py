"""
Service for product catalog and order creation.
Includes category normalization and hybrid lookup (category + semantic fallback).
"""

import logging
import unicodedata
import uuid
from typing import Dict, List, Optional, Any

from .models import Product, Order, OrderItem, OrderPromotion, get_db_session
from .customer_service import customer_service
from ..services.order_status_machine import STATUS_PENDING
from ..services import promotion_service
# Re-export AmbiguousProductError from the new search module for backward compat
from ..services.product_search import (
    AmbiguousProductError,
    ProductNotFoundError,
    search_products as _search_products_hybrid,
)

logger = logging.getLogger(__name__)

# Map natural-language category terms to canonical DB category values
CATEGORY_MAP = {
    # Multi-word phrases first (full-phrase lookup runs before word-by-word)
    "hamburguesas de pollo": "HAMBURGUESAS DE POLLO",
    "hamburguesa de pollo": "HAMBURGUESAS DE POLLO",
    "chicken burgers": "HAMBURGUESAS DE POLLO",
    "chicken burger": "HAMBURGUESAS DE POLLO",
    "perro caliente": "PERROS CALIENTES",
    "perros calientes": "PERROS CALIENTES",
    "hot dog": "PERROS CALIENTES",
    "hot dogs": "PERROS CALIENTES",
    "papas fritas": "SALCHIPAPAS",
    "menu infantil": "MENÚ INFANTIL",
    "steak & ribs": "PARRILLA",
    # Single-word lookups (word-by-word fallback)
    "hamburguesa": "HAMBURGUESAS",
    "hamburguesas": "HAMBURGUESAS",
    "burger": "HAMBURGUESAS",
    "burgers": "HAMBURGUESAS",
    "pollo": "HAMBURGUESAS DE POLLO",
    "chicken": "HAMBURGUESAS DE POLLO",
    "bebida": "BEBIDAS",
    "bebidas": "BEBIDAS",
    "drink": "BEBIDAS",
    "drinks": "BEBIDAS",
    "postre": "POSTRES",
    "postres": "POSTRES",
    "dessert": "POSTRES",
    "desserts": "POSTRES",
    "salchipapa": "SALCHIPAPAS",
    "salchipapas": "SALCHIPAPAS",
    "papas": "SALCHIPAPAS",
    "fries": "SALCHIPAPAS",
    "perro": "PERROS CALIENTES",
    "perros": "PERROS CALIENTES",
    "infantil": "MENÚ INFANTIL",
    "menu": "MENÚ INFANTIL",
    "ninos": "MENÚ INFANTIL",
    "ninas": "MENÚ INFANTIL",
    "parrilla": "PARRILLA",
    "steak": "PARRILLA",
    "ribs": "PARRILLA",
    "costilla": "PARRILLA",
    "costillas": "PARRILLA",
}


def _normalize_for_category_check(s: str) -> str:
    """Lowercase + accent-strip for case/accent-insensitive category overlap."""
    if not s:
        return ""
    raw = s.strip().lower()
    nfkd = unicodedata.normalize("NFD", raw)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


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
    # Try full phrase first, then accent-stripped, then individual words
    canonical = (
        CATEGORY_MAP.get(normalized_key)
        or CATEGORY_MAP.get(raw)
    )
    if not canonical:
        # Try individual words (e.g. "menu infantil" -> try "infantil")
        for word in normalized_key.split():
            canonical = CATEGORY_MAP.get(word)
            if canonical:
                break
    if canonical:
        logger.warning(
            "[CATEGORY_NORMALIZATION] input=%s normalized=%s",
            input_category.strip(),
            canonical,
        )
        return canonical
    return input_category.strip()


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
        Get a single product by ID or by name.

        For ID lookup: direct DB fetch.
        For name lookup: delegates to the hybrid search module with unique=True,
        which raises AmbiguousProductError if there's no clear winner.
        """
        try:
            if product_id:
                db_session = get_db_session()
                product = (
                    db_session.query(Product)
                    .filter(
                        Product.id == uuid.UUID(product_id),
                        Product.is_active == True,
                    )
                    .first()
                )
                result = product.to_dict() if product else None
                db_session.close()
                return result
            if product_name and business_id:
                results = _search_products_hybrid(
                    business_id=business_id,
                    query=product_name.strip(),
                    limit=5,
                    unique=True,
                )
                return results[0] if results else None
            return None
        except AmbiguousProductError:
            raise
        except Exception as e:
            logger.error(f"[PRODUCT_ORDER] Error getting product: {e}")
            return None

    def search_products(
        self,
        business_id: str,
        query: str,
        limit: int = 20,
        unique: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Search products by name, description, category, tags, and semantic
        similarity. Delegates to the hybrid product_search module.

        ``limit`` and ``unique`` are forwarded so callers (e.g.
        catalog_service.search_products, which accepts these as a public
        contract) don't crash with TypeError. Prior to this signature
        fix, the catalog_service refactor in 3e9e02e silently passed
        these kwargs and SEARCH_PRODUCTS in order_flow always crashed
        with "got an unexpected keyword argument 'limit'".
        """
        try:
            if not query or not query.strip():
                return []
            return _search_products_hybrid(
                business_id=business_id,
                query=query.strip(),
                limit=limit,
                unique=unique,
            )
        except Exception as e:
            logger.error(f"[PRODUCT_ORDER] Error searching products: {e}")
            return []

    def search_products_semantic(
        self,
        business_id: str,
        query: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Deprecated alias — kept for callers. Delegates to hybrid search."""
        try:
            if not query or not query.strip():
                return []
            return _search_products_hybrid(
                business_id=business_id,
                query=query.strip(),
                limit=limit,
                unique=False,
            )
        except Exception as e:
            logger.error(f"[PRODUCT_ORDER] Error in search_products_semantic: {e}")
            return []

    def list_products_with_fallback(
        self,
        business_id: str,
        category: str,
    ) -> List[Dict[str, Any]]:
        """
        List products by category with normalization and bounded fallback.

        1) Normalize category (e.g. hamburguesas -> BURGERS), list by category.
        2) If no rows, fall back to the hybrid search. The hybrid search's
           pure-embedding filter (in search_products) already handles the
           "pizza at a burger shop" case — it returns empty when only
           embedding-based candidates exist and no lexical/tag signal matches.
           This means sub-category terms like "cervezas" (which exist as tags
           on beer products but not as a DB category) now correctly resolve
           to the matching products instead of returning "no tenemos cervezas".

        Historical note: commit ac2a6a3 added a category-existence pre-check
        here that blocked the fallback when the search term didn't overlap
        any DB category name. That pre-check was redundant with the
        pure-embedding filter and overly aggressive — it blocked legitimate
        tag-based sub-category searches like "cervezas" (products are in
        category BEBIDAS but tagged "cerveza"). Removed in this commit.
        """
        raw = (category or "").strip()
        normalized = normalize_category(category) if raw else ""
        products = self.list_products(business_id=business_id, category=normalized or None)
        if products or not raw:
            return products

        logger.warning(
            "[LOOKUP_FALLBACK] category_lookup_empty → hybrid_search_used category=%s",
            raw,
        )
        return self.search_products_semantic(business_id=business_id, query=raw)

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

                # Pre-promo line_total. promotion_service.match_and_apply
                # may overwrite this and split the line into a bundled +
                # leftover pair if a promo only consumes part of it.
                order_items_data.append({
                    "product_id": product_id,
                    "quantity": quantity,
                    "unit_price": price,
                    "line_total": price * quantity,
                    "notes": (item.get("notes") or "").strip() or None,
                    "promotion_id": item.get("promotion_id"),
                    "promo_group_id": item.get("promo_group_id"),
                })

            # Run the promo matcher. Honors agent-set bindings, then
            # greedily applies the best-discount promo to leftovers.
            pricing = promotion_service.match_and_apply(
                business_id=business_id,
                cart_items=order_items_data,
            )
            order_items_data = pricing["items"]
            subtotal = float(pricing["subtotal_after_promos"])
            promo_discount = float(pricing["promo_discount_total"])
            applications = pricing["applications"]

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

            if customer_id is not None:
                customer_service.link_customer_to_business(
                    customer_id=customer_id,
                    business_id=business_id,
                    source="auto",
                )

            grand_total = subtotal + float(delivery_fee)

            order = Order(
                business_id=uuid.UUID(business_id),
                customer_id=customer_id,
                whatsapp_id=whatsapp_id,
                status=STATUS_PENDING,
                total_amount=grand_total,
                promo_discount_amount=promo_discount,
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
                    notes=oi.get("notes"),
                    promotion_id=uuid.UUID(oi["promotion_id"]) if oi.get("promotion_id") else None,
                    promo_group_id=uuid.UUID(oi["promo_group_id"]) if oi.get("promo_group_id") else None,
                )
                db_session.add(order_item)

            for app in applications:
                db_session.add(OrderPromotion(
                    order_id=order.id,
                    promotion_id=uuid.UUID(app["promotion_id"]),
                    promotion_name=app["promotion_name"],
                    pricing_mode=app["pricing_mode"],
                    discount_applied=app["discount_applied"],
                ))

            db_session.commit()
            order_id = str(order.id)
            db_session.close()

            logger.info(
                f"[PRODUCT_ORDER] Created order {order_id} for {whatsapp_id}, "
                f"subtotal={subtotal}, promo_discount={promo_discount}, "
                f"delivery_fee={delivery_fee}, total={grand_total}, "
                f"applied_promos={len(applications)}, address={bool(delivery_address)}"
            )
            return {
                "success": True,
                "order_id": order_id,
                "subtotal": subtotal,
                "total": grand_total,
                "promo_discount": promo_discount,
                "applied_promos": [
                    {"name": a["promotion_name"], "discount": a["discount_applied"]}
                    for a in applications
                ],
            }
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
