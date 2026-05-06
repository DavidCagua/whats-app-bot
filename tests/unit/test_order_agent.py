"""
Unit tests for OrderAgent response-prompt building.
Tests the branch logic in _build_response_prompt without any LLM or DB calls.
"""

from unittest.mock import patch

import pytest

from app.agents.order_agent import OrderAgent, PLANNER_SYSTEM_TEMPLATE
from app.orchestration.order_flow import (
    CART_ACTION_ADDED,
    RESULT_KIND_CART_CHANGE,
    RESULT_KIND_ORDER_PLACED,
    RESULT_KIND_PRODUCTS_LIST,
)


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


class TestCartChangeResponsePromptDoesNotCrash:
    """Regression: response prompt builders must not reference variables that
    only exist in `execute()`. Crashes here surface as `❌ Error...` on the
    customer's WhatsApp instead of the intended response.

    All preview_cart calls are stubbed because we don't want to hit the DB
    from a unit test — the test is purely about the prompt's local-variable
    bindings and template rendering.
    """

    def _stub_preview(self, items_count=2):
        return {
            "display_groups": [
                {
                    "kind": "promo_bundle",
                    "promotion_name": "2 Honey Burger con papas",
                    "promo_price": 30000.0,
                    "discount_applied": 26000.0,
                    "components": [
                        {"name": "HONEY BURGER", "quantity": 2},
                        {"name": "Papas", "quantity": 1},
                    ],
                },
                {
                    "kind": "item",
                    "name": "Coca-Cola",
                    "quantity": 2,
                    "unit_price": 5500.0,
                    "line_total": 11000.0,
                    "notes": None,
                },
            ],
            "subtotal_before_promos": 67000.0,
            "promo_discount_total": 26000.0,
            "subtotal": 41000.0,
            "applications": [
                {
                    "promotion_id": "p1",
                    "promotion_name": "2 Honey Burger con papas",
                    "pricing_mode": "fixed_price",
                    "discount_applied": 26000.0,
                    "promo_group_id": "g1",
                }
            ],
        }

    def test_cart_change_renders_without_crashing_when_promo_just_added(self):
        """The exact crash case: ADD_PROMO_TO_CART → cart_change → response
        prompt builder. Used to NameError on `business_id` because the
        variable only existed in execute()'s scope."""
        agent = OrderAgent()
        exec_result = {
            "cart_change": {
                "action": CART_ACTION_ADDED,
                "added": [
                    {
                        "product_id": "honey",
                        "name": "HONEY BURGER",
                        "quantity": 2,
                        "price": 28000,
                        "promotion_id": "p1",
                        "promo_group_id": "g1",
                    },
                ],
                "removed": [],
                "updated": [],
                "cart_after": [
                    {
                        "product_id": "honey",
                        "name": "HONEY BURGER",
                        "quantity": 2,
                        "price": 28000,
                        "promotion_id": "p1",
                        "promo_group_id": "g1",
                    },
                    {
                        "product_id": "papas",
                        "name": "Papas",
                        "quantity": 1,
                        "price": 8000,
                        "promotion_id": "p1",
                        "promo_group_id": "g1",
                    },
                ],
                "total_after": 64000,
            },
        }
        business_context = {"business_id": "biz-uuid", "business": {"name": "Biela"}}

        with patch(
            "app.agents.order_agent.promotion_service.preview_cart",
            return_value=self._stub_preview(),
        ):
            system, inp = agent._build_response_prompt(
                result_kind=RESULT_KIND_CART_CHANGE,
                exec_result=exec_result,
                message_body="dame una promo de honey",
                business_context=business_context,
                cart_summary_after="(unused on this branch)",
            )

        # Bundle line should appear with the promo name + price, not the
        # base-priced components as separate items.
        assert "PROMO" in inp
        assert "2 Honey Burger con papas" in inp
        assert "$30.000" in inp
        # Subtotal label must clarify it already reflects the promo.
        assert "ya con promo" in inp
        # System prompt must instruct the LLM not to redecompose the bundle
        # or recompute totals.
        assert "PROMO" in system
        assert "promo" in system.lower()

    def test_cart_change_renders_when_business_context_is_none(self):
        """Same render path with a None business_context — must still not raise."""
        agent = OrderAgent()
        exec_result = {
            "cart_change": {
                "action": CART_ACTION_ADDED,
                "added": [{"product_id": "x", "name": "AGUA", "quantity": 1, "price": 3000}],
                "removed": [],
                "updated": [],
                "cart_after": [
                    {"product_id": "x", "name": "AGUA", "quantity": 1, "price": 3000}
                ],
                "total_after": 3000,
            },
        }
        with patch(
            "app.agents.order_agent.promotion_service.preview_cart",
            return_value={
                "display_groups": [
                    {
                        "kind": "item",
                        "name": "AGUA",
                        "quantity": 1,
                        "unit_price": 3000.0,
                        "line_total": 3000.0,
                        "notes": None,
                    }
                ],
                "subtotal_before_promos": 3000.0,
                "promo_discount_total": 0.0,
                "subtotal": 3000.0,
                "applications": [],
            },
        ):
            system, inp = agent._build_response_prompt(
                result_kind=RESULT_KIND_CART_CHANGE,
                exec_result=exec_result,
                message_body="agrega una agua",
                business_context=None,
                cart_summary_after="(unused)",
            )
        assert "AGUA" in inp

    def test_order_placed_renders_without_crashing(self):
        """Same regression on the order_placed branch. The previous template
        also referenced `business_id` from outside its scope."""
        agent = OrderAgent()
        exec_result = {
            "order_placed": {
                "order_id_display": "ABC12345",
                "items": [
                    {
                        "product_id": "honey",
                        "name": "HONEY BURGER",
                        "quantity": 2,
                        "price": 28000,
                        "promotion_id": "p1",
                        "promo_group_id": "g1",
                    },
                    {
                        "product_id": "papas",
                        "name": "Papas",
                        "quantity": 1,
                        "price": 8000,
                        "promotion_id": "p1",
                        "promo_group_id": "g1",
                    },
                ],
                "subtotal": 30000,
                "promo_discount": 26000,
                "applied_promos": ["2 Honey Burger con papas"],
                "delivery_fee": 5000,
                "total": 35000,
            },
        }
        business_context = {"business_id": "biz-uuid", "business": {"name": "Biela"}}

        with patch(
            "app.agents.order_agent.promotion_service.preview_cart",
            return_value=self._stub_preview(),
        ):
            system, inp = agent._build_response_prompt(
                result_kind=RESULT_KIND_ORDER_PLACED,
                exec_result=exec_result,
                message_body="confirmar",
                business_context=business_context,
                cart_summary_after="(unused)",
            )
        # Receipt must reframe the discount as savings, not a math correction.
        assert "Ahorro con promo" in system
        assert "ABC12345" in inp


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

    def test_planner_prompt_routes_product_price_question_to_get_product(self):
        """
        Regression: "una picada que valor?" was being classified as CHAT
        because no planner rule covered price-of-product questions. Once
        routing was fixed, the order agent's planner still missed it. The
        rule must extend GET_PRODUCT to cover price/value/cost phrasings,
        with explicit guidance that ADD_TO_CART is NOT the right intent
        when the customer is asking for the price (they're deciding,
        not ordering yet).
        """
        prompt = PLANNER_SYSTEM_TEMPLATE
        lower = prompt.lower()
        # The rule itself.
        assert "precio" in lower, \
            "Planner prompt must mention price/precio under GET_PRODUCT"
        # Concrete examples the LLM can pattern-match against.
        for example in (
            "cuánto vale la x",
            "qué valor tiene la x",
            "una x qué valor",
            "qué precio tiene la x",
        ):
            assert example in lower, f"Planner prompt missing example: {example!r}"
        # Critical guardrail: don't accidentally ADD_TO_CART when asking
        # for a price.
        assert "una picada que valor" in lower, \
            "Planner prompt must use 'una picada que valor?' as an example"
        assert "no add_to_cart" in lower, \
            "Planner prompt must explicitly forbid ADD_TO_CART for price questions"

    @pytest.mark.parametrize("status,expected_phrase_substr", [
        ("pending", "cocina"),
        ("confirmed", "disfrutes"),
        ("out_for_delivery", "camino"),
        ("completed", "disfrutado"),
        ("cancelled", "vuelves"),
    ])
    def test_chat_response_prompt_uses_status_aware_closing(self, status, expected_phrase_substr):
        """
        Response generator's CHAT branch must produce a status-aware
        closing instruction when ``latest_order_status`` is set, and
        must NOT include the generic "¿qué te gustaría ordenar?"
        invitation.
        """
        from app.agents.order_agent import OrderAgent
        agent = OrderAgent()
        system, _ = agent._build_response_prompt(
            result_kind="chat",
            exec_result={},
            message_body="si gracias",
            business_context=None,
            cart_summary_after="Pedido vacío.",
            latest_order_status=status,
        )
        # Status-aware section must be present.
        assert "DESPEDIDA POST-PEDIDO" in system, (
            f"system prompt must trigger the post-order despedida branch for status={status!r}"
        )
        # The "qué te gustaría ordenar" phrase IS in the system, but only
        # as a NEGATIVE example ("NUNCA digas..."). Assert the negation
        # framing is present so the LLM is told NOT to use it.
        lower = system.lower()
        assert "nunca" in lower and "te gustaría ordenar" in lower
        # Branch-specific phrase guides the LLM toward the right tone.
        assert expected_phrase_substr.lower() in lower

    def test_chat_response_prompt_no_latest_order_uses_default(self):
        from app.agents.order_agent import OrderAgent
        agent = OrderAgent()
        system, _ = agent._build_response_prompt(
            result_kind="chat",
            exec_result={},
            message_body="hola",
            business_context=None,
            cart_summary_after="Pedido vacío.",
            latest_order_status=None,
        )
        # No status → fall back to the generic CHAT instructions.
        assert "DESPEDIDA POST-PEDIDO" not in system

    def test_confirm_rule_is_contextual_not_keyword_only(self):
        """
        Regression: 2026-05-05 (Biela / 3147139789) — user wrote "porfsvor"
        (typo for "por favor") after the bot asked "¿procedemos?".  The
        old CONFIRM rule was a keyword whitelist; "por favor" wasn't on it,
        so even when the message reached the order planner it would have
        missed.

        The new rule is contextual: anchor on the bot's prior continuation
        question (visible via Historial reciente / 10-msg uniform window)
        and treat ANY brief affirmative/courtesy/acceptance reply as
        CONFIRM. The keyword list is illustrative, not exhaustive.
        """
        prompt = PLANNER_SYSTEM_TEMPLATE
        lower = prompt.lower()
        # Rule heading + contextual framing.
        assert "confirmación (regla principal" in lower
        assert "historial reciente" in lower
        # Must enumerate what counts as a continuation question (the
        # anchor the LLM should look for).
        for cue in ("¿procedemos?", "¿algo más?"):
            assert cue.lower() in lower
        # Politeness affirmatives explicitly covered (the production miss).
        for word in ("por favor", "porfa", "please"):
            assert word in lower
        # The "ilustrativas, NO exhaustivas" framing — gives the LLM
        # permission to generalize to typos like "porfsvor" or
        # regional variants we didn't enumerate.
        assert "ilustrativas" in lower
        assert "no exhaustivas" in lower or "no exhaustivos" in lower

    def test_planner_prompt_routes_polite_close_after_recent_order_to_chat(self):
        """
        Regression: 2026-05-04 (Biela / 3177000722). User said "si gracias"
        right after PLACE_ORDER. Planner classified as CONFIRM, response
        template invited a new order ("¿qué te gustaría ordenar hoy?").

        And worse — same pattern on 2026-05-04 (Biela / 3108069647) caused
        the CS planner to hallucinate CANCEL_ORDER and delete the order.

        The planner prompt must classify polite-close turns as CHAT when
        the cart is empty AND there's a recent placed order. NEVER as
        CONFIRM (nothing to confirm, the order is already placed).
        """
        prompt = PLANNER_SYSTEM_TEMPLATE
        lower = prompt.lower()
        # The rule heading.
        assert "despedida / agradecimiento" in lower or "despedida" in lower, (
            "Planner prompt must declare a despedida-after-recent-order rule"
        )
        # The signal it depends on (rendered by turn_context).
        assert "último pedido (estado)" in lower or "ultimo pedido (estado)" in lower
        # The classification target.
        assert "intent\": \"chat\"" in lower
        # The example phrases the LLM can pattern-match against.
        for example in ("si gracias", "gracias", "ok gracias", "listo gracias"):
            assert example in lower, f"Planner prompt missing example: {example!r}"
        # Must explicitly forbid CONFIRM in this scenario.
        assert "nunca uses confirm" in lower or "no uses confirm" in lower
        # Must explicitly carve out REMOVE_FROM_CART and ABANDON_CART
        # so they keep working.
        assert "remove_from_cart" in lower
        assert "abandon_cart" in lower

    def test_planner_prompt_has_latest_order_block_placeholder(self):
        """
        The PLANNER_SYSTEM_TEMPLATE must accept a {latest_order_block}
        placeholder; older callers that don't pass one should pass an
        empty string. We assert the placeholder is in the raw template.
        """
        from app.agents.order_agent import PLANNER_SYSTEM_TEMPLATE as T
        assert "{latest_order_block}" in T

    def test_planner_prompt_honors_recognized_product_hint(self):
        """
        Regression: 2026-05-06 (Biela / 3147554464). User said
        "Tienes la a la Vuelta?" right after the bot listed
        "HONEY BURGER, MEXICAN BURGER, AL PASTOR, AMERICANA, ARRABBIATA"
        (LA VUELTA wasn't in the listed slice). The planner's
        RESOLUCIÓN DE NOMBRES ABREVIADOS rule kicked in and emitted
        ADD_TO_CART {product_name: "HONEY BURGER", notes: "a la vuelta"}
        because the LLM didn't recognize "la Vuelta" as a separate
        real product.

        After the router's multi-word product-name short-circuit,
        the turn context now carries
        ``Producto reconocido en el mensaje: LA VUELTA``. The planner
        must have a max-priority rule that uses that hint and
        explicitly OVERRIDES the abbreviated-name rule.
        """
        prompt = PLANNER_SYSTEM_TEMPLATE
        lower = prompt.lower()
        # The new max-priority rule heading.
        assert "producto reconocido" in lower
        # Must reference the exact context line emitted by
        # render_for_prompt so the LLM can pattern-match against it.
        assert "producto reconocido en el mensaje:" in lower
        # Must explicitly forbid redirecting to a listed option.
        assert "nunca lo reemplaces" in lower
        # Must explicitly carve out the abbreviated-name rule —
        # state that it does NOT apply when the hint is present.
        assert "resolución de nombres abreviados" in lower
        # Must scope notes correctly so the product name doesn't
        # end up in notes (the production failure mode).
        assert "nunca pongas el nombre del producto en `notes`" in lower or \
               "nunca pongas el nombre del producto en notes" in lower

    def test_planner_prompt_does_not_split_default_side_into_separate_item(self):
        """
        Regression: production observation 2026-05-04 (Biela / 3145798093)
        — "Quiero una barracuda con papas" was decomposed into two cart
        items (BARRACUDA + papas). The executor then resolved 'papas'
        against the catalog, hit 5 ambiguous matches (SALCHIPAPA, BIELA
        FRIES, CHEESE FRIES, SPECIAL FRIES, PAPAS PERGRETTI), added the
        BARRACUDA, and asked the user to disambiguate. But every burger
        at Biela includes fries by default — "con papas" was a redundant
        confirmation, not a second line.

        The planner prompt must rule that when a default-included
        accompaniment is mentioned with "con", it does NOT become a
        separate cart item.
        """
        prompt = PLANNER_SYSTEM_TEMPLATE
        lower = prompt.lower()
        # The rule heading.
        assert "acompañamientos incluidos por default" in lower, (
            "Planner prompt must call out default-included accompaniments "
            "as a non-decomposable case"
        )
        # The exact regression example.
        assert "una barracuda con papas" in lower, (
            "Planner prompt must use 'una barracuda con papas' as the "
            "canonical example of the no-split rule"
        )
        # Must reference the business-rules section so the planner
        # knows where the inclusion fact comes from.
        assert "reglas y contexto del negocio" in lower
        # Must enumerate the trigger words for the affirmative form.
        for word in ("con", "papas"):
            assert word in lower
        # Must explicitly call out the negation form (which uses notes).
        assert "sin papas" in lower, (
            "Planner prompt must show that 'sin papas' goes in notes, "
            "not as an item"
        )
        # Must enumerate the explicit exceptions (so the rule doesn't
        # over-apply).
        assert "biela fries" in lower or "salchipapa" in lower, (
            "Planner prompt must show explicit-name catalog requests "
            "DO get split into separate items"
        )
        assert "extras" in lower or "aparte" in lower, (
            "Planner prompt must show extra/additional sides DO get split"
        )
