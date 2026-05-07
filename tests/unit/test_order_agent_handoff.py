"""Unit tests for the order agent's mirror handoff guard.

The order agent hands off to customer_service when:
  - planner picked VIEW_CART
  - cart is empty
  - user's message phrasing signals a status query

This covers the symmetric case to customer_service's active-cart guard.
"""

from unittest.mock import patch, MagicMock

import pytest

from app.agents.order_agent import OrderAgent, _STATUS_INQUIRY_RE


def _llm_response(content: str):
    resp = MagicMock()
    resp.content = content
    return resp


BIELA_CTX = {
    "business_id": "biela",
    "business": {"name": "Biela", "settings": {}},
}


class TestStatusInquiryRegex:
    @pytest.mark.parametrize(
        "msg",
        [
            "dónde está mi pedido",
            "donde esta mi pedido",
            "cuál es el estado",
            "ya salió mi pedido?",
            "ya salio mi pedido",
            "cómo va mi pedido",
            "qué pasa con mi pedido",
            "mi pedido ya llegó?",
        ],
    )
    def test_matches_status_phrasings(self, msg):
        assert _STATUS_INQUIRY_RE.search(msg) is not None

    @pytest.mark.parametrize(
        "msg",
        [
            "qué tengo en mi pedido",
            "quiero pedir una barracuda",
            "qué tienen de bebidas",
            "a qué hora abren",
        ],
    )
    def test_no_match_for_non_status_phrasings(self, msg):
        # Note: "a qué hora abren" does not match because the regex
        # targets order-status keywords, not business info.
        assert _STATUS_INQUIRY_RE.search(msg) is None


class TestEmptyCartStatusHandoff:
    def test_empty_cart_plus_status_phrasing_hands_off_to_cs(self):
        agent = OrderAgent()
        llm = MagicMock()
        # Planner picks VIEW_CART.
        llm.invoke.return_value = _llm_response(
            '{"intent": "VIEW_CART", "params": {}}'
        )
        # Executor returns an empty cart view.
        empty_cart_result = {
            "result_kind": "cart_view",
            "success": True,
            "cart_view": {"items": [], "subtotal": 0, "delivery_fee": 0, "total": 0, "is_empty": True},
            "state_after": "GREETING",
            "cart_summary": "Pedido vacío.",
        }
        # planner_llm (temp=0) and llm (response, temp=0.3) are now distinct
        # properties — patch both with the same mock so the sequential
        # side_effect counter still works for tests that exercise both calls.
        with patch.object(OrderAgent, "llm", llm), \
             patch.object(OrderAgent, "planner_llm", llm), \
             patch("app.agents.order_agent.execute_order_intent", return_value=empty_cart_result), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"):
            output = agent.execute(
                message_body="dónde está mi pedido",
                wa_id="+573001234567",
                name="David",
                business_context=BIELA_CTX,
                conversation_history=[],
                session={"order_context": {"items": []}},
            )
        assert output["agent_type"] == "order"
        assert output["message"] == ""
        assert output["handoff"]["to"] == "customer_service"
        assert output["handoff"]["context"]["reason"] == "empty_cart_status_query"
        # Only the planner LLM should have run — the response generator
        # must NOT be called on the handoff path.
        assert llm.invoke.call_count == 1

    def test_empty_cart_but_non_status_phrasing_responds_normally(self):
        """User asks 'qué tengo en mi pedido' with empty cart — no handoff;
        just returns the normal empty-cart reply."""
        agent = OrderAgent()
        llm = MagicMock()
        llm.invoke.side_effect = [
            _llm_response('{"intent": "VIEW_CART", "params": {}}'),
            _llm_response("Tu carrito está vacío. ¿Qué te provoca?"),
        ]
        empty_cart_result = {
            "result_kind": "cart_view",
            "success": True,
            "cart_view": {"items": [], "subtotal": 0, "delivery_fee": 0, "total": 0, "is_empty": True},
            "state_after": "GREETING",
            "cart_summary": "Pedido vacío.",
        }
        with patch.object(OrderAgent, "llm", llm), \
             patch.object(OrderAgent, "planner_llm", llm), \
             patch("app.agents.order_agent.execute_order_intent", return_value=empty_cart_result), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"):
            output = agent.execute(
                message_body="qué tengo en mi pedido",
                wa_id="x", name="X",
                business_context=BIELA_CTX,
                conversation_history=[],
                session={"order_context": {"items": []}},
            )
        # No handoff — normal LLM response path ran.
        assert output.get("handoff") is None
        assert "vacío" in output["message"].lower()
        assert llm.invoke.call_count == 2  # planner + response

    def test_non_empty_cart_never_hands_off(self):
        """Status phrasing but cart is active → show cart, don't hand off."""
        agent = OrderAgent()
        llm = MagicMock()
        llm.invoke.side_effect = [
            _llm_response('{"intent": "VIEW_CART", "params": {}}'),
            _llm_response("Tienes una barracuda por $18.000."),
        ]
        non_empty_result = {
            "result_kind": "cart_view",
            "success": True,
            "cart_view": {
                "items": [{"name": "Barracuda", "quantity": 1, "price": 18000, "notes": None}],
                "subtotal": 18000, "delivery_fee": 0, "total": 18000, "is_empty": False,
            },
            "state_after": "ORDERING",
            "cart_summary": "1x Barracuda.",
        }
        with patch.object(OrderAgent, "llm", llm), \
             patch.object(OrderAgent, "planner_llm", llm), \
             patch("app.agents.order_agent.execute_order_intent", return_value=non_empty_result), \
             patch("app.agents.order_agent.conversation_service"), \
             patch("app.agents.order_agent.tracer"):
            output = agent.execute(
                message_body="dónde está mi pedido",
                wa_id="x", name="X",
                business_context=BIELA_CTX,
                conversation_history=[],
                session={"order_context": {"items": [{"name": "Barracuda"}]}},
            )
        assert output.get("handoff") is None
        assert "barracuda" in output["message"].lower()
