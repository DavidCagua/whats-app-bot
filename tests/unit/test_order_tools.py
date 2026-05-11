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
    def test_add_single_product_by_name_with_notes(self, fake_session, sample_products):
        """Adding a product by name should find it in DB and add to session cart with notes."""
        mock_product_service = MagicMock()
        mock_product_service.get_product.return_value = sample_products[0]  # BARRACUDA

        with patch("app.services.order_tools.product_order_service", mock_product_service), \
             patch("app.services.order_tools.session_state_service", fake_session):
            from app.services.order_tools import add_to_cart

            result = add_to_cart.invoke({
                "injected_business_context": _make_ctx(),
                "product_id": "",
                "product_name": "Barracuda",
                "notes": "sin cebolla crispy",
                "quantity": 1,
            })

        assert "✅" in result
        assert "BARRACUDA" in result
        assert "sin cebolla crispy" in result
        # Verify cart was saved to session
        session = fake_session.load(FAKE_WA_ID, FAKE_BUSINESS_ID)
        items = session["session"]["order_context"].get("items", [])
        assert len(items) == 1
        assert items[0]["name"] == "BARRACUDA"
        assert items[0]["quantity"] == 1
        assert items[0]["notes"] == "sin cebolla crispy"

    def test_add_twice_same_product_by_name(self, fake_session, sample_products):
        """Two adds of the same product without notes merge into one line (quantity increments)."""
        mock_product_service = MagicMock()
        mock_product_service.get_product.return_value = sample_products[0]  # BARRACUDA

        invoke_kw = {
            "injected_business_context": _make_ctx(),
            "product_id": "",
            "product_name": "barracuda",
            "quantity": 1,
            "notes": "",
        }
        with patch("app.services.order_tools.product_order_service", mock_product_service), \
             patch("app.services.order_tools.session_state_service", fake_session):
            from app.services.order_tools import add_to_cart

            r1 = add_to_cart.invoke(invoke_kw)
            r2 = add_to_cart.invoke(invoke_kw)

        assert "✅" in r1
        assert "✅" in r2
        assert "$36.000" in r2  # subtotal after second add (2 × 18.000)

        session = fake_session.load(FAKE_WA_ID, FAKE_BUSINESS_ID)
        items = session["session"]["order_context"].get("items", [])
        assert len(items) == 1
        assert items[0]["name"] == "BARRACUDA"
        assert items[0]["product_id"] == "prod-001"
        assert items[0]["quantity"] == 2
        assert session["session"]["order_context"].get("total") == 36000

    def test_add_twice_same_product_with_same_notes_stacks(self, fake_session, sample_products):
        """Two adds with identical notes ("sin bbq", "sin bbq") merge
        into one line at qty=2 — they used to create two separate
        lines, which made remove_from_cart / update_cart_item awkward
        (one nuked everything, the other only one of the duplicates).
        Regression: Biela / 2026-05-09."""
        mock_product_service = MagicMock()
        mock_product_service.get_product.return_value = sample_products[0]  # BARRACUDA

        invoke_kw = {
            "injected_business_context": _make_ctx(),
            "product_id": "",
            "product_name": "barracuda",
            "quantity": 1,
            "notes": "sin bbq",
        }
        with patch("app.services.order_tools.product_order_service", mock_product_service), \
             patch("app.services.order_tools.session_state_service", fake_session):
            from app.services.order_tools import add_to_cart
            add_to_cart.invoke(invoke_kw)
            add_to_cart.invoke(invoke_kw)

        session = fake_session.load(FAKE_WA_ID, FAKE_BUSINESS_ID)
        items = session["session"]["order_context"].get("items", [])
        assert len(items) == 1, (
            f"expected stacked line, got {len(items)} lines: {items}"
        )
        assert items[0]["quantity"] == 2
        assert items[0]["notes"] == "sin bbq"

    def test_add_twice_same_product_with_different_notes_keeps_two_lines(
        self, fake_session, sample_products,
    ):
        """Different notes → still two separate lines (one with the
        original note, one with the new). Stacking is by (product_id,
        notes) tuple, not by product_id alone."""
        mock_product_service = MagicMock()
        mock_product_service.get_product.return_value = sample_products[0]

        kw1 = {
            "injected_business_context": _make_ctx(),
            "product_id": "",
            "product_name": "barracuda",
            "quantity": 1,
            "notes": "sin bbq",
        }
        kw2 = {**kw1, "notes": "sin cebolla"}
        with patch("app.services.order_tools.product_order_service", mock_product_service), \
             patch("app.services.order_tools.session_state_service", fake_session):
            from app.services.order_tools import add_to_cart
            add_to_cart.invoke(kw1)
            add_to_cart.invoke(kw2)

        session = fake_session.load(FAKE_WA_ID, FAKE_BUSINESS_ID)
        items = session["session"]["order_context"].get("items", [])
        assert len(items) == 2
        assert {it["notes"] for it in items} == {"sin bbq", "sin cebolla"}

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

    def _seed_cart(self, fake_session, items):
        from app.database.session_state_service import session_state_service as real_svc
        # The fake_session fixture stores by (wa_id, business_id) — write
        # the seed cart there so remove_from_cart's _cart_from_session sees it.
        fake_session.save(
            FAKE_WA_ID, FAKE_BUSINESS_ID,
            {"order_context": {"items": items, "total": sum(
                int(it.get("price", 0)) * int(it.get("quantity", 0)) for it in items
            )}},
        )

    def test_remove_entire_product_default_qty(self, fake_session, sample_products):
        """quantity=0 (default): remove the entire product line (legacy)."""
        self._seed_cart(fake_session, [
            {"product_id": "prod-001", "name": "BARRACUDA", "price": 18000, "quantity": 2},
        ])
        with patch("app.services.order_tools.session_state_service", fake_session):
            from app.services.order_tools import remove_from_cart
            result = remove_from_cart.invoke({
                "injected_business_context": _make_ctx(),
                "product_id": "",
                "product_name": "BARRACUDA",
            })
        assert "✅ Producto quitado" in result
        items = fake_session.load(
            FAKE_WA_ID, FAKE_BUSINESS_ID,
        )["session"]["order_context"]["items"]
        assert items == []

    def test_remove_decrement_by_quantity(self, fake_session, sample_products):
        """quantity=1 on a line at qty=2 should decrement to qty=1."""
        self._seed_cart(fake_session, [
            {"product_id": "prod-001", "name": "BARRACUDA", "price": 18000, "quantity": 2},
        ])
        with patch("app.services.order_tools.session_state_service", fake_session):
            from app.services.order_tools import remove_from_cart
            result = remove_from_cart.invoke({
                "injected_business_context": _make_ctx(),
                "product_name": "BARRACUDA",
                "quantity": 1,
            })
        assert "Quitamos 1" in result
        items = fake_session.load(
            FAKE_WA_ID, FAKE_BUSINESS_ID,
        )["session"]["order_context"]["items"]
        assert len(items) == 1
        assert items[0]["quantity"] == 1

    def test_remove_decrement_drops_line_at_zero(self, fake_session, sample_products):
        """quantity equals line qty → drop the line entirely."""
        self._seed_cart(fake_session, [
            {"product_id": "prod-001", "name": "BARRACUDA", "price": 18000, "quantity": 2},
        ])
        with patch("app.services.order_tools.session_state_service", fake_session):
            from app.services.order_tools import remove_from_cart
            remove_from_cart.invoke({
                "injected_business_context": _make_ctx(),
                "product_name": "BARRACUDA",
                "quantity": 2,
            })
        items = fake_session.load(
            FAKE_WA_ID, FAKE_BUSINESS_ID,
        )["session"]["order_context"]["items"]
        assert items == []

    def test_remove_decrement_overshoots(self, fake_session, sample_products):
        """quantity > line qty → drop the line, surface the actual count removed."""
        self._seed_cart(fake_session, [
            {"product_id": "prod-001", "name": "BARRACUDA", "price": 18000, "quantity": 2},
        ])
        with patch("app.services.order_tools.session_state_service", fake_session):
            from app.services.order_tools import remove_from_cart
            result = remove_from_cart.invoke({
                "injected_business_context": _make_ctx(),
                "product_name": "BARRACUDA",
                "quantity": 5,
            })
        assert "Quitamos 2" in result  # only 2 were available
        assert "no había más" in result
        items = fake_session.load(
            FAKE_WA_ID, FAKE_BUSINESS_ID,
        )["session"]["order_context"]["items"]
        assert items == []

    def test_remove_decrement_cascades_across_notes_lines(self, fake_session, sample_products):
        """Different notes → separate lines. Decrement cascades from
        the first matching line into the next when one runs out."""
        self._seed_cart(fake_session, [
            {"product_id": "prod-001", "name": "BARRACUDA", "price": 18000,
             "quantity": 1, "notes": "sin bbq"},
            {"product_id": "prod-001", "name": "BARRACUDA", "price": 18000,
             "quantity": 2, "notes": "sin cebolla"},
        ])
        with patch("app.services.order_tools.session_state_service", fake_session):
            from app.services.order_tools import remove_from_cart
            remove_from_cart.invoke({
                "injected_business_context": _make_ctx(),
                "product_name": "BARRACUDA",
                "quantity": 2,
            })
        items = fake_session.load(
            FAKE_WA_ID, FAKE_BUSINESS_ID,
        )["session"]["order_context"]["items"]
        # First line (qty=1, sin bbq) drops; cascades 1 unit into the
        # second line (qty=2 → 1, sin cebolla) leaving qty=1.
        assert len(items) == 1
        assert items[0]["notes"] == "sin cebolla"
        assert items[0]["quantity"] == 1


# ---------------------------------------------------------------------------
# update_cart_item
# ---------------------------------------------------------------------------

class TestUpdateCartItem:
    """Test the update_cart_item tool."""

    def _seed_cart(self, fake_session, items):
        fake_session.save(
            FAKE_WA_ID, FAKE_BUSINESS_ID,
            {"order_context": {"items": items, "total": sum(
                int(it.get("price", 0)) * int(it.get("quantity", 0)) for it in items
            )}},
        )

    def test_update_by_product_id_changes_quantity(self, fake_session, sample_products):
        self._seed_cart(fake_session, [
            {"product_id": "prod-001", "name": "BARRACUDA", "price": 18000, "quantity": 2},
        ])
        with patch("app.services.order_tools.session_state_service", fake_session):
            from app.services.order_tools import update_cart_item
            update_cart_item.invoke({
                "injected_business_context": _make_ctx(),
                "product_id": "prod-001",
                "quantity": 1,
            })
        items = fake_session.load(
            FAKE_WA_ID, FAKE_BUSINESS_ID,
        )["session"]["order_context"]["items"]
        assert len(items) == 1
        assert items[0]["quantity"] == 1
        assert items[0]["product_id"] == "prod-001"

    def test_update_by_product_name_resolves_against_cart(
        self, fake_session, sample_products,
    ):
        """The model often passes product_name (especially for the
        first edit when the UUID isn't in scope). update_cart_item
        must resolve it against current cart contents."""
        self._seed_cart(fake_session, [
            {"product_id": "prod-001", "name": "BARRACUDA", "price": 18000, "quantity": 2},
        ])
        with patch("app.services.order_tools.session_state_service", fake_session):
            from app.services.order_tools import update_cart_item
            update_cart_item.invoke({
                "injected_business_context": _make_ctx(),
                "product_name": "BARRACUDA",
                "quantity": 1,
            })
        items = fake_session.load(
            FAKE_WA_ID, FAKE_BUSINESS_ID,
        )["session"]["order_context"]["items"]
        assert len(items) == 1
        assert items[0]["quantity"] == 1
        assert items[0]["product_id"] == "prod-001"  # NOT a phantom 'BARRACUDA' string

    def test_update_refuses_when_product_not_in_cart(
        self, fake_session, sample_products,
    ):
        """Regression: model passed product_id='MONTESA' (a name, not a
        UUID) and update_cart_item happily appended a new line with
        name='', price=0. Now it refuses with a clear redirect to
        add_to_cart instead of creating phantom data."""
        self._seed_cart(fake_session, [
            {"product_id": "prod-001", "name": "BARRACUDA", "price": 18000, "quantity": 1},
        ])
        with patch("app.services.order_tools.session_state_service", fake_session):
            from app.services.order_tools import update_cart_item
            result = update_cart_item.invoke({
                "injected_business_context": _make_ctx(),
                "product_id": "MONTESA",  # bogus id (it's a name)
                "quantity": 1,
            })
        assert "no está en el carrito" in result.lower()
        assert "add_to_cart" in result
        # Cart unchanged — no phantom line appended.
        items = fake_session.load(
            FAKE_WA_ID, FAKE_BUSINESS_ID,
        )["session"]["order_context"]["items"]
        assert len(items) == 1
        assert items[0]["product_id"] == "prod-001"
        assert items[0]["price"] == 18000  # not zeroed out

    def test_update_quantity_zero_removes_item(self, fake_session, sample_products):
        self._seed_cart(fake_session, [
            {"product_id": "prod-001", "name": "BARRACUDA", "price": 18000, "quantity": 1},
        ])
        with patch("app.services.order_tools.session_state_service", fake_session):
            from app.services.order_tools import update_cart_item
            result = update_cart_item.invoke({
                "injected_business_context": _make_ctx(),
                "product_name": "BARRACUDA",
                "quantity": 0,
            })
        assert "Producto quitado" in result
        items = fake_session.load(
            FAKE_WA_ID, FAKE_BUSINESS_ID,
        )["session"]["order_context"]["items"]
        assert items == []

    def test_update_refuses_when_multiple_lines_match_by_name(
        self, fake_session, sample_products,
    ):
        """Cart has 2 MONTESA lines (different notes). User says
        'solo una montesa'. Model calls update_cart_item(qty=1).
        Tool must refuse — picking the first match silently and
        reporting success while the cart total stays unchanged is
        the 'tool lied' bug (Biela / 2026-05-09)."""
        self._seed_cart(fake_session, [
            {"product_id": "prod-001", "name": "BARRACUDA", "price": 18000,
             "quantity": 1, "notes": "sin bbq"},
            {"product_id": "prod-001", "name": "BARRACUDA", "price": 18000,
             "quantity": 1},
        ])
        with patch("app.services.order_tools.session_state_service", fake_session):
            from app.services.order_tools import update_cart_item
            result = update_cart_item.invoke({
                "injected_business_context": _make_ctx(),
                "product_name": "BARRACUDA",
                "quantity": 1,
            })
        assert "❌" in result
        assert "2 líneas" in result
        assert "remove_from_cart" in result
        # Cart unchanged — both lines still present
        items = fake_session.load(
            FAKE_WA_ID, FAKE_BUSINESS_ID,
        )["session"]["order_context"]["items"]
        assert len(items) == 2

    def test_update_preserves_existing_notes_when_only_changing_qty(
        self, fake_session, sample_products,
    ):
        """Caller updates qty without passing notes → existing notes
        survive. Without this, the line would silently lose its notes."""
        self._seed_cart(fake_session, [
            {"product_id": "prod-001", "name": "BARRACUDA", "price": 18000,
             "quantity": 2, "notes": "sin cebolla"},
        ])
        with patch("app.services.order_tools.session_state_service", fake_session):
            from app.services.order_tools import update_cart_item
            update_cart_item.invoke({
                "injected_business_context": _make_ctx(),
                "product_name": "BARRACUDA",
                "quantity": 1,
            })
        items = fake_session.load(
            FAKE_WA_ID, FAKE_BUSINESS_ID,
        )["session"]["order_context"]["items"]
        assert len(items) == 1
        assert items[0]["quantity"] == 1
        assert items[0]["notes"] == "sin cebolla"


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


# ---------------------------------------------------------------------------
# Availability helpers (_product_availability / _search_listing_marker /
# _detail_availability_note / _format_unavailable_for_cart)
# ---------------------------------------------------------------------------

class TestAvailabilityHelpers:
    """
    Promo-only and operator-disabled products should be findable (so info
    questions answer) but blocked at add-to-cart. The four helpers below
    drive that UX. Production trigger: customers asking ingredients of a
    promo_only product got "no encontré" — and disabled products went
    silent instead of saying "no disponible por ahora".
    """

    def test_product_availability_classifies_correctly(self):
        from app.services.order_tools import _product_availability
        assert _product_availability({"is_active": True, "promo_only": False}) == "available"
        assert _product_availability({"is_active": True, "promo_only": True}) == "promo_only"
        assert _product_availability({"is_active": False, "promo_only": False}) == "inactive"
        # Missing keys default to available (preserves test fixtures without flags).
        assert _product_availability({}) == "available"
        # is_active=False wins even if promo_only=True (operator pulled it).
        assert _product_availability({"is_active": False, "promo_only": True}) == "inactive"

    def test_search_listing_marker_is_short(self):
        from app.services.order_tools import _search_listing_marker
        assert _search_listing_marker({"is_active": True, "promo_only": False}) == ""
        assert _search_listing_marker({"is_active": True, "promo_only": True}) == " (solo en promo)"
        assert _search_listing_marker({"is_active": False, "promo_only": False}) == " (no disponible por ahora)"

    def test_detail_note_promo_only_with_active_promo_names_the_promo(self):
        from app.services.order_tools import _detail_availability_note
        prod = {"id": "p1", "name": "Oregon", "is_active": True, "promo_only": True}
        buckets = {
            "active": [{"name": "Dos Oregon con papas", "fixed_price": 39900}],
            "upcoming": [],
        }
        with patch(
            "app.services.promotion_service.find_promos_containing_product",
            return_value=buckets,
        ):
            note = _detail_availability_note(prod, "biz", None)
        assert "Dos Oregon con papas" in note
        assert "$39.900" in note
        assert "Solo se vende" in note

    def test_detail_note_promo_only_with_only_upcoming_surfaces_the_day(self):
        from app.services.order_tools import _detail_availability_note
        prod = {"id": "p1", "name": "Oregon", "is_active": True, "promo_only": True}
        buckets = {
            "active": [],
            "upcoming": [{"name": "Combo Lunes", "next_active_day": 1}],
        }
        with patch(
            "app.services.promotion_service.find_promos_containing_product",
            return_value=buckets,
        ):
            note = _detail_availability_note(prod, "biz", None)
        assert "Combo Lunes" in note
        assert "lunes" in note

    def test_detail_note_inactive_is_neutral(self):
        from app.services.order_tools import _detail_availability_note
        prod = {"id": "p2", "name": "Oregon Especial", "is_active": False, "promo_only": False}
        note = _detail_availability_note(prod, "biz", None)
        assert "no está disponible por ahora" in note
        # Should NOT speculate about promos / timing.
        assert "promo" not in note.lower()

    def test_detail_note_available_returns_empty(self):
        from app.services.order_tools import _detail_availability_note
        prod = {"id": "p3", "name": "Barracuda", "is_active": True, "promo_only": False}
        assert _detail_availability_note(prod, "biz", None) == ""

    def test_cart_refusal_promo_only_with_active_promo_invites_to_promo(self):
        from app.services.order_tools import _format_unavailable_for_cart
        prod = {"id": "p1", "name": "Oregon", "is_active": True, "promo_only": True}
        buckets = {
            "active": [{"name": "Dos Oregon con papas", "fixed_price": 39900}],
            "upcoming": [],
        }
        with patch(
            "app.services.promotion_service.find_promos_containing_product",
            return_value=buckets,
        ):
            msg = _format_unavailable_for_cart(prod, "biz", None)
        assert msg.startswith("❌")
        assert "*Oregon*" in msg
        assert "Dos Oregon con papas" in msg
        assert "$39.900" in msg
        assert "promo" in msg.lower()

    def test_cart_refusal_promo_only_no_active_uses_upcoming_day(self):
        from app.services.order_tools import _format_unavailable_for_cart
        prod = {"id": "p1", "name": "Oregon", "is_active": True, "promo_only": True}
        buckets = {
            "active": [],
            "upcoming": [{"name": "Combo Lunes", "next_active_day": 1}],
        }
        with patch(
            "app.services.promotion_service.find_promos_containing_product",
            return_value=buckets,
        ):
            msg = _format_unavailable_for_cart(prod, "biz", None)
        assert "*Oregon*" in msg
        assert "Combo Lunes" in msg
        assert "lunes" in msg

    def test_cart_refusal_inactive_is_neutral(self):
        from app.services.order_tools import _format_unavailable_for_cart
        prod = {"id": "p2", "name": "Oregon Especial", "is_active": False, "promo_only": False}
        msg = _format_unavailable_for_cart(prod, "biz", None)
        assert msg.startswith("❌")
        assert "*Oregon Especial*" in msg
        assert "no está disponible por ahora" in msg
        # No promo speculation for inactive products.
        assert "promo" not in msg.lower()


# ---------------------------------------------------------------------------
# add_to_cart refusal paths for promo_only / inactive products
# ---------------------------------------------------------------------------

class TestAddToCartUnavailableRefusal:
    """add_to_cart must refuse promo_only and inactive products with a
    specific message instead of generic NOT_FOUND, so the LLM can echo
    the redirect (promo X, or "currently unavailable") to the customer."""

    def test_promo_only_product_is_refused_with_promo_redirect(self, fake_session):
        mock_product_service = MagicMock()
        mock_product_service.get_product.return_value = {
            "id": "p1", "name": "OREGON", "price": 25000, "currency": "COP",
            "is_active": True, "promo_only": True,
        }
        buckets = {
            "active": [{"name": "Dos Oregon con papas", "fixed_price": 39900}],
            "upcoming": [],
        }
        with patch("app.services.order_tools.product_order_service", mock_product_service), \
             patch("app.services.order_tools.session_state_service", fake_session), \
             patch(
                 "app.services.promotion_service.find_promos_containing_product",
                 return_value=buckets,
             ):
            from app.services.order_tools import add_to_cart
            result = add_to_cart.invoke({
                "injected_business_context": _make_ctx(),
                "product_id": "",
                "product_name": "oregon",
                "quantity": 1,
                "notes": "",
            })
        assert result.startswith("❌")
        assert "*OREGON*" in result
        assert "Dos Oregon con papas" in result
        # Cart must NOT have been mutated.
        session = fake_session.load(FAKE_WA_ID, FAKE_BUSINESS_ID)
        items = session["session"]["order_context"].get("items", [])
        assert items == []

    def test_inactive_product_is_refused_neutrally(self, fake_session):
        mock_product_service = MagicMock()
        mock_product_service.get_product.return_value = {
            "id": "p2", "name": "OREGON ESPECIAL", "price": 30000, "currency": "COP",
            "is_active": False, "promo_only": False,
        }
        with patch("app.services.order_tools.product_order_service", mock_product_service), \
             patch("app.services.order_tools.session_state_service", fake_session):
            from app.services.order_tools import add_to_cart
            result = add_to_cart.invoke({
                "injected_business_context": _make_ctx(),
                "product_id": "",
                "product_name": "oregon especial",
                "quantity": 1,
                "notes": "",
            })
        assert result.startswith("❌")
        assert "*OREGON ESPECIAL*" in result
        assert "no está disponible por ahora" in result
        session = fake_session.load(FAKE_WA_ID, FAKE_BUSINESS_ID)
        items = session["session"]["order_context"].get("items", [])
        assert items == []


# ---------------------------------------------------------------------------
# _format_promo_miss_message — no-match branch of add_promo_to_cart
# ---------------------------------------------------------------------------

class TestFormatPromoMissMessage:
    """
    When `add_promo_to_cart` can't resolve a query, the tool used to
    return just "❌ No encontré...". The enhanced helper now surfaces
    what IS available (active today + upcoming this week) so the
    customer learns alternatives in the same turn.

    Production trigger: 2026-05-11 (Biela / 3177000722) — customer asked
    for "una promo de oregon" with no active match and the bot only
    said "no encontré", forcing a follow-up turn for what's available.
    """

    def test_returns_active_list_when_active_promos_exist(self):
        buckets = {
            "active_now": [
                {"id": "p1", "name": "Dos Oregon con papas", "fixed_price": 39900},
                {"id": "p2", "name": "Honey Combo", "discount_pct": 15},
            ],
            "upcoming": [],
        }
        with patch(
            "app.services.promotion_service.list_promos_for_listing",
            return_value=buckets,
        ):
            from app.services.order_tools import _format_promo_miss_message
            result = _format_promo_miss_message("biz", "pegoretti", None)
        assert "pegoretti" in result
        assert "Dos Oregon con papas" in result
        assert "$39.900" in result
        assert "Honey Combo" in result
        assert "15% off" in result
        assert "¿Te interesa alguna?" in result

    def test_query_naming_upcoming_promo_surfaces_that_promo_with_day(self):
        # Production trigger: Monday, customer asks for "misuri", Misuri
        # promo applies Wednesday. The miss path must NAME the Misuri
        # promo and the day, NOT list Oregon as "what we have" — that
        # was hiding the real answer and tempting the LLM to substitute.
        buckets = {
            "active_now": [
                {"id": "p1", "name": "Dos Oregon con papas", "fixed_price": 39900},
            ],
            "upcoming": [
                {"id": "p2", "name": "Dos Misuri con papas", "next_active_day": 3},
            ],
        }
        with patch(
            "app.services.promotion_service.list_promos_for_listing",
            return_value=buckets,
        ):
            from app.services.order_tools import _format_promo_miss_message
            result = _format_promo_miss_message("biz", "misuri", None)
        # Names the upcoming promo + the day.
        assert "Dos Misuri con papas" in result
        assert "miércoles" in result
        # Does NOT advertise Oregon in the same line.
        assert "Oregon" not in result

    def test_query_naming_upcoming_works_for_full_phrase(self):
        # "Dos Misuri con papas" (what add_promo_to_cart actually receives
        # from the LLM after fuzzy-matching) must also hit the upcoming
        # branch — the substring check must tolerate the full phrase.
        buckets = {
            "active_now": [
                {"id": "p1", "name": "Dos Oregon con papas", "fixed_price": 39900},
            ],
            "upcoming": [
                {"id": "p2", "name": "Dos Misuri con papas", "next_active_day": 3},
            ],
        }
        with patch(
            "app.services.promotion_service.list_promos_for_listing",
            return_value=buckets,
        ):
            from app.services.order_tools import _format_promo_miss_message
            result = _format_promo_miss_message("biz", "Dos Misuri con papas", None)
        assert "Dos Misuri con papas" in result
        assert "miércoles" in result

    def test_query_not_matching_upcoming_falls_through_to_active_list(self):
        # "pegoretti" doesn't name any promo (upcoming or active) — the
        # existing active-list branch must still fire so the customer
        # learns what IS available today.
        buckets = {
            "active_now": [
                {"id": "p1", "name": "Dos Oregon con papas", "fixed_price": 39900},
            ],
            "upcoming": [
                {"id": "p2", "name": "Dos Misuri con papas", "next_active_day": 3},
            ],
        }
        with patch(
            "app.services.promotion_service.list_promos_for_listing",
            return_value=buckets,
        ):
            from app.services.order_tools import _format_promo_miss_message
            result = _format_promo_miss_message("biz", "pegoretti", None)
        assert "Dos Oregon con papas" in result
        assert "$39.900" in result

    def test_returns_upcoming_message_when_nothing_active_today(self):
        buckets = {
            "active_now": [],
            "upcoming": [
                {"id": "p3", "name": "Combo Lunes", "next_active_day": 1},
                {"id": "p4", "name": "2x1 Viernes", "next_active_day": 5},
            ],
        }
        with patch(
            "app.services.promotion_service.list_promos_for_listing",
            return_value=buckets,
        ):
            from app.services.order_tools import _format_promo_miss_message
            result = _format_promo_miss_message("biz", "oregon", None)
        assert "oregon" in result
        assert "Combo Lunes" in result
        assert "lunes" in result
        assert "2x1 Viernes" in result
        assert "viernes" in result

    def test_returns_no_promos_message_when_nothing_at_all(self):
        buckets = {"active_now": [], "upcoming": []}
        with patch(
            "app.services.promotion_service.list_promos_for_listing",
            return_value=buckets,
        ):
            from app.services.order_tools import _format_promo_miss_message
            result = _format_promo_miss_message("biz", "oregon", None)
        assert "oregon" in result
        assert "no tenemos" in result.lower()
        assert "menú" in result.lower()

    def test_promotion_service_failure_falls_back_to_simple_message(self):
        # If list_promos_for_listing raises, the helper must still return
        # SOMETHING — the original "no encontré" line — instead of
        # blowing up the whole tool call.
        with patch(
            "app.services.promotion_service.list_promos_for_listing",
            side_effect=RuntimeError("db down"),
        ):
            from app.services.order_tools import _format_promo_miss_message
            result = _format_promo_miss_message("biz", "oregon", None)
        assert "oregon" in result
        assert "No encontré" in result
