"""
Unit tests for order_tools.py — tool functions in isolation.
Tests each tool with mocked DB services (product_order_service, session_state_service, customer_service).
"""

import pytest
from unittest.mock import patch, MagicMock

from tests.conftest import FAKE_BUSINESS_ID, FAKE_WA_ID, SAMPLE_PRODUCTS


def _make_ctx(business_id=FAKE_BUSINESS_ID, wa_id=FAKE_WA_ID):
    return {"business_id": business_id, "wa_id": wa_id}


# ---------------------------------------------------------------------------
# add_to_cart
# ---------------------------------------------------------------------------

class TestAddToCart:
    """Test the add_to_cart tool."""

    def test_add_single_product_by_name(self, fake_session, sample_products):
        """Adding a product by name should find it in DB and add to session cart."""
        mock_product_service = MagicMock()
        mock_product_service.get_product.return_value = sample_products[0]  # BARRACUDA

        with patch("app.services.order_tools.product_order_service", mock_product_service), \
             patch("app.services.order_tools.session_state_service", fake_session):
            from app.services.order_tools import add_to_cart

            result = add_to_cart.invoke({
                "injected_business_context": _make_ctx(),
                "product_id": "",
                "product_name": "barracuda",
                "quantity": 1,
                "notes": "",
            })

        assert "✅" in result
        assert "BARRACUDA" in result
        # Verify cart was saved to session
        session = fake_session.load(FAKE_WA_ID, FAKE_BUSINESS_ID)
        items = session["session"]["order_context"].get("items", [])
        assert len(items) == 1
        assert items[0]["name"] == "BARRACUDA"
        assert items[0]["quantity"] == 1

    # Case: Add product with notes ("sin cebolla") — notes field saved on item
    # Case: Add same product twice without notes — quantity increments instead of duplicate
    # Case: Add same product with different notes — creates separate line item
    # Case: Add product with quantity > 1
    # Case: Product not found by name → returns ❌ error
    # Case: Quantity < 1 → returns ❌ error
    # Case: Missing wa_id or business_id → returns ❌ error
    # Case: Products not enabled (settings.products_enabled=false) → returns ❌


# ---------------------------------------------------------------------------
# remove_from_cart
# ---------------------------------------------------------------------------

class TestRemoveFromCart:
    """Test the remove_from_cart tool."""

    # Case: Remove by product_id — item removed, total recalculated
    # Case: Remove by product_name (exact match) — resolves to product_id, removes
    # Case: Remove by product_name (partial/fuzzy match) — e.g. "coca" matches "COCA COLA"
    # Case: Product not in cart → returns ❌ "no encontré ese producto"
    # Case: Remove last item → cart becomes empty, total = 0


# ---------------------------------------------------------------------------
# update_cart_item
# ---------------------------------------------------------------------------

class TestUpdateCartItem:
    """Test the update_cart_item tool."""

    # Case: Update notes on existing item (quantity stays same)
    # Case: Update quantity to 0 → item removed
    # Case: Update quantity to new value
    # Case: Product not in cart (invalid product_id) → returns ❌
    # Case: Both notes and quantity=0 → keeps item with new notes (quantity preserved)


# ---------------------------------------------------------------------------
# view_cart
# ---------------------------------------------------------------------------

class TestViewCart:
    """Test the view_cart tool."""

    # Case: Empty cart → "Tu pedido está vacío"
    # Case: Cart with items → shows list with quantities, prices, subtotal, delivery fee, total
    # Case: Items with notes → notes shown in parentheses


# ---------------------------------------------------------------------------
# search_products
# ---------------------------------------------------------------------------

class TestSearchProducts:
    """Test the search_products tool."""

    # Case: Search by exact product name → returns matching product(s)
    # Case: Search by ingredient ("queso azul") → returns products with ingredient in description
    # Case: No results → returns ❌ "no hay productos que coincidan"
    # Case: Empty query → returns ❌ "indica el término"
    # Case: Ingredient-like query includes description snippet in results


# ---------------------------------------------------------------------------
# submit_delivery_info
# ---------------------------------------------------------------------------

class TestSubmitDeliveryInfo:
    """Test the submit_delivery_info tool."""

    # Case: Submit address only → merges with existing delivery_info, other fields unchanged
    # Case: Submit all fields at once → all saved
    # Case: Submit with no fields → returns "sin cambios"
    # Case: Submit overwrites previous value (e.g. update address)
    # Case: Partial update preserves previously submitted fields


# ---------------------------------------------------------------------------
# place_order
# ---------------------------------------------------------------------------

class TestPlaceOrder:
    """Test the place_order tool."""

    # Case: Valid cart + complete delivery info → order created, cart cleared, returns ✅
    # Case: Empty cart → returns ❌ "pedido está vacío"
    # Case: Missing address → returns MISSING_DELIVERY_INFO
    # Case: Missing payment_method → returns MISSING_DELIVERY_INFO
    # Case: After success, session order_context is cleared (set to None)
    # Case: Item with invalid quantity or price → returns ❌


# ---------------------------------------------------------------------------
# get_customer_info
# ---------------------------------------------------------------------------

class TestGetCustomerInfo:
    """Test the get_customer_info tool."""

    # Case: No session delivery info, no DB customer → all fields missing
    # Case: Partial session delivery info → merges with DB, shows what's missing
    # Case: All info present (session + DB) → all_present=true
    # Case: Session overrides DB values (session takes priority)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

class TestHelpers:
    """Test internal helpers."""

    # Case: _format_price — formats COP prices with dots as thousands separator
    # Case: _is_ingredient_like_query — "queso azul" → True, "barracuda" → False
    # Case: _cart_from_session — returns correct structure when session is empty
    # Case: _cart_from_session — returns items, total, delivery_info from session
