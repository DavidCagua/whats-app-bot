"""Unit tests for app/orchestration/dispatcher.py."""

from unittest.mock import patch, MagicMock

import pytest

from app.orchestration import dispatcher


BIZ_CTX = {"business_id": "biz-1", "business": {"name": "Biela", "settings": {}}}


def _agent_output(agent_type: str, message: str = "", state_update: dict = None, handoff: dict = None):
    out = {
        "agent_type": agent_type,
        "message": message,
        "state_update": state_update or {},
    }
    if handoff is not None:
        out["handoff"] = handoff
    return out


class TestSingleSegment:
    def test_runs_one_agent_returns_its_message(self):
        with patch.object(dispatcher, "execute_agent") as m, \
             patch.object(dispatcher, "_persist_state_update"):
            m.return_value = _agent_output("order", "Agregué una barracuda")
            result = dispatcher.dispatch(
                [("order", "quiero una barracuda")],
                wa_id="w", name="n", business_context=BIZ_CTX,
            )
        assert result.message == "Agregué una barracuda"
        assert result.handoff_chain == ["order"]
        assert len(result.agent_outputs) == 1

    def test_composer_skipped_for_single_output(self):
        # Spy on compose to confirm it isn't called when len==1.
        with patch.object(dispatcher, "execute_agent") as m_agent, \
             patch.object(dispatcher, "_persist_state_update"), \
             patch("app.orchestration.response_composer.compose") as m_compose:
            m_agent.return_value = _agent_output("order", "ok")
            dispatcher.dispatch(
                [("order", "x")], wa_id="w", name="n", business_context=BIZ_CTX,
            )
            m_compose.assert_not_called()

    def test_empty_segments_returns_empty_result(self):
        result = dispatcher.dispatch([], wa_id="w", name="n", business_context=BIZ_CTX)
        assert result.message == ""
        assert result.agent_outputs == []

    def test_agent_exception_returns_error_output(self):
        with patch.object(dispatcher, "execute_agent", side_effect=RuntimeError("boom")), \
             patch.object(dispatcher, "_persist_state_update"):
            result = dispatcher.dispatch(
                [("order", "x")], wa_id="w", name="n", business_context=BIZ_CTX,
            )
        # Dispatcher wraps the crash with an error_output (empty message).
        assert result.message == ""
        assert len(result.agent_outputs) == 1
        assert result.agent_outputs[0].get("error") == "boom"


class TestMultiSegmentNoHandoff:
    def test_two_agents_runs_in_order_composes(self):
        outputs = iter([
            _agent_output("order", "Agregué una barracuda."),
            _agent_output("customer_service", "Mañana abrimos a las 5:30 PM."),
        ])
        with patch.object(dispatcher, "execute_agent", side_effect=lambda **_: next(outputs)), \
             patch.object(dispatcher, "_persist_state_update"), \
             patch("app.orchestration.response_composer.compose", return_value="Listo. Mañana abrimos.") as m_compose:
            result = dispatcher.dispatch(
                [("order", "barracuda"), ("customer_service", "a qué hora abren mañana")],
                wa_id="w", name="n", business_context=BIZ_CTX,
            )
        assert result.handoff_chain == ["order", "customer_service"]
        assert result.message == "Listo. Mañana abrimos."
        m_compose.assert_called_once()
        args = m_compose.call_args[0][0]
        assert args == ["Agregué una barracuda.", "Mañana abrimos a las 5:30 PM."]


class TestHandoffChain:
    def test_agent_a_hands_off_to_b(self):
        a_output = _agent_output(
            "booking", "Reserva confirmada.",
            handoff={"to": "order", "segment": "pre-orden de 2 barracudas", "context": {"booking_id": "b1"}},
        )
        b_output = _agent_output("order", "Pre-orden vinculada a la reserva.")
        outputs = iter([a_output, b_output])
        with patch.object(dispatcher, "execute_agent", side_effect=lambda **_: next(outputs)) as m, \
             patch.object(dispatcher, "_persist_state_update"), \
             patch("app.orchestration.response_composer.compose", return_value="MERGED"):
            result = dispatcher.dispatch(
                [("booking", "reserva + pedido")],
                wa_id="w", name="n", business_context=BIZ_CTX,
            )
        assert result.handoff_chain == ["booking", "order"]
        # Verify the handoff invocation passed handoff_context through.
        second_call_kwargs = m.call_args_list[1].kwargs
        assert second_call_kwargs["agent_type"] == "order"
        assert second_call_kwargs["handoff_context"] == {"booking_id": "b1"}
        assert second_call_kwargs["message_body"] == "pre-orden de 2 barracudas"

    def test_cycle_rejected(self):
        # A hands off to B, B tries to hand back to A → rejected.
        a_out_first = _agent_output(
            "order", "step 1",
            handoff={"to": "customer_service", "segment": "info", "context": {}},
        )
        cs_out = _agent_output(
            "customer_service", "step 2",
            handoff={"to": "order", "segment": "again", "context": {}},
        )
        outputs = iter([a_out_first, cs_out])
        with patch.object(dispatcher, "execute_agent", side_effect=lambda **_: next(outputs)) as m, \
             patch.object(dispatcher, "_persist_state_update"), \
             patch("app.orchestration.response_composer.compose", return_value="M"):
            result = dispatcher.dispatch(
                [("order", "x")], wa_id="w", name="n", business_context=BIZ_CTX,
            )
        assert result.handoff_chain == ["order", "customer_service"]  # cycle blocked
        assert m.call_count == 2  # no third invocation

    def test_max_hops_caps_chain(self):
        # Every agent hands off forever — chain should stop at MAX_HOPS.
        def never_ending(**kwargs):
            idx = never_ending.counter
            never_ending.counter += 1
            return _agent_output(
                f"agent_{idx}", f"msg {idx}",
                handoff={"to": f"agent_{idx+1}", "segment": "x", "context": {}},
            )
        never_ending.counter = 0

        with patch.object(dispatcher, "execute_agent", side_effect=never_ending), \
             patch.object(dispatcher, "_persist_state_update"), \
             patch("app.orchestration.response_composer.compose", return_value="M"):
            result = dispatcher.dispatch(
                [("agent_0", "x")], wa_id="w", name="n", business_context=BIZ_CTX,
            )
        assert len(result.handoff_chain) == dispatcher.MAX_HOPS

    def test_handoff_without_to_field_ignored(self):
        bad = _agent_output("order", "ok", handoff={"segment": "x", "context": {}})  # no "to"
        with patch.object(dispatcher, "execute_agent", return_value=bad), \
             patch.object(dispatcher, "_persist_state_update"):
            result = dispatcher.dispatch(
                [("order", "x")], wa_id="w", name="n", business_context=BIZ_CTX,
            )
        assert result.handoff_chain == ["order"]
        assert result.message == "ok"


class TestAbortDuringDispatch:
    def test_abort_before_first_agent_returns_empty(self):
        with patch.object(dispatcher, "_is_aborted", return_value=True), \
             patch.object(dispatcher, "execute_agent") as m:
            result = dispatcher.dispatch(
                [("order", "x")],
                wa_id="w", name="n", business_context=BIZ_CTX,
                abort_key="abort:biz:phone",
            )
            m.assert_not_called()
        assert result.aborted is True
        assert result.message == ""

    def test_abort_between_segments_stops_dispatch(self):
        aborted_sequence = iter([False, True])
        with patch.object(dispatcher, "_is_aborted", side_effect=lambda k: next(aborted_sequence)), \
             patch.object(dispatcher, "execute_agent", return_value=_agent_output("order", "ok")), \
             patch.object(dispatcher, "_persist_state_update"):
            result = dispatcher.dispatch(
                [("order", "x"), ("customer_service", "y")],
                wa_id="w", name="n", business_context=BIZ_CTX,
                abort_key="abort:biz:phone",
            )
        assert result.aborted is True
        assert result.handoff_chain == ["order"]  # second segment never ran


class TestStatePersistenceBetweenHops:
    def test_state_update_persisted_after_each_agent(self):
        outputs = iter([
            _agent_output("order", "step1", state_update={"order_context": {"cart": 1}}),
            _agent_output("customer_service", "step2", state_update={"customer_service_context": {}}),
        ])
        saves = []

        def record_save(wa_id, business_id, output):
            saves.append(output.get("state_update"))

        with patch.object(dispatcher, "execute_agent", side_effect=lambda **_: next(outputs)), \
             patch.object(dispatcher, "_persist_state_update", side_effect=record_save), \
             patch("app.orchestration.response_composer.compose", return_value="M"):
            dispatcher.dispatch(
                [("order", "a"), ("customer_service", "b")],
                wa_id="w", name="n", business_context=BIZ_CTX,
            )
        assert saves == [{"order_context": {"cart": 1}}, {"customer_service_context": {}}]
