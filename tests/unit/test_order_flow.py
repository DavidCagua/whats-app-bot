"""
Unit tests for order_flow.py — state machine transitions and intent guards.
Tests the executor logic without any LLM or DB calls.
"""

import pytest
from unittest.mock import patch, MagicMock

from app.database.session_state_service import (
    derive_order_state,
    ORDER_STATE_GREETING,
    ORDER_STATE_ORDERING,
    ORDER_STATE_COLLECTING_DELIVERY,
    ORDER_STATE_READY_TO_PLACE,
)
from app.orchestration.order_flow import (
    execute_order_intent,
    INTENT_GREET,
    INTENT_ADD_TO_CART,
    INTENT_VIEW_CART,
    INTENT_UPDATE_CART_ITEM,
    INTENT_REMOVE_FROM_CART,
    INTENT_PROCEED_TO_CHECKOUT,
    INTENT_SUBMIT_DELIVERY_INFO,
    INTENT_PLACE_ORDER,
    INTENT_CHAT,
    INTENT_GET_MENU_CATEGORIES,
    INTENT_LIST_PRODUCTS,
    INTENT_SEARCH_PRODUCTS,
    ALLOWED_INTENTS_BY_STATE,
    _normalize_product_name,
)


# ---------------------------------------------------------------------------
# derive_order_state
# ---------------------------------------------------------------------------

class TestDeriveOrderState:
    """Test state derivation from order_context."""

    def test_empty_context_returns_greeting(self):
        assert derive_order_state(None) == ORDER_STATE_GREETING
        assert derive_order_state({}) == ORDER_STATE_GREETING

    # Case: context with items but no delivery info → ORDERING
    # Case: context with items + partial delivery info → ORDERING
    # Case: context with items + full delivery info (name, address, phone, payment) → READY_TO_PLACE
    # Case: context with explicit state field → returns that state directly
    # Case: context with unknown state field → falls through to derivation logic


# ---------------------------------------------------------------------------
# Intent guards (ALLOWED_INTENTS_BY_STATE)
# ---------------------------------------------------------------------------

class TestIntentGuards:
    """Test that intents are only allowed in the correct states."""

    def test_place_order_blocked_in_greeting(self, fake_session, wa_id, business_context):
        """PLACE_ORDER should be rejected when state is GREETING."""
        session = {"order_context": {"state": ORDER_STATE_GREETING}}

        with patch("app.orchestration.order_flow.session_state_service", fake_session), \
             patch("app.orchestration.order_flow.order_tools"):
            result = execute_order_intent(
                wa_id=wa_id,
                business_id=business_context["business_id"],
                business_context=business_context,
                session=session,
                intent=INTENT_PLACE_ORDER,
            )

        assert result["success"] is False
        assert result.get("result_kind") == "user_error"
        assert result.get("error_kind") == "user_visible"
        assert result.get("error")  # some user-facing message exists
        assert result["state_after"] == ORDER_STATE_GREETING

    # Case: PROCEED_TO_CHECKOUT blocked in GREETING state
    # Case: VIEW_CART blocked in GREETING state (not in allowed list)
    # Case: REMOVE_FROM_CART blocked in GREETING state
    # Case: UPDATE_CART_ITEM blocked in GREETING state
    # Case: ADD_TO_CART allowed in GREETING state
    # Case: GREET allowed in GREETING state but not in ORDERING
    # Case: SUBMIT_DELIVERY_INFO blocked in ORDERING state
    # Case: PLACE_ORDER blocked in ORDERING state
    # Case: PLACE_ORDER allowed in READY_TO_PLACE state
    # Case: Menu browsing intents (GET_MENU_CATEGORIES, LIST_PRODUCTS, SEARCH_PRODUCTS) allowed in GREETING and ORDERING

    @pytest.mark.parametrize(
        "starting_state",
        [ORDER_STATE_READY_TO_PLACE, ORDER_STATE_COLLECTING_DELIVERY],
    )
    def test_add_to_cart_reopens_cart_from_post_cart_states(
        self, starting_state, fake_session, wa_id, business_context
    ):
        """
        A cart-mutating intent arriving after the user has moved past ORDERING
        (into COLLECTING_DELIVERY or READY_TO_PLACE) must not be rejected: the
        flow should drop back to ORDERING and execute the intent. Guards against
        the prod bug where users couldn't add items after starting checkout.
        """
        business_id = business_context["business_id"]
        fake_session.save(
            wa_id, business_id,
            {"order_context": {
                "items": [{"product_id": "prod-001", "name": "BARRACUDA", "quantity": 1, "price": 18000}],
                "total": 18000,
                "delivery_info": {
                    "name": "Luis", "address": "Calle 1", "phone": "+573001234567",
                    "payment_method": "efectivo",
                },
                "state": starting_state,
            }},
        )
        session = fake_session.load(wa_id, business_id)["session"]

        fake_tool = MagicMock()
        fake_tool.invoke = MagicMock(return_value=None)

        with patch("app.orchestration.order_flow.session_state_service", fake_session), \
             patch("app.orchestration.order_flow.order_tools") as mock_tools, \
             patch("app.orchestration.order_flow._find_tool", return_value=fake_tool), \
             patch("app.orchestration.order_flow._get_cart_for_logging") as mock_cart_log, \
             patch("app.orchestration.order_flow._build_cart_change",
                   return_value={"action": "added", "items": [], "total": 36000}):
            mock_cart_log.side_effect = [
                {"items": [{"name": "BARRACUDA", "quantity": 1}], "total": 18000},
                {"items": [{"name": "BARRACUDA", "quantity": 1}, {"name": "LIMONADA", "quantity": 1}], "total": 23000},
            ]
            mock_tools._cart_from_session.return_value = {"items": [], "total": 23000}

            result = execute_order_intent(
                wa_id=wa_id,
                business_id=business_id,
                business_context=business_context,
                session=session,
                intent=INTENT_ADD_TO_CART,
                params={"product_name": "LIMONADA", "quantity": 1},
            )

        assert result["success"] is True
        assert result["result_kind"] == "cart_change"
        # State was re-opened, not stuck post-ORDERING
        stored = fake_session.load(wa_id, business_id)["session"]
        assert stored["order_context"]["state"] == ORDER_STATE_ORDERING
        fake_tool.invoke.assert_called_once()


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------

class TestStateTransitions:
    """Test that successful tool execution causes correct state transitions."""

    # Case: ADD_TO_CART success in GREETING → transitions to ORDERING
    # Case: ADD_TO_CART success in ORDERING → stays in ORDERING
    # Case: PROCEED_TO_CHECKOUT in ORDERING with items → transitions to COLLECTING_DELIVERY
    # Case: PROCEED_TO_CHECKOUT in ORDERING with empty cart → rejected, stays in ORDERING
    # Case: SUBMIT_DELIVERY_INFO with all fields → transitions to READY_TO_PLACE
    # Case: SUBMIT_DELIVERY_INFO with partial fields → stays in COLLECTING_DELIVERY
    # Case: PLACE_ORDER success → resets to GREETING (context cleared)
    # Case: CHAT intent → no state change regardless of current state
    # Case: GREET intent → no state change


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

class TestNormalizeProductName:
    """Test _normalize_product_name fuzzy matching helper."""

    # Case: lowercase and strip whitespace
    # Case: collapse multiple spaces
    # Case: treat hyphens as spaces ("coca-cola" → "coca cola")
    # Case: empty string → empty string
    # Case: None → empty string


# ---------------------------------------------------------------------------
# Intent-to-tool mapping
# ---------------------------------------------------------------------------

class TestIntentToolMapping:
    """Test that intents are mapped to the correct tool with correct args."""

    # Case: INTENT_ADD_TO_CART maps to "add_to_cart" with product_name, quantity, notes
    # Case: INTENT_ADD_TO_CART with items[] list invokes add_to_cart multiple times (multi-item)
    # Case: INTENT_REMOVE_FROM_CART maps to "remove_from_cart" with product_id or product_name
    # Case: INTENT_UPDATE_CART_ITEM resolves product_id from product_name in cart
    # Case: INTENT_UPDATE_CART_ITEM with 2-item list (replace A with B) → reduce first, add second
    # Case: INTENT_LIST_PRODUCTS maps to "list_category_products" with category param
    # Case: INTENT_SEARCH_PRODUCTS maps to "search_products" with query param
    # Case: Unknown intent → returns error "no mapeado a herramienta"
