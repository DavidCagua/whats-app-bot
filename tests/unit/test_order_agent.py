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

        for name in ["AL PASTOR", "AMERICANA", "ARRABBIATA", "BARRACUDA",
                     "BETA", "BIELA", "BIMOTA", "HONEY BURGER"]:
            assert name in inp, f"All product names must be passed to the LLM, missing: {name}"
        assert "cebolla caramelizada" in inp, \
            "Product descriptions must be passed to the LLM input"

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


class TestPlannerPromptRules:
    """Verify planner prompt contains the rules that route intents correctly."""

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
