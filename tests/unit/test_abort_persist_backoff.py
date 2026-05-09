"""
Unit tests for the abort+requeue duplicate-persist fix and the adaptive
debounce backoff (window-extension on activity, exponential on requeue
thrash).

Coverage:
- Fix #1: skip-persist flag flows through requeue_aborted_text →
  _flush → process_whatsapp_message and the user-message persist is
  skipped on solo requeues. Mixed requeue+fresh flushes still persist
  so new content isn't lost.
- Fix #2: v2 OrderAgentToolCalling checks the abort flag at the top of
  each LLM iteration and short-circuits with __ABORTED__ + requeue.
- Backoff (a): _compute_initial_window returns the exponential curve
  capped at the configured max.
- Backoff (b): _bump_requeue_count atomically INCRs + EXPIREs and
  requeue_aborted_text calls it on each requeue.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services import debounce


# ---------------------------------------------------------------------------
# Fix #1 — skip-persist flag on the requeue path
# ---------------------------------------------------------------------------

class TestRequeueCarriesSkipPersistFlag:
    """The synthetic envelope must include `_skip_persist=True` so the
    handler can recognize the requeue path and avoid re-storing the
    user message."""

    def test_payload_includes_skip_persist_true(self):
        r = MagicMock(name="redis")
        # script_return=0 → the lock was already held by another flusher,
        # so the won_flusher branch (which needs Flask app context) is
        # skipped. Valid production scenario: requeue piggybacks on the
        # in-flight flusher instead of spawning a new one.
        script = MagicMock(name="lua", return_value=0)
        r.register_script.return_value = script
        with patch.object(debounce, "_get_redis", return_value=r), \
             patch.object(debounce, "_bump_requeue_count", return_value=1):
            debounce.requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573001234567",
                "Me envías la carta",
            )
        # The Lua script gets the JSON payload as its first arg.
        args = script.call_args.kwargs.get("args") or []
        assert args, "Lua script not called with args"
        payload = json.loads(args[0])
        assert payload.get("_skip_persist") is True, (
            "synthetic requeue envelope missing _skip_persist=True"
        )
        # Sanity: the normalized_body shape (and identity) is still intact
        # so _flush's merge loop and identity-picker still work.
        nb = payload["normalized_body"]
        contacts = nb["entry"][0]["changes"][0]["value"]["contacts"]
        assert contacts[0]["wa_id"] == "+573001234567"


class TestProcessWhatsappMessageHonorsSkipPersist:
    """The handler must NOT call store_conversation_message when
    skip_persist=True."""

    _MIN_TWILIO_BODY = {
        "entry": [{
            "changes": [{
                "value": {
                    "contacts": [{"wa_id": "+573001234567"}],
                    "messages": [{
                        "id": "requeue-abc-1",
                        "type": "text",
                        "text": {"body": "Me envías la carta"},
                    }],
                }
            }]
        }]
    }

    def _patch_pipeline(self):
        """Common patch stack: silence agent + customer + business
        lookups so we exercise only the persist gate."""
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch(
            "app.handlers.whatsapp_handler.conversation_manager"
        ))
        stack.enter_context(patch(
            "app.handlers.whatsapp_handler.customer_service"
        ))
        stack.enter_context(patch(
            "app.handlers.whatsapp_handler.business_service"
        ))
        stack.enter_context(patch(
            "app.handlers.whatsapp_handler.conversation_agent_service"
        ))
        stack.enter_context(patch(
            "app.handlers.whatsapp_handler.send_message"
        ))
        stack.enter_context(patch(
            "app.handlers.whatsapp_handler.turn_cache"
        ))
        return stack

    def test_skip_persist_true_does_not_store(self):
        from app.handlers import whatsapp_handler
        with self._patch_pipeline(), \
             patch.object(whatsapp_handler.conversation_service, "store_conversation_message") as store:
            whatsapp_handler.process_whatsapp_message(
                self._MIN_TWILIO_BODY,
                business_context=None,
                skip_persist=True,
            )
        store.assert_not_called()

    def test_skip_persist_false_still_stores(self):
        from app.handlers import whatsapp_handler
        with self._patch_pipeline(), \
             patch.object(whatsapp_handler.conversation_service, "store_conversation_message") as store:
            whatsapp_handler.process_whatsapp_message(
                self._MIN_TWILIO_BODY,
                business_context=None,
                skip_persist=False,
            )
        store.assert_called_once()
        # Confirm the role + body are passed through.
        kwargs = store.call_args.kwargs
        assert kwargs.get("role") == "user"
        assert kwargs.get("message") == "Me envías la carta"


# ---------------------------------------------------------------------------
# Fix #2 — v2 abort wiring
# ---------------------------------------------------------------------------

class TestV2AgentAbortWiring:
    """At the top of each LLM iteration the v2 agent must call
    check_abort; on True, it requeues the message body, clears the
    flag, and returns __ABORTED__ without invoking the LLM."""

    def _ctx(self):
        return {"business_id": "biz-1", "business": {"name": "Biela", "settings": {}}}

    def test_abort_pre_iteration_short_circuits(self):
        from app.agents.order_agent_tool_calling import OrderAgentToolCalling
        from app.orchestration.turn_context import TurnContext
        agent = OrderAgentToolCalling()
        llm = MagicMock()
        with patch.object(OrderAgentToolCalling, "llm", llm), \
             patch("app.agents.order_agent_tool_calling.conversation_service"), \
             patch("app.agents.order_agent_tool_calling.tracer"), \
             patch(
                 "app.agents.order_agent_tool_calling.build_turn_context",
                 return_value=TurnContext(),
             ), \
             patch(
                 "app.services.debounce.check_abort",
                 return_value=True,
             ) as ck, \
             patch(
                 "app.services.debounce.clear_abort",
             ) as cl, \
             patch(
                 "app.services.debounce.requeue_aborted_text",
             ) as rq:
            output = agent.execute(
                message_body="Me envías la carta",
                wa_id="+573001234567", name="X",
                business_context=self._ctx(),
                conversation_history=[],
                abort_key="abort:whatsapp:+14155238886:+573001234567",
            )
        # LLM never invoked because abort fired before iteration 0.
        llm.invoke.assert_not_called()
        ck.assert_called()
        cl.assert_called_once()
        rq.assert_called_once_with(
            "abort:whatsapp:+14155238886:+573001234567",
            "Me envías la carta",
        )
        assert output["message"] == "__ABORTED__"
        assert output["agent_type"] == "order"

    def test_no_abort_runs_normally(self):
        """check_abort=False → loop proceeds, LLM is invoked."""
        from app.agents.order_agent_tool_calling import OrderAgentToolCalling
        from app.orchestration.turn_context import TurnContext
        from langchain_core.messages import AIMessage
        agent = OrderAgentToolCalling()
        # Single AIMessage with a respond(...) tool call → terminates loop cleanly.
        respond_call = {
            "name": "respond",
            "args": {"kind": "chat", "summary": "ok"},
            "id": "r1",
            "type": "tool_call",
        }
        llm = MagicMock()
        llm.invoke.return_value = AIMessage(content="", tool_calls=[respond_call])
        with patch.object(OrderAgentToolCalling, "llm", llm), \
             patch("app.agents.order_agent_tool_calling.conversation_service"), \
             patch("app.agents.order_agent_tool_calling.tracer"), \
             patch(
                 "app.agents.order_agent_tool_calling.build_turn_context",
                 return_value=TurnContext(),
             ), \
             patch(
                 "app.agents.order_agent_tool_calling.render_response",
                 return_value={"type": "text", "body": "hola"},
             ), \
             patch(
                 "app.services.debounce.check_abort",
                 return_value=False,
             ), \
             patch(
                 "app.services.debounce.requeue_aborted_text",
             ) as rq:
            output = agent.execute(
                message_body="hola",
                wa_id="+573001234567", name="X",
                business_context=self._ctx(),
                conversation_history=[],
                abort_key="abort:whatsapp:+14155238886:+573001234567",
            )
        llm.invoke.assert_called()  # at least one LLM call
        rq.assert_not_called()  # no requeue when no abort
        assert output["message"] != "__ABORTED__"


# ---------------------------------------------------------------------------
# Backoff (a) — exponential initial window from requeue count
# ---------------------------------------------------------------------------

class TestComputeInitialWindow:
    def test_zero_count_uses_base(self):
        from app.services.debounce import (
            _compute_initial_window, DEBOUNCE_SECONDS,
        )
        assert _compute_initial_window(0) == DEBOUNCE_SECONDS

    def test_count_one_doubles_base(self):
        from app.services.debounce import (
            _compute_initial_window, DEBOUNCE_SECONDS,
        )
        assert _compute_initial_window(1) == DEBOUNCE_SECONDS * 2

    def test_count_two_quadruples_base(self):
        from app.services.debounce import (
            _compute_initial_window, DEBOUNCE_SECONDS,
        )
        assert _compute_initial_window(2) == DEBOUNCE_SECONDS * 4

    def test_high_count_capped_at_max(self):
        """Past the cap the window must NOT keep growing."""
        from app.services.debounce import (
            _compute_initial_window, _MAX_REQUEUE_BACKOFF_SECONDS,
        )
        assert _compute_initial_window(10) <= _MAX_REQUEUE_BACKOFF_SECONDS
        assert _compute_initial_window(100) <= _MAX_REQUEUE_BACKOFF_SECONDS
        # And stays exactly at the cap once the unbounded value would exceed.
        assert _compute_initial_window(50) == _MAX_REQUEUE_BACKOFF_SECONDS

    def test_negative_count_falls_back_to_base(self):
        """Defensive: if the counter ever goes negative (corrupted),
        we still serve a base window — no zero-second flushes."""
        from app.services.debounce import (
            _compute_initial_window, DEBOUNCE_SECONDS,
        )
        assert _compute_initial_window(-1) == DEBOUNCE_SECONDS


# ---------------------------------------------------------------------------
# Backoff (b) — Redis counter helpers + integration with requeue
# ---------------------------------------------------------------------------

class TestRequeueCounterHelpers:
    def test_bump_requeue_count_pipelines_incr_and_expire(self):
        r = MagicMock(name="redis")
        pipe = MagicMock(name="pipeline")
        pipe.execute.return_value = [3, True]
        r.pipeline.return_value = pipe
        with patch.object(debounce, "_get_redis", return_value=r):
            count = debounce._bump_requeue_count("whatsapp:+14155238886", "+573001234567")
        # INCR + EXPIRE were both queued.
        pipe.incr.assert_called_once()
        pipe.expire.assert_called_once()
        expected_key = "debounce:requeue_count:whatsapp:+14155238886:+573001234567"
        assert pipe.incr.call_args.args[0] == expected_key
        assert pipe.expire.call_args.args[0] == expected_key
        assert pipe.expire.call_args.args[1] == debounce._REQUEUE_COUNT_TTL
        assert count == 3

    def test_bump_handles_redis_failure_gracefully(self):
        r = MagicMock(name="redis")
        r.pipeline.side_effect = RuntimeError("redis down")
        with patch.object(debounce, "_get_redis", return_value=r):
            count = debounce._bump_requeue_count("to", "+57300")
        assert count == 0

    def test_read_requeue_count_returns_int(self):
        r = MagicMock(name="redis")
        r.get.return_value = "5"
        with patch.object(debounce, "_get_redis", return_value=r):
            assert debounce._read_requeue_count("to", "+57300") == 5

    def test_read_requeue_count_absent_is_zero(self):
        r = MagicMock(name="redis")
        r.get.return_value = None
        with patch.object(debounce, "_get_redis", return_value=r):
            assert debounce._read_requeue_count("to", "+57300") == 0

    def test_read_requeue_count_redis_blip_returns_zero(self):
        r = MagicMock(name="redis")
        r.get.side_effect = RuntimeError("redis down")
        with patch.object(debounce, "_get_redis", return_value=r):
            assert debounce._read_requeue_count("to", "+57300") == 0


class TestAbortSignalBumpsBackoffCounter:
    """Integration: setting the abort signal must bump the counter
    SYNCHRONOUSLY (in debounce_message), not asynchronously when the
    aborted agent finally calls requeue_aborted_text. The new flusher
    that just spawned will read the counter before the old turn's
    abort handler runs — without the synchronous bump, every new
    flusher sees the stale pre-thrash value and commits to the base
    window, defeating backoff (b)."""

    def test_requeue_does_not_double_bump(self):
        """Counter is bumped once at signal time; the requeue handler
        must NOT bump again (would double-count the same thrash)."""
        r = MagicMock(name="redis")
        script = MagicMock(name="lua", return_value=0)
        r.register_script.return_value = script
        with patch.object(debounce, "_get_redis", return_value=r), \
             patch.object(debounce, "_bump_requeue_count") as bump, \
             patch.object(debounce, "_read_requeue_count", return_value=2):
            debounce.requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573001234567",
                "Me envías la carta",
            )
        bump.assert_not_called()

    def test_debounce_message_bumps_when_setting_abort_signal(self):
        """When debounce_message detects an in-flight turn and sets
        the abort flag, it must also bump the counter synchronously
        so the new flusher's _read_requeue_count returns the
        post-bump value."""
        from app.services.debounce import debounce_message
        r = MagicMock(name="redis")
        # Lua wrapper returns 1 → won the flusher lock for this call.
        script = MagicMock(name="lua", return_value=1)
        r.register_script.return_value = script
        # processing flag exists → in-flight turn → abort path fires.
        r.exists.return_value = True
        flask_app = MagicMock()
        with patch.object(debounce, "_get_redis", return_value=r), \
             patch.object(debounce, "_bump_requeue_count", return_value=1) as bump, \
             patch.object(debounce, "threading"):
            debounce_message(
                phone="+573001234567",
                to_number="whatsapp:+14155238886",
                normalized_body={"entry": [{"changes": [{"value": {
                    "contacts": [{"wa_id": "+573001234567"}],
                    "messages": [{"id": "id1", "type": "text", "text": {"body": "x"}}],
                }}]}]},
                flask_app=flask_app,
            )
        # Counter bumped exactly once at signal-set time.
        bump.assert_called_once_with("whatsapp:+14155238886", "+573001234567")


# ---------------------------------------------------------------------------
# was_aborted return signal — counter-reset race fix (Option B)
# ---------------------------------------------------------------------------

class TestProcessWhatsappMessageReturnsAbortFlag:
    """``process_whatsapp_message`` must return True when the turn
    aborted (mid-turn or pre-send) so the debounce flusher can
    distinguish "clean turn → reset backoff counter" from "turn
    aborted → preserve counter for next attempt".

    Without this signal, the flusher used to read the abort flag's
    after-state and saw it cleared (because the dispatcher consumed
    it), reset the counter, and the next thrash cycle started over at
    requeue_count=1 instead of compounding."""

    _MIN_BODY = {
        "entry": [{
            "changes": [{
                "value": {
                    "contacts": [{"wa_id": "+573001234567"}],
                    "messages": [{
                        "id": "msg-1",
                        "type": "text",
                        "text": {"body": "hola"},
                    }],
                }
            }]
        }]
    }

    def _patch_pipeline(self):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch(
            "app.handlers.whatsapp_handler.customer_service"
        ))
        stack.enter_context(patch(
            "app.handlers.whatsapp_handler.business_service"
        ))
        stack.enter_context(patch(
            "app.handlers.whatsapp_handler.conversation_agent_service"
        ))
        stack.enter_context(patch(
            "app.handlers.whatsapp_handler.send_message", return_value="ok"
        ))
        stack.enter_context(patch(
            "app.handlers.whatsapp_handler.turn_cache"
        ))
        return stack

    def test_clean_turn_returns_false(self):
        from app.handlers import whatsapp_handler
        cm = MagicMock()
        cm.process.return_value = "Hola David"
        with self._patch_pipeline(), \
             patch.object(whatsapp_handler.conversation_service, "store_conversation_message"), \
             patch.object(whatsapp_handler, "conversation_manager", cm):
            result = whatsapp_handler.process_whatsapp_message(
                self._MIN_BODY, business_context=None,
            )
        assert result is False, "clean turn must return False (was_aborted=False)"

    def test_aborted_turn_returns_true(self):
        from app.handlers import whatsapp_handler
        cm = MagicMock()
        cm.process.return_value = "__ABORTED__"
        with self._patch_pipeline(), \
             patch.object(whatsapp_handler.conversation_service, "store_conversation_message"), \
             patch.object(whatsapp_handler, "conversation_manager", cm):
            result = whatsapp_handler.process_whatsapp_message(
                self._MIN_BODY, business_context=None,
            )
        assert result is True, "aborted turn must return True (was_aborted=True)"

    def test_suppress_send_returns_false(self):
        """__SUPPRESS_SEND__ is a clean dispatch (greeting CTA),
        not an abort. was_aborted must be False."""
        from app.handlers import whatsapp_handler
        cm = MagicMock()
        cm.process.return_value = "__SUPPRESS_SEND__"
        with self._patch_pipeline(), \
             patch.object(whatsapp_handler.conversation_service, "store_conversation_message"), \
             patch.object(whatsapp_handler, "conversation_manager", cm):
            result = whatsapp_handler.process_whatsapp_message(
                self._MIN_BODY, business_context=None,
            )
        assert result is False

    def test_pre_send_abort_returns_true(self):
        """Even when the agent itself returned a normal reply, if the
        pre-send abort gate fires (newer message landed during the
        agent run), was_aborted must be True so the counter sticks."""
        from app.handlers import whatsapp_handler
        cm = MagicMock()
        cm.process.return_value = "Tu pedido está confirmado"
        with self._patch_pipeline(), \
             patch.object(whatsapp_handler.conversation_service, "store_conversation_message"), \
             patch.object(whatsapp_handler, "conversation_manager", cm), \
             patch("app.services.debounce.check_abort", return_value=True), \
             patch("app.services.debounce.clear_abort"):
            result = whatsapp_handler.process_whatsapp_message(
                self._MIN_BODY, business_context=None,
                abort_key="abort:whatsapp:+1:+57300",
            )
        assert result is True
