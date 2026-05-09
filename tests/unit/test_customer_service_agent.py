"""Unit tests for app/agents/customer_service_agent.py — mocked LLMs."""

from unittest.mock import patch, MagicMock

import pytest

from app.agents.customer_service_agent import (
    CustomerServiceAgent,
    _parse_planner_response,
)
from app.orchestration import customer_service_flow as csf


BIELA_CTX = {
    "business_id": "biela",
    "business": {
        "name": "Biela",
        "settings": {
            "hours_text": "Lun-Vie 5PM a 10PM",
            "menu_url": "https://x.test/menu",
            "address": "Cra 7 #45-23",
            "payment_methods": ["efectivo", "nequi"],
        },
    },
}


def _llm_response(content: str):
    resp = MagicMock()
    resp.content = content
    return resp


class TestPlannerParsing:
    def test_clean_json(self):
        out = _parse_planner_response('{"intent": "GET_BUSINESS_INFO", "params": {"field": "hours"}}')
        assert out["intent"] == "GET_BUSINESS_INFO"
        assert out["params"] == {"field": "hours"}

    def test_markdown_fences_stripped(self):
        out = _parse_planner_response('```json\n{"intent": "GET_ORDER_STATUS", "params": {}}\n```')
        assert out["intent"] == "GET_ORDER_STATUS"

    def test_unparseable_defaults_to_chat(self):
        out = _parse_planner_response("completely not json")
        assert out["intent"] == csf.INTENT_CUSTOMER_SERVICE_CHAT
        assert out["params"] == {}

    def test_empty_defaults_to_chat(self):
        out = _parse_planner_response("")
        assert out["intent"] == csf.INTENT_CUSTOMER_SERVICE_CHAT


class TestAgentExecuteTemplatePath:
    """When the business info lookup succeeds, the agent should
    render a template reply WITHOUT making a response-LLM call."""

    def test_business_info_hours_uses_template_no_response_llm(self):
        agent = CustomerServiceAgent()
        planner_llm = MagicMock()
        planner_llm.invoke.return_value = _llm_response(
            '{"intent": "GET_BUSINESS_INFO", "params": {"field": "hours"}}'
        )
        # Patch the agent.llm property so any invoke() routes through our mock.
        with patch.object(CustomerServiceAgent, "llm", planner_llm), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"):
            output = agent.execute(
                message_body="a qué hora abren?",
                wa_id="+573001234567",
                name="David",
                business_context=BIELA_CTX,
                conversation_history=[],
            )
        # Exactly one LLM call — the planner. Response was templated.
        assert planner_llm.invoke.call_count == 1
        assert output["agent_type"] == "customer_service"
        assert "Lun-Vie 5PM a 10PM" in output["message"]
        assert output["state_update"]["active_agents"] == ["customer_service"]
        assert output["state_update"]["customer_service_context"]["last_intent"] == "GET_BUSINESS_INFO"
        assert output["state_update"]["customer_service_context"]["last_result_kind"] == csf.RESULT_KIND_BUSINESS_INFO

    def test_address_uses_template(self):
        agent = CustomerServiceAgent()
        llm = MagicMock()
        llm.invoke.return_value = _llm_response(
            '{"intent": "GET_BUSINESS_INFO", "params": {"field": "address"}}'
        )
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"):
            output = agent.execute(
                message_body="dónde quedan?", wa_id="x", name="X",
                business_context=BIELA_CTX, conversation_history=[],
            )
        assert "Cra 7 #45-23" in output["message"]
        assert llm.invoke.call_count == 1  # only planner

    def test_payment_details_field_name_as_intent_remap(self):
        # Production 2026-05-06 (Biela / 3177000722): planner emitted
        # {"intent": "PAYMENT_DETAILS", "params": {}} for "gracias Donde
        # transfiero?" instead of the canonical GET_BUSINESS_INFO with
        # field=payment_details. The CS flow logged "unknown intent —
        # falling back to chat" and the customer got "no entendí". The
        # agent now defensively remaps any field-name-as-intent to the
        # canonical shape so the lookup still resolves.
        agent = CustomerServiceAgent()
        ctx = {
            "business_id": "biela",
            "business": {
                "name": "Biela",
                "settings": {
                    "payment_details": "El pago es directo con el domiciliario, contra entrega.",
                },
            },
        }
        llm = MagicMock()
        # Field name as intent — wrong shape, must be remapped.
        llm.invoke.return_value = _llm_response(
            '{"intent": "PAYMENT_DETAILS", "params": {}}'
        )
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"):
            output = agent.execute(
                message_body="donde transfiero?",
                wa_id="x", name="X",
                business_context=ctx, conversation_history=[],
            )
        assert output["message"] == "El pago es directo con el domiciliario, contra entrega."
        assert llm.invoke.call_count == 1  # template path, no response LLM

    def test_payment_details_default_when_business_has_no_setting(self):
        # No settings.payment_details → fall back to the contra-entrega
        # default. Never return the business contact phone.
        agent = CustomerServiceAgent()
        ctx_no_payment = {
            "business_id": "biela",
            "business": {
                "name": "Biela",
                "settings": {"phone": "+573177000722"},  # phone present, payment_details absent
            },
        }
        llm = MagicMock()
        llm.invoke.return_value = _llm_response(
            '{"intent": "GET_BUSINESS_INFO", "params": {"field": "payment_details"}}'
        )
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"):
            output = agent.execute(
                message_body="a qué número pago?",
                wa_id="x", name="X",
                business_context=ctx_no_payment, conversation_history=[],
            )
        assert output["message"] == "El pago es contra entrega, directo con el domiciliario."
        # Critical: contact phone must NOT leak into a payment answer.
        assert "+573177000722" not in output["message"]

    def test_payment_details_uses_template(self):
        # Regression: production 2026-05-06 (Biela). Customer asked
        # "A qué número se realiza el pago?" / "Donde transfiero?" after
        # placing the order, and CS either returned the business contact
        # phone (misclassified as `phone`) or fell through to chat
        # ("¿puedes aclararme...?"). The fix routes payment-account
        # questions to the new `payment_details` field, which renders
        # the configured value verbatim.
        agent = CustomerServiceAgent()
        ctx = {
            "business_id": "biela",
            "business": {
                "name": "Biela",
                "settings": {
                    "payment_details": "El pago es directo con el domiciliario, contra entrega.",
                },
            },
        }
        llm = MagicMock()
        llm.invoke.return_value = _llm_response(
            '{"intent": "GET_BUSINESS_INFO", "params": {"field": "payment_details"}}'
        )
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"):
            output = agent.execute(
                message_body="a qué número se realiza el pago?",
                wa_id="x", name="X",
                business_context=ctx, conversation_history=[],
            )
        assert output["message"] == "El pago es directo con el domiciliario, contra entrega."
        assert llm.invoke.call_count == 1  # only planner — template path

    def test_menu_url_uses_template(self):
        # Regression: "carta" is a Colombian synonym for "menú". When the
        # planner classifies a carta/menú request as menu_url, the template
        # path must return the configured URL — not the chat fallback.
        agent = CustomerServiceAgent()
        llm = MagicMock()
        llm.invoke.return_value = _llm_response(
            '{"intent": "GET_BUSINESS_INFO", "params": {"field": "menu_url"}}'
        )
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"):
            output = agent.execute(
                message_body="quiero conocer la carta", wa_id="x", name="X",
                business_context=BIELA_CTX, conversation_history=[],
            )
        assert "https://x.test/menu" in output["message"]
        assert llm.invoke.call_count == 1  # only planner


class TestAgentExecuteLLMResponsePath:
    """When result_kind isn't a clean business_info, response LLM should run."""

    def test_no_order_path_calls_response_llm(self):
        agent = CustomerServiceAgent()
        llm = MagicMock()
        # First call: planner picks order status. Second call: response LLM.
        llm.invoke.side_effect = [
            _llm_response('{"intent": "GET_ORDER_STATUS", "params": {}}'),
            _llm_response("No tengo pedidos tuyos recientes, parce."),
        ]
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"), \
             patch(
                 "app.orchestration.customer_service_flow.order_lookup_service.get_latest_order",
                 return_value=None,
             ):
            output = agent.execute(
                message_body="dónde está mi pedido?",
                wa_id="+573001234567",
                name="David",
                business_context=BIELA_CTX,
                conversation_history=[],
            )
        assert llm.invoke.call_count == 2
        assert output["message"] == "No tengo pedidos tuyos recientes, parce."
        assert output["state_update"]["customer_service_context"]["last_result_kind"] == csf.RESULT_KIND_NO_ORDER

    def test_info_missing_path_calls_response_llm(self):
        agent = CustomerServiceAgent()
        llm = MagicMock()
        llm.invoke.side_effect = [
            _llm_response('{"intent": "GET_BUSINESS_INFO", "params": {"field": "floor_plan"}}'),
            _llm_response("No tengo ese dato exacto."),
        ]
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"):
            output = agent.execute(
                message_body="cuántas mesas tienen?",
                wa_id="x", name="X",
                business_context=BIELA_CTX, conversation_history=[],
            )
        assert llm.invoke.call_count == 2
        assert output["state_update"]["customer_service_context"]["last_result_kind"] == csf.RESULT_KIND_INFO_MISSING
        assert output["message"] == "No tengo ese dato exacto."

    def test_planner_exception_falls_back_to_chat(self):
        agent = CustomerServiceAgent()
        llm = MagicMock()
        llm.invoke.side_effect = [
            RuntimeError("planner down"),
            _llm_response("Puedo ayudarte con horarios, dirección..."),
        ]
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"):
            output = agent.execute(
                message_body="algo raro",
                wa_id="x", name="X",
                business_context=BIELA_CTX, conversation_history=[],
            )
        assert output["state_update"]["customer_service_context"]["last_intent"] == csf.INTENT_CUSTOMER_SERVICE_CHAT
        assert output["message"] == "Puedo ayudarte con horarios, dirección..."


class TestOrderStatusBreakdownPrompt:
    """RESULT_KIND_ORDER_STATUS response prompt — per-item breakdown rendering.

    Production 2026-05-06 (Biela / +573159280840): customer asked
    "Cuanto vale cada producto porfa?" right after order placement and
    got "no tengo esa información". Two regressions to prevent:
      1. items_lines must include the product NAME (not just qty + price).
      2. The system prompt must instruct the LLM to surface the breakdown
         when the customer explicitly asked for it.
    """

    def _exec_result(self):
        return {
            "result_kind": csf.RESULT_KIND_ORDER_STATUS,
            "order": {
                "id": "o1",
                "status": "confirmed",
                "total_amount": 62500,
                "items": [
                    {"name": "BARRACUDA", "quantity": 1, "unit_price": 28000, "line_total": 28000, "notes": None},
                    {"name": "Coca-Cola", "quantity": 2, "unit_price": 5500, "line_total": 11000, "notes": None},
                    {"name": "LA VUELTA", "quantity": 1, "unit_price": 29000, "line_total": 29000, "notes": "sin cebolla"},
                ],
                "eta_minutes": None,
                "cancellation_reason": None,
            },
        }

    def test_items_lines_include_product_name_and_price(self):
        agent = CustomerServiceAgent()
        system, inp = agent._build_response_prompt(
            result_kind=csf.RESULT_KIND_ORDER_STATUS,
            exec_result=self._exec_result(),
            message_body="cuanto vale cada producto?",
            business_context=BIELA_CTX,
        )
        # Each product name is rendered (not dropped from items_lines).
        assert "BARRACUDA" in inp
        assert "Coca-Cola" in inp
        assert "LA VUELTA" in inp
        # Per-line price, formatted in COP.
        assert "$28.000" in inp
        assert "$5.500" in inp
        assert "$29.000" in inp
        # Quantity > 1 surfaces line total.
        assert "$11.000" in inp
        # Notes flow through.
        assert "sin cebolla" in inp

    def test_breakdown_rules_present_in_system_prompt(self):
        agent = CustomerServiceAgent()
        system, _ = agent._build_response_prompt(
            result_kind=csf.RESULT_KIND_ORDER_STATUS,
            exec_result=self._exec_result(),
            message_body="cuanto vale cada producto?",
            business_context=BIELA_CTX,
        )
        # The LLM must know that an explicit breakdown question forces
        # the items list into the reply, otherwise it'll keep summarizing
        # to a one-liner status.
        assert "DESGLOSE" in system or "desglose" in system
        assert "cuánto vale cada producto" in system or "detalle del pedido" in system


class TestAgentHandoffPropagation:
    """When flow returns RESULT_KIND_HANDOFF, agent must surface it as output.handoff."""

    def test_active_cart_mi_pedido_hands_off_to_order(self):
        agent = CustomerServiceAgent()
        llm = MagicMock()
        # Planner picks GET_ORDER_STATUS; flow detects active cart → handoff.
        llm.invoke.return_value = _llm_response(
            '{"intent": "GET_ORDER_STATUS", "params": {}}'
        )
        session = {"order_context": {"items": [{"name": "Barracuda", "quantity": 1}]}}
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message") as m_store:
            output = agent.execute(
                message_body="qué tengo en mi pedido",
                wa_id="+573001234567",
                name="David",
                business_context=BIELA_CTX,
                conversation_history=[],
                session=session,
            )
        assert output["agent_type"] == "customer_service"
        assert output["message"] == ""
        assert output["handoff"]["to"] == "order"
        assert output["handoff"]["context"]["reason"] == "mi_pedido_active_cart"
        # Handoff path must NOT persist an assistant message — the target
        # agent's reply is what the user sees.
        m_store.assert_not_called()
        # Only the planner LLM was called (no response LLM).
        assert llm.invoke.call_count == 1


class TestCancelOrderGuard:
    """
    Deterministic guard: even if the planner emits CANCEL_ORDER, refuse to
    cancel unless turn_ctx says there's a placed cancellable order. Belt-
    and-suspenders for the production bug on 2026-04-27 where a cart was
    silently cancelled because "No más" reached the CS agent.
    """

    def _ctx(self, **kwargs):
        from app.orchestration.turn_context import TurnContext
        return TurnContext(**kwargs)

    def test_cancel_order_with_active_cart_no_placed_order_is_refused(self):
        agent = CustomerServiceAgent()
        llm = MagicMock()
        # Planner mistakenly emits CANCEL_ORDER; response LLM still runs
        # because the guard downgrades to CUSTOMER_SERVICE_CHAT.
        llm.invoke.side_effect = [
            _llm_response('{"intent": "CANCEL_ORDER", "params": {}}'),
            _llm_response("Tu pedido en curso lo manejamos por aquí mismo."),
        ]
        ctx = self._ctx(
            order_state="ORDERING",
            has_active_cart=True,
            cart_summary="1x DENVER",
            has_recent_cancellable_order=False,
        )
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"):
            output = agent.execute(
                message_body="no más",
                wa_id="+573001234567",
                name="David",
                business_context=BIELA_CTX,
                conversation_history=[],
                turn_ctx=ctx,
            )
        # Guard fired: NO order_modification_service.cancel_order should
        # have been called. We assert via the resulting last_intent and
        # last_result_kind: chat fallback, not cancellation.
        assert output["state_update"]["customer_service_context"]["last_intent"] == csf.INTENT_CUSTOMER_SERVICE_CHAT
        assert output["state_update"]["customer_service_context"]["last_result_kind"] != csf.RESULT_KIND_ORDER_CANCELLED

    def test_cancel_order_with_placed_cancellable_order_proceeds(self):
        agent = CustomerServiceAgent()
        llm = MagicMock()
        llm.invoke.side_effect = [
            _llm_response('{"intent": "CANCEL_ORDER", "params": {}}'),
            _llm_response("Listo, tu pedido fue cancelado."),
        ]
        fake_order = {
            "id": "abc-123",
            "status": "pending",
            "total_amount": 24500,
            "items": [],
        }
        ctx = self._ctx(
            order_state="GREETING",
            has_active_cart=False,
            has_recent_cancellable_order=True,
            recent_order_id="abc-123",
        )
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"), \
             patch(
                 "app.orchestration.customer_service_flow.order_lookup_service.get_latest_order",
                 return_value=fake_order,
             ), \
             patch(
                 "app.orchestration.customer_service_flow.order_modification_service.cancel_order",
                 return_value={**fake_order, "status": "cancelled"},
             ):
            output = agent.execute(
                message_body="cancela mi pedido",
                wa_id="+573001234567",
                name="David",
                business_context=BIELA_CTX,
                conversation_history=[],
                turn_ctx=ctx,
            )
        # Guard did NOT fire — the cancel handler ran and produced
        # RESULT_KIND_ORDER_CANCELLED.
        assert output["state_update"]["customer_service_context"]["last_intent"] == csf.INTENT_CANCEL_ORDER
        assert output["state_update"]["customer_service_context"]["last_result_kind"] == csf.RESULT_KIND_ORDER_CANCELLED

    def test_cancel_order_without_turn_ctx_does_not_block(self):
        """
        Backward-compat: callers that don't pass turn_ctx (e.g. older
        tests, direct unit invocations) still hit the executor. The
        guard only kicks in when turn_ctx is explicitly provided.
        """
        agent = CustomerServiceAgent()
        llm = MagicMock()
        llm.invoke.side_effect = [
            _llm_response('{"intent": "CANCEL_ORDER", "params": {}}'),
            _llm_response("No encontré pedido por cancelar."),
        ]
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"), \
             patch(
                 "app.orchestration.customer_service_flow.order_lookup_service.get_latest_order",
                 return_value=None,
             ):
            output = agent.execute(
                message_body="cancela",
                wa_id="x", name="X",
                business_context=BIELA_CTX,
                conversation_history=[],
            )
        assert output["state_update"]["customer_service_context"]["last_intent"] == csf.INTENT_CANCEL_ORDER


class TestExplicitCancelKeywordHelper:
    """Unit tests for _has_explicit_cancel_keyword."""

    @pytest.mark.parametrize(
        "msg",
        [
            "cancela mi pedido",
            "Cancela ya",
            "cancelar el pedido",
            "anula el pedido",
            "anúlalo por favor",  # accent + clitic
            "no quiero el pedido",
            "Ya no quiero la orden",
            "borra el pedido",
            "elimina el pedido",
            "descarta el pedido",
            "Cancel my order",  # English token also caught by "cancel"
            "CANCELA YA",
        ],
    )
    def test_explicit_cancel_phrases_match(self, msg):
        from app.services.cancel_keywords import has_explicit_cancel_keyword as _has_explicit_cancel_keyword
        assert _has_explicit_cancel_keyword(msg) is True, msg

    @pytest.mark.parametrize(
        "msg",
        [
            "Si",
            "Si\nGracias",
            "Si Gracias",
            "gracias",
            "Muchas gracias",
            "ok gracias",
            "listo",
            "perfecto",
            "vale",
            "dale",
            "que lo disfrutes",
            "ya no más",
            "así está bien",
            "No más, gracias",
            "ok",
            "perfect, thanks",
            "",
            None,
        ],
    )
    def test_polite_closes_do_not_match(self, msg):
        from app.services.cancel_keywords import has_explicit_cancel_keyword as _has_explicit_cancel_keyword
        assert _has_explicit_cancel_keyword(msg) is False, msg


class TestCancelOrderRequiresExplicitKeyword:
    """
    Regression: 2026-05-04 (Biela / 3108069647). User said "Si\\nGracias"
    right after PLACE_ORDER and the CS planner hallucinated CANCEL_ORDER,
    cancelling order #6A8D5250. The hard guard must refuse CANCEL_ORDER
    unless the message contains an explicit cancel keyword — even if
    has_recent_cancellable_order is True.
    """

    def _ctx(self, **kwargs):
        from app.orchestration.turn_context import TurnContext
        return TurnContext(**kwargs)

    @pytest.mark.parametrize(
        "msg",
        [
            "Si",
            "Si\nGracias",
            "Si Gracias",
            "gracias",
            "ok",
            "listo",
            "perfecto",
        ],
    )
    def test_polite_close_with_recent_cancellable_order_is_refused(self, msg):
        """
        Safety property: regardless of which guard fires (the cancel-
        keyword downgrade or the despedida-post-pedido handoff added
        2026-05-05), ``cancel_order`` MUST NOT be called and the
        result must NOT be ORDER_CANCELLED.
        """
        agent = CustomerServiceAgent()
        llm = MagicMock()
        # Planner hallucinates CANCEL_ORDER; either the cancel-keyword
        # guard downgrades to CHAT (and the response LLM runs) or the
        # despedida safety net hands off to order (no second LLM call).
        # Provide two stubs so both paths work.
        llm.invoke.side_effect = [
            _llm_response('{"intent": "CANCEL_ORDER", "params": {}}'),
            _llm_response("¡Con gusto!"),
        ]
        ctx = self._ctx(
            order_state="GREETING",
            has_active_cart=False,
            has_recent_cancellable_order=True,
            recent_order_id="abc-123",
            latest_order_status="confirmed",
            latest_order_id="abc-123",
        )
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"), \
             patch(
                 "app.orchestration.customer_service_flow.order_modification_service.cancel_order",
             ) as cancel_mock:
            output = agent.execute(
                message_body=msg,
                wa_id="+573001234567",
                name="David",
                business_context=BIELA_CTX,
                conversation_history=[],
                turn_ctx=ctx,
            )
        # Hard property: cancel_order MUST NOT have been called.
        cancel_mock.assert_not_called()
        # Either the despedida safety net handed off to order (preferred —
        # cleaner UX), or the cancel-keyword guard downgraded to CHAT.
        # Both prevent the cancellation.
        handoff = output.get("handoff") or {}
        if handoff:
            assert handoff.get("to") == "order"
            assert handoff.get("context", {}).get("reason") == "despedida_post_pedido_misroute"
        else:
            ctx_out = output["state_update"]["customer_service_context"]
            assert ctx_out["last_intent"] == csf.INTENT_CUSTOMER_SERVICE_CHAT
            assert ctx_out["last_result_kind"] != csf.RESULT_KIND_ORDER_CANCELLED

    def test_explicit_cancel_with_cancellable_order_proceeds(self):
        """Sanity: legitimate cancellations still go through."""
        agent = CustomerServiceAgent()
        llm = MagicMock()
        llm.invoke.side_effect = [
            _llm_response('{"intent": "CANCEL_ORDER", "params": {}}'),
            _llm_response("Listo, tu pedido fue cancelado."),
        ]
        fake_order = {
            "id": "abc-123",
            "status": "pending",
            "total_amount": 24500,
            "items": [],
        }
        ctx = self._ctx(
            order_state="GREETING",
            has_active_cart=False,
            has_recent_cancellable_order=True,
            recent_order_id="abc-123",
            latest_order_status="pending",
            latest_order_id="abc-123",
        )
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"), \
             patch(
                 "app.orchestration.customer_service_flow.order_lookup_service.get_latest_order",
                 return_value=fake_order,
             ), \
             patch(
                 "app.orchestration.customer_service_flow.order_modification_service.cancel_order",
                 return_value={**fake_order, "status": "cancelled"},
             ) as cancel_mock:
            output = agent.execute(
                message_body="cancela mi pedido",
                wa_id="+573001234567",
                name="David",
                business_context=BIELA_CTX,
                conversation_history=[],
                turn_ctx=ctx,
            )
        cancel_mock.assert_called_once()
        ctx_out = output["state_update"]["customer_service_context"]
        assert ctx_out["last_intent"] == csf.INTENT_CANCEL_ORDER
        assert ctx_out["last_result_kind"] == csf.RESULT_KIND_ORDER_CANCELLED


class TestPlannerHoursFieldReframedAsAvailability:
    """
    Regression: 2026-05-05 (Biela / 3147139789) — user wrote "hay atencion"
    (an availability/operating-hours question) and the CS planner emitted
    CUSTOMER_SERVICE_CHAT instead of GET_BUSINESS_INFO {field: "hours"}.

    The hours field's old description listed only time-explicit examples
    ("a qué hora abren", "cuándo cierran", "abren los domingos"), so the
    LLM didn't generalize to availability phrasings. Reframed as a
    CATEGORY ("horarios, disponibilidad, o si el local está operando")
    with both kinds of examples as anchors.
    """

    def test_prompt_describes_hours_as_availability_or_schedule(self):
        from app.agents.customer_service_agent import PLANNER_SYSTEM_TEMPLATE
        lower = PLANNER_SYSTEM_TEMPLATE.lower()
        # Category description (not a bare list).
        assert "disponibilidad" in lower or "operando" in lower
        # Both classic and availability anchor phrases must be present.
        for example in (
            "a qué hora abren",
            "cuándo cierran",
            "hay atención",
            "están atendiendo",
            "están abiertos",
            # Service-phrasing anchors (production observation
            # 2026-05-05, Biela / 3177000722: "hay servicio" was
            # misclassified as CUSTOMER_SERVICE_CHAT).
            "hay servicio",
            "tienen servicio",
            "ya abrieron",
        ):
            assert example.lower() in lower, f"hours rule missing example: {example!r}"
        # Must signal the LLM to generalize, not match keywords.
        assert "ilustrativas" in lower or "ilustrativos" in lower


class TestPostOrderCloseHelper:
    """Unit tests for _is_post_order_close — must NOT match questions."""

    @pytest.mark.parametrize(
        "msg",
        [
            "Gracias",
            "gracias",
            "muchas gracias",
            "si gracias",
            "ok gracias",
            "listo gracias",
            "perfecto gracias",
            "vale gracias",
            "bueno gracias",
            "dale gracias",
            "mil gracias",
            "perfecto",
            "listo",
            "ok",
            "okay",
            "dale",
            "genial",
            "con gusto",
            "todo bien",
            "así está bien",
            "chao",
            "bye",
            "que disfruten",
        ],
    )
    def test_polite_closes_match(self, msg):
        from app.agents.customer_service_agent import _is_post_order_close
        assert _is_post_order_close(msg) is True, msg

    @pytest.mark.parametrize(
        "msg",
        [
            "",
            None,
            "Hay atención?",
            "Una bimota",
            "quiero pedir algo",
            "cuánto vale el pegoretti",
            "qué tienen para tomar",
            "no quiero el pedido",
            "vale",                # ambiguous (price)
            "bueno",               # ambiguous (filler)
            "ok pero cuánto?",     # interrogative
            "cuánto cuesta?",
            "que tienen",
            "ok dame otra",        # too long + new request
        ],
    )
    def test_non_closes_do_not_match(self, msg):
        from app.agents.customer_service_agent import _is_post_order_close
        assert _is_post_order_close(msg) is False, msg


class TestDespedidaPostPedidoSafetyNet:
    """
    Regression: 2026-05-05 (Biela / 3177000722) — "Gracias" after
    PLACE_ORDER misrouted to CS, fell into cs_chat_fallback. The
    safety net hands off to the order agent so the status-aware
    DESPEDIDA template fires.
    """

    def _ctx_post_order(self, status="confirmed"):
        from app.orchestration.turn_context import TurnContext
        return TurnContext(
            order_state="GREETING",
            has_active_cart=False,
            latest_order_status=status,
            latest_order_id="abc-123",
        )

    @pytest.mark.parametrize(
        "msg",
        ["Gracias", "si gracias", "perfecto", "ok gracias", "listo gracias", "dale"],
    )
    def test_post_order_close_is_handed_off_to_order(self, msg):
        agent = CustomerServiceAgent()
        llm = MagicMock()
        llm.invoke.side_effect = [
            _llm_response('{"intent": "CUSTOMER_SERVICE_CHAT", "params": {}}'),
        ]
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"):
            output = agent.execute(
                message_body=msg,
                wa_id="+573001234567",
                name="David",
                business_context=BIELA_CTX,
                conversation_history=[],
                turn_ctx=self._ctx_post_order(),
            )
        # Handoff payload populated; order agent will run next.
        assert output["handoff"]["to"] == "order"
        assert output["handoff"]["context"]["reason"] == "despedida_post_pedido_misroute"
        assert output["message"] == ""

    def test_no_handoff_when_no_recent_order(self):
        # Without latest_order_status, the safety net must NOT fire —
        # falls through to the regular CS chat fallback.
        from app.orchestration.turn_context import TurnContext
        agent = CustomerServiceAgent()
        llm = MagicMock()
        llm.invoke.side_effect = [
            _llm_response('{"intent": "CUSTOMER_SERVICE_CHAT", "params": {}}'),
            _llm_response("Hola, ¿en qué te ayudo?"),
        ]
        ctx = TurnContext(order_state="GREETING")
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"):
            output = agent.execute(
                message_body="gracias",
                wa_id="+573001234567",
                name="David",
                business_context=BIELA_CTX,
                conversation_history=[],
                turn_ctx=ctx,
            )
        assert output.get("handoff") in (None, {})
        assert "Hola" in output["message"]

    def test_no_handoff_when_msg_is_a_question(self):
        # Even with a recent order, a question must NOT trigger the
        # despedida safety net — the user is asking, not closing.
        agent = CustomerServiceAgent()
        llm = MagicMock()
        llm.invoke.side_effect = [
            _llm_response('{"intent": "CUSTOMER_SERVICE_CHAT", "params": {}}'),
            _llm_response("Información, claro."),
        ]
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"):
            output = agent.execute(
                message_body="cuánto cuesta el domicilio?",
                wa_id="+573001234567",
                name="David",
                business_context=BIELA_CTX,
                conversation_history=[],
                turn_ctx=self._ctx_post_order(),
            )
        assert output.get("handoff") in (None, {})


class TestOrderClosedHandoff:
    """
    When the order agent's availability gate fires, it hands off to CS
    with handoff_context.reason="order_closed". The CS agent answers
    deterministically using business_info_service.format_open_status_sentence
    so the prose matches the existing "¿están abiertos?" reply.
    Skips the planner LLM entirely on this path.
    """

    _CLOSED_STATUS = {
        "is_open": False,
        "has_data": True,
        "opens_at": None,
        "closes_at": None,
        "next_open_dow": 1,  # Monday
        "next_open_time": None,
        "now_local": None,
    }

    def test_no_active_cart_uses_chat_invitation(self):
        agent = CustomerServiceAgent()
        llm = MagicMock()
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch(
                 "app.services.business_info_service.compute_open_status",
                 return_value=self._CLOSED_STATUS,
             ), \
             patch(
                 "app.services.business_info_service.format_open_status_sentence",
                 return_value="Por ahora estamos cerrados.",
             ), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"), \
             patch("app.agents.customer_service_agent.tracer"):
            output = agent.execute(
                message_body="una barracuda",
                wa_id="+573001234567", name="David",
                business_context=BIELA_CTX,
                conversation_history=[],
                handoff_context={
                    "reason": "order_closed",
                    "has_active_cart": False,
                    "blocked_intents": ["ADD_TO_CART"],
                },
            )

        # Planner LLM must NOT have been called — deterministic path.
        llm.invoke.assert_not_called()
        msg = output["message"]
        assert "cerrados" in msg.lower()
        # Empty-cart tail invites the customer to chat / browse.
        assert "menú" in msg.lower() or "duda" in msg.lower()
        assert output.get("handoff") in (None, {})

    def test_active_cart_says_cart_is_saved(self):
        agent = CustomerServiceAgent()
        llm = MagicMock()
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch(
                 "app.services.business_info_service.compute_open_status",
                 return_value=self._CLOSED_STATUS,
             ), \
             patch(
                 "app.services.business_info_service.format_open_status_sentence",
                 return_value="Por ahora estamos cerrados.",
             ), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"), \
             patch("app.agents.customer_service_agent.tracer"):
            output = agent.execute(
                message_body="confirmo",
                wa_id="+573001234567", name="David",
                business_context=BIELA_CTX,
                conversation_history=[],
                handoff_context={
                    "reason": "order_closed",
                    "has_active_cart": True,
                    "blocked_intents": ["CONFIRM"],
                },
            )

        llm.invoke.assert_not_called()
        msg = output["message"]
        assert "cerrados" in msg.lower()
        # Active-cart tail mentions the cart is preserved.
        assert "guardado" in msg.lower() or "retomamos" in msg.lower()


class TestOutOfZoneHandoff:
    """
    Out-of-zone delivery redirect: order agent hands off to CS with
    ``handoff_context.reason="out_of_zone"`` plus city/phone. CS renders
    a polished, deterministic message — no LLM, no hallucinated phone.
    """

    def test_out_of_zone_redirect_message(self):
        agent = CustomerServiceAgent()
        llm = MagicMock()
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"), \
             patch("app.agents.customer_service_agent.tracer"):
            output = agent.execute(
                message_body="quiero pedir a Ipiales",
                wa_id="+573001234567", name="David",
                business_context=BIELA_CTX,
                conversation_history=[],
                handoff_context={
                    "reason": "out_of_zone",
                    "city": "Ipiales",
                    "phone": "3239609582",
                },
            )

        # Planner LLM must NOT have been called — deterministic path.
        llm.invoke.assert_not_called()
        msg = output["message"]
        assert "Ipiales" in msg
        assert "3239609582" in msg
        # Should NOT re-trigger order flow.
        assert output.get("handoff") in (None, {})

    def test_out_of_zone_missing_phone_falls_back_to_generic(self):
        agent = CustomerServiceAgent()
        llm = MagicMock()
        with patch.object(CustomerServiceAgent, "llm", llm), \
             patch("app.agents.customer_service_agent.conversation_service.store_conversation_message"), \
             patch("app.agents.customer_service_agent.tracer"):
            output = agent.execute(
                message_body="quiero pedir a Ipiales",
                wa_id="+573001234567", name="David",
                business_context=BIELA_CTX,
                conversation_history=[],
                handoff_context={
                    "reason": "out_of_zone",
                    "city": "",
                    "phone": "",
                },
            )

        llm.invoke.assert_not_called()
        msg = output["message"]
        # Generic fallback when context is incomplete — still informative.
        assert "cobertura" in msg.lower()
