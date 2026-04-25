"""
Unit tests for requeue_aborted_text in app.services.debounce.

Context: when a turn aborts after the planner runs (because a newer message
arrived), we drop the assistant reply but the user's text would otherwise be
lost — the next turn's planner would only see the trailing message and miss
the order intent. requeue_aborted_text fixes that by RPUSHing the aborted
text back into the same Redis list that the next flusher drains, where the
existing merge loop in _flush() coalesces it transparently.

Contract we care about:
1. Happy path: pushes a Meta-shaped payload to debounce:msgs:{to}:{phone}
   and refreshes the message TTL.
2. abort_key parsing tolerates Twilio "whatsapp:+E164" to_numbers (which
   contain extra colons) — must rsplit, not plain split.
3. Empty/whitespace text → no-op, no Redis calls.
4. Malformed abort_key (no "abort:" prefix, no separator) → no-op.
5. Redis unavailable → silent no-op (we never want to crash the abort path).
6. Redis raises mid-RPUSH → exception swallowed, abort still completes.
7. Payload shape matches what _flush()'s merge loop expects, so multi-turn
   coalescing works end-to-end.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services import debounce
from app.services.debounce import requeue_aborted_text


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestRequeueHappyPath:
    def test_rpushes_meta_shaped_payload(self):
        r = MagicMock(name="redis")
        with patch.object(debounce, "_get_redis", return_value=r):
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "quiero una picada",
            )

        # One RPUSH to the matching debounce:msgs:{to}:{phone} key
        r.rpush.assert_called_once()
        key = r.rpush.call_args.args[0]
        payload = r.rpush.call_args.args[1]
        assert key == "debounce:msgs:whatsapp:+14155238886:+573177000722"

        # Payload is JSON with a Meta-shaped envelope so _flush()'s merge
        # loop (entry[0].changes[0].value.messages[0].text.body) finds it.
        decoded = json.loads(payload)
        body = (
            decoded["normalized_body"]["entry"][0]["changes"][0]["value"]
            ["messages"][0]["text"]["body"]
        )
        assert body == "quiero una picada"

    def test_refreshes_message_ttl(self):
        r = MagicMock(name="redis")
        with patch.object(debounce, "_get_redis", return_value=r):
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "una picada",
            )

        r.expire.assert_called_once()
        ttl_key, ttl_seconds = r.expire.call_args.args
        assert ttl_key == "debounce:msgs:whatsapp:+14155238886:+573177000722"
        assert ttl_seconds == debounce._MSG_TTL

    def test_strips_whitespace_in_payload(self):
        r = MagicMock(name="redis")
        with patch.object(debounce, "_get_redis", return_value=r):
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "  hola  \n",
            )
        payload = json.loads(r.rpush.call_args.args[1])
        body = (
            payload["normalized_body"]["entry"][0]["changes"][0]["value"]
            ["messages"][0]["text"]["body"]
        )
        assert body == "hola"

    def test_meta_path_to_number_without_whatsapp_prefix(self):
        """Meta path to_number is bare digits like '14155238886'."""
        r = MagicMock(name="redis")
        with patch.object(debounce, "_get_redis", return_value=r):
            requeue_aborted_text(
                "abort:14155238886:573177000722",
                "una picada",
            )
        key = r.rpush.call_args.args[0]
        assert key == "debounce:msgs:14155238886:573177000722"


# ---------------------------------------------------------------------------
# abort_key parsing — Twilio paths use "whatsapp:+E164" so parsing must rsplit
# ---------------------------------------------------------------------------

class TestAbortKeyParsing:
    def test_twilio_to_number_with_extra_colon_round_trips(self):
        """
        debounce_message stores under 'debounce:msgs:{to}:{phone}'. The
        abort_key uses the same to_number, so the requeued key must match
        exactly — otherwise the next flusher reads from a different list.
        """
        to_number = "whatsapp:+14155238886"
        phone = "+573177000722"
        abort_key = f"abort:{to_number}:{phone}"

        r = MagicMock(name="redis")
        with patch.object(debounce, "_get_redis", return_value=r):
            requeue_aborted_text(abort_key, "una picada")

        expected_key = f"debounce:msgs:{to_number}:{phone}"
        assert r.rpush.call_args.args[0] == expected_key

    def test_phone_is_taken_from_last_segment(self):
        """rsplit anchors on the trailing phone number, not the first ':'."""
        r = MagicMock(name="redis")
        with patch.object(debounce, "_get_redis", return_value=r):
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "x",
            )
        # If we used split(':', 2) by mistake, to_number would be 'whatsapp'
        # and the key would be wrong.
        assert (
            r.rpush.call_args.args[0]
            == "debounce:msgs:whatsapp:+14155238886:+573177000722"
        )


# ---------------------------------------------------------------------------
# No-op short circuits — never raise from the abort path
# ---------------------------------------------------------------------------

class TestRequeueNoOps:
    def test_empty_text_does_not_touch_redis(self):
        r = MagicMock(name="redis")
        with patch.object(debounce, "_get_redis", return_value=r):
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "",
            )
        r.rpush.assert_not_called()

    def test_whitespace_only_text_does_not_touch_redis(self):
        r = MagicMock(name="redis")
        with patch.object(debounce, "_get_redis", return_value=r):
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "   \n\t  ",
            )
        r.rpush.assert_not_called()

    def test_none_text_does_not_touch_redis(self):
        r = MagicMock(name="redis")
        with patch.object(debounce, "_get_redis", return_value=r):
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                None,  # type: ignore[arg-type]
            )
        r.rpush.assert_not_called()

    def test_empty_abort_key_does_not_touch_redis(self):
        r = MagicMock(name="redis")
        with patch.object(debounce, "_get_redis", return_value=r):
            requeue_aborted_text("", "una picada")
        r.rpush.assert_not_called()

    def test_missing_abort_prefix_does_not_touch_redis(self):
        r = MagicMock(name="redis")
        with patch.object(debounce, "_get_redis", return_value=r):
            requeue_aborted_text("processing:foo:+573177000722", "una picada")
        r.rpush.assert_not_called()

    def test_malformed_abort_key_no_separator_does_not_touch_redis(self):
        r = MagicMock(name="redis")
        with patch.object(debounce, "_get_redis", return_value=r):
            requeue_aborted_text("abort:onlyonepiece", "una picada")
        # No ':' between to_number and phone after the prefix → rsplit fails.
        r.rpush.assert_not_called()


# ---------------------------------------------------------------------------
# Resilience — abort path must never raise
# ---------------------------------------------------------------------------

class TestRequeueResilience:
    def test_redis_unavailable_silently_noops(self):
        with patch.object(debounce, "_get_redis", return_value=None):
            # Must not raise even though Redis is down
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "una picada",
            )

    def test_rpush_raises_is_swallowed(self):
        r = MagicMock(name="redis")
        r.rpush.side_effect = Exception("connection reset")
        with patch.object(debounce, "_get_redis", return_value=r):
            # Must not propagate — the abort path's job is to fail safe
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "una picada",
            )

    def test_expire_raises_is_swallowed(self):
        r = MagicMock(name="redis")
        r.expire.side_effect = Exception("timeout")
        with patch.object(debounce, "_get_redis", return_value=r):
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "una picada",
            )


# ---------------------------------------------------------------------------
# Multi-abort scaling — N consecutive aborts must stack into one batch
# ---------------------------------------------------------------------------

class TestRequeueScalesToN:
    def test_multiple_aborts_append_in_order(self):
        """
        N aborted turns in a row should leave N entries in the list, in
        arrival order, so the next flusher's merge loop sees the full
        thread joined with newlines.
        """
        # Simulate a real-ish list backing
        store: dict[str, list[str]] = {}

        def fake_rpush(key, value):
            store.setdefault(key, []).append(value)

        r = MagicMock(name="redis")
        r.rpush.side_effect = fake_rpush

        abort_key = "abort:whatsapp:+14155238886:+573177000722"
        with patch.object(debounce, "_get_redis", return_value=r):
            requeue_aborted_text(abort_key, "quiero una picada")
            requeue_aborted_text(abort_key, "que valor")
            requeue_aborted_text(abort_key, "domicilio")

        key = "debounce:msgs:whatsapp:+14155238886:+573177000722"
        assert len(store[key]) == 3

        bodies = [
            json.loads(p)["normalized_body"]["entry"][0]["changes"][0]
            ["value"]["messages"][0]["text"]["body"]
            for p in store[key]
        ]
        assert bodies == [
            "quiero una picada",
            "que valor",
            "domicilio",
        ]
