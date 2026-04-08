"""
Unit tests for _parse_planner_response — extracting intent + params from LLM output.
No LLM calls, no DB — pure string parsing logic.
"""

import pytest

from app.agents.order_agent import _parse_planner_response


class TestParsePlannerResponse:
    """Test planner response JSON extraction."""

    def test_clean_json(self):
        """Clean JSON string should be parsed correctly."""
        text = '{"intent": "ADD_TO_CART", "params": {"product_name": "BARRACUDA", "quantity": 1}}'
        result = _parse_planner_response(text)
        assert result["intent"] == "ADD_TO_CART"
        assert result["params"]["product_name"] == "BARRACUDA"
        assert result["params"]["quantity"] == 1

    # Case: JSON wrapped in markdown code block (```json ... ```)
    # Case: JSON with extra whitespace and newlines
    # Case: JSON embedded in text ("Here is the result: {...}")
    # Case: Completely malformed text (no JSON at all) → falls back to {"intent": "CHAT", "params": {}}
    # Case: Empty string → falls back to CHAT
    # Case: None → falls back to CHAT
    # Case: JSON with nested params (e.g. items list with multiple products)
    # Case: JSON with unicode characters in product names (e.g. "jalapeño")
    # Case: Partial JSON (missing closing brace) → falls back to CHAT
    # Case: Multiple JSON objects in text → extracts the first valid one
