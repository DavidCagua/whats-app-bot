"""
Unit tests for the v2 (tool-calling) order agent's availability gate.

Mirror of v1's behavior in
``app/orchestration/order_flow.ORDER_MUTATING_INTENTS`` — when the
business is closed (no ``business_availability`` row covers the current
Bogotá time), mutating tool calls (add_to_cart, place_order, etc.) are
intercepted and the turn hands off to ``customer_service`` with
``reason='order_closed'``. Browse / read-only tools (menu lookups,
product search, view_cart, get_customer_info) pass through so
customers can still read the menu while the shop is closed.

Coverage:
- ``MUTATING_TOOL_NAMES`` is the canonical set the v2 agent reads.
- Gate closed + mutating tool → handoff to CS, no tool ran, cart
  preserved.
- Gate closed + browse tool → tool runs normally, no handoff.
- Gate open → mutating tool runs normally.
- Gate disabled (``settings.order_gate_enabled = False``) → no gating.
- ``awaiting_confirmation`` is disarmed when the gate fires (so a CS
  follow-up doesn't accidentally trip place_order).
"""

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agents.order_agent_tool_calling import OrderAgentToolCalling
from app.orchestration.turn_context import TurnContext


BIELA_CTX = {
    "business_id": "biela-eval",
    "business": {"name": "Biela", "settings": {}},
}

BIELA_CTX_GATE_DISABLED = {
    "business_id": "biela-eval",
    "business": {
        "name": "Biela",
        "settings": {"order_gate_enabled": False},
    },
}


def _ai_with_tools(tool_calls):
    return AIMessage(content="", tool_calls=tool_calls)


def _stub_turn_context(**overrides):
    """Minimal TurnContext for tests, mirrors test_order_agent_tool_calling
    so we can patch ``build_turn_context`` to return a deterministic
    snapshot."""
    has_active_cart = overrides.pop("has_active_cart", False)
    cart_summary = overrides.pop("cart_summary", "")
    delivery_info = overrides.pop("delivery_info", None) or {}
    awaiting_confirmation = overrides.pop("awaiting_confirmation", False)
    order_state = overrides.pop("order_state", None) or (
        "ORDERING" if has_active_cart else "GREETING"
    )
    return TurnContext(
        order_state=order_state,
        has_active_cart=has_active_cart,
        cart_summary=cart_summary,
        delivery_info=delivery_info,
        awaiting_confirmation=awaiting_confirmation,
    )


_GATE_OPEN = {"can_take_orders": True, "reason": "open"}
_GATE_CLOSED = {
    "can_take_orders": False,
    "reason": "closed",
    "opens_at": None,
    "next_open_dow": 1,
    "next_open_time": None,
    "now_local": None,
}


# ---------------------------------------------------------------------------
# MUTATING_TOOL_NAMES is the canonical set
# ---------------------------------------------------------------------------

class TestMutatingToolNames:
    def test_mutating_set_matches_v1_intents_one_to_one(self):
        from app.services.order_tools import MUTATING_TOOL_NAMES
        assert MUTATING_TOOL_NAMES == frozenset({
            "add_to_cart",
            "add_promo_to_cart",
            "update_cart_item",
            "remove_from_cart",
            "submit_delivery_info",
            "place_order",
        })

    def test_browse_tools_not_in_set(self):
        """Read-only tools must be allowed even when the shop is closed."""
        from app.services.order_tools import MUTATING_TOOL_NAMES
        for browse_tool in (
            "get_menu_categories",
            "list_category_products",
            "search_products",
            "get_product_details",
            "view_cart",
            "get_customer_info",
        ):
            assert browse_tool not in MUTATING_TOOL_NAMES, (
                f"{browse_tool} should NOT be gated — customers must still "
                "read the menu while the shop is closed"
            )


# ---------------------------------------------------------------------------
# Gate closed + mutating tool → handoff
# ---------------------------------------------------------------------------

class TestGateClosedBlocksMutating:
    def test_closed_no_cart_short_circuits_before_llm(self):
        """Closed + no active cart → short-circuit handoff to CS BEFORE
        the LLM is invoked. blocked_intents is empty because no tools
        ever got dispatched. Replaces the older test that asserted
        post-LLM blocked_intents — order openers like "quiero una
        barracuda" can now slip the planner without calling a mutating
        tool, so the only reliable gate is the pre-LLM short-circuit."""
        agent = OrderAgentToolCalling()
        llm = MagicMock()

        with patch.object(OrderAgentToolCalling, "llm", llm), \
             patch("app.agents.order_agent_tool_calling.conversation_service"), \
             patch("app.agents.order_agent_tool_calling.tracer"), \
             patch(
                 "app.agents.order_agent_tool_calling.build_turn_context",
                 return_value=_stub_turn_context(),
             ), \
             patch(
                 "app.services.business_info_service.is_taking_orders_now",
                 return_value=_GATE_CLOSED,
             ):
            output = agent.execute(
                message_body="quiero una barracuda",
                wa_id="+57300", name="X",
                business_context=BIELA_CTX,
                conversation_history=[],
            )

        # LLM must NOT have been invoked.
        llm.invoke.assert_not_called()
        assert output["agent_type"] == "order"
        assert output["message"] == ""
        hand = output.get("handoff") or {}
        assert hand.get("to") == "customer_service"
        ctx = hand.get("context") or {}
        assert ctx.get("reason") == "order_closed"
        assert ctx.get("has_active_cart") is False

    def test_place_order_blocked_returns_handoff(self):
        agent = OrderAgentToolCalling()
        only = _ai_with_tools([{
            "name": "place_order", "args": {}, "id": "c1", "type": "tool_call",
        }])
        llm = MagicMock()
        llm.invoke.return_value = only

        with patch.object(OrderAgentToolCalling, "llm", llm), \
             patch("app.agents.order_agent_tool_calling.conversation_service"), \
             patch("app.agents.order_agent_tool_calling.tracer"), \
             patch(
                 "app.agents.order_agent_tool_calling.build_turn_context",
                 return_value=_stub_turn_context(has_active_cart=True),
             ), \
             patch(
                 "app.services.business_info_service.is_taking_orders_now",
                 return_value=_GATE_CLOSED,
             ):
            output = agent.execute(
                message_body="confirma",
                wa_id="+57300", name="X",
                business_context=BIELA_CTX,
                conversation_history=[],
            )
        hand = output.get("handoff") or {}
        assert hand.get("to") == "customer_service"
        # has_active_cart=True propagates so CS announces "your cart is
        # saved, we'll resume when we open."
        assert hand.get("context", {}).get("has_active_cart") is True

    def test_blocked_intents_with_active_cart_lists_every_mutating_call(self):
        """When the cart already has items, the pre-LLM short-circuit
        does NOT fire (returning customer; they may want VIEW_CART).
        The in-loop gate is the safety net here and it lists every
        mutating tool emitted that turn so the CS message references
        all of them."""
        agent = OrderAgentToolCalling()
        only = _ai_with_tools([
            {"name": "add_to_cart", "args": {"product_name": "X"},
             "id": "c1", "type": "tool_call"},
            {"name": "submit_delivery_info", "args": {"name": "Camilo"},
             "id": "c2", "type": "tool_call"},
            {"name": "view_cart", "args": {}, "id": "c3", "type": "tool_call"},
        ])
        llm = MagicMock()
        llm.invoke.return_value = only

        with patch.object(OrderAgentToolCalling, "llm", llm), \
             patch("app.agents.order_agent_tool_calling.conversation_service"), \
             patch("app.agents.order_agent_tool_calling.tracer"), \
             patch(
                 "app.agents.order_agent_tool_calling.build_turn_context",
                 return_value=_stub_turn_context(has_active_cart=True),
             ), \
             patch(
                 "app.services.business_info_service.is_taking_orders_now",
                 return_value=_GATE_CLOSED,
             ):
            output = agent.execute(
                message_body="X y soy Camilo",
                wa_id="+57300", name="X",
                business_context=BIELA_CTX,
                conversation_history=[],
            )

        blocked = (output.get("handoff") or {}).get("context", {}).get("blocked_intents") or []
        # Both mutating calls reported; view_cart (browse) NOT included.
        assert "add_to_cart" in blocked
        assert "submit_delivery_info" in blocked
        assert "view_cart" not in blocked

    def test_awaiting_confirmation_is_disarmed_on_gate_block(self):
        """If a stale confirm flag is set when the gate fires, it must
        be cleared so the eventual CS follow-up doesn't trip place_order."""
        agent = OrderAgentToolCalling()
        only = _ai_with_tools([{
            "name": "place_order", "args": {}, "id": "c1", "type": "tool_call",
        }])
        llm = MagicMock()
        llm.invoke.return_value = only

        with patch.object(OrderAgentToolCalling, "llm", llm), \
             patch("app.agents.order_agent_tool_calling.conversation_service"), \
             patch("app.agents.order_agent_tool_calling.tracer"), \
             patch(
                 "app.agents.order_agent_tool_calling.build_turn_context",
                 return_value=_stub_turn_context(
                     has_active_cart=True, awaiting_confirmation=True,
                 ),
             ), \
             patch(
                 "app.services.business_info_service.is_taking_orders_now",
                 return_value=_GATE_CLOSED,
             ), \
             patch(
                 "app.agents.order_agent_tool_calling.set_awaiting_confirmation",
             ) as set_flag:
            agent.execute(
                message_body="sí",
                wa_id="+57300", name="X",
                business_context=BIELA_CTX,
                conversation_history=[],
            )
        # Disarmed: the only call should set the flag to False.
        set_flag.assert_called_with("+57300", "biela-eval", False)


# ---------------------------------------------------------------------------
# Gate closed + browse tool → tool runs, no handoff
# ---------------------------------------------------------------------------

class TestGateClosedAllowsBrowseWithActiveCart:
    """With an active cart, the closed-shop short-circuit does NOT fire
    (returning customer; we don't want to handoff every browse). Browse
    tools (view_cart, search_products) run normally so the customer can
    inspect what they have and read the menu while waiting to reopen."""

    def test_view_cart_runs_normally_with_active_cart_on_closed_shop(self):
        agent = OrderAgentToolCalling()
        first = _ai_with_tools([{
            "name": "view_cart", "args": {}, "id": "c1", "type": "tool_call",
        }])
        second = _ai_with_tools([{
            "name": "respond",
            "args": {"kind": "cart_view", "summary": "Tu carrito tiene 1 ítem."},
            "id": "r1", "type": "tool_call",
        }])
        llm = MagicMock()
        llm.invoke.side_effect = [first, second]

        with patch.object(OrderAgentToolCalling, "llm", llm), \
             patch("app.agents.order_agent_tool_calling.conversation_service"), \
             patch("app.agents.order_agent_tool_calling.tracer"), \
             patch(
                 "app.agents.order_agent_tool_calling.build_turn_context",
                 return_value=_stub_turn_context(has_active_cart=True),
             ), \
             patch(
                 "app.services.business_info_service.is_taking_orders_now",
                 return_value=_GATE_CLOSED,
             ), \
             patch(
                 "app.agents.order_agent_tool_calling.render_response",
                 return_value={"type": "text", "body": "Tu carrito tiene 1 ítem."},
             ):
            output = agent.execute(
                message_body="qué tengo",
                wa_id="+57300", name="X",
                business_context=BIELA_CTX,
                conversation_history=[],
            )

        # No handoff fired; the turn ran to its renderer.
        assert "handoff" not in output or not output["handoff"]
        assert "ítem" in output.get("message", "").lower() or "item" in output.get("message", "").lower()

    def test_search_products_runs_normally_with_active_cart_on_closed_shop(self):
        agent = OrderAgentToolCalling()
        first = _ai_with_tools([{
            "name": "search_products", "args": {"query": "barracuda"},
            "id": "c1", "type": "tool_call",
        }])
        second = _ai_with_tools([{
            "name": "respond",
            "args": {"kind": "product_info", "summary": "BARRACUDA está en el menú"},
            "id": "r1", "type": "tool_call",
        }])
        llm = MagicMock()
        llm.invoke.side_effect = [first, second]

        with patch.object(OrderAgentToolCalling, "llm", llm), \
             patch("app.agents.order_agent_tool_calling.conversation_service"), \
             patch("app.agents.order_agent_tool_calling.tracer"), \
             patch(
                 "app.agents.order_agent_tool_calling.build_turn_context",
                 return_value=_stub_turn_context(has_active_cart=True),
             ), \
             patch(
                 "app.services.business_info_service.is_taking_orders_now",
                 return_value=_GATE_CLOSED,
             ), \
             patch(
                 "app.agents.order_agent_tool_calling.render_response",
                 return_value={"type": "text", "body": "BARRACUDA está en el menú"},
             ):
            output = agent.execute(
                message_body="tienes barracuda?",
                wa_id="+57300", name="X",
                business_context=BIELA_CTX,
                conversation_history=[],
            )
        assert "handoff" not in output or not output["handoff"]

    def test_no_cart_browse_intent_short_circuits_to_cs(self):
        """No active cart + closed shop: even a pure browse message
        ("tienes barracuda?") short-circuits to CS. Surfacing menu
        details encourages building a cart that fails at submit time
        (incident +573172908887, 2026-05-11). CS still answers menu
        URL on demand."""
        agent = OrderAgentToolCalling()
        llm = MagicMock()

        with patch.object(OrderAgentToolCalling, "llm", llm), \
             patch("app.agents.order_agent_tool_calling.conversation_service"), \
             patch("app.agents.order_agent_tool_calling.tracer"), \
             patch(
                 "app.agents.order_agent_tool_calling.build_turn_context",
                 return_value=_stub_turn_context(),
             ), \
             patch(
                 "app.services.business_info_service.is_taking_orders_now",
                 return_value=_GATE_CLOSED,
             ):
            output = agent.execute(
                message_body="tienes barracuda?",
                wa_id="+57300", name="X",
                business_context=BIELA_CTX,
                conversation_history=[],
            )

        llm.invoke.assert_not_called()
        hand = output.get("handoff") or {}
        assert hand.get("to") == "customer_service"
        assert (hand.get("context") or {}).get("reason") == "order_closed"


# ---------------------------------------------------------------------------
# Gate open / no_data / disabled → no gating
# ---------------------------------------------------------------------------

class TestGateOpenOrDisabled:
    def test_gate_open_allows_mutating(self):
        agent = OrderAgentToolCalling()
        first = _ai_with_tools([{
            "name": "add_to_cart", "args": {"product_name": "X"},
            "id": "c1", "type": "tool_call",
        }])
        second = _ai_with_tools([{
            "name": "respond",
            "args": {"kind": "items_added", "summary": "Listo"},
            "id": "r1", "type": "tool_call",
        }])
        llm = MagicMock()
        llm.invoke.side_effect = [first, second]

        with patch.object(OrderAgentToolCalling, "llm", llm), \
             patch("app.agents.order_agent_tool_calling.conversation_service"), \
             patch("app.agents.order_agent_tool_calling.tracer"), \
             patch(
                 "app.agents.order_agent_tool_calling.build_turn_context",
                 return_value=_stub_turn_context(),
             ), \
             patch(
                 "app.services.business_info_service.is_taking_orders_now",
                 return_value=_GATE_OPEN,
             ), \
             patch(
                 "app.agents.order_agent_tool_calling.render_response",
                 return_value={"type": "text", "body": "Listo"},
             ):
            output = agent.execute(
                message_body="una X",
                wa_id="+57300", name="X",
                business_context=BIELA_CTX,
                conversation_history=[],
            )
        assert "handoff" not in output or not output["handoff"]
        assert output.get("message") == "Listo"

    def test_gate_disabled_via_settings_skips_compute(self):
        """``business.settings.order_gate_enabled = False`` opts out
        entirely. is_taking_orders_now must NOT be called."""
        agent = OrderAgentToolCalling()
        first = _ai_with_tools([{
            "name": "add_to_cart", "args": {"product_name": "X"},
            "id": "c1", "type": "tool_call",
        }])
        second = _ai_with_tools([{
            "name": "respond",
            "args": {"kind": "items_added", "summary": "Listo"},
            "id": "r1", "type": "tool_call",
        }])
        llm = MagicMock()
        llm.invoke.side_effect = [first, second]

        gate_fn = MagicMock()
        with patch.object(OrderAgentToolCalling, "llm", llm), \
             patch("app.agents.order_agent_tool_calling.conversation_service"), \
             patch("app.agents.order_agent_tool_calling.tracer"), \
             patch(
                 "app.agents.order_agent_tool_calling.build_turn_context",
                 return_value=_stub_turn_context(),
             ), \
             patch(
                 "app.services.business_info_service.is_taking_orders_now",
                 gate_fn,
             ), \
             patch(
                 "app.agents.order_agent_tool_calling.render_response",
                 return_value={"type": "text", "body": "Listo"},
             ):
            output = agent.execute(
                message_body="una X",
                wa_id="+57300", name="X",
                business_context=BIELA_CTX_GATE_DISABLED,
                conversation_history=[],
            )
        gate_fn.assert_not_called()
        assert "handoff" not in output or not output["handoff"]
