"""
Integration tests for the debounce + abort + requeue + flusher-wait pipeline.

These cover the slice that had four production bugs in a row that all unit
tests stayed green for:

  1. Requeue rpushed without spawning a flusher.
  2. Requeued payload missing identity → Twilio 21211 ('To' number "+").
  3. Inverted merge order from RPUSH instead of LPUSH.
  4. Race between the new flusher and the in-flight requeue.

The fixes were small per bug; the bugs were missed because no test wired
the pieces together. These integration tests use fakeredis (in-process) so
they're deterministic and run without Docker.

Strategy: drive `debounce_message` directly from multiple threads to
simulate concurrent webhook arrivals, mock `process_whatsapp_message` so
we control how long a turn takes (and what it requeues). Assert on:

  - Number of times `process_whatsapp_message` was called.
  - The text it was called with (chronological merge order matters).
  - That a single coalesced reply path was followed instead of two
    disconnected turns.

What we deliberately don't test here: agent / planner content. Those have
their own unit tests. This file is about the surrounding plumbing.
"""

import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest


# Skip ahead of the autouse OPENAI_API_KEY check in tests/integration/conftest.py.
# These tests don't call any LLM; the key check would otherwise skip them.
os.environ.setdefault("OPENAI_API_KEY", "test-stub")

import fakeredis  # noqa: E402

from app.services import debounce  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _bypass_openai_check(monkeypatch):
    """Override the integration conftest's OPENAI_API_KEY autouse skip.
    Defining a same-named autouse fixture in a closer scope wins."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-stub")


@pytest.fixture
def fake_redis():
    """Fresh in-process Redis per test. fakeredis 2.x supports the Lua
    commands we use (RPUSH/LPUSH/SET NX EX/LRANGE/DEL)."""
    server = fakeredis.FakeServer()
    client = fakeredis.FakeStrictRedis(server=server, decode_responses=True)
    return client


@pytest.fixture(autouse=True)
def _patch_redis(fake_redis, monkeypatch):
    """Route every _get_redis() call to our fakeredis instance."""
    monkeypatch.setattr(debounce, "_get_redis", lambda: fake_redis)
    # Also pre-empt the cached singleton inside debounce.
    monkeypatch.setattr(debounce, "_redis_client", fake_redis)


@pytest.fixture
def flask_app():
    from flask import Flask
    app = Flask("test")
    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_meta_payload(wa_id: str, text: str, msg_id: str) -> dict:
    """A normalize_twilio_to_meta-shaped payload."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {
            "contacts": [{"wa_id": wa_id}],
            "messages": [{
                "id": msg_id,
                "type": "text",
                "text": {"body": text},
            }],
        }}]}],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDebounceRequeueCoalesce:
    """
    Two messages arrive: first one starts processing, second arrives mid-turn,
    causes ABORT. The first turn requeues its text. The second flusher waits
    for the in-flight turn to finish, then drains both in chronological
    (LPUSH-preserved) order. Net result: one call to process_whatsapp_message
    with the merged body.
    """

    def test_inflight_abort_then_requeue_coalesces_in_chronological_order(
        self, fake_redis, flask_app, monkeypatch,
    ):
        # Speed up the debounce window so the test runs in <1s wall-clock.
        monkeypatch.setattr(debounce, "DEBOUNCE_SECONDS", 0.05)
        # Capture every call to process_whatsapp_message.
        process_calls: list[dict] = []

        # The first turn simulates a slow planner: while it's processing,
        # the abort signal will fire (set by the second arriving message).
        # When the agent layer detects the abort, it calls requeue_aborted_text.
        # We replicate that call path here without running the real agent.
        first_turn_started = threading.Event()
        first_turn_can_finish = threading.Event()

        def fake_process(body, business_context=None, abort_key=None, stale_turn=False):
            # Snapshot the body's coalesced text for assertion.
            value = body["entry"][0]["changes"][0]["value"]
            text = value["messages"][0]["text"]["body"]
            wa_id = value["contacts"][0]["wa_id"]
            process_calls.append({"text": text, "wa_id": wa_id, "abort_key": abort_key})

            # First call: stall until the test signals + then check abort.
            if len(process_calls) == 1:
                first_turn_started.set()
                first_turn_can_finish.wait(timeout=2.0)
                # Simulate the agent's abort detection + requeue.
                if abort_key and debounce.check_abort(abort_key):
                    debounce.clear_abort(abort_key)
                    debounce.requeue_aborted_text(abort_key, "Una barracuda")

        monkeypatch.setattr(
            "app.handlers.whatsapp_handler.process_whatsapp_message",
            fake_process,
        )

        # Mock business context resolution (we don't care about real DB lookups).
        monkeypatch.setattr(
            "app.utils.twilio_utils.resolve_twilio_business_context",
            lambda to: {"business_id": "biela", "business": {"name": "Biela"}},
        )

        # Push message 1 — wins the flusher.
        with flask_app.app_context():
            assert debounce.debounce_message(
                phone="+573177000722",
                to_number="whatsapp:+14155238886",
                normalized_body=_make_meta_payload("+573177000722", "Una barracuda", "SM001"),
                flask_app=flask_app,
            ) is True

        # Wait until message 1's flusher has drained and is processing.
        assert first_turn_started.wait(timeout=2.0), "first turn never started"

        # Push message 2 mid-flight — sets ABORT and spawns its own flusher.
        with flask_app.app_context():
            assert debounce.debounce_message(
                phone="+573177000722",
                to_number="whatsapp:+14155238886",
                normalized_body=_make_meta_payload("+573177000722", "Mejor dos", "SM002"),
                flask_app=flask_app,
            ) is True

        # Let the first turn finish — it'll detect abort, requeue "Una barracuda".
        first_turn_can_finish.set()

        # Wait for the second flusher to finish polling the in-flight key
        # (which clears in `_flush`'s finally block) and drain the coalesced
        # buffer. Be generous on wall-clock; the in-flight wait is real time.
        deadline = time.time() + 6.0
        while time.time() < deadline and len(process_calls) < 2:
            time.sleep(0.05)

        assert len(process_calls) == 2, (
            f"expected exactly 2 process_whatsapp_message calls "
            f"(first turn aborted, second drains coalesced batch); got {len(process_calls)}: "
            f"{process_calls}"
        )

        # Second call should have BOTH messages, with the requeued first
        # message AHEAD of the new one (LPUSH preserves chronological order).
        second_text = process_calls[1]["text"]
        assert "Una barracuda" in second_text
        assert "Mejor dos" in second_text
        idx_barracuda = second_text.index("Una barracuda")
        idx_mejor = second_text.index("Mejor dos")
        assert idx_barracuda < idx_mejor, (
            f"requeued (chronologically older) text must precede the newer "
            f"message in the merged body; got: {second_text!r}"
        )

        # And the wa_id survived the requeue (regression for the Twilio 21211
        # 'To' number whatsapp:+ bug).
        assert process_calls[1]["wa_id"] == "+573177000722"


class TestRequeueAlonePreservesIdentity:
    """
    Solo requeue case: a turn aborts and requeues, no companion message
    arrives. The new flusher we spawn drains the requeued entry alone —
    its payload must carry contacts[].wa_id or send_message would fail
    with Twilio 21211 ('To' number is "whatsapp:+").
    """

    def test_solo_requeue_drains_with_populated_wa_id(
        self, fake_redis, flask_app, monkeypatch,
    ):
        monkeypatch.setattr(debounce, "DEBOUNCE_SECONDS", 0.05)

        # Wait for "Hola" and the requeued solo drain.
        process_calls: list[dict] = []
        first_started = threading.Event()
        first_can_finish = threading.Event()

        def fake_process(body, business_context=None, abort_key=None, stale_turn=False):
            value = body["entry"][0]["changes"][0]["value"]
            text = value["messages"][0]["text"]["body"]
            wa_id = value["contacts"][0]["wa_id"]
            process_calls.append({"text": text, "wa_id": wa_id})
            if len(process_calls) == 1:
                first_started.set()
                first_can_finish.wait(timeout=2.0)
                # Simulate abort detection in the agent + requeue. We set
                # the abort flag ourselves to simulate the second arrival.
                if abort_key:
                    fake_redis.set(abort_key, "1", ex=30)
                    debounce.clear_abort(abort_key)  # consume + log
                    debounce.requeue_aborted_text(abort_key, "Para pedir")

        monkeypatch.setattr(
            "app.handlers.whatsapp_handler.process_whatsapp_message",
            fake_process,
        )
        monkeypatch.setattr(
            "app.utils.twilio_utils.resolve_twilio_business_context",
            lambda to: {"business_id": "biela", "business": {"name": "Biela"}},
        )

        with flask_app.app_context():
            debounce.debounce_message(
                phone="+573177000722",
                to_number="whatsapp:+14155238886",
                normalized_body=_make_meta_payload("+573177000722", "Hola", "SM010"),
                flask_app=flask_app,
            )

        assert first_started.wait(timeout=2.0)
        first_can_finish.set()

        # Wait for the requeue's spawned flusher to drain.
        deadline = time.time() + 6.0
        while time.time() < deadline and len(process_calls) < 2:
            time.sleep(0.05)

        assert len(process_calls) == 2
        # Solo requeue payload MUST carry the wa_id (the bug that caused
        # Twilio 21211 was an empty contacts[]).
        assert process_calls[1]["wa_id"] == "+573177000722"
        assert process_calls[1]["text"] == "Para pedir"


class TestNoAbortNoExtraFlusher:
    """Sanity: a single message with no concurrent arrival drains exactly
    once and doesn't trigger any extra flusher / requeue paths."""

    def test_single_message_drains_once(
        self, fake_redis, flask_app, monkeypatch,
    ):
        monkeypatch.setattr(debounce, "DEBOUNCE_SECONDS", 0.05)
        calls: list[dict] = []

        def fake_process(body, business_context=None, abort_key=None, stale_turn=False):
            value = body["entry"][0]["changes"][0]["value"]
            calls.append({"text": value["messages"][0]["text"]["body"]})

        monkeypatch.setattr(
            "app.handlers.whatsapp_handler.process_whatsapp_message",
            fake_process,
        )
        monkeypatch.setattr(
            "app.utils.twilio_utils.resolve_twilio_business_context",
            lambda to: {"business_id": "biela", "business": {"name": "Biela"}},
        )

        with flask_app.app_context():
            debounce.debounce_message(
                phone="+573177000722",
                to_number="whatsapp:+14155238886",
                normalized_body=_make_meta_payload("+573177000722", "Hola", "SM020"),
                flask_app=flask_app,
            )

        deadline = time.time() + 3.0
        while time.time() < deadline and not calls:
            time.sleep(0.05)

        assert len(calls) == 1
        assert calls[0]["text"] == "Hola"
