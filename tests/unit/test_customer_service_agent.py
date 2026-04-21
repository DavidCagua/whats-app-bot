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
