"""
Integration tests for the planner — intent classification with real LLM calls.
Run with: pytest -m integration

Uses VCR cassettes (pytest-recording) to record/replay HTTP calls.
Delete cassettes/ and rerun when prompts or tools change.
"""

import pytest
import json

from app.agents.order_agent import _parse_planner_response, PLANNER_SYSTEM_TEMPLATE
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage


pytestmark = pytest.mark.integration


def _classify_intent(message: str, order_state: str = "GREETING", cart_summary: str = "Pedido vacío."):
    """Helper: send a message through the real planner LLM and return parsed intent + params."""
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=256)
    planner_system = PLANNER_SYSTEM_TEMPLATE.format(
        order_state=order_state,
        cart_summary=cart_summary,
    )
    messages = [
        SystemMessage(content=planner_system),
        HumanMessage(content=f"Historial reciente:\n\nUsuario: {message}\n\nResponde solo con JSON: intent y params."),
    ]
    response = llm.invoke(messages)
    return _parse_planner_response(response.content)


class TestPlannerIntentClassification:
    """Test planner classifies real Spanish messages to correct intents."""

    def test_greeting_classified_as_greet(self):
        """A simple 'Hola' should be classified as GREET."""
        result = _classify_intent("Hola")
        assert result["intent"] == "GREET"

    # Case: "Qué tienen de bebidas?" → LIST_PRODUCTS with category containing "bebidas"
    # Case: "Una barracuda y una coca cola" → ADD_TO_CART with items list (2 items)
    # Case: "Sin cebolla la barracuda" (state=ORDERING, cart has barracuda) → UPDATE_CART_ITEM with notes
    # Case: "Quita la malteada" → REMOVE_FROM_CART with product_name
    # Case: "Listo, procede" → PROCEED_TO_CHECKOUT
    # Case: "Calle 19 #29-99" (state=COLLECTING_DELIVERY) → SUBMIT_DELIVERY_INFO with address
    # Case: "Sí, confirma" (state=READY_TO_PLACE) → PLACE_ORDER
    # Case: "Hola, dame una barracuda" → ADD_TO_CART (NOT GREET — greeting mixed with order)
    # Case: "Tienen algo con queso azul?" → SEARCH_PRODUCTS with query
    # Case: "Qué trae la montesa?" → GET_PRODUCT with product_name
    # Case: "Cuánto cuesta el domicilio?" → CHAT (general question, not a specific intent)
    # Case: "Efectivo" (state=COLLECTING_DELIVERY) → SUBMIT_DELIVERY_INFO with payment_method
    # Case: "No es esa dirección, es calle 80" (state=COLLECTING_DELIVERY) → SUBMIT_DELIVERY_INFO correcting address
