"""
Unit tests for app/orchestration/turn_context.py — focus on the
``latest_order_status`` staleness rule and the prompt rendering.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.orchestration import turn_context as tc
from app.orchestration.turn_context import TurnContext, render_for_prompt


def _iso_minutes_ago(n: int) -> str:
    return (datetime.now(tz=timezone.utc) - timedelta(minutes=n)).isoformat()


class TestLatestOrderRelevance:
    """``_latest_order_is_relevant`` decides which orders surface in the prompt."""

    @pytest.mark.parametrize("status", ["pending", "confirmed", "out_for_delivery"])
    def test_active_states_always_relevant(self, status):
        # Even with a stale created_at, active orders are always relevant —
        # a customer may legitimately ask about delivery hours later.
        order = {"status": status, "completed_at": None, "cancelled_at": None}
        assert tc._latest_order_is_relevant(order) is True

    @pytest.mark.parametrize("status,ts_field", [
        ("completed", "completed_at"),
        ("cancelled", "cancelled_at"),
    ])
    def test_terminal_states_relevant_within_window(self, status, ts_field):
        order = {"status": status, ts_field: _iso_minutes_ago(10)}
        assert tc._latest_order_is_relevant(order) is True

    @pytest.mark.parametrize("status,ts_field", [
        ("completed", "completed_at"),
        ("cancelled", "cancelled_at"),
    ])
    def test_terminal_states_excluded_past_window(self, status, ts_field):
        order = {"status": status, ts_field: _iso_minutes_ago(120)}  # 2h old
        assert tc._latest_order_is_relevant(order) is False

    @pytest.mark.parametrize("status", ["completed", "cancelled"])
    def test_terminal_state_without_timestamp_excluded(self, status):
        # No timestamp = conservatively drop. A terminal status without
        # the corresponding timestamp is unusual — better to fall back to
        # the no-recent-order default.
        assert tc._latest_order_is_relevant({"status": status}) is False

    def test_unknown_status_excluded(self):
        assert tc._latest_order_is_relevant({"status": ""}) is False
        assert tc._latest_order_is_relevant({}) is False

    def test_z_suffix_iso_parsed(self):
        # PG sometimes returns "Z" instead of "+00:00". The parser must accept both.
        order = {
            "status": "completed",
            "completed_at": (datetime.now(tz=timezone.utc) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        }
        assert tc._latest_order_is_relevant(order) is True

    def test_naive_datetime_assumed_utc(self):
        # If a datetime instance arrives without tzinfo, assume UTC and check the window.
        order = {
            "status": "completed",
            "completed_at": datetime.utcnow() - timedelta(minutes=5),
        }
        assert tc._latest_order_is_relevant(order) is True


class TestRecentHistoryRendering:
    """``recent_history`` should produce a uniform multi-turn block visible to every layer."""

    def test_renders_multi_turn_block_when_set(self):
        ctx = TurnContext(
            order_state="ORDERING",
            has_active_cart=True,
            cart_summary="1x BARRACUDA",
            recent_history=(
                ("user", "necesito una barracuda sin queso azul"),
                ("assistant", "Listo, hemos agregado la BARRACUDA... procedemos con el pedido?"),
                ("user", "porfsvor"),
            ),
        )
        out = render_for_prompt(ctx)
        assert "Historial reciente" in out
        # All three turns surface.
        assert "necesito una barracuda" in out
        assert "procedemos con el pedido" in out
        assert "porfsvor" in out
        # Role labels are humanized.
        assert "usuario:" in out
        assert "bot:" in out

    def test_falls_back_to_last_assistant_when_history_empty(self):
        # Backward-compat: callers that build TurnContext by hand and only
        # set last_assistant_message still get the legacy single-line form.
        ctx = TurnContext(last_assistant_message="¿procedemos con el pedido?")
        out = render_for_prompt(ctx)
        assert "Historial reciente" not in out
        assert "Última respuesta del bot" in out
        assert "procedemos" in out

    def test_empty_history_omits_block(self):
        ctx = TurnContext()
        out = render_for_prompt(ctx)
        assert "Historial reciente" not in out
        assert "Última respuesta del bot" not in out


class TestBuildTurnContextHistoryUniformity:
    """``build_turn_context`` must populate recent_history to ≤ 10 turns."""

    def _patch_no_orders(self):
        return patch(
            "app.orchestration.turn_context.order_lookup_service.get_latest_order",
            return_value=None,
        )

    def _patch_session(self):
        return patch(
            "app.orchestration.turn_context.session_state_service.load",
            return_value={"session": {}},
        )

    def test_history_capped_at_10_messages(self):
        # Even if the loader returns more, the recent_history field must
        # have at most _HISTORY_MAX_MESSAGES entries.
        long_history = [
            {"role": "user" if i % 2 == 0 else "assistant", "message": f"msg-{i}"}
            for i in range(20)
        ]
        with self._patch_session(), self._patch_no_orders(), patch(
            "app.orchestration.turn_context.conversation_service.get_conversation_history",
            return_value=long_history[:10],  # the loader is the one capped via limit=
        ) as history_mock:
            ctx = tc.build_turn_context(wa_id="+1", business_id="biz")
        # Loader called with limit=10.
        history_mock.assert_called_once()
        _, kwargs = history_mock.call_args
        assert kwargs.get("limit") == 10
        assert len(ctx.recent_history) == 10

    def test_each_message_truncated_to_max_chars(self):
        long_msg = "x" * 1000
        with self._patch_session(), self._patch_no_orders(), patch(
            "app.orchestration.turn_context.conversation_service.get_conversation_history",
            return_value=[{"role": "assistant", "message": long_msg}],
        ):
            ctx = tc.build_turn_context(wa_id="+1", business_id="biz")
        assert len(ctx.recent_history) == 1
        role, msg = ctx.recent_history[0]
        # 240 chars + ellipsis marker.
        assert len(msg) <= 241  # 240 + "…"
        assert msg.endswith("…")

    def test_last_assistant_message_still_populated_for_backcompat(self):
        with self._patch_session(), self._patch_no_orders(), patch(
            "app.orchestration.turn_context.conversation_service.get_conversation_history",
            return_value=[
                {"role": "user", "message": "hola"},
                {"role": "assistant", "message": "Hola David, en qué te ayudo?"},
                {"role": "user", "message": "barracuda"},
                {"role": "assistant", "message": "¿procedemos con el pedido?"},
            ],
        ):
            ctx = tc.build_turn_context(wa_id="+1", business_id="biz")
        # Most recent assistant turn lands in last_assistant_message.
        assert ctx.last_assistant_message == "¿procedemos con el pedido?"
        # And the full history is preserved in recent_history.
        assert len(ctx.recent_history) == 4

    def test_blank_role_or_message_skipped(self):
        with self._patch_session(), self._patch_no_orders(), patch(
            "app.orchestration.turn_context.conversation_service.get_conversation_history",
            return_value=[
                {"role": "", "message": "noop"},
                {"role": "user", "message": ""},
                {"role": "user", "message": "hola"},
            ],
        ):
            ctx = tc.build_turn_context(wa_id="+1", business_id="biz")
        assert ctx.recent_history == (("user", "hola"),)


class TestRenderForPromptLatestOrderLine:
    def test_emits_latest_order_line_when_status_set(self):
        ctx = TurnContext(latest_order_status="confirmed", latest_order_id="abc")
        out = render_for_prompt(ctx)
        # Match by ASCII tail to dodge any utf-8 surprises.
        assert "estado): confirmed" in out

    def test_omits_line_when_status_none(self):
        ctx = TurnContext()
        out = render_for_prompt(ctx)
        assert "estado): " not in out
        assert "stado): " not in out  # paranoia

    @pytest.mark.parametrize("status", [
        "pending", "confirmed", "out_for_delivery", "completed", "cancelled",
    ])
    def test_renders_each_supported_status(self, status):
        ctx = TurnContext(latest_order_status=status)
        out = render_for_prompt(ctx)
        assert f"estado): {status}" in out


class TestBuildTurnContextPopulatesLatestOrder:
    """``build_turn_context`` should hand back the lifecycle fields populated."""

    def _patch_session(self):
        return patch(
            "app.orchestration.turn_context.session_state_service.load",
            return_value={"session": {}},
        )

    def _patch_history(self):
        return patch(
            "app.orchestration.turn_context.conversation_service.get_conversation_history",
            return_value=[],
        )

    def test_active_order_populates_latest_status(self):
        order = {
            "id": "ord-1",
            "status": "confirmed",
            "confirmed_at": _iso_minutes_ago(2),
        }
        with self._patch_session(), self._patch_history(), patch(
            "app.orchestration.turn_context.order_lookup_service.get_latest_order",
            return_value=order,
        ):
            ctx = tc.build_turn_context(wa_id="+1", business_id="biz")
        assert ctx.latest_order_status == "confirmed"
        assert ctx.latest_order_id == "ord-1"

    def test_old_completed_order_is_dropped(self):
        order = {
            "id": "ord-1",
            "status": "completed",
            "completed_at": _iso_minutes_ago(120),
        }
        with self._patch_session(), self._patch_history(), patch(
            "app.orchestration.turn_context.order_lookup_service.get_latest_order",
            return_value=order,
        ):
            ctx = tc.build_turn_context(wa_id="+1", business_id="biz")
        # Old terminal orders MUST NOT bias the prompt.
        assert ctx.latest_order_status is None
        assert ctx.latest_order_id is None

    def test_no_order_yields_none(self):
        with self._patch_session(), self._patch_history(), patch(
            "app.orchestration.turn_context.order_lookup_service.get_latest_order",
            return_value=None,
        ):
            ctx = tc.build_turn_context(wa_id="+1", business_id="biz")
        assert ctx.latest_order_status is None
        assert ctx.latest_order_id is None

    def test_lookup_failure_yields_none(self):
        with self._patch_session(), self._patch_history(), patch(
            "app.orchestration.turn_context.order_lookup_service.get_latest_order",
            side_effect=RuntimeError("db down"),
        ):
            ctx = tc.build_turn_context(wa_id="+1", business_id="biz")
        assert ctx.latest_order_status is None
        assert ctx.latest_order_id is None
