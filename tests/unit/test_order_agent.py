"""
Unit tests for OrderAgent response-prompt building.
Tests the branch logic in _build_response_prompt without any LLM or DB calls.
"""

from app.agents.order_agent import OrderAgent, PLANNER_SYSTEM_TEMPLATE
from app.orchestration.order_flow import RESULT_KIND_PRODUCTS_LIST


class TestProductsListResponsePrompt:
    """Verify the prompt instructions for RESULT_KIND_PRODUCTS_LIST."""

    def _exec_result_with_descriptions(self):
        return {
            "products": [
                {"name": "AL PASTOR", "price": 27000, "description": "Pan artesanal, 150gr carne, mozzarella, cerdo al pastor con piña, cebolla crispy, chipotle, papas."},
                {"name": "AMERICANA", "price": 22000, "description": "Pan, carne, queso cheddar, tocineta, lechuga, tomate, papas."},
                {"name": "ARRABBIATA", "price": 27000, "description": "Pan, carne, mozzarella, salsa arrabbiata picante, rúgula, papas."},
                {"name": "BARRACUDA", "price": 28000, "description": "Doble carne, cheddar, tocineta, cebolla caramelizada, papas."},
                {"name": "BETA", "price": 28000, "description": "Carne, queso azul, champiñones salteados, cebolla crispy, papas."},
                {"name": "BIELA", "price": 28000, "description": "Carne, tocineta, huevo, cheddar, chipotle, papas."},
                {"name": "BIMOTA", "price": 27000, "description": "Carne, mozzarella, pesto, rúgula, tomate seco, papas."},
                {"name": "HONEY BURGER", "price": 28000, "description": "Carne, cheddar, tocineta, miel mostaza, cebolla caramelizada, papas."},
            ],
            "category_label": "HAMBURGUESAS",
            "query_label": None,
        }

    def test_products_list_prompt_requires_descriptions_when_present(self):
        """
        Regression: when the category list has 8 burgers (>6) and each has a description,
        the response system prompt must instruct the LLM to always include descriptions,
        not summarize them away. Previously the rule said "si son muchos (>6), puedes
        agrupar o resumir" which caused the bot to drop descriptions entirely.

        With the 5-item cap, only the first 5 products are sent to the LLM
        to keep WhatsApp messages readable. The remaining count is shown so
        the LLM can tell the user there are more options.
        """
        agent = OrderAgent()
        system, inp = agent._build_response_prompt(
            result_kind=RESULT_KIND_PRODUCTS_LIST,
            exec_result=self._exec_result_with_descriptions(),
            message_body="qué hamburguesas tienes?",
            business_context=None,
            cart_summary_after="Pedido vacío.",
        )

        assert "INCLÚYELA SIEMPRE" in system, \
            "Prompt must require always including descriptions when present"
        assert "resumir" not in system, \
            "Prompt must not allow summarizing descriptions away"

        # First 5 products shown with descriptions
        for name in ["AL PASTOR", "AMERICANA", "ARRABBIATA", "BARRACUDA", "BETA"]:
            assert name in inp, f"Top-5 product must be in LLM input, missing: {name}"
        assert "cebolla caramelizada" in inp, \
            "Product descriptions must be passed to the LLM input"

        # Remaining count communicated
        assert "3 más" in inp, \
            "LLM input must mention how many products are not shown"
        assert "5 de 8" in inp, \
            "LLM input must show X of Y products"

    def test_products_list_prompt_without_descriptions_is_name_and_price_only(self):
        """If products have no descriptions, the prompt still renders and just lists name+price."""
        agent = OrderAgent()
        exec_result = {
            "products": [
                {"name": "COCA COLA", "price": 5000, "description": None},
                {"name": "AGUA", "price": 3000, "description": None},
            ],
            "category_label": "BEBIDAS",
            "query_label": None,
        }
        system, inp = agent._build_response_prompt(
            result_kind=RESULT_KIND_PRODUCTS_LIST,
            exec_result=exec_result,
            message_body="qué bebidas tienes?",
            business_context=None,
            cart_summary_after="Pedido vacío.",
        )
        assert "COCA COLA" in inp
        assert "AGUA" in inp
        assert "INCLÚYELA SIEMPRE" in system


class TestPhoneFormatFromWaId:
    """Unit tests for wa_id → phone normalization used by <SENDER> substitution."""

    def test_meta_style_digits_only(self):
        from app.orchestration.order_flow import _format_phone_from_wa_id
        assert _format_phone_from_wa_id("573001234567") == "+573001234567"

    def test_twilio_style_with_plus(self):
        from app.orchestration.order_flow import _format_phone_from_wa_id
        assert _format_phone_from_wa_id("+573001234567") == "+573001234567"

    def test_twilio_prefix_stripped(self):
        from app.orchestration.order_flow import _format_phone_from_wa_id
        assert _format_phone_from_wa_id("whatsapp:+573001234567") == "+573001234567"

    def test_empty(self):
        from app.orchestration.order_flow import _format_phone_from_wa_id
        assert _format_phone_from_wa_id("") == ""
        assert _format_phone_from_wa_id(None) == ""


class TestCategoryNormalization:
    """Verify CATEGORY_MAP correctly normalizes Spanish category terms."""

    def test_hamburguesas_de_pollo_full_phrase(self):
        from app.database.product_order_service import normalize_category
        assert normalize_category("hamburguesas de pollo") == "HAMBURGUESAS DE POLLO"

    def test_hamburguesa_de_pollo_singular(self):
        from app.database.product_order_service import normalize_category
        assert normalize_category("hamburguesa de pollo") == "HAMBURGUESAS DE POLLO"

    def test_hamburguesas_maps_to_hamburguesas(self):
        from app.database.product_order_service import normalize_category
        assert normalize_category("hamburguesas") == "HAMBURGUESAS"

    def test_pollo_maps_to_chicken(self):
        from app.database.product_order_service import normalize_category
        assert normalize_category("pollo") == "HAMBURGUESAS DE POLLO"

    def test_perros_calientes_full_phrase(self):
        from app.database.product_order_service import normalize_category
        assert normalize_category("perros calientes") == "PERROS CALIENTES"

    def test_hot_dog_legacy(self):
        from app.database.product_order_service import normalize_category
        assert normalize_category("hot dogs") == "PERROS CALIENTES"

    def test_parrilla(self):
        from app.database.product_order_service import normalize_category
        assert normalize_category("parrilla") == "PARRILLA"

    def test_costillas_maps_to_parrilla(self):
        from app.database.product_order_service import normalize_category
        assert normalize_category("costillas") == "PARRILLA"

    def test_postres(self):
        from app.database.product_order_service import normalize_category
        assert normalize_category("postres") == "POSTRES"

    def test_salchipapas_unchanged(self):
        from app.database.product_order_service import normalize_category
        assert normalize_category("salchipapas") == "SALCHIPAPAS"

    def test_full_phrase_wins_over_word_by_word(self):
        """'hamburguesas de pollo' must match full phrase → HAMBURGUESAS DE POLLO,
        not word-by-word → HAMBURGUESAS (from 'hamburguesas')."""
        from app.database.product_order_service import normalize_category
        result = normalize_category("hamburguesas de pollo")
        assert result == "HAMBURGUESAS DE POLLO", (
            f"Full phrase must win over word-by-word fallback, got {result!r}"
        )


class TestPlannerPromptRules:
    """Verify planner prompt contains the rules that route intents correctly."""

    def test_planner_prompt_has_implicit_drinks_rule(self):
        """
        Regression: "qué hay para tomar?" should go straight to LIST_PRODUCTS with
        category=bebidas, not GET_MENU_CATEGORIES (which makes the bot ask "¿quieres
        ver las bebidas?" instead of just showing them).
        """
        prompt = PLANNER_SYSTEM_TEMPLATE
        assert "para tomar" in prompt, \
            "Planner prompt must describe the implicit 'para tomar' → bebidas case"
        assert "LIST_PRODUCTS" in prompt

    def test_planner_prompt_has_sender_phone_rule(self):
        """
        Regression: when the user says "este número" / "este mismo" while the bot is
        collecting delivery info, the planner must emit SUBMIT_DELIVERY_INFO with
        phone="<SENDER>" (a literal marker) so the backend can substitute the
        actual wa_id. Previously the bot kept asking for the phone because the
        planner emitted params={} with no phone at all.
        """
        prompt = PLANNER_SYSTEM_TEMPLATE
        assert "<SENDER>" in prompt, "Planner prompt must describe the <SENDER> marker"
        assert "este número" in prompt or "este mismo" in prompt
        assert "SUBMIT_DELIVERY_INFO" in prompt

    def test_planner_prompt_has_plural_details_rule(self):
        """
        Regression: "qué tiene cada una de esas hamburguesas?" must be classified as
        LIST_PRODUCTS (showing all with descriptions), NOT GET_PRODUCT (which would
        pick only the first match). The planner prompt must explicitly mention the
        plural/collective case.
        """
        prompt = PLANNER_SYSTEM_TEMPLATE
        assert "cada una" in prompt, \
            "Planner prompt must describe the 'qué tiene cada una' plural case"
        assert "LIST_PRODUCTS" in prompt
        lower = prompt.lower()
        idx_plural = lower.find("cada una")
        idx_list = lower.find("list_products", idx_plural - 200 if idx_plural > 200 else 0)
        assert 0 <= idx_list, "LIST_PRODUCTS must be referenced near the 'cada una' rule"

    def test_planner_prompt_has_category_attribute_exception(self):
        """
        Regression: "tienes hamburguesas picantes?" must route to
        SEARCH_PRODUCTS (attribute search), NOT LIST_PRODUCTS (category).
        The planner prompt must include the exception for category + adjective.
        """
        prompt = PLANNER_SYSTEM_TEMPLATE
        lower = prompt.lower()
        assert "adjetivo" in lower or "modificador" in lower, \
            "Planner prompt must describe the category+attribute exception"
        assert "search_products" in lower
        assert "hamburguesas picantes" in lower, \
            "Planner prompt must use 'hamburguesas picantes' as an example"
