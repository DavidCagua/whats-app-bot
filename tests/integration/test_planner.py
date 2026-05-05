"""
Integration tests for the planner — intent classification with real LLM calls.
Run with: pytest -m integration

Uses VCR cassettes (pytest-recording) to record/replay HTTP calls.
Delete cassettes/ and rerun when prompts or tools change.
"""

import pytest
import json

from app.agents.order_agent import (
    _parse_planner_response,
    PLANNER_SYSTEM_TEMPLATE,
    apply_disamb_reply_flavor_fallback,
    build_pending_disambiguation_prompt_block,
)
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage


pytestmark = pytest.mark.integration


def _classify_intent(message: str, order_state: str = "GREETING", cart_summary: str = "Pedido vacío.", latest_order_status: str = ""):
    """Helper: send a message through the real planner LLM and return parsed intent + params."""
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=256)
    latest_order_block = (
        f"\nÚltimo pedido (estado): {latest_order_status}"
        if latest_order_status else ""
    )
    planner_system = PLANNER_SYSTEM_TEMPLATE.format(
        order_state=order_state,
        cart_summary=cart_summary,
        latest_order_block=latest_order_block,
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
    runtime, then runs the same deterministic flavor-preservation
    post-processor. Use to validate disambiguation-reply classification
    as a full "what would production emit" check.
    """
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=256)
    planner_system = PLANNER_SYSTEM_TEMPLATE.format(
        order_state=order_state,
        cart_summary="Pedido vacío.",
        latest_order_block="",
    )
    pending = {
        "requested_name": requested_name,
        "options": pending_options,
    }
    planner_system += build_pending_disambiguation_prompt_block(pending)
    messages = [
        SystemMessage(content=planner_system),
        HumanMessage(content=f"Historial reciente:\n\nUsuario: {message}\n\nResponde solo con JSON: intent y params."),
    ]
    response = llm.invoke(messages)
    parsed = _parse_planner_response(response.content)
    # Apply the same deterministic post-processor OrderAgent.execute
    # runs in production so the integration test sees the full planner
    # contract, not just the raw LLM output.
    parsed = apply_disamb_reply_flavor_fallback(parsed, message, pending)
    return parsed


class TestPlannerIntentClassification:
    """Test planner classifies real Spanish messages to correct intents."""

    # Greeting classification moved to the router fast-path
    # (app/services/business_greeting.py). The order planner no longer
    # handles GREET; pure greetings never reach it.

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


# ---------------------------------------------------------------------------
# Disambiguation reply with flavor qualifier — Bug 1 from the Biela
# +573177000722 transcript (2026-04-16).
#
# The pending-disamb resolver used to instruct the planner to emit just
# `product_name` EXACTLY matching the option name. When the user's reply
# also contained a flavor qualifier ("un jugo de mora en agua"), the
# planner obediently stripped the flavor and emitted `Jugos en agua` with
# no notes — the flavor was lost before reaching the cart. After the fix
# the planner must carry the flavor over as `notes`.
# ---------------------------------------------------------------------------


class TestDisambiguationReplyWithFlavorQualifier:
    """
    When the pending options are generic products ('Jugos en agua',
    'Jugos en leche') and the user replies with both the option choice
    AND a flavor word, the planner must emit ADD_TO_CART with BOTH the
    exact option name and the flavor preserved in `notes`.
    """

    JUGOS_OPTIONS = [
        {"name": "Hervido Mora", "price": 9500},
        {"name": "Jugos en agua", "price": 7500},
        {"name": "Jugos en leche", "price": 7500},
    ]

    def test_jugo_de_mora_en_agua_reply_preserves_mora_flavor(self):
        """
        Regression: "un jugo de mora en agua" as a disamb reply must
        NOT drop the 'mora' flavor. Two valid planner shapes survive:

          A. Disamb-reply interpretation: product_name='Jugos en agua',
             notes='mora' (new Bug 1 planner rule)
          B. Fresh-order interpretation: product_name carries the full
             phrase 'jugo de mora en agua' (the executor's search layer
             then applies Fix B's token-containment rule and attaches
             _derived_notes='mora' on the winning generic row).

        Both end up with the cart line 'Jugos en agua (mora)'. This
        test accepts either planner shape — what it pins is that the
        flavor never disappears before reaching the executor.
        """
        result = _classify_with_pending(
            message="un jugo de mora en agua",
            pending_options=self.JUGOS_OPTIONS,
            requested_name="jugo de mora",
        )
        assert result["intent"] == "ADD_TO_CART"
        params = result.get("params") or {}

        # Single-product shape
        name = (params.get("product_name") or "").lower()
        notes = (params.get("notes") or "").lower()

        # Multi-item shape fallback (planner may decide to emit items)
        if not name and isinstance(params.get("items"), list) and params["items"]:
            first = params["items"][0] or {}
            name = (first.get("product_name") or "").lower()
            notes = (first.get("notes") or "").lower()

        flavor_in_notes = "mora" in notes
        flavor_in_name = "mora" in name
        assert flavor_in_notes or flavor_in_name, (
            f"'mora' must appear in either notes or product_name, "
            f"but got name={name!r}, notes={notes!r}"
        )
        # Sanity: the target product is some kind of jugo en agua, not
        # the hot-drink Hervido Mora
        assert "hervido" not in name, f"wrong product selected: {name!r}"

    def test_jugo_de_mango_en_leche_reply_preserves_mango_as_notes(self):
        """Parallel case with a different flavor / different generic row."""
        result = _classify_with_pending(
            message="el de mango en leche",
            pending_options=self.JUGOS_OPTIONS,
            requested_name="jugo",
        )
        assert result["intent"] == "ADD_TO_CART"
        params = result.get("params") or {}
        name = (params.get("product_name") or "").lower()
        notes = (params.get("notes") or "").lower()
        assert "jugos en leche" in name, f"expected 'Jugos en leche', got '{name}'"
        assert "mango" in notes, f"expected flavor 'mango' in notes, got notes='{notes}'"

    def test_plain_option_reply_without_qualifier_has_no_notes_needed(self):
        """
        Guard: when the user's reply is JUST the option choice with no
        extra qualifier words ("el de agua"), the planner shouldn't
        hallucinate notes. Empty or absent notes both acceptable.
        """
        result = _classify_with_pending(
            message="el de agua",
            pending_options=self.JUGOS_OPTIONS,
            requested_name="jugo",
        )
        assert result["intent"] == "ADD_TO_CART"
        params = result.get("params") or {}
        name = (params.get("product_name") or "").lower()
        notes = (params.get("notes") or "").strip().lower()
        assert "jugos en agua" in name
        # Notes should be empty (no flavor was specified). Tolerate both
        # absent key and empty string.
        assert notes in ("", ""), f"unexpected notes: {notes!r}"


class TestDisambiguationReplyDoesNotLeakBotQuestionWords:
    """
    Regression for the production bug on 2026-04-27 (Biela / 3177000722):
    bot asked "¿prefieres Hervido Maracuyá o Hervido Mora?", user replied
    "maracuya", and the planner attached notes='prefiero' — pulled from
    the bot's own question word, not from the user's reply.

    `notes` MUST only be populated from words in the CURRENT user message.
    Words like 'prefieres' / 'prefiero' / 'quieres' / 'gustaría' that
    only appear in the bot's question must never leak through.
    """

    HERVIDO_OPTIONS = [
        {"name": "Hervido Maracuyá", "price": 9500},
        {"name": "Hervido Mora", "price": 9500},
    ]

    @pytest.mark.parametrize(
        "user_reply, expected_name_substr",
        [
            ("maracuya", "hervido maracuyá"),
            ("Maracuya", "hervido maracuyá"),
            ("el de maracuyá", "hervido maracuyá"),
            ("mora", "hervido mora"),
            ("el de mora", "hervido mora"),
        ],
    )
    def test_bare_choice_does_not_leak_prefiero_into_notes(
        self, user_reply, expected_name_substr,
    ):
        result = _classify_with_pending(
            message=user_reply,
            pending_options=self.HERVIDO_OPTIONS,
            requested_name="hervido",
        )
        assert result["intent"] == "ADD_TO_CART"
        params = result.get("params") or {}
        name = (params.get("product_name") or "").lower()
        notes = (params.get("notes") or "").strip().lower()

        # Multi-item shape fallback (rare for disamb-reply, but be safe).
        if not name and isinstance(params.get("items"), list) and params["items"]:
            first = params["items"][0] or {}
            name = (first.get("product_name") or "").lower()
            notes = (first.get("notes") or "").strip().lower()

        assert expected_name_substr in name, (
            f"expected name to contain {expected_name_substr!r}, got {name!r}"
        )
        # The bug words: any of these means the bot's question leaked into notes.
        bot_question_artifacts = {"prefiero", "prefieres", "quieres", "gustaria", "gustaría"}
        leaked = bot_question_artifacts.intersection(set(notes.split()))
        assert not leaked, (
            f"bot-question word leaked into notes: {leaked} (full notes={notes!r})"
        )
        # And the broader rule: bare-name reply → no notes.
        assert notes == "", f"expected empty notes for bare choice, got {notes!r}"


# ---------------------------------------------------------------------------
# Bug 6 — VIEW_CART routing on "qué tengo en mi pedido" variants
# ---------------------------------------------------------------------------


class TestViewCartRouting:
    """
    The Biela +573177000722 transcript: user typed "Que tengo en mi
    pedido?" and got the full menu back instead of the cart contents.
    The planner was classifying this as LIST_PRODUCTS / GET_MENU_CATEGORIES
    because VIEW_CART had no keyword rules in the system prompt.
    """

    def test_que_tengo_en_mi_pedido_routes_to_view_cart(self):
        result = _classify_intent(
            "Que tengo en mi pedido?",
            order_state="ORDERING",
            cart_summary="1x BARRACUDA. Subtotal: $28.000",
        )
        assert result["intent"] == "VIEW_CART"

    def test_como_va_mi_pedido_routes_to_view_cart(self):
        result = _classify_intent(
            "cómo va mi pedido",
            order_state="ORDERING",
            cart_summary="1x BARRACUDA. Subtotal: $28.000",
        )
        assert result["intent"] == "VIEW_CART"

    def test_muestrame_mi_pedido_routes_to_view_cart(self):
        result = _classify_intent(
            "muéstrame mi pedido",
            order_state="ORDERING",
            cart_summary="1x BARRACUDA. Subtotal: $28.000",
        )
        assert result["intent"] == "VIEW_CART"


# ---------------------------------------------------------------------------
# Bug 7 — notes-addition UPDATE_CART_ITEM for "el X también es de Y"
# ---------------------------------------------------------------------------


class TestNotesAdditionUpdateCartItem:
    """
    When the user is describing an EXISTING cart item to add a flavor
    / note ("el jugo en agua también es de mora"), the planner must
    route to UPDATE_CART_ITEM with notes — NOT a fresh SEARCH_PRODUCTS
    or ADD_TO_CART. Regression from Biela +573177000722 where the user
    said "El jugo en agua también es de mora" and got disambiguation
    instead of a notes update.
    """

    def test_el_jugo_tambien_es_de_mora_routes_to_update_cart_item(self):
        result = _classify_intent(
            "el jugo en agua también es de mora",
            order_state="ORDERING",
            cart_summary="1x BARRACUDA; 1x Jugos en agua. Subtotal: $35.500",
        )
        assert result["intent"] == "UPDATE_CART_ITEM"
        params = result.get("params") or {}
        name = (params.get("product_name") or "").lower()
        notes = (params.get("notes") or "").lower()
        # The planner should target the jugo in the cart, not the burger
        assert "jugo" in name, f"expected target 'jugo', got {name!r}"
        # And carry 'mora' across as notes
        assert "mora" in notes, f"expected notes to include 'mora', got {notes!r}"

    def test_agregale_mango_routes_to_update_cart_item_with_notes(self):
        """Imperative variant."""
        result = _classify_intent(
            "al jugo en leche agrégale mango",
            order_state="ORDERING",
            cart_summary="1x Jugos en leche. Subtotal: $7.500",
        )
        assert result["intent"] == "UPDATE_CART_ITEM"
        params = result.get("params") or {}
        notes = (params.get("notes") or "").lower()
        assert "mango" in notes, f"expected 'mango' in notes, got {notes!r}"


# ---------------------------------------------------------------------------
# Negative CONFIRM — "que no" after "¿algo más?"
# ---------------------------------------------------------------------------


class TestNegativeConfirmRouting:
    """
    Regression for f2764bf: when the bot's last message asked "¿algo más?"
    or "¿procedemos?" and the user responds with a negative ("que no",
    "nada más", "eso es todo"), the planner must route to CONFIRM, not
    CHAT. The backend resolves CONFIRM by state (ORDERING → checkout,
    READY_TO_PLACE → place order).
    """

    def test_que_no_after_algo_mas_routes_to_confirm(self):
        result = _classify_intent(
            "que no",
            order_state="ORDERING",
            cart_summary="1x BARRACUDA. Subtotal: $28.000",
        )
        assert result["intent"] == "CONFIRM"

    def test_nada_mas_after_algo_mas_routes_to_confirm(self):
        result = _classify_intent(
            "nada más",
            order_state="ORDERING",
            cart_summary="1x BARRACUDA. Subtotal: $28.000",
        )
        assert result["intent"] == "CONFIRM"

    def test_eso_es_todo_after_algo_mas_routes_to_confirm(self):
        result = _classify_intent(
            "eso es todo",
            order_state="ORDERING",
            cart_summary="1x BARRACUDA; 1x Club Colombia. Subtotal: $35.500",
        )
        assert result["intent"] == "CONFIRM"
