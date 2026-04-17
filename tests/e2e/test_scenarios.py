"""
End-to-end scenario tests — full multi-turn conversation flows.
Tests the complete pipeline: Planner → Executor → Response Generator.

Uses GenericFakeChatModel to script LLM responses, making tests deterministic.
Uses FakeSessionStateService for in-memory state (no DB).
"""

import pytest
from unittest.mock import patch, MagicMock
from langchain_core.language_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from app.agents.order_agent import OrderAgent
from app.database.session_state_service import ORDER_STATE_GREETING, ORDER_STATE_ORDERING
from tests.conftest import FAKE_BUSINESS_ID, FAKE_WA_ID, SAMPLE_PRODUCTS


def _make_fake_llm(responses):
    """Create a GenericFakeChatModel with scripted responses."""
    return GenericFakeChatModel(messages=iter([
        AIMessage(content=r) if isinstance(r, str) else r
        for r in responses
    ]))


class TestHappyPathOrder:
    """Test a complete order flow from greeting to placement."""

    def test_greeting_returns_deterministic_response(self, fake_session, business_context):
        """Greeting should return the hardcoded welcome message without hitting response generator LLM."""
        fake_llm = _make_fake_llm([
            # Planner returns GREET intent
            '{"intent": "GREET", "params": {}}',
        ])

        agent = OrderAgent()
        agent.llm = fake_llm

        with patch("app.agents.order_agent.execute_order_intent") as mock_exec, \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"):

            mock_exec.return_value = {
                "success": True,
                "tool_result": "GREET",
                "state_after": ORDER_STATE_GREETING,
                "error": None,
                "cart_summary": "Pedido vacío.",
            }

            result = agent.execute(
                message_body="Hola",
                wa_id=FAKE_WA_ID,
                name="Juan",
                business_context=business_context,
                conversation_history=[],
                session={"order_context": {"state": "GREETING"}},
            )

        assert result["agent_type"] == "order"
        assert "Test Restaurant" in result["message"] or "BIELA" in result["message"]
        # Greeting is deterministic — should not call the response generator LLM
        # Only 1 LLM call (planner), not 2

    # Case: Full happy path — greeting → add item → checkout → delivery info → place order
    #   Script 2 LLM calls per non-GREET turn (planner + response generator).
    #   After place_order, verify state resets to GREETING and order_context is cleared.

    # Case: Add multiple items in one message ("una barracuda y una coca cola")
    #   Planner returns ADD_TO_CART with items list.
    #   Executor calls add_to_cart for each item.
    #   Response includes both items in cart summary.


class TestCartModifications:
    """Test cart modification scenarios."""

    # Case: Add item → update notes ("sin cebolla") → verify notes saved
    #   Planner returns UPDATE_CART_ITEM with product_name and notes.
    #   Response generator confirms the modification.

    # Case: Add item → remove item → verify cart is empty
    #   After removal, cart_summary should show "Pedido vacío."

    # Case: Add item → replace with different item (UPDATE_CART_ITEM with 2-item list)
    #   First item reduced/removed, second item added.


class TestReturningCustomer:
    """Test flows where customer has saved info in DB."""

    # Case: Customer with full delivery info in DB → PROCEED_TO_CHECKOUT → skips COLLECTING_DELIVERY
    #   get_customer_info returns all_present=true.
    #   State transitions directly to READY_TO_PLACE.

    # Case: Customer with partial DB info → PROCEED_TO_CHECKOUT → asks only for missing fields
    #   Response should mention which specific fields are missing.


class TestEdgeCases:
    """Test error handling and edge cases."""

    # Case: No business_id in context → returns error message immediately
    # Case: Planner returns malformed JSON → falls back to CHAT intent
    # Case: Tool execution fails (exception) → response includes error message
    # Case: Empty message body → agent still processes (planner classifies as CHAT)
    # Case: Session is None → agent loads session from session_state_service
