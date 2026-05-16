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
        lines, which made later set_cart_items calls ambiguous when the
        planner wanted to address "the line with notes X" but two lines
        shared the same notes. Regression: Biela / 2026-05-09."""
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


class TestSubmitDeliveryInfo:
    """Test the submit_delivery_info tool."""

    def test_routes_to_place_order_when_already_awaiting_confirmation(
        self, fake_session, business_context
    ):
        # If awaiting_confirmation is already true, the customer has
        # already seen the confirmation card and is responding to it —
        # we should not re-prompt with another ready_to_confirm card.
        fake_session._store[(FAKE_WA_ID, FAKE_BUSINESS_ID)] = {
            "active_agents": [],
            "order_context": {
                "items": [{"product_id": "p1", "name": "X", "price": 1, "quantity": 1}],
                "awaiting_confirmation": True,
            },
            "booking_context": {},
            "agent_contexts": {},
            "last_order_id": None,
            "last_booking_id": None,
        }

        mock_cust_svc = MagicMock()
        mock_cust_svc.get_customer.return_value = None

        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.database.session_state_service.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service", mock_cust_svc):
            from app.services.order_tools import submit_delivery_info

            result = submit_delivery_info.invoke({
                "injected_business_context": _make_ctx(),
                "address": "calle 18",
                "name": "Vanessa",
                "phone": "3177000722",
                "payment_method": "Efectivo",
            })

        assert "place_order AHORA" in result
        assert "NO emitas otra tarjeta" in result

    def test_routes_to_ready_to_confirm_when_not_yet_awaiting(
        self, fake_session, business_context
    ):
        # Baseline: when awaiting_confirmation is false, the legacy
        # two-step flow still applies — agent should call
        # respond(kind='ready_to_confirm') first.
        fake_session._store[(FAKE_WA_ID, FAKE_BUSINESS_ID)] = {
            "active_agents": [],
            "order_context": {
                "items": [{"product_id": "p1", "name": "X", "price": 1, "quantity": 1}],
            },
            "booking_context": {},
            "agent_contexts": {},
            "last_order_id": None,
            "last_booking_id": None,
        }

        mock_cust_svc = MagicMock()
        mock_cust_svc.get_customer.return_value = None

        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.database.session_state_service.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service", mock_cust_svc):
            from app.services.order_tools import submit_delivery_info

            result = submit_delivery_info.invoke({
                "injected_business_context": _make_ctx(),
                "address": "calle 18",
                "name": "Vanessa",
                "phone": "3177000722",
                "payment_method": "Efectivo",
            })

        assert "respond(kind='ready_to_confirm')" in result
        assert "place_order AHORA" not in result


# ---------------------------------------------------------------------------
# submit_delivery_info — payment_method × fulfillment_type validation
# ---------------------------------------------------------------------------


_BIELA_PAYMENT_SETTINGS = {
    "payment_methods": [
        {"name": "Efectivo", "contexts": ["delivery_on_fulfillment", "on_site_on_fulfillment"]},
        {"name": "Tarjeta", "contexts": ["on_site_on_fulfillment"]},
        {
            "name": "Nequi",
            "contexts": [
                "delivery_pay_now", "delivery_on_fulfillment",
                "on_site_pay_now", "on_site_on_fulfillment",
            ],
        },
        {"name": "Transferencia", "contexts": ["delivery_pay_now", "on_site_pay_now"]},
        {"name": "Llave BreB", "contexts": ["delivery_pay_now", "on_site_pay_now"]},
    ],
}


def _ctx_with_payments(fulfillment_type=None, settings=None):
    """Build an injected_business_context with the new payment_methods shape."""
    s = settings if settings is not None else _BIELA_PAYMENT_SETTINGS
    ctx = {
        "business_id": FAKE_BUSINESS_ID,
        "wa_id": FAKE_WA_ID,
        "business": {"name": "Biela", "settings": s},
    }
    return ctx


class TestSubmitDeliveryInfoPaymentValidation:
    """Reject payment_method × fulfillment_type combinations that can't fulfill.

    Phase-1 enforcement: when the business has a per-method config and the
    customer names a method that's not valid for the current
    fulfillment_type, the tool returns an error message listing valid
    alternatives instead of persisting bad data.
    """

    def _setup_session(self, fake_session, fulfillment_type="delivery"):
        fake_session._store[(FAKE_WA_ID, FAKE_BUSINESS_ID)] = {
            "active_agents": [],
            "order_context": {
                "items": [{"product_id": "p1", "name": "X", "price": 1, "quantity": 1}],
                "fulfillment_type": fulfillment_type,
            },
            "booking_context": {},
            "agent_contexts": {},
            "last_order_id": None,
            "last_booking_id": None,
        }

    def test_tarjeta_plus_delivery_rejected_with_alternatives(self, fake_session):
        self._setup_session(fake_session, fulfillment_type="delivery")
        mock_cust_svc = MagicMock()
        mock_cust_svc.get_customer.return_value = None
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.database.session_state_service.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service", mock_cust_svc):
            from app.services.order_tools import submit_delivery_info
            result = submit_delivery_info.invoke({
                "injected_business_context": _ctx_with_payments(),
                "name": "Vanessa",
                "address": "calle 18",
                "phone": "3177000722",
                "payment_method": "Tarjeta",
            })
        assert result.startswith("❌")
        assert "Tarjeta" in result
        assert "domicilio" in result.lower()
        # Lists valid alternatives for delivery.
        assert "Efectivo" in result or "Nequi" in result
        # Mentions where Tarjeta IS accepted.
        assert "local" in result.lower()

    def test_tarjeta_plus_pickup_accepted(self, fake_session):
        self._setup_session(fake_session, fulfillment_type="pickup")
        mock_cust_svc = MagicMock()
        mock_cust_svc.get_customer.return_value = None
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.database.session_state_service.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service", mock_cust_svc):
            from app.services.order_tools import submit_delivery_info
            result = submit_delivery_info.invoke({
                "injected_business_context": _ctx_with_payments(),
                "name": "Vanessa",
                "payment_method": "Tarjeta",
            })
        # Pickup with Tarjeta is valid → no rejection.
        assert not result.startswith("❌ Tarjeta")

    def test_nequi_plus_delivery_accepted(self, fake_session):
        self._setup_session(fake_session, fulfillment_type="delivery")
        mock_cust_svc = MagicMock()
        mock_cust_svc.get_customer.return_value = None
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.database.session_state_service.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service", mock_cust_svc):
            from app.services.order_tools import submit_delivery_info
            result = submit_delivery_info.invoke({
                "injected_business_context": _ctx_with_payments(),
                "name": "Vanessa",
                "address": "calle 18",
                "phone": "3177000722",
                "payment_method": "Nequi",
            })
        assert not result.startswith("❌ Nequi")

    def test_efectivo_plus_delivery_accepted(self, fake_session):
        self._setup_session(fake_session, fulfillment_type="delivery")
        mock_cust_svc = MagicMock()
        mock_cust_svc.get_customer.return_value = None
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.database.session_state_service.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service", mock_cust_svc):
            from app.services.order_tools import submit_delivery_info
            result = submit_delivery_info.invoke({
                "injected_business_context": _ctx_with_payments(),
                "name": "Vanessa",
                "address": "calle 18",
                "phone": "3177000722",
                "payment_method": "Efectivo",
            })
        assert not result.startswith("❌ Efectivo")

    def test_no_payment_config_skips_validation(self, fake_session):
        """When the business has no per-method config, accept anything verbatim."""
        self._setup_session(fake_session, fulfillment_type="delivery")
        mock_cust_svc = MagicMock()
        mock_cust_svc.get_customer.return_value = None
        empty_ctx = _ctx_with_payments(settings={})
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.database.session_state_service.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service", mock_cust_svc):
            from app.services.order_tools import submit_delivery_info
            result = submit_delivery_info.invoke({
                "injected_business_context": empty_ctx,
                "name": "Vanessa",
                "address": "calle 18",
                "phone": "3177000722",
                "payment_method": "Tarjeta",  # would be rejected with config
            })
        # No enforcement → no rejection.
        assert not result.startswith("❌")

    def test_unknown_method_with_config_rejected(self, fake_session):
        """Method not in the business's config is rejected with alternatives."""
        self._setup_session(fake_session, fulfillment_type="delivery")
        mock_cust_svc = MagicMock()
        mock_cust_svc.get_customer.return_value = None
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.database.session_state_service.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service", mock_cust_svc):
            from app.services.order_tools import submit_delivery_info
            result = submit_delivery_info.invoke({
                "injected_business_context": _ctx_with_payments(),
                "name": "Vanessa",
                "address": "calle 18",
                "phone": "3177000722",
                "payment_method": "Bitcoin",
            })
        assert result.startswith("❌")
        assert "Bitcoin" in result
        assert "domicilio" in result.lower()

    def test_explicit_pickup_switch_revalidates(self, fake_session):
        """When the same call switches to pickup, validate against the NEW ftype."""
        self._setup_session(fake_session, fulfillment_type="delivery")
        mock_cust_svc = MagicMock()
        mock_cust_svc.get_customer.return_value = None
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.database.session_state_service.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service", mock_cust_svc):
            from app.services.order_tools import submit_delivery_info
            # Switching to pickup AND paying with Tarjeta in the same call:
            # should be accepted because the new ftype permits Tarjeta.
            result = submit_delivery_info.invoke({
                "injected_business_context": _ctx_with_payments(),
                "name": "Vanessa",
                "fulfillment_type": "pickup",
                "payment_method": "Tarjeta",
            })
        assert not result.startswith("❌ Tarjeta")


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
