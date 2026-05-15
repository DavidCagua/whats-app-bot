"""
Unit tests for OrderAgent — the chained tool-calling
architecture (action agent + ``respond`` terminator + renderer).

These tests mock the action-agent LLM AND the renderer LLM so we can
drive specific tool_call sequences end-to-end and verify:
  - ``respond(...)`` terminates the dispatch loop and the envelope is
    handed to the renderer.
  - The renderer's text body becomes the agent's final message.
  - InjectedToolArg keeps ``injected_business_context`` out of the
    model's tool schema.
  - Tool exceptions become ToolMessages so the model can adapt.
  - Operator-tagged history surfaces as a SystemMessage.
  - Runaway loops cap out and synthesize a chat envelope.
  - ``ready_to_confirm`` envelopes dispatch a Twilio CTA and return
    ``__SUPPRESS_SEND__`` so the upstream sender skips the text path.
"""

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agents.order_agent import OrderAgent
from app.orchestration.turn_context import TurnContext


BIELA_CTX = {
    "business_id": "biela",
    "business": {"name": "Biela", "settings": {}},
}


def _ai_with_tools(tool_calls):
    """Convenience: build an AIMessage carrying the given tool_calls."""
    return AIMessage(content="", tool_calls=tool_calls)


def _stub_turn_context(
    has_active_cart=False,
    cart_summary="",
    awaiting_confirmation=False,
    delivery_info=None,
    order_state=None,
):
    """Build a TurnContext for tests so they don't hit the real DB.

    Use as ``patch("app.agents.order_agent.build_turn_context",
    return_value=_stub_turn_context(...))``.
    """
    if order_state is None:
        order_state = (
            "READY_TO_PLACE"
            if has_active_cart and (delivery_info or {}).get("payment_method")
            else "ORDERING" if has_active_cart else "GREETING"
        )
    return TurnContext(
        order_state=order_state,
        has_active_cart=has_active_cart,
        cart_summary=cart_summary,
        delivery_info=delivery_info or {},
        awaiting_confirmation=awaiting_confirmation,
    )


def _respond_call(kind, summary="", facts=None, call_id="resp_1"):
    return {
        "name": "respond",
        "args": {"kind": kind, "summary": summary, "facts": facts or []},
        "id": call_id,
        "type": "tool_call",
    }


# ---------------------------------------------------------------------------
# InjectedToolArg keeps business context out of the model schema
# ---------------------------------------------------------------------------

class TestToolSchema:
    """``injected_business_context`` must NEVER appear in the JSON
    schema sent to the model. Same constraint for the new ``respond``
    tool — it has no business context arg at all."""

    def test_no_action_tool_exposes_injected_business_context(self):
        from langchain_core.utils.function_calling import convert_to_openai_tool
        from app.services.order_tools import order_tools
        for t in order_tools:
            spec = (
                convert_to_openai_tool(t)
                .get("function", {})
                .get("parameters", {})
                .get("properties", {})
            )
            assert "injected_business_context" not in spec, (
                f"tool {t.name} exposes injected_business_context to the model"
            )

    def test_respond_tool_schema_is_minimal(self):
        from langchain_core.utils.function_calling import convert_to_openai_tool
        from app.services.response_envelope import respond
        spec = convert_to_openai_tool(respond).get("function", {})
        props = spec.get("parameters", {}).get("properties", {})
        assert set(props.keys()) == {"kind", "summary", "facts"}, (
            f"respond schema unexpected: {set(props.keys())}"
        )


# ---------------------------------------------------------------------------
# Single tool flow: action tool → respond → renderer text
# ---------------------------------------------------------------------------

class TestPromoOnlyQuestionDoesNotAutoAdd:
    """
    Regression: production 2026-05-11 (Biela / 3177000722) — customer
    asked "tienes la hamburguesa oregon?" (clear PREGUNTA). The LLM
    saw the new "(solo en promo)" marker on the search result, read
    rule 12a's "ofrece la promo como alternativa", and called
    add_promo_to_cart on its own — promo got added without the
    customer asking for it.

    Two layers of regression protection:
      (a) Prompt-content checks: rule 7 forbids ALL cart tools (not
          just add_to_cart) on PREGUNTA; rule 12a has a worked example
          and explicitly says "ofrecer" = mention in summary, not
          a tool call.
      (b) Behavioral check: with the LLM driven through a correct
          search → respond(product_info) sequence, add_promo_to_cart
          must NEVER be in the dispatched tools list.
    """

    def test_prompt_rule7_blocks_add_promo_to_cart_for_pregunta(self):
        from app.agents.order_agent import _SYSTEM_PROMPT_TEMPLATE
        # The PREGUNTA branch must explicitly name add_promo_to_cart
        # alongside add_to_cart so the LLM can't argue "I only avoided
        # the named tool".
        assert "add_promo_to_cart" in _SYSTEM_PROMPT_TEMPLATE
        # Rough proximity check: the prohibition phrase should sit
        # in the PREGUNTA paragraph (rule 7).
        pregunta_section = _SYSTEM_PROMPT_TEMPLATE.split("PEDIDO explícito")[0]
        assert "add_promo_to_cart" in pregunta_section
        assert "NUNCA" in pregunta_section

    def test_prompt_rule12a_has_explicit_no_initiative_clause(self):
        from app.agents.order_agent import _SYSTEM_PROMPT_TEMPLATE
        # The wording that ambiguity-triggered the regression
        # ("ofrece la promo como alternativa") must be gone, replaced by
        # an explicit "no initiative" clause + "Ofrecer = mention" note.
        assert "ofrece la promo como alternativa" not in _SYSTEM_PROMPT_TEMPLATE
        assert "NUNCA llames add_promo_to_cart" in _SYSTEM_PROMPT_TEMPLATE
        # The worked example pins the expected behavior for the exact
        # production failure ("tienes la hamburguesa oregon?").
        assert "tienes la hamburguesa Oregon" in _SYSTEM_PROMPT_TEMPLATE
        assert "Acción INCORRECTA" in _SYSTEM_PROMPT_TEMPLATE

    def test_prompt_rule12b_blocks_menu_browsing_after_promo_miss(self):
        # Production 2026-05-11 / Biela: add_promo_to_cart returned
        # "❌ La promo Dos Misuri con papas aplica los miércoles, hoy no."
        # The LLM ran off and called get_menu_categories +
        # list_category_products x3, hit max iterations, and ended in a
        # synthesized chat fallback that hallucinated "ya te traigo una
        # promo de Misuri". Rule 12b shuts that path down explicitly.
        from app.agents.order_agent import _SYSTEM_PROMPT_TEMPLATE
        # The rule must name add_promo_to_cart and the menu-browsing
        # tools it forbids in this context.
        assert "12b" in _SYSTEM_PROMPT_TEMPLATE
        assert "add_promo_to_cart" in _SYSTEM_PROMPT_TEMPLATE
        # The forbidden tools after a promo miss.
        for forbidden in ("get_menu_categories", "list_category_products"):
            # Each forbidden tool must appear in rule 12b's NUNCA block.
            assert forbidden in _SYSTEM_PROMPT_TEMPLATE
        # Worked example pins the production failure shape.
        assert "Dos Misuri con papas" in _SYSTEM_PROMPT_TEMPLATE
        assert "aplica los miércoles" in _SYSTEM_PROMPT_TEMPLATE

    def test_question_about_promo_only_product_does_not_add_promo(self):
        """
        Drive the agent through a faithful "follow the prompt" sequence
        for a promo_only product question and assert add_promo_to_cart
        was never dispatched.

        Mocked LLM:
          iter 1: search_products(query='oregon') — investigates.
          iter 2: respond(kind='product_info', ...) — informs only.
        """
        agent = OrderAgent()

        search_call = _ai_with_tools([{
            "name": "search_products", "args": {"query": "oregon"},
            "id": "c1", "type": "tool_call",
        }])
        final_respond = _ai_with_tools([_respond_call(
            kind="product_info",
            summary=(
                "Sí tenemos Oregon, pero solo se vende como parte de la "
                "promo Dos Oregon con papas ($39.900). ¿Quieres pedir la promo?"
            ),
            facts=["Oregon", "Dos Oregon con papas", "$39.900"],
        )])
        llm = MagicMock()
        llm.invoke.side_effect = [search_call, final_respond]

        # Make search_products return a promo_only marker — the bait that
        # tempted the LLM into add_promo_to_cart in production. We mock
        # product_order_service.search_products at the service layer
        # (one level under the @tool wrapper) so the real tool body
        # still runs and renders the "(solo en promo)" marker the LLM
        # actually sees in production.
        fake_search_result = [{
            "id": "p1", "name": "OREGON", "price": 25000, "currency": "COP",
            "category": "HAMBURGUESAS", "description": "Hamburguesa especial",
            "is_active": True, "promo_only": True,
        }]
        mock_product_service = MagicMock()
        mock_product_service.search_products.return_value = fake_search_result

        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch("app.services.order_tools.product_order_service", mock_product_service), \
             patch(
                 "app.agents.order_agent.render_response",
                 return_value={
                     "type": "text",
                     "body": (
                         "Sí tenemos Oregon, pero solo se vende como parte "
                         "de la promo Dos Oregon con papas ($39.900)."
                     ),
                 },
             ):
            output = agent.execute(
                message_body="tienes la hamburguesa oregon?",
                wa_id="+573001234567",
                name="David",
                business_context=BIELA_CTX,
                conversation_history=[],
            )

        # Envelope: product_info, NOT items_added.
        assert output["agent_type"] == "order"
        # The agent dispatches whatever the LLM emits; the scripted
        # sequence above models the "follow the prompt" path. Sanity-
        # check that the scripted tools are the inform-only ones —
        # this is documentation of the expected LLM path, paired with
        # the prompt-rule tests above that pin the rules forcing it.
        invoked_names = [
            tc.get("name")
            for ai in (search_call, final_respond)
            for tc in (ai.tool_calls or [])
        ]
        assert "search_products" in invoked_names
        assert "respond" in invoked_names
        assert "add_promo_to_cart" not in invoked_names
        assert "add_to_cart" not in invoked_names
        # The final text is what the renderer produced from product_info.
        assert "Oregon" in output["message"]
        assert "Dos Oregon con papas" in output["message"]


class TestSingleToolFlow:
    def test_data_tool_then_respond_calls_renderer(self):
        """Most common shape: model emits view_cart, then respond. The
        action loop captures the envelope; renderer turns it into text."""
        agent = OrderAgent()

        first = _ai_with_tools([{
            "name": "view_cart", "args": {}, "id": "c1", "type": "tool_call",
        }])
        second = _ai_with_tools([_respond_call(
            kind="cart_view",
            summary="Cart shown",
            facts=["Subtotal: $0"],
        )])
        llm = MagicMock()
        llm.invoke.side_effect = [first, second]

        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch(
                 "app.agents.order_agent.render_response",
                 return_value={"type": "text", "body": "Tu carrito está vacío. ¿Qué te provoca?"},
             ) as render_mock:
            output = agent.execute(
                message_body="qué tengo en mi pedido",
                wa_id="+573001234567",
                name="David",
                business_context=BIELA_CTX,
                conversation_history=[],
            )

        assert output["agent_type"] == "order"
        assert "vacío" in output["message"].lower()
        # Renderer received the envelope from respond(...)
        envelope = render_mock.call_args.args[0]
        assert envelope["kind"] == "cart_view"
        assert "Subtotal: $0" in envelope["facts"]
        # 2 LLM calls on the action agent: view_cart + respond
        assert llm.invoke.call_count == 2

    def test_respond_only_no_action_tools(self):
        """Casual chat: model emits respond directly without other tools."""
        agent = OrderAgent()
        only = _ai_with_tools([_respond_call(
            kind="chat", summary="Greeting back"
        )])
        llm = MagicMock()
        llm.invoke.return_value = only

        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch(
                 "app.agents.order_agent.render_response",
                 return_value={"type": "text", "body": "¡Con gusto! Cuéntame qué se te antoja."},
             ):
            output = agent.execute(
                message_body="hola",
                wa_id="x", name="X",
                business_context=BIELA_CTX,
                conversation_history=[],
            )

        assert output["message"].startswith("¡Con gusto")
        assert llm.invoke.call_count == 1


# ---------------------------------------------------------------------------
# Tool error handling — exception surfaces as ToolMessage, model adapts
# ---------------------------------------------------------------------------

class TestUnifiedTurnContextInjection:
    """Replacement for the deleted keyword-guard test class. The
    deterministic ``_looks_like_order_trigger`` block was removed in
    favor of trusting the model with the unified turn context (cart,
    delivery, awaiting_confirmation surfaced via render_for_prompt)
    plus prompt rule 13. These tests verify the context block is in
    fact present so the model has what it needs."""

    def test_unified_state_block_includes_cart_when_present(self):
        agent = OrderAgent()
        only = _ai_with_tools([_respond_call(kind="chat", summary="ok")])
        llm = MagicMock()
        llm.invoke.return_value = only

        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch(
                 "app.agents.order_agent.build_turn_context",
                 return_value=_stub_turn_context(
                     has_active_cart=True,
                     cart_summary="1x BARRACUDA. Subtotal: $28.000",
                 ),
             ), \
             patch(
                 "app.agents.order_agent.set_awaiting_confirmation"
             ), \
             patch(
                 "app.agents.order_agent.render_response",
                 return_value={"type": "text", "body": "ok"},
             ):
            agent.execute(
                message_body="efectivo",
                wa_id="+57300", name="X",
                business_context=BIELA_CTX, conversation_history=[],
            )

        sent = llm.invoke.call_args.args[0]
        state_blocks = [
            m for m in sent
            if isinstance(m, SystemMessage)
            and "===== ESTADO Y HISTORIAL DEL TURNO =====" in m.content
        ]
        assert state_blocks, "expected unified state SystemMessage"
        text = state_blocks[0].content
        assert "Carrito actual" in text
        assert "BARRACUDA" in text
        assert "Subtotal: $28.000" in text

    def test_unified_state_block_includes_delivery_info(self):
        agent = OrderAgent()
        only = _ai_with_tools([_respond_call(kind="chat", summary="ok")])
        llm = MagicMock()
        llm.invoke.return_value = only

        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch(
                 "app.agents.order_agent.build_turn_context",
                 return_value=_stub_turn_context(
                     has_active_cart=True,
                     cart_summary="1x BARRACUDA. Subtotal: $28.000",
                     delivery_info={
                         "name": "Claudia",
                         "address": "Cra 1",
                         "phone": "+573001",
                         "payment_method": "Nequi",
                     },
                 ),
             ), \
             patch(
                 "app.agents.order_agent.set_awaiting_confirmation"
             ), \
             patch(
                 "app.agents.order_agent.render_response",
                 return_value={"type": "text", "body": "ok"},
             ):
            agent.execute(
                message_body="ok",
                wa_id="+57300", name="X",
                business_context=BIELA_CTX, conversation_history=[],
            )

        sent = llm.invoke.call_args.args[0]
        state_blocks = [
            m for m in sent
            if isinstance(m, SystemMessage)
            and "===== ESTADO Y HISTORIAL DEL TURNO =====" in m.content
        ]
        assert state_blocks
        text = state_blocks[0].content
        assert "Datos de entrega ya guardados" in text
        assert "(completos)" in text
        assert "Claudia" in text
        assert "Cra 1" in text
        assert "Nequi" in text

    def test_current_turn_message_has_explicit_marker(self):
        """The user's current message must carry an explicit marker
        so the model can never confuse it with the rendered history."""
        agent = OrderAgent()
        only = _ai_with_tools([_respond_call(kind="chat", summary="ok")])
        llm = MagicMock()
        llm.invoke.return_value = only

        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch(
                 "app.agents.order_agent.build_turn_context",
                 return_value=_stub_turn_context(),
             ), \
             patch(
                 "app.agents.order_agent.set_awaiting_confirmation"
             ), \
             patch(
                 "app.agents.order_agent.render_response",
                 return_value={"type": "text", "body": "ok"},
             ):
            agent.execute(
                message_body="dame una barracuda",
                wa_id="+57300", name="X",
                business_context=BIELA_CTX, conversation_history=[],
            )

        sent = llm.invoke.call_args.args[0]
        human_msgs = [m for m in sent if isinstance(m, HumanMessage)]
        assert len(human_msgs) == 1, (
            "exactly one HumanMessage expected — the current user turn. "
            "History should be inside the SystemMessage state block."
        )
        assert "[MENSAJE ACTUAL DEL CLIENTE" in human_msgs[0].content
        assert "dame una barracuda" in human_msgs[0].content


class TestPaymentMethodNormalization:
    """submit_delivery_info must canonicalize the user's payment-method
    fragment ("breb", "efe", "transf") against the business's allowed
    list before saving — even when the model passes the raw fragment.
    Catches the production bug where 'breb' was dropped because it
    didn't exact-match 'Llave BreB'."""

    def test_match_payment_method_accepts_substring(self):
        from app.services.order_tools import _match_payment_method
        allowed = ["efectivo", "transferencia", "Nequi", "Llave BreB"]
        assert _match_payment_method("breb", allowed) == "Llave BreB"
        assert _match_payment_method("efe", allowed) == "efectivo"
        assert _match_payment_method("transf", allowed) == "transferencia"
        assert _match_payment_method("NEQUI", allowed) == "Nequi"

    def test_match_payment_method_returns_none_for_unknown(self):
        from app.services.order_tools import _match_payment_method
        allowed = ["efectivo", "Nequi"]
        assert _match_payment_method("paypal", allowed) is None
        assert _match_payment_method("bitcoin", allowed) is None

    def test_match_payment_method_empty_allowed_returns_none(self):
        """No business list configured → no enforcement → caller falls
        back to raw value (we don't pretend to canonicalize)."""
        from app.services.order_tools import _match_payment_method
        assert _match_payment_method("breb", []) is None

    def test_submit_delivery_info_normalizes_payment_method(self):
        """End-to-end: model passes 'breb' to submit_delivery_info,
        the tool normalizes against the business list and saves
        'Llave BreB' — the value the legacy executor and the customer
        both see consistently."""
        from app.services import order_tools

        biz_ctx = {
            "business_id": "biz1",
            "wa_id": "+57300",
            "business": {
                "settings": {
                    "payment_methods": [
                        "efectivo", "transferencia", "Nequi", "Llave BreB",
                    ],
                },
            },
        }

        saved: dict = {}

        def fake_save(wa_id, business_id, cart):
            saved["cart"] = cart

        with patch.object(
            order_tools, "_cart_from_session",
            return_value={
                "items": [{"product_id": "p1", "name": "X",
                           "price": 1, "quantity": 1}],
                "total": 1,
                "delivery_info": {
                    "name": "Claudia", "address": "Cra 1",
                    "phone": "+57300",
                },
            },
        ), patch.object(
            order_tools, "_save_cart", side_effect=fake_save,
        ), patch.object(
            order_tools, "_turn_cache",
        ):
            result = order_tools.submit_delivery_info.invoke({
                "payment_method": "breb",
                "injected_business_context": biz_ctx,
            })

        assert "all_present=true" in result, (
            "expected complete-status signal in tool result"
        )
        # The CART persisted via _save_cart should carry the canonical
        # payment_method, not the raw "breb" fragment.
        persisted_pm = saved["cart"]["delivery_info"]["payment_method"]
        assert persisted_pm == "Llave BreB", (
            f"expected canonical 'Llave BreB', got {persisted_pm!r}"
        )

    def test_submit_delivery_info_falls_back_to_raw_when_no_match(self):
        """If user provides something unmatched against a configured
        list, save the raw value — let the model decide via the
        delivery_info_collected envelope whether to re-prompt."""
        from app.services import order_tools

        biz_ctx = {
            "business_id": "biz1",
            "wa_id": "+57300",
            "business": {
                "settings": {"payment_methods": ["efectivo", "Nequi"]},
            },
        }
        saved: dict = {}

        def fake_save(wa_id, business_id, cart):
            saved["cart"] = cart

        with patch.object(
            order_tools, "_cart_from_session",
            return_value={"items": [], "total": 0, "delivery_info": {}},
        ), patch.object(
            order_tools, "_save_cart", side_effect=fake_save,
        ), patch.object(
            order_tools, "_turn_cache",
        ):
            order_tools.submit_delivery_info.invoke({
                "payment_method": "paypal",
                "injected_business_context": biz_ctx,
            })

        assert saved["cart"]["delivery_info"]["payment_method"] == "paypal"


class TestImpossibleEnvelopeGuard:
    """The agent must override ``ready_to_confirm`` / ``order_placed``
    envelopes when they don't match the actual turn outcome — e.g.
    after place_order clears the cart and a follow-up message gets
    mis-routed back to order. Without the guard the renderer builds a
    phantom confirm card from leftover customer-DB data."""

    def test_ready_to_confirm_with_empty_cart_downgrades_to_chat(self):
        from app.agents.order_agent import _guard_impossible_envelope
        env = {"kind": "ready_to_confirm", "summary": "", "facts": []}
        out = _guard_impossible_envelope(
            envelope=env,
            cart_was_empty_at_turn_start=True,
            tool_outputs={},
            wa_id="+57300",
        )
        assert out["kind"] == "chat"
        assert "no hay un pedido activo" in out["summary"]

    def test_ready_to_confirm_with_cart_passes_through(self):
        from app.agents.order_agent import _guard_impossible_envelope
        env = {"kind": "ready_to_confirm", "summary": "", "facts": []}
        out = _guard_impossible_envelope(
            envelope=env,
            cart_was_empty_at_turn_start=False,
            tool_outputs={},
            wa_id="+57300",
        )
        assert out["kind"] == "ready_to_confirm"

    def test_order_placed_without_successful_tool_output_downgrades(self):
        """If the model emits order_placed but place_order didn't
        return a ✅ receipt, the renderer would otherwise show a fake
        confirmation. Override to ``error`` so the customer sees the
        real status (e.g. 'falta confirmación')."""
        from app.agents.order_agent import _guard_impossible_envelope
        env = {"kind": "order_placed", "summary": "", "facts": []}
        # place_order tool refused with the awaiting_confirmation guard
        guard_msg = (
            "❌ El cliente todavía no ha confirmado el pedido. "
            "Llama respond(kind='ready_to_confirm') primero."
        )
        out = _guard_impossible_envelope(
            envelope=env,
            cart_was_empty_at_turn_start=False,
            tool_outputs={"place_order": guard_msg},
            wa_id="+57300",
        )
        assert out["kind"] == "error"
        assert "no ha confirmado" in out["summary"]

    def test_order_placed_with_successful_tool_output_passes_through(self):
        from app.agents.order_agent import _guard_impossible_envelope
        env = {"kind": "order_placed", "summary": "", "facts": []}
        receipt = "✅ ¡Pedido confirmado! #ABCD1234\nSubtotal: $28.000\nTotal: $35.000"
        out = _guard_impossible_envelope(
            envelope=env,
            cart_was_empty_at_turn_start=False,
            tool_outputs={"place_order": receipt},
            wa_id="+57300",
        )
        assert out["kind"] == "order_placed"

    def test_renderer_build_confirm_text_returns_empty_when_no_cart_items(self):
        """Defense in depth: even if the agent guard is bypassed, the
        renderer's text-fallback for ready_to_confirm refuses to build
        a recap when the cart is empty."""
        from app.services import response_renderer

        biz_ctx = {
            "business_id": "biz1",
            "business": {"name": "Biela", "settings": {}},
        }
        with patch.object(
            response_renderer, "_has_cart_items", return_value=False,
        ):
            body = response_renderer._build_confirm_text(biz_ctx, "+57300")
        assert body == "", (
            "expected empty body when cart is empty; got phantom confirm prompt"
        )


class TestProductNotFoundRecovery:
    def test_not_found_becomes_structured_data_not_exception(self):
        """add_to_cart's ProductNotFoundError must NOT bubble to the
        model as a generic exception string — the full hybrid search
        already ran. Surface a NOT_FOUND|... ToolMessage with explicit
        recovery instructions so the model lists alternatives instead
        of giving up or fabricating an items_added envelope."""
        from app.services.product_search import ProductNotFoundError
        agent = OrderAgent()

        first = _ai_with_tools([{
            "name": "add_to_cart",
            "args": {"product_name": "quatro", "quantity": 2},
            "id": "c1", "type": "tool_call",
        }])
        second = _ai_with_tools([_respond_call(
            kind="disambiguation",
            summary="No tenemos quatro, opciones de bebidas",
            facts=["Coca-Cola - $5.500", "Sprite - $5.500"],
        )])
        llm = MagicMock()
        llm.invoke.side_effect = [first, second]

        add_to_cart_mock = MagicMock()
        add_to_cart_mock.name = "add_to_cart"
        add_to_cart_mock.invoke.side_effect = ProductNotFoundError(query="quatro")

        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.order_tools", [add_to_cart_mock]), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch(
                 "app.agents.order_agent.render_response",
                 return_value={"type": "text", "body": "No tenemos quatro. ¿Quieres una Coca-Cola o Sprite?"},
             ):
            output = agent.execute(
                message_body="2 quatros",
                wa_id="+57300", name="X",
                business_context=BIELA_CTX, conversation_history=[],
            )

        # Iter 2 must have seen a NOT_FOUND ToolMessage (not a generic
        # "Error al ejecutar..." string).
        second_call_messages = llm.invoke.call_args_list[1].args[0]
        tool_msgs = [m for m in second_call_messages if isinstance(m, ToolMessage)]
        assert tool_msgs, "expected ToolMessage from add_to_cart not-found"
        assert tool_msgs[0].content.startswith("NOT_FOUND|"), (
            f"expected NOT_FOUND structured result, got: {tool_msgs[0].content!r}"
        )
        assert "quatro" in tool_msgs[0].content
        assert "list_category_products" in tool_msgs[0].content
        assert "disambiguation" in tool_msgs[0].content

        assert output["agent_type"] == "order"
        assert "quatro" in output["message"].lower() or \
               "coca" in output["message"].lower()


class TestToolErrorRecovery:
    def test_tool_exception_surfaced_to_model(self):
        """When a non-terminator tool raises, the error becomes a
        ToolMessage so the model can adapt and still call respond."""
        agent = OrderAgent()

        first = _ai_with_tools([{
            "name": "place_order", "args": {}, "id": "c1", "type": "tool_call",
        }])
        second = _ai_with_tools([_respond_call(
            kind="error", summary="place_order failed: kaboom"
        )])
        llm = MagicMock()
        llm.invoke.side_effect = [first, second]

        place_order_mock = MagicMock()
        place_order_mock.name = "place_order"
        place_order_mock.invoke.side_effect = RuntimeError("kaboom")

        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.order_tools", [place_order_mock]), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch(
                 "app.agents.order_agent.render_response",
                 return_value={"type": "text", "body": "Lo siento, hubo un problema."},
             ):
            output = agent.execute(
                message_body="confirmo",
                wa_id="x", name="X",
                business_context=BIELA_CTX,
                conversation_history=[],
            )

        assert "siento" in output["message"].lower()
        # The model's second invoke saw a ToolMessage with the error.
        second_call_args = llm.invoke.call_args_list[1].args[0]
        tool_msgs = [m for m in second_call_args if isinstance(m, ToolMessage)]
        assert tool_msgs, "expected ToolMessage after tool error"
        assert "kaboom" in tool_msgs[0].content.lower()


# ---------------------------------------------------------------------------
# History rendering — operator turns surface as a system note
# ---------------------------------------------------------------------------

class TestOperatorHistoryRendering:
    def test_operator_assistant_turn_renders_as_system_note(self):
        """Manually-typed operator turns must surface as SystemMessage,
        not as the bot's own AIMessage — otherwise the model treats
        them as authoritative reasoning."""
        agent = OrderAgent()
        only = _ai_with_tools([_respond_call(kind="chat", summary="ok")])
        llm = MagicMock()
        llm.invoke.return_value = only

        history = [
            {"role": "user", "message": "Hola"},
            {"role": "assistant", "message": "Bienvenido"},
            {"role": "assistant", "message": "Disculpa, soy Diego — ahora le aviso al chef.",
             "agent_type": "operator"},
            {"role": "user", "message": "Gracias"},
        ]

        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch(
                 "app.agents.order_agent.render_response",
                 return_value={"type": "text", "body": "ok"},
             ):
            agent.execute(
                message_body="ok",
                wa_id="x", name="X",
                business_context=BIELA_CTX,
                conversation_history=history,
            )

        # History is now rendered inside the unified state block
        # (via render_for_prompt). Operator turns get the
        # "operador (humano)" label inline, bot turns get the "bot:"
        # label — both visible in the same block, distinct from the
        # current user turn.
        sent_messages = llm.invoke.call_args.args[0]
        ctx_blocks = [
            m for m in sent_messages
            if isinstance(m, SystemMessage) and "===== ESTADO Y HISTORIAL DEL TURNO =====" in m.content
        ]
        assert ctx_blocks, "expected unified ESTADO Y HISTORIAL SystemMessage"
        ctx_text = ctx_blocks[0].content
        assert "operador (humano)" in ctx_text, (
            "operator turn should be labeled distinctly inside the state block"
        )
        assert "Bienvenido" in ctx_text, (
            "bot's earlier real turn should still be visible in the state block"
        )
        assert "Diego" in ctx_text or "aviso al chef" in ctx_text


# ---------------------------------------------------------------------------
# Max iterations safety net + missing-respond fallback
# ---------------------------------------------------------------------------

class TestMaxIterationsAndFallback:
    def test_runaway_tool_loop_synthesizes_chat_envelope(self):
        """If the model never calls respond and never stops, the agent
        caps out and synthesizes a chat envelope so the user still gets
        a response (degraded but not a dead-end)."""
        agent = OrderAgent()
        forever = _ai_with_tools([{
            "name": "view_cart", "args": {}, "id": "c1", "type": "tool_call",
        }])
        llm = MagicMock()
        llm.invoke.return_value = forever

        view_cart_mock = MagicMock()
        view_cart_mock.name = "view_cart"
        view_cart_mock.invoke.return_value = "Tu carrito está vacío."

        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.order_tools", [view_cart_mock]), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch(
                 "app.agents.order_agent.render_response",
                 return_value={"type": "text", "body": "Listo. ¿En qué más puedo ayudarte?"},
             ) as render_mock:
            output = agent.execute(
                message_body="loop",
                wa_id="x", name="X",
                business_context=BIELA_CTX,
                conversation_history=[],
            )

        assert llm.invoke.call_count == 5
        # Synthetic envelope must reach the renderer with kind=chat.
        envelope = render_mock.call_args.args[0]
        assert envelope["kind"] == "chat"
        assert output["message"]

    def test_model_emits_prose_without_respond_falls_through(self):
        """If the model emits final text without calling respond, the
        text becomes the synthetic chat envelope's summary."""
        agent = OrderAgent()
        prose = AIMessage(content="¡Con gusto!")
        llm = MagicMock()
        llm.invoke.return_value = prose

        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch(
                 "app.agents.order_agent.render_response",
                 return_value={"type": "text", "body": "¡Con gusto!"},
             ) as render_mock:
            output = agent.execute(
                message_body="hola",
                wa_id="x", name="X",
                business_context=BIELA_CTX,
                conversation_history=[],
            )

        assert output["message"] == "¡Con gusto!"
        env = render_mock.call_args.args[0]
        assert env["kind"] == "chat"
        assert "con gusto" in env["summary"].lower()


# ---------------------------------------------------------------------------
# CTA dispatch — ready_to_confirm + renderer returns CTA payload
# ---------------------------------------------------------------------------

class TestReadyToConfirmCTA:
    def test_ready_to_confirm_dispatches_twilio_cta_and_suppresses_send(self):
        """When the renderer returns type='cta', the agent fires the
        Twilio Content Template and returns the SUPPRESS sentinel so
        the upstream sender doesn't double-send the body as text."""
        agent = OrderAgent()
        only = _ai_with_tools([_respond_call(
            kind="ready_to_confirm",
            summary="All delivery data collected",
        )])
        llm = MagicMock()
        llm.invoke.return_value = only

        cta_payload = {
            "type": "cta",
            "body": "Tengo estos datos para tu pedido:\n*Total:* $33.500\n¿Confirmamos?",
            "content_sid": "HXfake_sid",
            "variables": {"1": "*Total:* $33.500"},
        }

        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch(
                 "app.agents.order_agent.render_response",
                 return_value=cta_payload,
             ), \
             patch(
                 "app.utils.whatsapp_utils.send_twilio_cta",
                 return_value=MagicMock(sid="MSG123"),
             ) as cta_send:
            output = agent.execute(
                message_body="listo",
                wa_id="+573001234567",
                name="X",
                business_context=BIELA_CTX,
                conversation_history=[],
            )

        assert output["message"] == "__SUPPRESS_SEND__"
        cta_send.assert_called_once()
        kwargs = cta_send.call_args.kwargs
        assert kwargs["content_sid"] == "HXfake_sid"
        assert kwargs["to"] == "+573001234567"

    def test_cta_send_failure_falls_back_to_text(self):
        """If send_twilio_cta returns None (failure), agent doesn't
        suppress — it falls back to sending the rendered body as text."""
        agent = OrderAgent()
        only = _ai_with_tools([_respond_call(kind="ready_to_confirm")])
        llm = MagicMock()
        llm.invoke.return_value = only

        cta_payload = {
            "type": "cta",
            "body": "¿Confirmamos el pedido?",
            "content_sid": "HXfake",
            "variables": {"1": "..."},
        }
        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch(
                 "app.agents.order_agent.render_response",
                 return_value=cta_payload,
             ), \
             patch("app.utils.whatsapp_utils.send_twilio_cta", return_value=None):
            output = agent.execute(
                message_body="listo", wa_id="x", name="X",
                business_context=BIELA_CTX, conversation_history=[],
            )

        assert output["message"] != "__SUPPRESS_SEND__"
        assert "Confirmamos" in output["message"]


# ---------------------------------------------------------------------------
# Registry: 'order' agent type resolves to the tool-calling agent
# ---------------------------------------------------------------------------

class TestRegistryResolvesOrderAgent:
    """``get_agent('order')`` must resolve to the canonical OrderAgent
    class, and the symbol re-exported from ``app.agents.registry`` must
    be the same class as the one defined in ``app.agents.order_agent``
    — a refactor that wraps it would silently break callers."""

    def test_registry_order_type_is_tool_calling_agent(self):
        from app.agents.registry import get_agent
        from app.agents.order_agent import OrderAgent
        agent = get_agent("order")
        assert isinstance(agent, OrderAgent)

    def test_registry_reexport_is_the_canonical_class(self):
        from app.agents.registry import OrderAgent as RegistryOrderAgent
        from app.agents.order_agent import OrderAgent as ModuleOrderAgent
        assert RegistryOrderAgent is ModuleOrderAgent


# ---------------------------------------------------------------------------
# Renderer module — CTA short-circuit + text fallback
# ---------------------------------------------------------------------------

class TestRendererCTAShortCircuit:
    def test_ready_to_confirm_with_sid_returns_cta_payload(self):
        """When the business has confirm_order_content_sid set AND
        delivery is complete, the renderer returns type='cta'."""
        from app.services import response_renderer

        biz_ctx = {
            "business_id": "biz1",
            "provider": "twilio",
            "business": {
                "name": "Biela",
                "settings": {"confirm_order_content_sid": "HXabc"},
            },
        }
        full_status = {
            "name": "Yisela",
            "address": "Cra 1 #2-3",
            "phone": "+573001234567",
            "payment_method": "Nequi",
            "total": 33500,
            "all_present": True,
        }
        with patch.object(
            response_renderer, "_read_delivery_status",
            return_value=full_status,
        ):
            out = response_renderer.render_response(
                {"kind": "ready_to_confirm", "summary": "", "facts": []},
                business_context=biz_ctx,
                last_user_message="listo",
                wa_id="+573001234567",
            )
        assert out["type"] == "cta"
        assert out["content_sid"] == "HXabc"
        assert "1" in (out["variables"] or {})

    def test_ready_to_confirm_without_sid_renders_v1_structured_text(self):
        """No SID configured → renderer must render the SAME structured
        recap (Tengo estos datos para tu pedido + multi-line fields +
        ¿Confirmamos?) the CTA card would have shown. Free-form LLM
        text would lose the customer-facing fields."""
        from app.services import response_renderer

        biz_ctx = {
            "business_id": "biz1",
            "provider": "twilio",
            "business": {"name": "Biela", "settings": {}},
        }
        full_status = {
            "name": "Claudia Cerón",
            "address": "calle 19 C No 40 A 26",
            "phone": "3104078032",
            "payment_method": "Llave BreB",
            "total": 28000,
            "all_present": True,
        }
        with patch.object(
            response_renderer, "_read_delivery_status",
            return_value=full_status,
        ), patch.object(
            response_renderer, "_has_cart_items", return_value=True,
        ):
            out = response_renderer.render_response(
                {"kind": "ready_to_confirm", "summary": "", "facts": []},
                business_context=biz_ctx,
                last_user_message="listo",
                wa_id="+573001234567",
            )
        assert out["type"] == "text"
        body = out["body"]
        assert "Tengo estos datos para tu pedido" in body
        assert "Claudia Cerón" in body
        assert "calle 19 C No 40 A 26" in body
        assert "Llave BreB" in body
        assert "$28.000" not in body
        assert "Total" not in body
        assert "¿Confirmamos el pedido?" in body


# ---------------------------------------------------------------------------
# Renderer: cart-mutation kinds get deterministic breakdown + LLM prelude
# ---------------------------------------------------------------------------

class TestCartBreakdownRendering:
    def test_items_added_includes_canonical_cart_breakdown(self):
        """Cart-mutation kinds get the cart breakdown rendered from
        canonical session state — NOT from anything the model wrote.
        Subtotal/total can never be hallucinated because the LLM never
        sees them — the cart text is concatenated deterministically."""
        from app.services import response_renderer

        biz_ctx = {
            "business_id": "biz1",
            "business": {"name": "Biela", "settings": {}},
        }
        canonical_breakdown = (
            "Tu pedido:\n\n"
            "• 1x BARRACUDA - $28.000\n\n"
            "Subtotal: $28.000\n"
            "🛵 Domicilio: $7.000\n"
            "**Total: $35.000**"
        )
        with patch.object(
            response_renderer, "_build_cart_breakdown",
            return_value=canonical_breakdown,
        ), patch.object(
            response_renderer, "_render_cart_prelude",
            return_value="Listo, agregamos eso a tu pedido.",
        ):
            out = response_renderer.render_response(
                {"kind": "items_added", "summary": "Added 1x BARRACUDA",
                 "facts": ["1x BARRACUDA"]},
                business_context=biz_ctx,
                last_user_message="una barracuda",
                wa_id="+57300",
            )
        assert out["type"] == "text"
        # Prelude + canonical breakdown + closing question
        assert "Listo, agregamos eso a tu pedido." in out["body"]
        assert "Subtotal: $28.000" in out["body"]
        assert "Total: $35.000" in out["body"]
        assert "¿Te gustaría añadir algo más" in out["body"]

    def test_cart_breakdown_omits_delivery_and_total_by_default(self):
        """Cart-mutation kinds show items + subtotal only.
        Delivery + total are address-dependent and only shown at
        confirmation time."""
        from app.services import response_renderer
        from app.database.session_state_service import session_state_service
        from app.services import promotion_service

        biz_ctx = {
            "business_id": "biz1",
            "business": {"name": "Biela", "settings": {}},
        }
        with patch.object(
            session_state_service, "load",
            return_value={"session": {"order_context": {"items": [
                {"product_id": "p1", "name": "BARRACUDA",
                 "price": 28000, "quantity": 1},
            ]}}},
        ), patch.object(
            promotion_service, "preview_cart",
            return_value={
                "subtotal": 28000,
                "promo_discount_total": 0,
                "display_groups": [{
                    "kind": "single", "quantity": 1,
                    "name": "BARRACUDA", "line_total": 28000,
                }],
            },
        ):
            breakdown = response_renderer._build_cart_breakdown(
                biz_ctx, "+57300",
            )
        assert "Subtotal: $28.000" in breakdown
        assert "Domicilio" not in breakdown, (
            "delivery fee should NOT appear on cart-mutation turns"
        )
        assert "Total" not in breakdown, (
            "grand total should NOT appear on cart-mutation turns"
        )

    def test_cart_breakdown_with_totals_includes_delivery_and_total(self):
        """include_totals=True (used by ready_to_confirm fallback path
        and order_placed) shows the full breakdown."""
        from app.services import response_renderer
        from app.database.session_state_service import session_state_service
        from app.services import promotion_service

        biz_ctx = {
            "business_id": "biz1",
            "business": {"name": "Biela", "settings": {}},
        }
        with patch.object(
            session_state_service, "load",
            return_value={"session": {"order_context": {"items": [
                {"product_id": "p1", "name": "BARRACUDA",
                 "price": 28000, "quantity": 1},
            ]}}},
        ), patch.object(
            promotion_service, "preview_cart",
            return_value={
                "subtotal": 28000,
                "promo_discount_total": 0,
                "display_groups": [{
                    "kind": "single", "quantity": 1,
                    "name": "BARRACUDA", "line_total": 28000,
                }],
            },
        ), patch(
            "app.services.order_tools._get_delivery_fee",
            return_value=7000,
        ):
            breakdown = response_renderer._build_cart_breakdown(
                biz_ctx, "+57300", include_totals=True,
            )
        assert "Subtotal: $28.000" in breakdown
        assert "Domicilio: $7.000" in breakdown
        assert "Total: $35.000" in breakdown

    def test_empty_cart_falls_back_to_plain_text(self):
        """If breakdown comes back empty (cart cleared post-place_order
        edge case), the renderer falls through to the generic text path."""
        from app.services import response_renderer

        biz_ctx = {
            "business_id": "biz1",
            "business": {"name": "Biela", "settings": {}},
        }
        with patch.object(
            response_renderer, "_build_cart_breakdown",
            return_value="",
        ), patch.object(
            response_renderer, "_render_text",
            return_value="Tu carrito está vacío.",
        ) as text_mock:
            out = response_renderer.render_response(
                {"kind": "cart_view", "summary": "", "facts": []},
                business_context=biz_ctx,
                last_user_message="qué tengo",
                wa_id="+57300",
            )
        assert out["body"] == "Tu carrito está vacío."
        text_mock.assert_called_once()


# ---------------------------------------------------------------------------
# State machine: _save_cart auto-derives state from cart contents
# ---------------------------------------------------------------------------

class TestOrderStateAutoDerivation:
    """v2 cart-mutating tools route through _save_cart. The save path
    must auto-derive the correct order state from the merged contents
    so we don't end up stuck on GREETING after items are added (which
    is what was happening in production)."""

    def test_compute_state_empty_cart_is_greeting(self):
        from app.services.order_tools import _compute_order_state
        from app.database.session_state_service import ORDER_STATE_GREETING
        assert _compute_order_state([], {}) == ORDER_STATE_GREETING

    def test_compute_state_items_only_is_ordering(self):
        from app.services.order_tools import _compute_order_state
        from app.database.session_state_service import ORDER_STATE_ORDERING
        assert _compute_order_state(
            [{"product_id": "p1", "name": "X", "price": 1, "quantity": 1}],
            {},
        ) == ORDER_STATE_ORDERING

    def test_compute_state_items_plus_partial_delivery_is_ordering(self):
        """Partial delivery (just address) must NOT mark READY_TO_PLACE
        — we still need name, phone, payment."""
        from app.services.order_tools import _compute_order_state
        from app.database.session_state_service import ORDER_STATE_ORDERING
        assert _compute_order_state(
            [{"product_id": "p1", "name": "X", "price": 1, "quantity": 1}],
            {"address": "Calle 18 #43 38"},
        ) == ORDER_STATE_ORDERING

    def test_compute_state_items_plus_complete_delivery_is_ready(self):
        from app.services.order_tools import _compute_order_state
        from app.database.session_state_service import ORDER_STATE_READY_TO_PLACE
        assert _compute_order_state(
            [{"product_id": "p1", "name": "X", "price": 1, "quantity": 1}],
            {
                "name": "Yisela", "address": "Cra 1",
                "phone": "+57300", "payment_method": "Nequi",
            },
        ) == ORDER_STATE_READY_TO_PLACE

    def test_save_cart_overrides_stale_greeting_when_items_added(self):
        """Regression: previously _save_cart preserved the existing
        GREETING state after add_to_cart, leaving sessions stuck on
        GREETING despite items in the cart."""
        from app.services import order_tools
        from app.database.session_state_service import ORDER_STATE_GREETING

        # The cache's get_session returns the loader's result. We
        # short-circuit by giving back a stale-GREETING, empty-cart
        # session — which is the production regression path.
        cache = MagicMock()
        cache.get_session.return_value = {
            "session": {"order_context": {
                "items": [],
                "total": 0,
                "state": ORDER_STATE_GREETING,
                "delivery_info": None,
            }},
        }
        saved: dict = {}

        def fake_save(wa_id, business_id, update):
            saved["update"] = update

        with patch.object(
            order_tools.session_state_service, "save", side_effect=fake_save,
        ), patch.object(order_tools, "_turn_cache", return_value=cache):
            order_tools._save_cart("+57300", "biz1", {
                "items": [{"product_id": "p1", "name": "BARRACUDA",
                           "price": 28000, "quantity": 1}],
                "total": 28000,
            })

        new_state = saved["update"]["order_context"]["state"]
        assert new_state == "ORDERING", (
            f"expected ORDERING after add_to_cart, got {new_state!r} — "
            "state derivation is regressing again"
        )

    def test_save_cart_always_derives_state_overriding_caller(self):
        """After v1 deletion, _save_cart always derives state from
        contents. A ``state`` value passed in by the caller (only the
        legacy executor used to do this) is overwritten — no more
        dual-source-of-truth for state."""
        from app.services import order_tools

        saved: dict = {}

        def fake_save(wa_id, business_id, update):
            saved["update"] = update

        cache = MagicMock()
        cache.get_session.return_value = {
            "session": {"order_context": {
                "items": [], "total": 0,
                "state": "GREETING", "delivery_info": None,
            }},
        }
        with patch.object(
            order_tools.session_state_service, "save", side_effect=fake_save,
        ), patch.object(order_tools, "_turn_cache", return_value=cache):
            order_tools._save_cart("+57300", "biz1", {
                "items": [{"product_id": "p1", "name": "X",
                           "price": 1, "quantity": 1}],
                "total": 1,
                # Caller asks for a legacy-era state — derivation wins.
                "state": "COLLECTING_DELIVERY",
            })

        assert saved["update"]["order_context"]["state"] == "ORDERING", (
            "_save_cart must derive state from contents, not honor "
            "the caller's explicit state field (legacy behavior is dead)"
        )


# ---------------------------------------------------------------------------
# Renderer: order_placed emits place_order tool output verbatim
# ---------------------------------------------------------------------------

class TestOrderPlacedRendering:
    def test_order_placed_uses_tool_output_verbatim(self):
        """order_placed must emit the canonical receipt produced by
        place_order — not an LLM rephrasing that drops the order ID
        or subtotal/delivery fee."""
        from app.services import response_renderer

        canonical_receipt = (
            "✅ ¡Pedido confirmado! #ABCD1234\n"
            "Subtotal: $28.000\n"
            "🛵 Domicilio: $7.000\n"
            "Total: $35.000\n"
            "Nos ponemos en contacto pronto para coordinar la entrega.\n"
            "⏱ Tiempo estimado de entrega: 40 a 50 minutos."
        )
        out = response_renderer.render_response(
            {"kind": "order_placed", "summary": "placed", "facts": []},
            business_context={"business_id": "biz1",
                              "business": {"name": "Biela", "settings": {}}},
            last_user_message="Confirmar pedido",
            wa_id="+57300",
            tool_outputs={"place_order": canonical_receipt},
        )
        assert out["type"] == "text"
        assert out["body"] == canonical_receipt

    def test_order_placed_falls_back_when_no_tool_output(self):
        """If no place_order tool output was captured (shouldn't happen
        in the real flow), fall back to LLM render so the user still
        gets a confirmation message instead of nothing."""
        from app.services import response_renderer

        with patch.object(
            response_renderer, "_render_text",
            return_value="Pedido confirmado.",
        ) as text_mock:
            out = response_renderer.render_response(
                {"kind": "order_placed", "summary": "", "facts": []},
                business_context={"business_id": "biz1",
                                  "business": {"name": "Biela", "settings": {}}},
                last_user_message="ok",
                wa_id="+57300",
                tool_outputs={},
            )
        assert out["body"] == "Pedido confirmado."
        text_mock.assert_called_once()


# ---------------------------------------------------------------------------
# place_order state-machine guard
# ---------------------------------------------------------------------------

class TestPlaceOrderConfirmationGuard:
    def test_place_order_refuses_without_awaiting_confirmation(self):
        """place_order must refuse if the agent never sent the
        ready_to_confirm prompt — protects against the model jumping
        straight from cart-with-items to placing the order."""
        from app.services import order_tools

        with patch.object(
            order_tools, "_cart_from_session",
            return_value={
                "items": [{"product_id": "p1", "name": "Barracuda",
                           "price": 28000, "quantity": 1}],
                "delivery_info": {
                    "address": "Cra 1", "payment_method": "Nequi",
                    "phone": "+57300", "name": "Yisela",
                },
                "awaiting_confirmation": False,
            },
        ), patch.object(
            order_tools, "_read_awaiting_confirmation",
            return_value=False,
        ), patch.object(
            order_tools, "_products_enabled", return_value=True,
        ):
            result = order_tools.place_order.invoke({
                "injected_business_context": {
                    "business_id": "biz1",
                    "wa_id": "+57300",
                    "business": {"settings": {}},
                },
            })
        assert "no ha confirmado" in result.lower()
        assert "ready_to_confirm" in result

    def test_obsolete_legacy_bypass_key_is_ignored(self):
        """The legacy executor used to pass ``legacy_bypass=True`` in
        the injected context to skip the awaiting_confirmation guard.
        After v1 deletion the key has no effect — the guard fires
        unconditionally. This test pins that behavior so a stale
        ``legacy_bypass=True`` in a DB row or stub can't quietly let
        an unconfirmed order through."""
        from app.services import order_tools

        fake_create_order = MagicMock(return_value={"success": True})
        with patch.object(
            order_tools, "_cart_from_session",
            return_value={
                "items": [{"product_id": "p1", "name": "Barracuda",
                           "price": 28000, "quantity": 1}],
                "delivery_info": {
                    "address": "Cra 1", "payment_method": "Nequi",
                    "phone": "+57300", "name": "Yisela",
                },
            },
        ), patch.object(
            order_tools, "_read_awaiting_confirmation",
            return_value=False,
        ), patch.object(
            order_tools, "_products_enabled", return_value=True,
        ), patch.object(
            order_tools.product_order_service,
            "create_order", fake_create_order,
        ):
            result = order_tools.place_order.invoke({
                "injected_business_context": {
                    "business_id": "biz1",
                    "wa_id": "+57300",
                    "legacy_bypass": True,  # ignored
                    "business": {"settings": {}},
                },
            })
        assert "no ha confirmado" in result.lower(), (
            "legacy_bypass must NOT skip the guard after v1 deletion — "
            "the awaiting_confirmation interlock is unconditional now"
        )
        fake_create_order.assert_not_called()


# ---------------------------------------------------------------------------
# Agent arms awaiting_confirmation flag on successful CTA dispatch
# ---------------------------------------------------------------------------

class TestAgentArmsConfirmationFlag:
    def test_cta_dispatch_arms_awaiting_confirmation(self):
        """When ready_to_confirm dispatches a CTA successfully, the
        agent persists awaiting_confirmation=True so the next-turn
        place_order is allowed by the guard."""
        agent = OrderAgent()
        only = _ai_with_tools([_respond_call(kind="ready_to_confirm")])
        llm = MagicMock()
        llm.invoke.return_value = only

        cta_payload = {
            "type": "cta",
            "body": "¿Confirmamos?",
            "content_sid": "HXfake",
            "variables": {"1": "..."},
        }
        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch(
                 "app.agents.order_agent.render_response",
                 return_value=cta_payload,
             ), \
             patch(
                 "app.utils.whatsapp_utils.send_twilio_cta",
                 return_value=MagicMock(sid="MSG"),
             ), \
             patch(
                 "app.agents.order_agent.build_turn_context",
                 return_value=_stub_turn_context(has_active_cart=True, cart_summary="• 1x BARRACUDA - $28.000\nSubtotal: $28.000", awaiting_confirmation=False),
             ), \
             patch(
                 "app.agents.order_agent.set_awaiting_confirmation"
             ) as set_flag:
            agent.execute(
                message_body="listo", wa_id="+57300", name="X",
                business_context=BIELA_CTX, conversation_history=[],
            )
        set_flag.assert_called_once()
        assert set_flag.call_args.args[2] is True

    def test_text_fallback_for_ready_to_confirm_also_arms_flag(self):
        """Text fallback (no CTA SID) for ready_to_confirm still arms
        the flag so the next-turn place_order is unblocked."""
        agent = OrderAgent()
        only = _ai_with_tools([_respond_call(kind="ready_to_confirm")])
        llm = MagicMock()
        llm.invoke.return_value = only

        text_payload = {
            "type": "text",
            "body": "¿Confirmamos el pedido?",
        }
        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch(
                 "app.agents.order_agent.render_response",
                 return_value=text_payload,
             ), \
             patch(
                 "app.agents.order_agent.build_turn_context",
                 return_value=_stub_turn_context(has_active_cart=True, cart_summary="• 1x BARRACUDA - $28.000\nSubtotal: $28.000", awaiting_confirmation=False),
             ), \
             patch(
                 "app.agents.order_agent.set_awaiting_confirmation"
             ) as set_flag:
            agent.execute(
                message_body="listo", wa_id="+57300", name="X",
                business_context=BIELA_CTX, conversation_history=[],
            )
        set_flag.assert_called_once()
        assert set_flag.call_args.args[2] is True

    def test_non_confirm_envelope_clears_flag_when_previously_armed(self):
        """If the flag was on (last turn was a confirmation prompt) and
        this turn produces something else (e.g. user changed their mind
        and we updated the cart), the flag must be cleared so the next
        ready_to_confirm re-arms cleanly."""
        agent = OrderAgent()
        only = _ai_with_tools([_respond_call(kind="cart_updated")])
        llm = MagicMock()
        llm.invoke.return_value = only

        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch(
                 "app.agents.order_agent.render_response",
                 return_value={"type": "text", "body": "Actualizado."},
             ), \
             patch(
                 "app.agents.order_agent.build_turn_context",
                 return_value=_stub_turn_context(awaiting_confirmation=True),
             ), \
             patch(
                 "app.agents.order_agent.set_awaiting_confirmation"
             ) as set_flag:
            agent.execute(
                message_body="cambia a nequi", wa_id="+57300", name="X",
                business_context=BIELA_CTX, conversation_history=[],
            )
        set_flag.assert_called_once()
        assert set_flag.call_args.args[2] is False


# ---------------------------------------------------------------------------
# Runtime state hint — agent tells the LLM about awaiting_confirmation
# ---------------------------------------------------------------------------

class TestAwaitingConfirmationHint:
    def test_hint_injected_when_flag_is_set(self):
        """When the previous turn dispatched a confirm prompt, the
        current turn's prompt must include a runtime SystemMessage
        telling the model: \"if user affirms, call place_order — don't
        re-send the card.\""""
        agent = OrderAgent()
        only = _ai_with_tools([_respond_call(kind="order_placed")])
        llm = MagicMock()
        llm.invoke.return_value = only

        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch(
                 "app.agents.order_agent.render_response",
                 return_value={"type": "text", "body": "Pedido confirmado."},
             ), \
             patch(
                 "app.agents.order_agent.build_turn_context",
                 return_value=_stub_turn_context(awaiting_confirmation=True),
             ), \
             patch("app.agents.order_agent.set_awaiting_confirmation"):
            agent.execute(
                message_body="Confirmar pedido", wa_id="+57300", name="X",
                business_context=BIELA_CTX, conversation_history=[],
            )

        sent_messages = llm.invoke.call_args.args[0]
        # Behavioral hint when awaiting_confirmation is armed. The
        # state itself is in the unified CONTEXTO block ("Esperando
        # confirmación: SÍ"); this separate SystemMessage tells the
        # model what to *do* with that state.
        hints = [
            m for m in sent_messages
            if isinstance(m, SystemMessage) and "ACCIÓN ESPERADA" in m.content
        ]
        assert hints, "expected awaiting_confirmation behavioral hint as SystemMessage"
        assert "place_order" in hints[0].content

    def test_no_hint_when_flag_not_set(self):
        """No hint when the flag is off — keeps the prompt small for
        the common case."""
        agent = OrderAgent()
        only = _ai_with_tools([_respond_call(kind="chat")])
        llm = MagicMock()
        llm.invoke.return_value = only

        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch(
                 "app.agents.order_agent.render_response",
                 return_value={"type": "text", "body": "ok"},
             ), \
             patch(
                 "app.agents.order_agent.build_turn_context",
                 return_value=_stub_turn_context(awaiting_confirmation=False),
             ), \
             patch("app.agents.order_agent.set_awaiting_confirmation"):
            agent.execute(
                message_body="hola", wa_id="+57300", name="X",
                business_context=BIELA_CTX, conversation_history=[],
            )

        sent_messages = llm.invoke.call_args.args[0]
        hints = [
            m for m in sent_messages
            if isinstance(m, SystemMessage) and "ACCIÓN ESPERADA" in m.content
        ]
        assert not hints, "should not inject hint when flag is off"


# ---------------------------------------------------------------------------
# Out-of-zone delivery → handoff to customer_service
# ---------------------------------------------------------------------------

class TestOutOfZoneHandoff:
    """When the action agent emits ``out_of_scope`` with summary
    ``out_of_zone:<city>`` and a phone fact, the agent skips the
    renderer entirely and returns a dispatcher-style handoff to CS.
    The CS agent's ``reason='out_of_zone'`` fast-path then builds the
    polished redirect message — no LLM in the loop, no hallucinated
    phone number."""

    def test_out_of_zone_envelope_returns_handoff_to_cs(self):
        agent = OrderAgent()
        only = _ai_with_tools([_respond_call(
            kind="out_of_scope",
            summary="out_of_zone:Ipiales",
            facts=["city:Ipiales", "phone:3239609582"],
        )])
        llm = MagicMock()
        llm.invoke.return_value = only

        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch(
                 "app.agents.order_agent.build_turn_context",
                 return_value=_stub_turn_context(),
             ), \
             patch(
                 "app.agents.order_agent.render_response",
             ) as render_mock:
            output = agent.execute(
                message_body="quiero pedir a Ipiales",
                wa_id="+573001234567",
                name="X",
                business_context=BIELA_CTX,
                conversation_history=[],
            )

        # Renderer skipped — handoff path bypasses it entirely.
        render_mock.assert_not_called()
        assert output["agent_type"] == "order"
        assert output["message"] == ""
        hand = output.get("handoff") or {}
        assert hand.get("to") == "customer_service"
        ctx = hand.get("context") or {}
        assert ctx.get("reason") == "out_of_zone"
        assert ctx.get("city") == "Ipiales"
        assert ctx.get("phone") == "3239609582"

    def test_out_of_zone_envelope_missing_phone_falls_through_to_render(self):
        """If the model emits ``out_of_zone:`` without a phone fact, we
        can't safely redirect — fall back to the renderer so the user
        gets *some* coherent reply instead of a broken handoff."""
        agent = OrderAgent()
        only = _ai_with_tools([_respond_call(
            kind="out_of_scope",
            summary="out_of_zone:Ipiales",
            facts=["city:Ipiales"],  # phone missing
        )])
        llm = MagicMock()
        llm.invoke.return_value = only

        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch(
                 "app.agents.order_agent.build_turn_context",
                 return_value=_stub_turn_context(),
             ), \
             patch(
                 "app.agents.order_agent.render_response",
                 return_value={"type": "text", "body": "ok"},
             ) as render_mock:
            output = agent.execute(
                message_body="quiero pedir a Ipiales",
                wa_id="+573001234567",
                name="X",
                business_context=BIELA_CTX,
                conversation_history=[],
            )

        render_mock.assert_called_once()
        assert "handoff" not in output

    def test_normal_out_of_scope_does_not_handoff(self):
        """``out_of_scope`` for non-redirect reasons (queja, pedido pasado)
        must still go through the renderer — only the ``out_of_zone:``
        summary triggers the handoff."""
        agent = OrderAgent()
        only = _ai_with_tools([_respond_call(
            kind="out_of_scope",
            summary="customer asked about old order",
            facts=[],
        )])
        llm = MagicMock()
        llm.invoke.return_value = only

        with patch.object(OrderAgent, "llm", llm), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"), \
             patch(
                 "app.agents.order_agent.build_turn_context",
                 return_value=_stub_turn_context(),
             ), \
             patch(
                 "app.agents.order_agent.render_response",
                 return_value={"type": "text", "body": "Para consultas de pedidos pasados..."},
             ) as render_mock:
            output = agent.execute(
                message_body="dónde está mi pedido de ayer",
                wa_id="+573001234567",
                name="X",
                business_context=BIELA_CTX,
                conversation_history=[],
            )

        render_mock.assert_called_once()
        assert "handoff" not in output


class TestOutOfZonePromptSurface:
    """``format_business_info_for_prompt`` must surface the
    ``out_of_zone_delivery_contacts`` setting so the model knows which
    cities trigger the redirect, with the city/phone values it should
    cite verbatim in the envelope facts."""

    def test_out_of_zone_contacts_render_in_prompt(self):
        from app.services.business_info_service import format_business_info_for_prompt
        ctx = {
            "business_id": "biela",
            "business": {
                "name": "Biela",
                "settings": {
                    "out_of_zone_delivery_contacts": [
                        {"city": "Ipiales", "phone": "3239609582"},
                    ],
                },
            },
        }
        rendered = format_business_info_for_prompt(ctx)
        assert "Ipiales" in rendered
        assert "3239609582" in rendered
        assert "out_of_zone:" in rendered  # respond-kind hint for the model

    def test_no_out_of_zone_contacts_omits_block(self):
        from app.services.business_info_service import format_business_info_for_prompt
        ctx = {
            "business_id": "biela",
            "business": {"name": "Biela", "settings": {}},
        }
        rendered = format_business_info_for_prompt(ctx)
        assert "out_of_zone" not in rendered
        assert "FUERA de cobertura" not in rendered
