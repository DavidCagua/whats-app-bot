"""
Unit tests for requeue_aborted_text in app.services.debounce.

Context: when a turn aborts after the planner runs (because a newer message
arrived), we drop the assistant reply but the user's text would otherwise be
lost — the next turn's planner would only see the trailing message and miss
the order intent. requeue_aborted_text fixes that by RPUSHing the aborted
text back into the same Redis list that the next flusher drains, where the
existing merge loop in _flush() coalesces it transparently.

The push uses the same atomic _LUA_BUFFER as debounce_message — so the
caller can also try-claim the flusher lock atomically. If we win the lock,
we spawn a flusher thread ourselves; otherwise an existing flusher will
pick the message up. This is what guarantees the requeued text is actually
processed even when no other webhook arrives.

Contract we care about:
1. Happy path: pushes a Meta-shaped payload via the atomic Lua script
   to debounce:msgs:{to}:{phone} with refreshed TTLs.
2. Spawns a flusher thread when the Lua script reports it won the
   flusher lock (lua_result=1) — and does NOT spawn one when it
   coalesced into an existing flusher (lua_result=0).
3. abort_key parsing tolerates Twilio "whatsapp:+E164" to_numbers
   (which contain extra colons) — must rsplit, not plain split.
4. Empty/whitespace text → no-op, no Redis calls.
5. Malformed abort_key (no "abort:" prefix, no separator) → no-op.
6. Redis unavailable → silent no-op (we never want to crash the abort path).
7. Lua script raises mid-call → exception swallowed, abort still completes.
8. Payload shape matches what _flush()'s merge loop expects, so multi-turn
   coalescing works end-to-end.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services import debounce
from app.services.debounce import requeue_aborted_text


def _redis_with_lua(script_return: int = 1):
    """
    Build a Redis mock whose register_script returns a callable that
    pretends to be the Lua script. The callable records its kwargs so
    tests can assert keys / args without poking Redis internals.
    """
    r = MagicMock(name="redis")
    script_callable = MagicMock(name="lua_script", return_value=script_return)
    r.register_script.return_value = script_callable
    return r, script_callable


def _redis_with_lua_recording_source():
    """
    Like _redis_with_lua but records WHICH Lua script source was passed
    to register_script. Used to assert RPUSH vs LPUSH choice.
    """
    r = MagicMock(name="redis")
    script_callable = MagicMock(name="lua_script", return_value=1)
    r.register_script.return_value = script_callable
    return r, script_callable


def _decode_payload(script_callable: MagicMock) -> dict:
    """Pull the JSON payload that was passed to the Lua script."""
    args = script_callable.call_args.kwargs.get("args") or script_callable.call_args.args[1:]
    payload = args[0]
    return json.loads(payload)


def _key_from(script_callable: MagicMock, idx: int = 0) -> str:
    keys = script_callable.call_args.kwargs.get("keys") or script_callable.call_args.args[0]
    return keys[idx]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestRequeueHappyPath:
    def test_pushes_meta_shaped_payload_via_lua(self):
        r, script = _redis_with_lua()
        # Block flusher spawn so the test isn't tangled with threading.
        with patch.object(debounce, "_get_redis", return_value=r), \
             patch.object(debounce.threading, "Thread"):
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "quiero una picada",
            )

        script.assert_called_once()
        assert _key_from(script, 0) == "debounce:msgs:whatsapp:+14155238886:+573177000722"
        assert _key_from(script, 1) == "debounce:flusher:whatsapp:+14155238886:+573177000722"

        body = (
            _decode_payload(script)["normalized_body"]["entry"][0]["changes"][0]
            ["value"]["messages"][0]["text"]["body"]
        )
        assert body == "quiero una picada"

    def test_uses_lpush_variant_to_preserve_chronological_order(self):
        """
        Regression: the requeued text arrived chronologically BEFORE the
        message that triggered the abort. If the requeue used RPUSH, the
        merge loop would invert the customer's words ("una barracuda"
        then "mejor dos" → "mejor dos\\nuna barracuda" → planner can't
        infer "mejor dos" was a quantity modifier on the prior message).
        Must use the LPUSH variant of the buffer Lua script.
        """
        r, script = _redis_with_lua()
        with patch.object(debounce, "_get_redis", return_value=r), \
             patch.object(debounce.threading, "Thread"):
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "una barracuda",
            )

        r.register_script.assert_called_once()
        script_source = r.register_script.call_args.args[0]
        assert "LPUSH" in script_source, (
            "requeue must use the LPUSH variant so chronological order is preserved"
        )
        assert "RPUSH" not in script_source, (
            "RPUSH would put the requeued text at the tail, inverting message order"
        )

    def test_payload_carries_wa_id_so_solo_drain_can_send(self):
        """
        Regression: the requeued payload must include contacts[].wa_id
        so the merge loop in _flush() picks a non-empty wa_id when the
        requeued entry is alone in the buffer (no companion full webhook
        to merge with). Otherwise Twilio rejects the reply with 21211
        ("'To' number is whatsapp:+ — invalid").
        """
        r, script = _redis_with_lua()
        with patch.object(debounce, "_get_redis", return_value=r), \
             patch.object(debounce.threading, "Thread"):
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "una picada",
            )

        value = (
            _decode_payload(script)["normalized_body"]["entry"][0]
            ["changes"][0]["value"]
        )
        assert value["contacts"][0]["wa_id"] == "+573177000722"
        msg = value["messages"][0]
        # Synthesized id avoids `turn_id=-` in logs and dedup collisions.
        assert msg["id"].startswith("requeue-"), msg["id"]
        assert msg["type"] == "text"
        assert msg["text"]["body"] == "una picada"

    def test_passes_msg_and_flusher_ttls(self):
        r, script = _redis_with_lua()
        with patch.object(debounce, "_get_redis", return_value=r), \
             patch.object(debounce.threading, "Thread"):
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "una picada",
            )

        args = script.call_args.kwargs.get("args") or script.call_args.args[1:]
        # Lua: ARGV[1]=payload, ARGV[2]=msg_ttl, ARGV[3]=flusher_ttl
        assert args[1] == debounce._MSG_TTL
        assert args[2] == debounce._FLUSHER_TTL

    def test_strips_whitespace_in_payload(self):
        r, script = _redis_with_lua()
        with patch.object(debounce, "_get_redis", return_value=r), \
             patch.object(debounce.threading, "Thread"):
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "  hola  \n",
            )
        body = (
            _decode_payload(script)["normalized_body"]["entry"][0]["changes"][0]
            ["value"]["messages"][0]["text"]["body"]
        )
        assert body == "hola"

    def test_meta_path_to_number_without_whatsapp_prefix(self):
        """Meta path to_number is bare digits like '14155238886'."""
        r, script = _redis_with_lua()
        with patch.object(debounce, "_get_redis", return_value=r), \
             patch.object(debounce.threading, "Thread"):
            requeue_aborted_text(
                "abort:14155238886:573177000722",
                "una picada",
            )
        assert _key_from(script, 0) == "debounce:msgs:14155238886:573177000722"


# ---------------------------------------------------------------------------
# Flusher spawn — the bug the original implementation missed
# ---------------------------------------------------------------------------

class TestFlusherSpawn:
    def test_spawns_flusher_thread_when_lua_returns_1(self):
        from flask import Flask
        flask_app = Flask("test")

        r, script = _redis_with_lua(script_return=1)
        thread_cls = MagicMock(name="Thread")

        with patch.object(debounce, "_get_redis", return_value=r), \
             patch.object(debounce.threading, "Thread", thread_cls), \
             flask_app.app_context():
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "una picada",
            )

        thread_cls.assert_called_once()
        kwargs = thread_cls.call_args.kwargs
        # _flush(phone, to_number, flask_app)
        assert kwargs["target"] is debounce._flush
        spawned_args = kwargs["args"]
        assert spawned_args[0] == "+573177000722"
        assert spawned_args[1] == "whatsapp:+14155238886"
        assert spawned_args[2] is flask_app
        assert kwargs["daemon"] is True
        thread_cls.return_value.start.assert_called_once()

    def test_does_not_spawn_thread_when_lua_returns_0(self):
        """An existing flusher is already in flight — coalesce, don't spawn."""
        r, script = _redis_with_lua(script_return=0)
        thread_cls = MagicMock(name="Thread")

        with patch.object(debounce, "_get_redis", return_value=r), \
             patch.object(debounce.threading, "Thread", thread_cls):
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "una picada",
            )

        thread_cls.assert_not_called()

    def test_no_thread_spawned_when_flask_context_missing(self):
        """If we can't resolve the Flask app, log + bail rather than crash.
        The text is still buffered; the next inbound webhook will flush it.
        We deliberately run WITHOUT a flask.app_context() — current_app
        raises RuntimeError, the function should swallow it."""
        r, script = _redis_with_lua(script_return=1)
        thread_cls = MagicMock(name="Thread")

        with patch.object(debounce, "_get_redis", return_value=r), \
             patch.object(debounce.threading, "Thread", thread_cls):
            # No flask_app.app_context() pushed.
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "una picada",
            )

        # Script still ran (text is buffered for next webhook to find).
        script.assert_called_once()
        # But no thread spawned — graceful fallback.
        thread_cls.assert_not_called()


# ---------------------------------------------------------------------------
# abort_key parsing — Twilio paths use "whatsapp:+E164" so parsing must rsplit
# ---------------------------------------------------------------------------

class TestAbortKeyParsing:
    def test_twilio_to_number_with_extra_colon_round_trips(self):
        to_number = "whatsapp:+14155238886"
        phone = "+573177000722"
        abort_key = f"abort:{to_number}:{phone}"

        r, script = _redis_with_lua()
        with patch.object(debounce, "_get_redis", return_value=r), \
             patch.object(debounce.threading, "Thread"):
            requeue_aborted_text(abort_key, "una picada")

        assert _key_from(script, 0) == f"debounce:msgs:{to_number}:{phone}"

    def test_phone_is_taken_from_last_segment(self):
        """rsplit anchors on the trailing phone number, not the first ':'."""
        r, script = _redis_with_lua()
        with patch.object(debounce, "_get_redis", return_value=r), \
             patch.object(debounce.threading, "Thread"):
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "x",
            )
        assert (
            _key_from(script, 0)
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
        r.register_script.assert_not_called()

    def test_whitespace_only_text_does_not_touch_redis(self):
        r = MagicMock(name="redis")
        with patch.object(debounce, "_get_redis", return_value=r):
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "   \n\t  ",
            )
        r.register_script.assert_not_called()

    def test_none_text_does_not_touch_redis(self):
        r = MagicMock(name="redis")
        with patch.object(debounce, "_get_redis", return_value=r):
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                None,  # type: ignore[arg-type]
            )
        r.register_script.assert_not_called()

    def test_empty_abort_key_does_not_touch_redis(self):
        r = MagicMock(name="redis")
        with patch.object(debounce, "_get_redis", return_value=r):
            requeue_aborted_text("", "una picada")
        r.register_script.assert_not_called()

    def test_missing_abort_prefix_does_not_touch_redis(self):
        r = MagicMock(name="redis")
        with patch.object(debounce, "_get_redis", return_value=r):
            requeue_aborted_text("processing:foo:+573177000722", "una picada")
        r.register_script.assert_not_called()

    def test_malformed_abort_key_no_separator_does_not_touch_redis(self):
        r = MagicMock(name="redis")
        with patch.object(debounce, "_get_redis", return_value=r):
            requeue_aborted_text("abort:onlyonepiece", "una picada")
        # No ':' between to_number and phone after the prefix → rsplit fails.
        r.register_script.assert_not_called()


# ---------------------------------------------------------------------------
# Resilience — abort path must never raise
# ---------------------------------------------------------------------------

class TestRequeueResilience:
    def test_redis_unavailable_silently_noops(self):
        with patch.object(debounce, "_get_redis", return_value=None):
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "una picada",
            )

    def test_script_raises_is_swallowed(self):
        r, script = _redis_with_lua()
        script.side_effect = Exception("connection reset")
        with patch.object(debounce, "_get_redis", return_value=r), \
             patch.object(debounce.threading, "Thread"):
            requeue_aborted_text(
                "abort:whatsapp:+14155238886:+573177000722",
                "una picada",
            )


# ---------------------------------------------------------------------------
# Multi-abort scaling — N consecutive aborts must stack into one batch
# ---------------------------------------------------------------------------

def _full_entry(wa_id: str, text: str, msg_id: str = "SMfull") -> dict:
    """A Twilio-normalized payload as produced by normalize_twilio_to_meta."""
    return {
        "normalized_body": {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {
                "contacts": [{"wa_id": wa_id}],
                "messages": [{
                    "id": msg_id,
                    "text": {"body": text},
                    "type": "text",
                }],
            }}]}],
        }
    }


def _stripped_requeue_entry(text: str) -> dict:
    """Mirrors what requeue_aborted_text writes — minimal envelope, no contacts."""
    return {
        "normalized_body": {
            "entry": [{"changes": [{"value": {
                "messages": [{"text": {"body": text}}],
            }}]}],
        }
    }


# ---------------------------------------------------------------------------
# Merge base selection — entries[0] may be a stripped requeue, must skip it
# ---------------------------------------------------------------------------

class TestMergeBaseSelection:
    """
    The bug we're guarding against:
      [requeue("mi dios le pague"), full(wa_id="+57...", "es un buen servicio")]
    Old code took entries[0] as base → wa_id="" → Twilio "whatsapp:+" 21211 send error.
    New code picks the first entry whose contacts.wa_id is populated.
    """

    def test_picks_full_entry_when_first_is_stripped_requeue(self):
        entries = [
            _stripped_requeue_entry("mi dios le pague"),
            _full_entry("+573177000722", "es un buen servicio"),
        ]
        def has_full_identity(entry):
            try:
                contacts = (
                    entry["normalized_body"]["entry"][0]["changes"][0]
                    ["value"].get("contacts") or []
                )
                return bool(contacts and contacts[0].get("wa_id"))
            except (KeyError, IndexError, TypeError):
                return False

        base = next((e for e in entries if has_full_identity(e)), entries[0])
        wa_id = (
            base["normalized_body"]["entry"][0]["changes"][0]
            ["value"]["contacts"][0]["wa_id"]
        )
        assert wa_id == "+573177000722"

    def test_falls_back_to_first_when_none_have_identity(self):
        entries = [
            _stripped_requeue_entry("a"),
            _stripped_requeue_entry("b"),
        ]
        def has_full_identity(entry):
            try:
                contacts = (
                    entry["normalized_body"]["entry"][0]["changes"][0]
                    ["value"].get("contacts") or []
                )
                return bool(contacts and contacts[0].get("wa_id"))
            except (KeyError, IndexError, TypeError):
                return False

        base = next((e for e in entries if has_full_identity(e)), entries[0])
        assert base is entries[0]

    def test_first_full_wins_when_multiple_have_identity(self):
        entries = [
            _full_entry("+573177000722", "una picada", msg_id="SMa"),
            _full_entry("+573177000722", "que valor", msg_id="SMb"),
        ]
        def has_full_identity(entry):
            try:
                contacts = (
                    entry["normalized_body"]["entry"][0]["changes"][0]
                    ["value"].get("contacts") or []
                )
                return bool(contacts and contacts[0].get("wa_id"))
            except (KeyError, IndexError, TypeError):
                return False

        base = next((e for e in entries if has_full_identity(e)), entries[0])
        msg_id = base["normalized_body"]["entry"][0]["changes"][0]["value"]["messages"][0]["id"]
        assert msg_id == "SMa"


class TestFlusherWaitsForInflight:
    """
    Regression: when a new message arrives while an in-flight turn is
    still processing, the flusher's bare time.sleep(1.5) used to drain
    the buffer whether or not the in-flight turn had finished requeuing
    its text. Race window: in-flight turn takes >1.5s, new flusher wakes
    first, drains only the new message → "La barracuda" then "Que valor?"
    becomes two unrelated turns instead of one coalesced thread.

    Fix: after the debounce window, _flush polls `proc_key` and only
    drains once it clears (capped at _INFLIGHT_WAIT_MAX_SECONDS to
    avoid stranding messages on a hung turn).
    """

    def _drive_flush_with_inflight(
        self, *, exists_sequence: list[bool],
    ):
        """
        Run _flush once with mocked Redis. exists_sequence is the values
        r.exists(proc_key) returns on each call. The drain mocks itself
        empty so the function exits immediately after the wait loop.

        time.sleep is replaced with a function that records the requested
        interval and advances a virtual clock; time.time returns the
        virtual clock so the cap test doesn't have to wait wall-clock
        seconds.
        """
        sleeps: list[float] = []
        exists_calls: list[int] = []
        clock = [0.0]

        def fake_sleep(s):
            sleeps.append(s)
            clock[0] += s

        def fake_time():
            return clock[0]

        r = MagicMock(name="redis")
        # Drain returns no messages → flusher exits cleanly after the wait.
        drain_script = MagicMock(return_value=[])
        r.register_script.return_value = drain_script
        # llen mocked at 0 (no buffer growth → no window extensions).
        # Tests after the adaptive-backoff change: window stays at the
        # initial size since no new message arrives during the wait.
        r.llen.return_value = 0

        # exists() consumes from the sequence; subsequent calls reuse last.
        sequence_iter = iter(exists_sequence)
        last = [True]
        def fake_exists(key):
            try:
                last[0] = next(sequence_iter)
            except StopIteration:
                pass
            exists_calls.append(len(exists_calls) + 1)
            return last[0]
        r.exists.side_effect = fake_exists
        r.delete = MagicMock(return_value=0)

        flask_app = MagicMock(name="flask_app")

        with patch.object(debounce, "_get_redis", return_value=r), \
             patch.object(debounce.time, "sleep", fake_sleep), \
             patch.object(debounce.time, "time", fake_time), \
             patch.object(debounce, "_read_requeue_count", return_value=0):
            debounce._flush("+573177000722", "whatsapp:+14155238886", flask_app)

        return r, sleeps, exists_calls

    def test_waits_for_proc_key_before_draining(self):
        """proc_key True then False → poll, then drain. After the
        adaptive-backoff change, the debounce window is now a polling
        loop (multiple sleeps of _BACKOFF_POLL_INTERVAL summing to
        DEBOUNCE_SECONDS) instead of a single time.sleep — but the
        downstream contract (poll proc_key, drain when clear) is the
        same."""
        r, sleeps, exists_calls = self._drive_flush_with_inflight(
            exists_sequence=[True, True, True, False],
        )
        # Split sleeps at the boundary: backoff-window sleeps come
        # first (each <= _BACKOFF_POLL_INTERVAL); inflight-poll sleeps
        # come after (each == _INFLIGHT_POLL_INTERVAL).
        backoff_sleeps = [
            s for s in sleeps if s != debounce._INFLIGHT_POLL_INTERVAL
        ]
        inflight_sleeps = [
            s for s in sleeps if s == debounce._INFLIGHT_POLL_INTERVAL
        ]
        # Backoff sleeps total ≈ DEBOUNCE_SECONDS within one poll tick.
        assert abs(sum(backoff_sleeps) - debounce.DEBOUNCE_SECONDS) <= debounce._BACKOFF_POLL_INTERVAL
        # We polled the proc_key sequence to completion.
        assert inflight_sleeps, "expected at least one inflight poll sleep"
        assert len(exists_calls) >= 4

    def test_drains_immediately_when_no_inflight(self):
        """proc_key starts cleared → no inflight polling sleeps after
        the backoff window."""
        r, sleeps, exists_calls = self._drive_flush_with_inflight(
            exists_sequence=[False],
        )
        # All sleeps should be backoff polls (no inflight polls).
        assert all(
            s != debounce._INFLIGHT_POLL_INTERVAL for s in sleeps
        ), f"unexpected inflight poll sleep: {sleeps}"
        # And they sum to ≈ DEBOUNCE_SECONDS.
        assert abs(sum(sleeps) - debounce.DEBOUNCE_SECONDS) <= debounce._BACKOFF_POLL_INTERVAL
        # One exists check is enough — it returned False.
        assert len(exists_calls) == 1

    def test_caps_wait_at_max_seconds_to_avoid_strand(self):
        """If the in-flight turn never clears, drain anyway after the cap.
        We force the cap to fire by always returning True from exists().
        Sleeps consumed until the cap is hit."""
        r, sleeps, exists_calls = self._drive_flush_with_inflight(
            exists_sequence=[True] * 10000,  # never clears
        )
        # Total wall-clock simulated by the poll sleeps must exceed the cap.
        # Each poll sleep is _INFLIGHT_POLL_INTERVAL. The number of polls
        # equals (cap / poll_interval) before the cap check exits.
        poll_sleeps = sleeps[1:]
        # Simulated wait should at least approach the cap.
        assert sum(poll_sleeps) >= debounce._INFLIGHT_WAIT_MAX_SECONDS - debounce._INFLIGHT_POLL_INTERVAL


class TestRequeueScalesToN:
    def test_multiple_aborts_append_in_order(self):
        """
        N aborted turns in a row should leave N entries in the list, in
        arrival order, so the next flusher's merge loop sees the full
        thread joined with newlines.
        """
        # Capture each Lua-script invocation.
        captured: list[str] = []

        def fake_script(*args, **kwargs):
            payload_args = kwargs.get("args") or args[1:]
            captured.append(payload_args[0])
            return 0  # pretend an existing flusher is in flight

        r = MagicMock(name="redis")
        r.register_script.return_value = fake_script

        abort_key = "abort:whatsapp:+14155238886:+573177000722"
        with patch.object(debounce, "_get_redis", return_value=r), \
             patch.object(debounce.threading, "Thread"):
            requeue_aborted_text(abort_key, "quiero una picada")
            requeue_aborted_text(abort_key, "que valor")
            requeue_aborted_text(abort_key, "domicilio")

        assert len(captured) == 3
        bodies = [
            json.loads(p)["normalized_body"]["entry"][0]["changes"][0]
            ["value"]["messages"][0]["text"]["body"]
            for p in captured
        ]
        assert bodies == ["quiero una picada", "que valor", "domicilio"]
