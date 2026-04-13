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


def _classify_with_pending(message: str, pending_options, requested_name: str, order_state: str = "ORDERING"):
    """
    Variant of _classify_intent that injects a pending_disambiguation block
    into the planner system prompt the same way OrderAgent.execute does at
    runtime. Use to validate disambiguation-reply classification.
    """
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=256)
    planner_system = PLANNER_SYSTEM_TEMPLATE.format(
        order_state=order_state,
        cart_summary="Pedido vacío.",
    )
    opts_lines = "\n".join(
        f"  - {o['name']} (${int(o['price']):,})".replace(",", ".")
        for o in pending_options
    )
    planner_system += (
        "\n\nCONTEXTO DE ACLARACIÓN PENDIENTE: En tu turno ANTERIOR ofreciste al cliente "
        f"estas opciones porque preguntó por \"{requested_name}\":\n"
        f"{opts_lines}\n"
        "Si el mensaje actual del cliente es una elección (ej. 'la normal', 'la primera', 'la barata', "
        "'dame la Corona', 'la michelada', un nombre o un número), mapea su respuesta a UNA de estas opciones y "
        "clasifícalo como ADD_TO_CART con product_name EXACTO de la opción elegida. "
        "Ejemplos: 'la normal' → la opción SIN modificador (ej. \"Michelada\" no \"Corona michelada\"); "
        "'la primera' → la primera de la lista; 'la más barata' → la de menor precio. "
        "Si el cliente está cambiando de tema o pidiendo otra cosa, ignora este contexto y clasifica normalmente."
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

    # --- "para tomar" implicit category routing (Bug: GET_MENU_CATEGORIES instead of LIST_PRODUCTS) ---

    def test_que_hay_para_tomar_classified_as_list_products_bebidas(self):
        """
        'qué hay para tomar?' is an implicit drinks-category query. Must route
        to LIST_PRODUCTS with category=bebidas, not GET_MENU_CATEGORIES.
        """
        result = _classify_intent("que hay para tomar?")
        assert result["intent"] == "LIST_PRODUCTS"
        cat = (result.get("params") or {}).get("category", "").lower()
        assert "bebida" in cat or "beber" in cat or "tomar" in cat

    def test_algo_para_beber_classified_as_list_products_bebidas(self):
        """Synonym phrasing: 'algo para beber' → LIST_PRODUCTS bebidas."""
        result = _classify_intent("algo para beber?")
        assert result["intent"] == "LIST_PRODUCTS"
        cat = (result.get("params") or {}).get("category", "").lower()
        assert "bebida" in cat or "beber" in cat


class TestVariantSwapClassification:
    """
    Regression: "la soda que sea de frutos rojos" must classify as
    UPDATE_CART_ITEM with `new_product_name`, NOT as `notes`. The notes path
    silently dropped price (the classic Bug 2 in the soda flow).
    """

    def test_soda_variant_swap_emits_new_product_name(self):
        result = _classify_intent(
            "la soda que sea de frutos rojos",
            order_state="ORDERING",
            cart_summary="1x Soda ($4.500). Subtotal: $4.500.",
        )
        assert result["intent"] == "UPDATE_CART_ITEM"
        params = result.get("params") or {}
        assert "new_product_name" in params, \
            f"expected new_product_name for a variant swap; got {params}"
        new_name = (params.get("new_product_name") or "").lower()
        assert "frutos" in new_name or "rojos" in new_name
        # Critical: must NOT be shoehorned into `notes` (the pre-fix bug)
        assert not params.get("notes"), \
            f"variant swap must not emit a cosmetic note; got notes={params.get('notes')}"

    def test_ingredient_exclusion_still_uses_notes(self):
        """
        Negative control: 'sin morcilla' is an ingredient modification, must
        stay on the notes path — not misrouted as new_product_name.
        """
        result = _classify_intent(
            "a la picada que no le pongan morcilla",
            order_state="ORDERING",
            cart_summary="1x PICADA ($35.000).",
        )
        assert result["intent"] == "UPDATE_CART_ITEM"
        params = result.get("params") or {}
        assert params.get("notes"), f"expected notes, got {params}"
        assert not params.get("new_product_name"), \
            "ingredient exclusion must not emit new_product_name"


class TestDisambiguationReplyClassification:
    """
    Regression: when the bot has just asked 'which soda?', the user's reply
    must resolve to a single exact option name so the executor bypass can
    map it to a product_id without re-triggering ambiguity.
    """

    SODA_OPTIONS = [
        {"name": "Soda", "price": 4500},
        {"name": "Soda Frutos rojos", "price": 15000},
        {"name": "Soda Uvilla y maracuyá", "price": 15000},
    ]

    def test_plain_soda_reply_picks_generic_option(self):
        """User says 'soda' → planner must pick the exact 'Soda' option name."""
        result = _classify_with_pending(
            message="soda",
            pending_options=self.SODA_OPTIONS,
            requested_name="soda",
        )
        assert result["intent"] == "ADD_TO_CART"
        params = result.get("params") or {}
        name = (params.get("product_name") or "").strip().lower()
        # Must be the exact plain "Soda" — not a variant
        assert name == "soda", f"expected plain 'Soda', got '{name}'"

    def test_variant_reply_picks_variant_option(self):
        """User says 'frutos rojos' → must map to 'Soda Frutos rojos'."""
        result = _classify_with_pending(
            message="frutos rojos",
            pending_options=self.SODA_OPTIONS,
            requested_name="soda",
        )
        assert result["intent"] == "ADD_TO_CART"
        params = result.get("params") or {}
        name = (params.get("product_name") or "").lower()
        assert "frutos" in name and "rojos" in name, \
            f"expected 'Soda Frutos rojos', got '{name}'"
        assert "uvilla" not in name, "must not pick the wrong variant"
