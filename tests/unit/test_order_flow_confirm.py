"""
Regression tests for the CONFIRM semantic intent and the rejection-recovery path.

Background: on 2026-04-13 a Biela customer was dead-ended at the final
confirmation step. The bot asked "¿Procedemos?", the user replied "Procedemos",
the planner mapped that to PROCEED_TO_CHECKOUT, the executor rejected it because
READY_TO_PLACE's allowlist only permitted PLACE_ORDER, and the response
generator surfaced the rejection as "En este momento no podemos proceder con
eso." The user abandoned the order.

These tests pin the fix:
1. CONFIRM resolves to the state-appropriate concrete intent.
2. PROCEED_TO_CHECKOUT in READY_TO_PLACE is coerced to PLACE_ORDER, not rejected.
3. A truly illegal intent produces a recovery result (DELIVERY_STATUS or CHAT),
   never a user_error.
"""

from unittest.mock import patch, MagicMock

import pytest

from app.database.session_state_service import (
    ORDER_STATE_GREETING,
    ORDER_STATE_ORDERING,
    ORDER_STATE_COLLECTING_DELIVERY,
    ORDER_STATE_READY_TO_PLACE,
)
from app.orchestration.order_flow import (
    CONFIRM_RESOLUTION,
    INTENT_CONFIRM,
    INTENT_PROCEED_TO_CHECKOUT,
    INTENT_PLACE_ORDER,
    INTENT_GET_CUSTOMER_INFO,
    INTENT_CHAT,
    RESULT_KIND_USER_ERROR,
    RESULT_KIND_DELIVERY_STATUS,
    RESULT_KIND_CHAT,
    execute_order_intent,
)


# ---------------------------------------------------------------------------
# Resolution table
# ---------------------------------------------------------------------------

class TestConfirmResolutionTable:
    """The CONFIRM_RESOLUTION mapping is the canonical source of truth."""

    def test_greeting_maps_to_chat(self):
        # Nothing to confirm from the greeting state — treat as small talk.
        assert CONFIRM_RESOLUTION[ORDER_STATE_GREETING] == INTENT_CHAT

    def test_ordering_maps_to_proceed_to_checkout(self):
        assert CONFIRM_RESOLUTION[ORDER_STATE_ORDERING] == INTENT_PROCEED_TO_CHECKOUT

    def test_collecting_delivery_maps_to_get_customer_info(self):
        # "Listo" in COLLECTING_DELIVERY means "show me what's missing".
        assert CONFIRM_RESOLUTION[ORDER_STATE_COLLECTING_DELIVERY] == INTENT_GET_CUSTOMER_INFO

    def test_ready_to_place_maps_to_place_order(self):
        # The exact bug: "Procedemos" in READY_TO_PLACE must place the order.
        assert CONFIRM_RESOLUTION[ORDER_STATE_READY_TO_PLACE] == INTENT_PLACE_ORDER


# ---------------------------------------------------------------------------
# Executor behavior: CONFIRM translation
# ---------------------------------------------------------------------------

class TestConfirmExecution:
    """CONFIRM should never reach the allowlist gate — it's translated first."""

    def test_confirm_in_ready_to_place_runs_place_order(
        self, fake_session, wa_id, business_context
    ):
        """The Biela regression: CONFIRM at READY_TO_PLACE runs PLACE_ORDER."""
        business_id = business_context["business_id"]
        fake_session.save(
            wa_id, business_id,
            {"order_context": {
                "items": [{"product_id": "p-1", "name": "HONEY BURGER", "quantity": 1, "price": 28000}],
                "total": 28000,
                "delivery_info": {
                    "name": "Tatiana", "address": "Calle 30", "phone": "+573151234567",
                    "payment_method": "efectivo",
                },
                "state": ORDER_STATE_READY_TO_PLACE,
            }},
        )
        session = fake_session.load(wa_id, business_id)["session"]

        fake_tool = MagicMock()
        # place_order returns a success string with the ✅ marker + order id.
        fake_tool.invoke = MagicMock(return_value="✅ Pedido confirmado #ABC1234")

        with patch("app.orchestration.order_flow.session_state_service", fake_session), \
             patch("app.orchestration.order_flow.order_tools"), \
             patch("app.orchestration.order_flow._find_tool", return_value=fake_tool), \
             patch("app.orchestration.order_flow._clear_pending_disambiguation"), \
             patch("app.orchestration.order_flow._get_cart_for_logging",
                   return_value={
                       "items": [{"product_id": "p-1", "name": "HONEY BURGER", "quantity": 1, "price": 28000}],
                       "total": 28000,
                   }), \
             patch("app.orchestration.order_flow._get_delivery_fee", return_value=0):
            result = execute_order_intent(
                wa_id=wa_id,
                business_id=business_id,
                business_context=business_context,
                session=session,
                intent=INTENT_CONFIRM,
                params={},
            )

        # No USER_ERROR, no rejection — the customer's confirmation did
        # what they expected it to do.
        assert result["result_kind"] != RESULT_KIND_USER_ERROR
        assert result.get("error_kind") != "user_visible"
        # place_order tool was invoked and the structured order_placed payload
        # was returned to the response generator.
        fake_tool.invoke.assert_called_once()
        assert result["result_kind"] == "order_placed"
        assert result["order_placed"]["order_id_display"] == "ABC1234"

    def test_confirm_in_ordering_runs_proceed_to_checkout(
        self, fake_session, wa_id, business_context
    ):
        """CONFIRM while still building the cart moves us to collecting delivery."""
        business_id = business_context["business_id"]
        fake_session.save(
            wa_id, business_id,
            {"order_context": {
                "items": [{"product_id": "p-1", "name": "BARRACUDA", "quantity": 1, "price": 18000}],
                "total": 18000,
                "state": ORDER_STATE_ORDERING,
            }},
        )
        session = fake_session.load(wa_id, business_id)["session"]

        with patch("app.orchestration.order_flow.session_state_service", fake_session), \
             patch("app.orchestration.order_flow.order_tools") as mock_tools, \
             patch("app.orchestration.order_flow._get_cart_for_logging",
                   return_value={"items": [{"name": "BARRACUDA", "quantity": 1}], "total": 18000}):
            mock_tools._cart_from_session.return_value = {
                "items": [{"product_id": "p-1", "name": "BARRACUDA", "quantity": 1, "price": 18000}],
                "total": 18000,
            }
            result = execute_order_intent(
                wa_id=wa_id,
                business_id=business_id,
                business_context=business_context,
                session=session,
                intent=INTENT_CONFIRM,
                params={},
            )

        # The translated intent was PROCEED_TO_CHECKOUT, so we should be
        # either in COLLECTING_DELIVERY (missing info) or READY_TO_PLACE
        # (all info already present). Either way: not a user_error.
        assert result["result_kind"] != RESULT_KIND_USER_ERROR
        assert result["state_after"] in (
            ORDER_STATE_COLLECTING_DELIVERY,
            ORDER_STATE_READY_TO_PLACE,
        )


# ---------------------------------------------------------------------------
# Safety coercion
# ---------------------------------------------------------------------------

class TestProceedToCheckoutCoercion:
    """
    If the planner drifts and emits PROCEED_TO_CHECKOUT in READY_TO_PLACE
    anyway (shouldn't happen now that CONFIRM exists, but belt-and-suspenders),
    the executor must coerce it to PLACE_ORDER instead of rejecting.
    """

    def test_proceed_to_checkout_in_ready_to_place_is_coerced(
        self, fake_session, wa_id, business_context
    ):
        business_id = business_context["business_id"]
        fake_session.save(
            wa_id, business_id,
            {"order_context": {
                "items": [{"product_id": "p-1", "name": "HONEY BURGER", "quantity": 1, "price": 28000}],
                "total": 28000,
                "delivery_info": {
                    "name": "T", "address": "C1", "phone": "+573001234567",
                    "payment_method": "efectivo",
                },
                "state": ORDER_STATE_READY_TO_PLACE,
            }},
        )
        session = fake_session.load(wa_id, business_id)["session"]

        fake_tool = MagicMock()
        fake_tool.invoke = MagicMock(return_value="✅ Pedido confirmado #DEF5678")

        with patch("app.orchestration.order_flow.session_state_service", fake_session), \
             patch("app.orchestration.order_flow.order_tools"), \
             patch("app.orchestration.order_flow._find_tool", return_value=fake_tool), \
             patch("app.orchestration.order_flow._clear_pending_disambiguation"), \
             patch("app.orchestration.order_flow._get_cart_for_logging",
                   return_value={
                       "items": [{"product_id": "p-1", "name": "HONEY BURGER", "quantity": 1, "price": 28000}],
                       "total": 28000,
                   }), \
             patch("app.orchestration.order_flow._get_delivery_fee", return_value=0):
            result = execute_order_intent(
                wa_id=wa_id,
                business_id=business_id,
                business_context=business_context,
                session=session,
                intent=INTENT_PROCEED_TO_CHECKOUT,
                params={},
            )

        assert result["result_kind"] == "order_placed"
        assert result["order_placed"]["order_id_display"] == "DEF5678"
        fake_tool.invoke.assert_called_once()


# ---------------------------------------------------------------------------
# Recovery on rejection
# ---------------------------------------------------------------------------

class TestRejectionRecovery:
    """
    When a truly illegal (non-coercible, non-cart-mutating) intent lands,
    the executor must still not return USER_ERROR. It returns a soft
    recovery result the response generator can turn into a natural reply.
    """

    def test_place_order_in_greeting_returns_recovery_not_user_error(
        self, fake_session, wa_id, business_context
    ):
        business_id = business_context["business_id"]
        fake_session.save(
            wa_id, business_id,
            {"order_context": {"state": ORDER_STATE_GREETING}},
        )
        session = fake_session.load(wa_id, business_id)["session"]

        with patch("app.orchestration.order_flow.session_state_service", fake_session), \
             patch("app.orchestration.order_flow.order_tools"):
            result = execute_order_intent(
                wa_id=wa_id,
                business_id=business_id,
                business_context=business_context,
                session=session,
                intent=INTENT_PLACE_ORDER,
                params={},
            )

        # The old behavior was {success:False, result_kind:"user_error",
        # error:"Esa acción no se puede hacer..."}. Now we return a soft
        # CHAT recovery — the response generator will handle it without
        # dead-ending the user.
        assert result["result_kind"] == RESULT_KIND_CHAT
        assert result.get("error_kind") != "user_visible"
        assert result["state_after"] == ORDER_STATE_GREETING

    def test_place_order_in_collecting_delivery_returns_delivery_status(
        self, fake_session, wa_id, business_context
    ):
        """Rejection in COLLECTING_DELIVERY/READY_TO_PLACE re-renders the prompt."""
        business_id = business_context["business_id"]
        fake_session.save(
            wa_id, business_id,
            {"order_context": {
                "items": [{"product_id": "p-1", "name": "X", "quantity": 1, "price": 1000}],
                "total": 1000,
                "state": ORDER_STATE_COLLECTING_DELIVERY,
            }},
        )
        session = fake_session.load(wa_id, business_id)["session"]

        with patch("app.orchestration.order_flow.session_state_service", fake_session), \
             patch("app.orchestration.order_flow.order_tools"), \
             patch("app.orchestration.order_flow._build_delivery_status",
                   return_value={"all_present": False, "missing": ["phone", "payment_method"]}):
            # GET_MENU_CATEGORIES is not in COLLECTING_DELIVERY's allowlist
            # and isn't cart-mutating (so it won't reopen the cart), and isn't
            # PROCEED_TO_CHECKOUT (so it won't be coerced). Pure rejection path.
            from app.orchestration.order_flow import INTENT_GET_MENU_CATEGORIES
            result = execute_order_intent(
                wa_id=wa_id,
                business_id=business_id,
                business_context=business_context,
                session=session,
                intent=INTENT_GET_MENU_CATEGORIES,
                params={},
            )

        assert result["result_kind"] == RESULT_KIND_DELIVERY_STATUS
        assert result.get("error_kind") != "user_visible"
