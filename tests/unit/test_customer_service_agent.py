"""
Smoke tests for the customer-service agent (tool-calling architecture).

Targets:
  - Sentinel parsing (FINAL / HANDOFF) round-trips cleanly.
  - Each @tool returns the expected sentinel shape for representative
    handler results.
  - Agent fast-paths fire before the LLM loop (order-closed, out-of-zone).
  - Pre-loop safety nets hand off to the order agent for the patterns
    that historically misrouted (price-of-product, despedida post-pedido).
  - cancel_order guard refuses without an explicit keyword AND without
    a cancellable placed order.
  - The dispatch loop terminates on a FINAL sentinel without a second
    LLM iteration.
"""

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from app.agents.customer_service_agent import CustomerServiceAgent
from app.orchestration import customer_service_flow as csf
from app.orchestration.turn_context import TurnContext
from app.services import cs_tools


BIZ_CTX = {
    "business_id": "biz-1",
    "business": {"name": "Biela", "settings": {"hours_text": "Lun-Vie 5PM"}},
}


def _tool_ctx(**overrides):
    """Per-turn context dict the tools read from."""
    base = {
        "wa_id": "+573001234567",
        "business_id": "biz-1",
        "business_context": BIZ_CTX,
        "session": None,
        "turn_ctx": None,
        "message_body": "",
    }
    base.update(overrides)
    return base


def _tool_call(name, args, call_id="c1"):
    return {"name": name, "args": args or {}, "id": call_id, "type": "tool_call"}


# ── Sentinel helpers ───────────────────────────────────────────────────


class TestSentinelHelpers:
    def test_parse_final_strips_prefix(self):
        assert cs_tools.parse_final("FINAL|hola") == "hola"

    def test_parse_final_returns_none_for_non_final(self):
        assert cs_tools.parse_final("HANDOFF|to=order") is None
        assert cs_tools.parse_final("plain text") is None
        assert cs_tools.parse_final("") is None

    def test_parse_handoff_extracts_dict(self):
        out = cs_tools.parse_handoff("HANDOFF|to=order|segment=hi|reason=x")
        assert out == {"to": "order", "segment": "hi", "reason": "x"}

    def test_parse_handoff_returns_none_for_non_handoff(self):
        assert cs_tools.parse_handoff("FINAL|hola") is None
        assert cs_tools.parse_handoff("") is None


# ── Tool wrappers ──────────────────────────────────────────────────────


class TestGetBusinessInfoTool:
    def test_known_field_returns_final_with_template(self):
        # hours uses the `{value}` template (no preamble).
        token = cs_tools.set_tool_context(_tool_ctx())
        try:
            result = cs_tools.get_business_info.invoke({
                "field": "hours",
                "injected_business_context": _tool_ctx(),
            })
        finally:
            cs_tools.reset_tool_context(token)
        assert result.startswith("FINAL|")
        assert "Lun-Vie 5PM" in cs_tools.parse_final(result)

    def test_missing_field_returns_plain_text_for_llm(self):
        # The INFO_MISSING shape is NOT a FINAL sentinel — the LLM has
        # to compose the apology (with the business voice) from it.
        ctx = {
            "business_id": "biz-1",
            "business": {"name": "X", "settings": {}},  # no hours_text
        }
        ictx = _tool_ctx(business_context=ctx)
        token = cs_tools.set_tool_context(ictx)
        try:
            result = cs_tools.get_business_info.invoke({
                "field": "hours",
                "injected_business_context": ictx,
            })
        finally:
            cs_tools.reset_tool_context(token)
        assert not result.startswith("FINAL|")
        assert not result.startswith("HANDOFF|")
        assert "INFO_MISSING" in result


class TestGetOrderStatusTool:
    def test_no_order_returns_final(self):
        with patch.object(csf.order_lookup_service, "get_latest_order", return_value=None):
            ictx = _tool_ctx()
            token = cs_tools.set_tool_context(ictx)
            try:
                result = cs_tools.get_order_status.invoke({
                    "injected_business_context": ictx,
                })
            finally:
                cs_tools.reset_tool_context(token)
        assert result.startswith("FINAL|")
        assert "No tengo registro" in cs_tools.parse_final(result)

    def test_active_cart_returns_handoff_to_order(self):
        ictx = _tool_ctx(session={
            "order_context": {"items": [{"name": "Barracuda", "quantity": 1}]},
        })
        token = cs_tools.set_tool_context(ictx)
        try:
            with patch.object(csf.order_lookup_service, "get_latest_order") as m:
                result = cs_tools.get_order_status.invoke({
                    "injected_business_context": ictx,
                })
                m.assert_not_called()  # short-circuits before DB lookup
        finally:
            cs_tools.reset_tool_context(token)
        parsed = cs_tools.parse_handoff(result)
        assert parsed is not None
        assert parsed["to"] == "order"
        assert parsed["reason"] == "mi_pedido_active_cart"


class TestSelectListedPromoTool:
    def test_unique_match_returns_handoff_with_promo_id(self):
        ictx = _tool_ctx(session={
            "agent_contexts": {"customer_service": {"last_listed_promos": [
                {"id": "p1", "name": "Honey Burger Combo"},
                {"id": "p2", "name": "Familiar"},
            ]}},
        })
        token = cs_tools.set_tool_context(ictx)
        try:
            result = cs_tools.select_listed_promo.invoke({
                "selector": "primera",
                "injected_business_context": ictx,
            })
        finally:
            cs_tools.reset_tool_context(token)
        parsed = cs_tools.parse_handoff(result)
        assert parsed is not None
        assert parsed["to"] == "order"
        assert parsed["promo_id"] == "p1"


# ── Agent fast-paths ───────────────────────────────────────────────────


class TestOrderClosedFastPath:
    def test_renders_deterministic_reply_without_llm(self):
        agent = CustomerServiceAgent()
        with patch(
            "app.agents.customer_service_agent.business_info_service.compute_open_status",
            return_value={
                "has_data": True, "is_open": False,
                "closes_at": None, "opens_at": None,
                "next_open_dow": None, "next_open_time": None,
                "now_local": None,
            },
        ), patch(
            "app.agents.customer_service_agent.business_info_service.format_open_status_sentence",
            return_value="Por ahora estamos cerrados.",
        ), patch(
            "app.agents.customer_service_agent.business_info_service.is_fully_closed_today",
            return_value=False,
        ), patch(
            "app.agents.customer_service_agent.conversation_service.store_conversation_message"
        ), patch.object(
            agent, "_llm",
        ) as fake_llm:
            out = agent.execute(
                message_body="quiero pedir",
                wa_id="+573001234567", name="",
                business_context=BIZ_CTX, conversation_history=[],
                handoff_context={"reason": "order_closed", "has_active_cart": False},
            )
            fake_llm.invoke.assert_not_called()  # fast-path skipped the LLM
        assert "estamos cerrados" in out["message"].lower()


class TestOutOfZoneFastPath:
    def test_renders_redirect_with_city_and_phone(self):
        agent = CustomerServiceAgent()
        with patch(
            "app.agents.customer_service_agent.conversation_service.store_conversation_message"
        ), patch.object(agent, "_llm") as fake_llm:
            out = agent.execute(
                message_body="quiero a Medellín",
                wa_id="+573001234567", name="",
                business_context=BIZ_CTX, conversation_history=[],
                handoff_context={
                    "reason": "out_of_zone",
                    "city": "Medellín",
                    "phone": "+573001112233",
                },
            )
            fake_llm.invoke.assert_not_called()
        assert "Medellín" in out["message"]
        assert "+573001112233" in out["message"]


class TestCancelOrderGuard:
    def test_refuses_without_explicit_keyword(self):
        agent = CustomerServiceAgent()
        # turn_ctx has a cancellable order — only the keyword guard should fire.
        tctx = TurnContext(has_recent_cancellable_order=True)
        out = agent._cancel_order_guard("gracias bro", tctx)
        assert out is not None
        assert "no_cancel_keyword" in out

    def test_refuses_without_cancellable_order(self):
        agent = CustomerServiceAgent()
        tctx = TurnContext(has_recent_cancellable_order=False)
        # Real cancel verb but nothing to cancel.
        out = agent._cancel_order_guard("cancela mi pedido", tctx)
        assert out is not None
        assert "no_cancellable_order" in out

    def test_passes_when_both_conditions_hold(self):
        agent = CustomerServiceAgent()
        tctx = TurnContext(has_recent_cancellable_order=True)
        out = agent._cancel_order_guard("cancela mi pedido", tctx)
        assert out is None


class TestDespedidaSafetyNet:
    def test_post_order_gracias_hands_off_to_order(self):
        agent = CustomerServiceAgent()
        # latest_order_status set — turn is right after place_order.
        tctx = TurnContext(latest_order_status="confirmed")
        out = agent._pre_loop_safety_nets("gracias", BIZ_CTX, tctx)
        assert out is not None
        assert out["handoff"]["to"] == "order"
        assert out["handoff"]["context"]["reason"] == "despedida_post_pedido_misroute"

    def test_no_latest_status_means_no_handoff(self):
        agent = CustomerServiceAgent()
        # No placed order in turn_ctx — plain "gracias" is just chat.
        tctx = TurnContext(latest_order_status=None)
        # Mute the other safety-net branches by patching the helpers
        # they call so we isolate the despedida check.
        with patch("app.orchestration.router._deterministic_price_of_product", return_value=False):
            out = agent._pre_loop_safety_nets("gracias", BIZ_CTX, tctx)
        assert out is None


# ── Dispatch loop ──────────────────────────────────────────────────────


class TestDispatchLoopTerminatesOnFinal:
    def test_final_sentinel_returns_text_without_second_iteration(self):
        agent = CustomerServiceAgent()
        # Model calls get_business_info once. The tool returns a FINAL
        # sentinel, so the loop must terminate immediately — the LLM
        # is NOT invoked a second time.
        tool_call_msg = AIMessage(
            content="",
            tool_calls=[_tool_call("get_business_info", {"field": "hours"})],
        )
        with patch.object(agent, "_llm") as fake_llm, patch(
            "app.agents.customer_service_agent.conversation_service.store_conversation_message"
        ), patch(
            "app.orchestration.router._deterministic_price_of_product", return_value=False,
        ), patch(
            "app.orchestration.router._expand_stuck_articles", side_effect=lambda m, _l: m,
        ):
            fake_llm.invoke.return_value = tool_call_msg
            out = agent.execute(
                message_body="a qué hora abren",
                wa_id="+573001234567", name="",
                business_context=BIZ_CTX, conversation_history=[],
            )
            # Exactly ONE iteration: tool ran, FINAL sentinel terminated the loop.
            assert fake_llm.invoke.call_count == 1
        assert out["message"]  # final text was set
        assert "Lun-Vie 5PM" in out["message"]
        assert out["state_update"] == {"active_agents": ["customer_service"]}
