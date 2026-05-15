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

    # Greeting is handled by the router fast-path (app/services/business_greeting.py)
    # before any agent runs. Tests for it live in tests/unit/test_business_greeting.py
    # and tests/unit/test_router.py — no agent-level greeting test anymore.

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
