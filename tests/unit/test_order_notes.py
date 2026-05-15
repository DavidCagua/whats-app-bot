"""
Unit tests for order-level notes.

Notes captured during the conversation flow:
  ``submit_delivery_info(notes=...)`` → ``order_context.notes`` (session)
                                        → ``orders.notes`` (DB) at place_order time

Distinct from ``OrderItem.notes`` (per-product modifications). These are
order-level instructions: pickup time, callback requests, change/cash
requests, "déjenlo en portería", etc. Surfaced in the confirm card,
the CTA summary, and the place_order receipt.

Coverage:
- ``submit_delivery_info`` saves notes; replaces (not appends); a single
  space clears.
- ``TurnContext.order_notes`` is populated by ``build_turn_context`` and
  rendered by ``render_for_prompt``.
- ``_build_confirm_text`` and ``_summary_block`` show the *Notas:* line
  in both pickup and delivery shapes.
- ``place_order`` forwards notes to ``create_order`` and includes them
  in the receipt.
"""

from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import FAKE_BUSINESS_ID, FAKE_WA_ID


def _make_ctx(business_id=FAKE_BUSINESS_ID, wa_id=FAKE_WA_ID):
    return {"business_id": business_id, "wa_id": wa_id}


# ---------------------------------------------------------------------------
# submit_delivery_info — notes arg
# ---------------------------------------------------------------------------

class TestSubmitDeliveryInfoNotes:
    def _seed_cart(self, fake_session, **overrides):
        oc = {"items": [{"product_id": "p", "name": "X", "price": 10000, "quantity": 1}]}
        oc.update(overrides)
        fake_session.save(FAKE_WA_ID, FAKE_BUSINESS_ID, {"order_context": oc})

    def test_notes_saved_on_first_call(self, fake_session):
        self._seed_cart(fake_session)
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service.get_customer", return_value={}):
            from app.services.order_tools import submit_delivery_info
            result = submit_delivery_info.invoke({
                "injected_business_context": _make_ctx(),
                "name": "Camilo",
                "notes": "Llámenme cuando estén afuera",
            })
        assert "Datos guardados" in result
        assert "notas guardadas" in result
        oc = fake_session.load(FAKE_WA_ID, FAKE_BUSINESS_ID)["session"]["order_context"]
        assert oc["notes"] == "Llámenme cuando estén afuera"

    def test_notes_replaces_not_appends(self, fake_session):
        """Recommendation A: tool replaces. Model passes the consolidated
        string each turn, so the kitchen note never accumulates obsolete
        versions."""
        self._seed_cart(fake_session, notes="A las 8 pm")
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service.get_customer", return_value={}):
            from app.services.order_tools import submit_delivery_info
            submit_delivery_info.invoke({
                "injected_business_context": _make_ctx(),
                "notes": "A las 9 pm. Traigan cambio de $100.000.",
            })
        oc = fake_session.load(FAKE_WA_ID, FAKE_BUSINESS_ID)["session"]["order_context"]
        # New consolidated value, NOT "A las 8 pm | A las 9 pm".
        assert oc["notes"] == "A las 9 pm. Traigan cambio de $100.000."

    def test_empty_notes_leaves_existing_unchanged(self, fake_session):
        """Empty string ≠ "clear" — that's how the model says
        "I'm not touching notes this turn". Any other field could still
        be the reason for the call."""
        self._seed_cart(fake_session, notes="A las 8 pm", delivery_info={"name": "C"})
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service.get_customer", return_value={}):
            from app.services.order_tools import submit_delivery_info
            submit_delivery_info.invoke({
                "injected_business_context": _make_ctx(),
                "name": "Camilo Nuevo",  # only updating name
                # notes omitted
            })
        oc = fake_session.load(FAKE_WA_ID, FAKE_BUSINESS_ID)["session"]["order_context"]
        assert oc["notes"] == "A las 8 pm"
        assert oc["delivery_info"]["name"] == "Camilo Nuevo"

    def test_single_space_clears_notes(self, fake_session):
        """Escape hatch: model passes ' ' to clear the field entirely."""
        self._seed_cart(fake_session, notes="A las 8 pm")
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service.get_customer", return_value={}):
            from app.services.order_tools import submit_delivery_info
            result = submit_delivery_info.invoke({
                "injected_business_context": _make_ctx(),
                "notes": " ",
            })
        assert "notas borradas" in result
        oc = fake_session.load(FAKE_WA_ID, FAKE_BUSINESS_ID)["session"]["order_context"]
        assert oc["notes"] == ""


# ---------------------------------------------------------------------------
# TurnContext + render
# ---------------------------------------------------------------------------

class TestTurnContextOrderNotes:
    def test_default_is_empty_string(self):
        from app.orchestration.turn_context import TurnContext
        assert TurnContext().order_notes == ""

    def test_render_for_prompt_shows_saved_notes(self):
        from app.orchestration.turn_context import TurnContext, render_for_prompt
        rendered = render_for_prompt(TurnContext(order_notes="A las 8 pm"))
        assert "Notas del pedido (ya guardadas)" in rendered
        assert "A las 8 pm" in rendered

    def test_render_for_prompt_omits_notes_line_when_empty(self):
        """Empty notes don't render a "ninguna" line — the absence is
        signal enough and reduces context noise that biased the router
        on greeting / chat turns."""
        from app.orchestration.turn_context import TurnContext, render_for_prompt
        rendered = render_for_prompt(TurnContext())
        assert "Notas del pedido" not in rendered


# ---------------------------------------------------------------------------
# Confirm text + CTA summary
# ---------------------------------------------------------------------------

class TestConfirmTextNotes:
    _BIZ_CTX = {"business_id": "biz1", "business": {"name": "Biela", "settings": {}}}

    def test_pickup_confirm_text_includes_notes(self):
        from app.services import response_renderer
        with patch.object(response_renderer, "_has_cart_items", return_value=True), \
             patch.object(
                 response_renderer, "_read_delivery_status",
                 return_value={
                     "name": "Camilo", "address": "", "phone": "",
                     "payment_method": "", "total": 18000, "all_present": True,
                     "fulfillment_type": "pickup",
                     "notes": "A las 8 pm",
                 },
             ):
            body = response_renderer._build_confirm_text(self._BIZ_CTX, "+57300")
        assert "*Nombre:* Camilo" in body
        assert "Recoger en local" in body
        assert "*Notas:* A las 8 pm" in body

    def test_delivery_confirm_text_includes_notes(self):
        from app.services import response_renderer
        with patch.object(response_renderer, "_has_cart_items", return_value=True), \
             patch.object(
                 response_renderer, "_read_delivery_status",
                 return_value={
                     "name": "Erik", "address": "Calle 19", "phone": "3152133998",
                     "payment_method": "Efectivo", "total": 140000, "all_present": True,
                     "fulfillment_type": "delivery",
                     "notes": "Traigan cambio de $100.000",
                 },
             ):
            body = response_renderer._build_confirm_text(self._BIZ_CTX, "+57300")
        assert "*Nombre:* Erik" in body
        assert "*Dirección:* Calle 19" in body
        assert "*Notas:* Traigan cambio de $100.000" in body

    def test_no_notes_no_line(self):
        from app.services import response_renderer
        with patch.object(response_renderer, "_has_cart_items", return_value=True), \
             patch.object(
                 response_renderer, "_read_delivery_status",
                 return_value={
                     "name": "Camilo", "address": "", "phone": "",
                     "payment_method": "", "total": 18000, "all_present": True,
                     "fulfillment_type": "pickup", "notes": "",
                 },
             ):
            body = response_renderer._build_confirm_text(self._BIZ_CTX, "+57300")
        assert "*Notas:*" not in body


class TestSummaryBlockNotes:
    def test_pickup_summary_block_includes_notes(self):
        from app.services.order_cta import _summary_block
        out = _summary_block({
            "name": "Camilo", "fulfillment_type": "pickup",
            "notes": "A las 8 pm",
        })
        assert "*Nombre:* Camilo" in out
        assert "Recoger en local" in out
        assert "*Notas:* A las 8 pm" in out
        # Twilio constraint: no newlines in variable values.
        assert "\n" not in out

    def test_delivery_summary_block_includes_notes(self):
        from app.services.order_cta import _summary_block
        out = _summary_block({
            "name": "Erik", "address": "Cl 19", "phone": "3152",
            "payment_method": "Efectivo", "fulfillment_type": "delivery",
            "notes": "Llámenme al llegar",
        })
        assert "*Nombre:* Erik" in out
        assert "*Dirección:* Cl 19" in out
        assert "*Notas:* Llámenme al llegar" in out


# ---------------------------------------------------------------------------
# place_order forwards notes + receipt shows them
# ---------------------------------------------------------------------------

class TestPlaceOrderNotes:
    def _seed_cart(self, fake_session, *, ftype="delivery", notes="A las 8 pm"):
        delivery_info = {"name": "David Zambrano"}
        if ftype == "delivery":
            delivery_info.update({
                "address": "Cl 19", "phone": "3152133998",
                "payment_method": "Efectivo",
            })
        fake_session.save(
            FAKE_WA_ID, FAKE_BUSINESS_ID,
            {"order_context": {
                "items": [{"product_id": "00000000-0000-0000-0000-000000000abc",
                           "name": "DENVER", "price": 27000, "quantity": 1}],
                "fulfillment_type": ftype,
                "delivery_info": delivery_info,
                "notes": notes,
                "awaiting_confirmation": True,
            }},
        )

    def test_pickup_receipt_shows_notes_and_forwards_to_create_order(self, fake_session):
        self._seed_cart(fake_session, ftype="pickup", notes="A las 8 pm")
        biz_ctx = {
            "business_id": FAKE_BUSINESS_ID, "wa_id": FAKE_WA_ID,
            "business": {"settings": {"products_enabled": True}},
        }
        fake_create_order = MagicMock(return_value={
            "success": True, "order_id": "33333333-3333-3333-3333-333333333333",
            "subtotal": 27000.0, "total": 27000.0, "promo_discount": 0.0,
            "applied_promos": [],
        })
        fake_preview = {
            "subtotal": 27000.0, "promo_discount_total": 0.0,
            "display_groups": [
                {"kind": "item", "quantity": 1, "name": "DENVER",
                 "line_total": 27000.0, "notes": None},
            ],
        }
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service.get_customer", return_value=None), \
             patch("app.services.order_tools.product_order_service.create_order", fake_create_order), \
             patch("app.services.order_tools._read_awaiting_confirmation", return_value=True), \
             patch("app.services.order_tools.promotion_service.preview_cart", return_value=fake_preview):
            from app.services.order_tools import place_order
            result = place_order.invoke({"injected_business_context": biz_ctx})
        assert "📝 Notas: A las 8 pm" in result
        kwargs = fake_create_order.call_args.kwargs
        assert kwargs["notes"] == "A las 8 pm"

    def test_delivery_receipt_shows_notes_and_forwards_to_create_order(self, fake_session):
        self._seed_cart(
            fake_session, ftype="delivery",
            notes="Traigan cambio de $100.000. Llámenme al llegar.",
        )
        biz_ctx = {
            "business_id": FAKE_BUSINESS_ID, "wa_id": FAKE_WA_ID,
            "business": {"settings": {"delivery_fee": 7000, "products_enabled": True}},
        }
        fake_create_order = MagicMock(return_value={
            "success": True, "order_id": "44444444-4444-4444-4444-444444444444",
            "subtotal": 27000.0, "total": 34000.0, "promo_discount": 0.0,
            "applied_promos": [],
        })
        fake_preview = {
            "subtotal": 27000.0, "promo_discount_total": 0.0,
            "display_groups": [
                {"kind": "item", "quantity": 1, "name": "DENVER",
                 "line_total": 27000.0, "notes": None},
            ],
        }
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service.get_customer", return_value=None), \
             patch("app.services.order_tools.product_order_service.create_order", fake_create_order), \
             patch("app.services.order_tools._read_awaiting_confirmation", return_value=True), \
             patch("app.services.order_tools.promotion_service.preview_cart", return_value=fake_preview):
            from app.services.order_tools import place_order
            result = place_order.invoke({"injected_business_context": biz_ctx})
        assert "📝 Notas: Traigan cambio de $100.000" in result
        assert "Llámenme al llegar" in result
        kwargs = fake_create_order.call_args.kwargs
        assert kwargs["notes"] == "Traigan cambio de $100.000. Llámenme al llegar."

    def test_no_notes_no_receipt_line(self, fake_session):
        """When notes are empty the receipt drops the line entirely (not
        a stray '📝 Notas: ' header)."""
        self._seed_cart(fake_session, ftype="pickup", notes="")
        biz_ctx = {
            "business_id": FAKE_BUSINESS_ID, "wa_id": FAKE_WA_ID,
            "business": {"settings": {"products_enabled": True}},
        }
        fake_create_order = MagicMock(return_value={
            "success": True, "order_id": "55555555-5555-5555-5555-555555555555",
            "subtotal": 27000.0, "total": 27000.0, "promo_discount": 0.0,
            "applied_promos": [],
        })
        fake_preview = {
            "subtotal": 27000.0, "promo_discount_total": 0.0,
            "display_groups": [
                {"kind": "item", "quantity": 1, "name": "DENVER",
                 "line_total": 27000.0, "notes": None},
            ],
        }
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service.get_customer", return_value=None), \
             patch("app.services.order_tools.product_order_service.create_order", fake_create_order), \
             patch("app.services.order_tools._read_awaiting_confirmation", return_value=True), \
             patch("app.services.order_tools.promotion_service.preview_cart", return_value=fake_preview):
            from app.services.order_tools import place_order
            result = place_order.invoke({"injected_business_context": biz_ctx})
        assert "📝 Notas:" not in result
        kwargs = fake_create_order.call_args.kwargs
        # Empty notes get passed as None so the orders.notes column
        # stays null rather than empty-string.
        assert kwargs["notes"] is None
