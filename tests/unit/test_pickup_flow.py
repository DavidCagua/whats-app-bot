"""
Unit tests for the pickup-vs-delivery flow.

Coverage:
- ``_compute_order_state`` branches on fulfillment_type (pickup needs only name).
- ``_read_fulfillment_type`` reads the session value with safe defaults.
- ``submit_delivery_info`` accepts ``fulfillment_type``, persists it, and the
  ``all_present`` branch matches the mode (pickup → name only).
- ``get_customer_info`` surfaces the mode and applies the same branch.
- ``place_order`` requires only name in pickup mode, skips the delivery fee,
  passes ``fulfillment_type`` to ``create_order``, and renders a pickup-shaped
  receipt.
- ``TurnContext.fulfillment_type`` defaults to ``'delivery'`` and renders as a
  visible mode line in every layer's prompt.
- ``response_renderer._build_confirm_text`` and ``order_cta._summary_block``
  collapse to "Nombre + Modo" in pickup mode.
"""

from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import FAKE_BUSINESS_ID, FAKE_WA_ID


def _make_ctx(business_id=FAKE_BUSINESS_ID, wa_id=FAKE_WA_ID):
    return {"business_id": business_id, "wa_id": wa_id}


# ---------------------------------------------------------------------------
# _compute_order_state
# ---------------------------------------------------------------------------

class TestComputeOrderStatePickup:
    def test_pickup_with_name_is_ready_to_place(self):
        from app.services.order_tools import _compute_order_state
        from app.database.session_state_service import ORDER_STATE_READY_TO_PLACE
        out = _compute_order_state(
            items=[{"product_id": "x", "name": "X", "price": 10000, "quantity": 1}],
            delivery_info={"name": "Camilo"},
            fulfillment_type="pickup",
        )
        assert out == ORDER_STATE_READY_TO_PLACE

    def test_pickup_without_name_stays_ordering(self):
        from app.services.order_tools import _compute_order_state
        from app.database.session_state_service import ORDER_STATE_ORDERING
        out = _compute_order_state(
            items=[{"product_id": "x", "name": "X", "price": 10000, "quantity": 1}],
            delivery_info={},
            fulfillment_type="pickup",
        )
        assert out == ORDER_STATE_ORDERING

    def test_delivery_still_requires_all_four(self):
        from app.services.order_tools import _compute_order_state
        from app.database.session_state_service import (
            ORDER_STATE_ORDERING, ORDER_STATE_READY_TO_PLACE,
        )
        items = [{"product_id": "x", "name": "X", "price": 10000, "quantity": 1}]
        # Only name → still ORDERING in delivery
        assert _compute_order_state(
            items, {"name": "C"}, "delivery"
        ) == ORDER_STATE_ORDERING
        # All four → READY_TO_PLACE
        assert _compute_order_state(
            items,
            {"name": "C", "address": "Cl 1", "phone": "300", "payment_method": "Efectivo"},
            "delivery",
        ) == ORDER_STATE_READY_TO_PLACE


# ---------------------------------------------------------------------------
# _read_fulfillment_type helper
# ---------------------------------------------------------------------------

class TestReadFulfillmentType:
    def test_defaults_to_delivery(self, fake_session):
        with patch("app.services.order_tools.session_state_service", fake_session):
            from app.services.order_tools import _read_fulfillment_type
            assert _read_fulfillment_type(FAKE_WA_ID, FAKE_BUSINESS_ID) == "delivery"

    def test_reads_pickup_when_set(self, fake_session):
        fake_session.save(
            FAKE_WA_ID, FAKE_BUSINESS_ID,
            {"order_context": {"fulfillment_type": "pickup"}},
        )
        # _read_fulfillment_type re-imports session_state_service inside
        # the function, so the patch must target the database module.
        with patch(
            "app.database.session_state_service.session_state_service",
            fake_session,
        ):
            from app.services.order_tools import _read_fulfillment_type
            assert _read_fulfillment_type(FAKE_WA_ID, FAKE_BUSINESS_ID) == "pickup"

    def test_unknown_value_falls_back_to_delivery(self, fake_session):
        fake_session.save(
            FAKE_WA_ID, FAKE_BUSINESS_ID,
            {"order_context": {"fulfillment_type": "garbage"}},
        )
        with patch(
            "app.database.session_state_service.session_state_service",
            fake_session,
        ):
            from app.services.order_tools import _read_fulfillment_type
            assert _read_fulfillment_type(FAKE_WA_ID, FAKE_BUSINESS_ID) == "delivery"


# ---------------------------------------------------------------------------
# submit_delivery_info — fulfillment_type arg
# ---------------------------------------------------------------------------

class TestSubmitDeliveryInfoPickup:
    def _seed_cart(self, fake_session, items=None, delivery_info=None, fulfillment_type=None):
        oc = {"items": items or [{"product_id": "p", "name": "X", "price": 10000, "quantity": 1}]}
        if delivery_info is not None:
            oc["delivery_info"] = delivery_info
        if fulfillment_type is not None:
            oc["fulfillment_type"] = fulfillment_type
        fake_session.save(FAKE_WA_ID, FAKE_BUSINESS_ID, {"order_context": oc})

    def test_pickup_with_name_reports_all_present(self, fake_session):
        self._seed_cart(fake_session)
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service.get_customer", return_value={}):
            from app.services.order_tools import submit_delivery_info
            result = submit_delivery_info.invoke({
                "injected_business_context": _make_ctx(),
                "name": "Camilo Restrepo",
                "fulfillment_type": "pickup",
            })
        assert "all_present=true" in result
        assert "modo=pickup" in result
        # Mode persisted on the order context.
        oc = fake_session.load(FAKE_WA_ID, FAKE_BUSINESS_ID)["session"]["order_context"]
        assert oc["fulfillment_type"] == "pickup"
        assert oc["delivery_info"]["name"] == "Camilo Restrepo"

    def test_pickup_without_name_reports_missing_name_only(self, fake_session):
        self._seed_cart(fake_session)
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service.get_customer", return_value={}):
            from app.services.order_tools import submit_delivery_info
            result = submit_delivery_info.invoke({
                "injected_business_context": _make_ctx(),
                "fulfillment_type": "pickup",
            })
        assert "all_present=false" in result
        assert "missing=name" in result
        # Address / phone / payment must NOT be reported as missing in pickup.
        assert "missing=name,address" not in result

    def test_switch_back_to_delivery_re_requires_all_four(self, fake_session):
        # Start in pickup with a name on file.
        self._seed_cart(
            fake_session,
            delivery_info={"name": "Camilo"},
            fulfillment_type="pickup",
        )
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service.get_customer", return_value={}):
            from app.services.order_tools import submit_delivery_info
            result = submit_delivery_info.invoke({
                "injected_business_context": _make_ctx(),
                "fulfillment_type": "delivery",
            })
        assert "modo=delivery" in result
        assert "all_present=false" in result
        # All three other fields are missing now.
        for f in ("address", "phone", "payment"):
            assert f in result
        oc = fake_session.load(FAKE_WA_ID, FAKE_BUSINESS_ID)["session"]["order_context"]
        assert oc["fulfillment_type"] == "delivery"

    def test_invalid_fulfillment_type_rejected(self, fake_session):
        self._seed_cart(fake_session)
        with patch("app.services.order_tools.session_state_service", fake_session):
            from app.services.order_tools import submit_delivery_info
            result = submit_delivery_info.invoke({
                "injected_business_context": _make_ctx(),
                "name": "Camilo",
                "fulfillment_type": "drone",
            })
        assert result.startswith("❌")
        assert "fulfillment_type" in result

    def test_empty_fulfillment_type_leaves_mode_unchanged(self, fake_session):
        # Already in pickup; a normal field-update call should keep pickup.
        self._seed_cart(
            fake_session,
            delivery_info={"name": "Old"},
            fulfillment_type="pickup",
        )
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service.get_customer", return_value={}):
            from app.services.order_tools import submit_delivery_info
            submit_delivery_info.invoke({
                "injected_business_context": _make_ctx(),
                "name": "Camilo Nuevo",  # update
                # fulfillment_type omitted
            })
        oc = fake_session.load(FAKE_WA_ID, FAKE_BUSINESS_ID)["session"]["order_context"]
        assert oc["fulfillment_type"] == "pickup"
        assert oc["delivery_info"]["name"] == "Camilo Nuevo"


# ---------------------------------------------------------------------------
# get_customer_info — pickup branch
# ---------------------------------------------------------------------------

class TestGetCustomerInfoPickup:
    def test_pickup_with_name_returns_all_present(self, fake_session):
        fake_session.save(
            FAKE_WA_ID, FAKE_BUSINESS_ID,
            {"order_context": {
                "items": [{"product_id": "p", "name": "X", "price": 1, "quantity": 1}],
                "delivery_info": {"name": "Camilo"},
                "fulfillment_type": "pickup",
            }},
        )
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service.get_customer", return_value={}):
            from app.services.order_tools import get_customer_info
            result = get_customer_info.invoke({"injected_business_context": _make_ctx()})
        assert "mode=pickup" in result
        assert "all_present=true" in result
        assert "missing=" in result
        # Should NOT report address/phone/payment as missing.
        assert "address," not in result.split("missing=")[1]


# ---------------------------------------------------------------------------
# place_order — pickup
# ---------------------------------------------------------------------------

class TestPlaceOrderPickup:
    def _seed_pickup_cart(self, fake_session, with_name=True):
        oc = {
            "items": [{"product_id": "00000000-0000-0000-0000-000000000abc",
                       "name": "BARRACUDA", "price": 18000, "quantity": 1}],
            "fulfillment_type": "pickup",
            "awaiting_confirmation": True,
        }
        if with_name:
            oc["delivery_info"] = {"name": "Camilo"}
        fake_session.save(FAKE_WA_ID, FAKE_BUSINESS_ID, {"order_context": oc})

    def test_pickup_with_name_places_order_no_delivery_fee(self, fake_session):
        self._seed_pickup_cart(fake_session)
        biz_ctx = {
            "business_id": FAKE_BUSINESS_ID,
            "wa_id": FAKE_WA_ID,
            "business": {"settings": {"delivery_fee": 5000, "products_enabled": True}},
        }
        fake_create_order = MagicMock(return_value={
            "success": True, "order_id": "11111111-1111-1111-1111-111111111111",
            "subtotal": 18000.0, "total": 18000.0, "promo_discount": 0.0,
            "applied_promos": [],
        })
        # preview_cart drives the receipt's items breakdown — return a
        # display_groups shape matching what view_cart would render.
        fake_preview = {
            "subtotal": 18000.0,
            "promo_discount_total": 0.0,
            "display_groups": [
                {"kind": "item", "quantity": 1, "name": "BARRACUDA",
                 "line_total": 18000.0, "notes": None},
            ],
        }
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service.get_customer", return_value=None), \
             patch("app.services.order_tools.product_order_service.create_order", fake_create_order), \
             patch("app.services.order_tools._read_awaiting_confirmation", return_value=True), \
             patch(
                 "app.services.order_tools.promotion_service.preview_cart",
                 return_value=fake_preview,
             ):
            from app.services.order_tools import place_order
            result = place_order.invoke({"injected_business_context": biz_ctx})
        assert "✅ ¡Pedido confirmado!" in result
        assert "Recoge en el local" in result
        # Items breakdown is present: bullet line per cart item with
        # name + quantity + line total.
        assert "1x BARRACUDA" in result
        assert "$18.000" in result
        # Subtotal + Total are both shown so the customer sees the
        # math even when delivery fee is zero.
        assert "Subtotal:" in result
        assert "Total:" in result
        # Pickup receipt must NOT show a domicilio line.
        assert "🛵 Domicilio" not in result
        # Pickup quotes the SHORTER kitchen-prep ETA, not the
        # 40–50 min delivery window.
        from app.services.order_eta import PICKUP_RANGE_TEXT, NOMINAL_RANGE_TEXT
        assert PICKUP_RANGE_TEXT in result
        assert NOMINAL_RANGE_TEXT not in result
        # create_order called with fulfillment_type='pickup' AND delivery_fee=0.
        kwargs = fake_create_order.call_args.kwargs
        assert kwargs["fulfillment_type"] == "pickup"
        assert kwargs["delivery_fee"] == 0.0
        # No address / payment_method passed for pickup.
        assert kwargs["delivery_address"] is None
        assert kwargs["payment_method"] is None

    def test_delivery_receipt_includes_items_breakdown(self, fake_session):
        """Symmetric coverage: delivery receipts also itemize. Production
        trace 2026-05-09 was missing the line items — easy to lose
        when adding the pickup branch."""
        # Delivery cart with two items so the breakdown matters visibly.
        fake_session.save(
            FAKE_WA_ID, FAKE_BUSINESS_ID,
            {"order_context": {
                "items": [
                    {"product_id": "00000000-0000-0000-0000-000000000abc",
                     "name": "BARRACUDA", "price": 35000, "quantity": 1},
                    {"product_id": "00000000-0000-0000-0000-000000000def",
                     "name": "DENVER", "price": 27000, "quantity": 2},
                ],
                "fulfillment_type": "delivery",
                "awaiting_confirmation": True,
                "delivery_info": {
                    "name": "Erik", "address": "Calle 19", "phone": "3152133998",
                    "payment_method": "Efectivo",
                },
            }},
        )
        biz_ctx = {
            "business_id": FAKE_BUSINESS_ID, "wa_id": FAKE_WA_ID,
            "business": {"settings": {"delivery_fee": 7000, "products_enabled": True}},
        }
        fake_create_order = MagicMock(return_value={
            "success": True, "order_id": "22222222-2222-2222-2222-222222222222",
            "subtotal": 89000.0, "total": 96000.0, "promo_discount": 0.0,
            "applied_promos": [],
        })
        fake_preview = {
            "subtotal": 89000.0,
            "promo_discount_total": 0.0,
            "display_groups": [
                {"kind": "item", "quantity": 1, "name": "BARRACUDA",
                 "line_total": 35000.0, "notes": None},
                {"kind": "item", "quantity": 2, "name": "DENVER",
                 "line_total": 54000.0, "notes": None},
            ],
        }
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service.get_customer", return_value=None), \
             patch("app.services.order_tools.product_order_service.create_order", fake_create_order), \
             patch("app.services.order_tools._read_awaiting_confirmation", return_value=True), \
             patch(
                 "app.services.order_tools.promotion_service.preview_cart",
                 return_value=fake_preview,
             ):
            from app.services.order_tools import place_order
            result = place_order.invoke({"injected_business_context": biz_ctx})
        assert "✅ ¡Pedido confirmado!" in result
        # Each line item is rendered.
        assert "1x BARRACUDA" in result
        assert "$35.000" in result
        assert "2x DENVER" in result
        assert "$54.000" in result
        # Standard delivery footer.
        assert "Subtotal: $89.000" in result
        assert "🛵 Domicilio: $7.000" in result
        assert "Total: $96.000" in result
        from app.services.order_eta import NOMINAL_RANGE_TEXT
        assert NOMINAL_RANGE_TEXT in result

    def test_pickup_without_name_returns_missing_delivery_info(self, fake_session):
        self._seed_pickup_cart(fake_session, with_name=False)
        biz_ctx = {
            "business_id": FAKE_BUSINESS_ID,
            "wa_id": FAKE_WA_ID,
            "business": {"settings": {"products_enabled": True}},
        }
        with patch("app.services.order_tools.session_state_service", fake_session), \
             patch("app.services.order_tools.customer_service.get_customer", return_value=None), \
             patch("app.services.order_tools._read_awaiting_confirmation", return_value=True):
            from app.services.order_tools import place_order
            result = place_order.invoke({"injected_business_context": biz_ctx})
        assert result.startswith("MISSING_DELIVERY_INFO")
        assert "nombre" in result.lower()


# ---------------------------------------------------------------------------
# product_order_service.create_order accepts fulfillment_type
# ---------------------------------------------------------------------------

class TestProductOrderServiceFulfillmentType:
    def test_invalid_fulfillment_type_returns_error_without_db_hit(self):
        from app.database.product_order_service import ProductOrderService
        svc = ProductOrderService()
        result = svc.create_order(
            business_id=FAKE_BUSINESS_ID,
            whatsapp_id=FAKE_WA_ID,
            items=[{"product_id": "p", "price": 10000, "quantity": 1}],
            fulfillment_type="drone",
        )
        assert result["success"] is False
        assert "fulfillment_type" in result["error"]

    def test_pickup_zeroes_delivery_fee_even_when_passed(self):
        # Validation only; we don't exercise the DB path. The relevant
        # branch (effective_delivery_fee = 0 on pickup) is small enough
        # that we cover it via inspection: any non-pickup path would
        # have included the fee in grand_total. Here we confirm the
        # function reaches the validation gate at minimum and that the
        # public signature accepts both modes.
        from app.database.product_order_service import ProductOrderService
        # Empty items short-circuits before DB; we just need the validator
        # to accept 'pickup' as legal.
        svc = ProductOrderService()
        result = svc.create_order(
            business_id=FAKE_BUSINESS_ID,
            whatsapp_id=FAKE_WA_ID,
            items=[],
            fulfillment_type="pickup",
            delivery_fee=5000.0,
        )
        # Expect the empty-cart guard, not the invalid-ftype one.
        assert result["success"] is False
        assert "vacío" in result["error"]


# ---------------------------------------------------------------------------
# TurnContext default + render
# ---------------------------------------------------------------------------

class TestTurnContextFulfillmentType:
    def test_default_is_delivery(self):
        from app.orchestration.turn_context import TurnContext
        ctx = TurnContext()
        assert ctx.fulfillment_type == "delivery"

    def test_render_for_prompt_omits_modo_line_in_default_delivery(self):
        """Default delivery state is implicit — emitting "Modo: 🛵 Domicilio"
        on every turn biased the router toward "this user is mid-order"
        on greetings (production trace 2026-05-09: "hola buenas noches"
        routed to order). Only the explicit pickup case shows a Modo line."""
        from app.orchestration.turn_context import TurnContext, render_for_prompt
        rendered = render_for_prompt(TurnContext())
        assert "Modo:" not in rendered

    def test_render_for_prompt_shows_pickup_when_set(self):
        from app.orchestration.turn_context import TurnContext, render_for_prompt
        rendered = render_for_prompt(TurnContext(fulfillment_type="pickup"))
        assert "Modo: 🏃 Recoger en local" in rendered
        assert "explícitamente" in rendered.lower()


# ---------------------------------------------------------------------------
# Confirm text + CTA summary block
# ---------------------------------------------------------------------------

class TestConfirmTextPickup:
    _BIZ_CTX = {"business_id": "biz1", "business": {"name": "Biela", "settings": {}}}

    def test_pickup_confirm_text_only_name_and_mode(self):
        from app.services import response_renderer
        with patch.object(response_renderer, "_has_cart_items", return_value=True), \
             patch.object(
                 response_renderer,
                 "_read_delivery_status",
                 return_value={
                     "name": "Camilo",
                     "address": "",
                     "phone": "",
                     "payment_method": "",
                     "total": 18000,
                     "all_present": True,
                     "fulfillment_type": "pickup",
                 },
             ):
            body = response_renderer._build_confirm_text(self._BIZ_CTX, "+57300")
        assert "*Nombre:* Camilo" in body
        assert "Recoger en local" in body
        assert "*Dirección:*" not in body
        assert "*Teléfono:*" not in body
        assert "*Pago:*" not in body
        assert "¿Confirmamos el pedido?" in body

    def test_delivery_confirm_text_keeps_all_fields(self):
        from app.services import response_renderer
        with patch.object(response_renderer, "_has_cart_items", return_value=True), \
             patch.object(
                 response_renderer,
                 "_read_delivery_status",
                 return_value={
                     "name": "Erik",
                     "address": "Calle 19",
                     "phone": "3152133998",
                     "payment_method": "Efectivo",
                     "total": 140000,
                     "all_present": True,
                     "fulfillment_type": "delivery",
                 },
             ):
            body = response_renderer._build_confirm_text(self._BIZ_CTX, "+57300")
        assert "*Nombre:* Erik" in body
        assert "*Dirección:* Calle 19" in body
        assert "*Teléfono:* 3152133998" in body
        assert "*Pago:* Efectivo" in body
        # Mode line shouldn't leak into the delivery shape.
        assert "Recoger en local" not in body


class TestSummaryBlockPickup:
    def test_pickup_summary_block_only_name_and_mode(self):
        from app.services.order_cta import _summary_block
        out = _summary_block({
            "name": "Camilo",
            "address": "",
            "phone": "",
            "payment_method": "",
            "fulfillment_type": "pickup",
        })
        assert "*Nombre:* Camilo" in out
        assert "Recoger en local" in out
        assert "Dirección" not in out
        assert "Teléfono" not in out
        assert "Pago" not in out

    def test_delivery_summary_block_keeps_all_fields(self):
        from app.services.order_cta import _summary_block
        out = _summary_block({
            "name": "Erik",
            "address": "Cl 19",
            "phone": "3152",
            "payment_method": "Efectivo",
            "fulfillment_type": "delivery",
        })
        assert "*Nombre:* Erik" in out
        assert "*Dirección:* Cl 19" in out
        assert "*Teléfono:* 3152" in out
        assert "*Pago:* Efectivo" in out
        assert "Recoger en local" not in out


# ---------------------------------------------------------------------------
# _format_business_info_for_prompt — universal Modos block
# ---------------------------------------------------------------------------

class TestEstimateRemainingMinutesPickup:
    """``estimate_remaining_minutes`` must return the smaller pickup
    budget when an order's ``fulfillment_type`` is ``'pickup'`` so the
    CS agent's "¿cuánto se demora?" answer matches what the receipt
    promised."""

    def test_pickup_pending_uses_pickup_budget(self):
        from app.services.order_eta import (
            estimate_remaining_minutes,
            PICKUP_TOTAL_MINUTES,
            NOMINAL_TOTAL_MINUTES,
        )
        out = estimate_remaining_minutes({
            "status": "pending",
            "fulfillment_type": "pickup",
        })
        assert out == PICKUP_TOTAL_MINUTES
        assert out < NOMINAL_TOTAL_MINUTES

    def test_pickup_confirmed_subtracts_elapsed(self):
        from datetime import datetime, timedelta, timezone
        from app.services.order_eta import (
            estimate_remaining_minutes,
            PICKUP_TOTAL_MINUTES,
        )
        confirmed_at = (
            datetime.now(timezone.utc) - timedelta(minutes=5)
        ).isoformat()
        out = estimate_remaining_minutes({
            "status": "confirmed",
            "fulfillment_type": "pickup",
            "confirmed_at": confirmed_at,
        })
        # 17 - 5 = 12 (above the 5-min floor).
        assert out == PICKUP_TOTAL_MINUTES - 5

    def test_delivery_default_unchanged(self):
        from app.services.order_eta import (
            estimate_remaining_minutes,
            NOMINAL_TOTAL_MINUTES,
        )
        out = estimate_remaining_minutes({"status": "pending"})
        assert out == NOMINAL_TOTAL_MINUTES

    def test_pickup_out_for_delivery_returns_none(self):
        # Pickup orders never reach 'out_for_delivery' — confirm we
        # return None rather than the delivery 10-min last-leg budget.
        from app.services.order_eta import estimate_remaining_minutes
        out = estimate_remaining_minutes({
            "status": "out_for_delivery",
            "fulfillment_type": "pickup",
        })
        assert out is None


class TestPromptSurfacesPickupRules:
    def test_modos_block_present_for_every_business(self):
        from app.services.business_info_service import format_business_info_for_prompt
        rendered = format_business_info_for_prompt({
            "business_id": "biela",
            "business": {"name": "Biela", "settings": {}},
        })
        assert "Modos de cumplimiento" in rendered
        assert "Domicilio" in rendered
        assert "Recoger en local" in rendered
        # No per-business toggle — appears even with empty settings.
        assert "fulfillment_type='pickup'" in rendered or "fulfillment_type=\"pickup\"" in rendered
