"""
Per-phone message debounce for WhatsApp webhooks.

Problem: users send 2-4 messages in quick succession ("hola",
"quiero pedir", "a las 3") before the bot replies. Without debouncing,
each message triggers a separate LLM call, producing multiple partial
replies out of order.

Fix: buffer messages in Redis for a 3-second quiet window keyed by
phone number. When the window expires, flush the buffer as a single
concatenated message to process_whatsapp_message.

Design:
- Redis list  debounce:msgs:{phone}    buffered (normalized_body, ctx) pairs
- Redis key   debounce:flusher:{phone} NX lock owned by the flusher thread

Agent-agnostic: sits above process_whatsapp_message, knows nothing
about which agent handles the conversation. Works for both Twilio and
Meta (Meta uses the same phone key extracted from contacts[0].wa_id).

Fallback: if Redis is unavailable, returns False and the caller
processes synchronously via the existing turn_lock path.
"""

import json
import logging
import os
import threading
import time
import uuid

DEBOUNCE_SECONDS = float(os.getenv("DEBOUNCE_SECONDS", "1.5"))
_FLUSHER_TTL = 90   # safety expiry on flusher lock: sleep(3) + max LLM time
_MSG_TTL = 120      # safety expiry on buffered messages (seconds)
_PROCESSING_TTL = 60  # safety expiry on processing flag (seconds)
_ABORT_TTL = 30       # safety expiry on abort signal (seconds)

# ── Adaptive debounce backoff ────────────────────────────────────────
#
# (a) Window-extension on continued activity: while the flusher is
#     sleeping, if a new message arrives, push the wake time out by
#     ``_EXTENSION_SECONDS``. Caps the total wait at
#     ``_MAX_TOTAL_WAIT_SECONDS`` so the user never sits unanswered
#     forever on a long burst. Polled every ``_BACKOFF_POLL_INTERVAL``.
#
# (b) Requeue-driven backoff: each abort+requeue increments a
#     per-conversation counter (``debounce:requeue_count:...``) with
#     ``_REQUEUE_COUNT_TTL``. The next flush reads the counter and
#     starts at ``DEBOUNCE_SECONDS * 2^count``, capped at
#     ``_MAX_REQUEUE_BACKOFF_SECONDS``. After ``_REQUEUE_COUNT_TTL`` of
#     calm, the counter expires and the window resets to base — no
#     manual reset needed for healthy turns.
_EXTENSION_SECONDS = float(os.getenv("DEBOUNCE_EXTENSION_SECONDS", "1.0"))
_MAX_TOTAL_WAIT_SECONDS = float(os.getenv("DEBOUNCE_MAX_TOTAL_WAIT_SECONDS", "12.0"))
_BACKOFF_POLL_INTERVAL = float(os.getenv("DEBOUNCE_BACKOFF_POLL_INTERVAL", "0.25"))
_MAX_REQUEUE_BACKOFF_SECONDS = float(os.getenv("DEBOUNCE_MAX_REQUEUE_BACKOFF_SECONDS", "8.0"))
_REQUEUE_COUNT_TTL = int(os.getenv("DEBOUNCE_REQUEUE_COUNT_TTL", "60"))


def _requeue_count_key(to_number: str, phone: str) -> str:
    return f"debounce:requeue_count:{to_number}:{phone}"


def _bump_requeue_count(to_number: str, phone: str) -> int:
    """Atomically INCR + EXPIRE the per-conversation requeue counter.

    Each call extends the TTL so a steady stream of requeues keeps the
    counter alive; calm conversations let it expire naturally. Returns
    the new count, or 0 on failure (degrades gracefully).
    """
    r = _get_redis()
    if r is None:
        return 0
    try:
        pipe = r.pipeline()
        pipe.incr(_requeue_count_key(to_number, phone))
        pipe.expire(_requeue_count_key(to_number, phone), _REQUEUE_COUNT_TTL)
        results = pipe.execute()
        return int(results[0]) if results else 0
    except Exception:
        return 0


def _read_requeue_count(to_number: str, phone: str) -> int:
    """Read the current requeue counter; 0 when absent or Redis blips."""
    r = _get_redis()
    if r is None:
        return 0
    try:
        raw = r.get(_requeue_count_key(to_number, phone))
        return int(raw) if raw else 0
    except Exception:
        return 0


def _compute_initial_window(requeue_count: int) -> float:
    """Exponential backoff window for the next flush.

    Healthy turn (count=0)  → DEBOUNCE_SECONDS (e.g. 1.5s).
    1 recent requeue       → DEBOUNCE_SECONDS * 2 (3s).
    2 recent requeues      → DEBOUNCE_SECONDS * 4 (6s, capped at MAX).
    Capped at ``_MAX_REQUEUE_BACKOFF_SECONDS`` so we never sit forever.
    """
    if requeue_count <= 0:
        return DEBOUNCE_SECONDS
    raw = DEBOUNCE_SECONDS * (2 ** min(requeue_count, 6))  # 2^6 ≈ 96x cap defense
    return min(raw, _MAX_REQUEUE_BACKOFF_SECONDS)


# After the debounce window, also wait for any in-flight turn to finish
# before draining. This prevents the race where a new message's flusher
# wakes faster than the in-flight turn can detect+requeue: the requeued
# text would arrive after the new flusher already drained, splitting
# what should be a coalesced thread into two unrelated turns.
_INFLIGHT_WAIT_MAX_SECONDS = 10.0
_INFLIGHT_POLL_INTERVAL = 0.1


def _processing_key(to_number: str, phone: str) -> str:
    return f"processing:{to_number}:{phone}"


def _abort_key(to_number: str, phone: str) -> str:
    return f"abort:{to_number}:{phone}"


def check_abort(abort_key: str) -> bool:
    """
    Check if an abort signal exists for the given key.
    Called by the handler layer before sending a response — if True,
    a newer message arrived during processing and this response is stale.
    """
    if not abort_key:
        return False
    r = _get_redis()
    if r is None:
        return False
    try:
        return bool(r.exists(abort_key))
    except Exception:
        return False


def clear_abort(abort_key: str) -> None:
    """Delete the abort flag after it has been consumed."""
    if not abort_key:
        return
    r = _get_redis()
    if r is None:
        return
    try:
        r.delete(abort_key)
    except Exception:
        pass


def requeue_aborted_text(abort_key: str, text: str) -> None:
    """
    On abort-after-planner, push the aborted user text back into the
    debounce buffer AND make sure a flusher will eventually pick it up.

    We derive (to_number, phone) from the abort_key itself — format is
    "abort:{to_number}:{phone}" — so callers don't need to thread the
    routing info through the agent layer.

    The previous flusher already released its lock at drain time
    (_LUA_DRAIN deletes both buffer and lock), so just rpushing leaves
    the requeued text orphaned until the next inbound webhook. To avoid
    that, we use the same atomic _LUA_BUFFER (rpush + SET NX flusher
    lock); if we win the lock, we spawn a flusher thread ourselves.

    Wraps the text in a minimal Meta-shaped payload matching what
    debounce_message() stores, so _flush()'s merge loop picks it up
    transparently.
    """
    if not (abort_key and (text or "").strip()):
        return
    if not abort_key.startswith("abort:"):
        return
    # phone is the last segment (E.164, contains no ':'); to_number may
    # be "whatsapp:+57..." so we rsplit instead of plain split.
    rest = abort_key[len("abort:"):]
    try:
        to_number, phone = rest.rsplit(":", 1)
    except ValueError:
        return
    if not (to_number and phone):
        return
    r = _get_redis()
    if r is None:
        return
    key_msgs = f"debounce:msgs:{to_number}:{phone}"
    key_flusher = f"debounce:flusher:{to_number}:{phone}"
    # Carry identity (wa_id from the abort_key) into the requeued payload.
    # Earlier versions wrote a stripped envelope with no contacts[]; that
    # worked only when a *future* full webhook arrived to merge — the new
    # flusher we now spawn for solo requeues would drain an identity-less
    # entry and the send would fail with Twilio 21211 ("To" number is "+").
    # Synthesizing a message id avoids logging `turn_id=-` and prevents
    # any dedup collision when the same text is requeued twice.
    synthetic_id = f"requeue-{uuid.uuid4().hex[:8]}-{int(time.time())}"
    # _skip_persist marks this entry as "do not persist as a new
    # conversations row" — the original Twilio webhook already
    # persisted the user message before the agent ran. Without this
    # flag, the requeued text gets re-persisted on the next flush,
    # producing duplicate `role='user'` rows for one Twilio inbound
    # (the visible "user message duplicated in inbox" bug).
    payload = json.dumps({
        "_skip_persist": True,
        "normalized_body": {
            "entry": [{"changes": [{"value": {
                "contacts": [{"wa_id": phone}],
                "messages": [{
                    "id": synthetic_id,
                    "type": "text",
                    "text": {"body": text.strip()},
                }],
            }}]}]
        }
    })
    try:
        # LPUSH so the requeued (chronologically older) text lands at the
        # head of the buffer ahead of any message that arrived during the
        # aborted turn — preserves the customer's actual order of speech.
        buf = r.register_script(_LUA_BUFFER_PREPEND)
        lua_result = buf(
            keys=[key_msgs, key_flusher],
            args=[payload, _MSG_TTL, _FLUSHER_TTL],
        )
        won_flusher = bool(lua_result)
        # Note: we do NOT bump the requeue counter here — it was
        # already bumped at abort-signal time (inside debounce_message)
        # so the new flusher reads the post-bump value before sleeping.
        # Bumping again here would double-count the same thrash.
        current_count = _read_requeue_count(to_number, phone)
        logging.warning(
            "[DEBOUNCE] %s: requeued aborted text (%d chars) to=%s "
            "won_flusher=%s requeue_count=%d",
            phone, len(text), to_number, won_flusher, current_count,
        )
        if won_flusher:
            # Capture the Flask app from the current context so the
            # background _flush thread can push one for send_message /
            # config access. Requeue runs inside flask_app.app_context()
            # (set by the parent flusher), so current_app is available.
            try:
                from flask import current_app
                flask_app = current_app._get_current_object()
            except Exception as exc:
                logging.error(
                    "[DEBOUNCE] %s: cannot resolve flask_app for requeued flusher: %s",
                    phone, exc,
                )
                return
            t = threading.Thread(
                target=_flush,
                args=(phone, to_number, flask_app),
                daemon=True,
                name=f"debounce-{phone}-requeue",
            )
            t.start()
    except Exception as exc:
        logging.warning("[DEBOUNCE] %s: requeue failed: %s", phone, exc)

_redis_client = None
_init_lock = threading.Lock()


def _get_redis():
    """
    Lazy-init a shared Redis client from REDIS_URL.
    Returns None (silently) if REDIS_URL is not set or Redis is unreachable.
    """
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    with _init_lock:
        if _redis_client is not None:
            return _redis_client
        url = os.getenv("REDIS_URL")
        if not url:
            return None
        try:
            import redis as redis_lib
            client = redis_lib.from_url(url, decode_responses=True, socket_timeout=2)
            client.ping()
            _redis_client = client
            logging.info("[DEBOUNCE] Redis connected")
        except Exception as exc:
            logging.warning("[DEBOUNCE] Redis unavailable (%s); debounce disabled", exc)
    return _redis_client



# Atomically push a message and try to claim the flusher lock in one round trip.
# Returns 1 if this caller won the NX race (should start the flusher), 0 otherwise.
# Using Lua ensures RPUSH and SET NX are never interleaved with another client's
# commands — the only way to guarantee exactly-one-flusher-per-window.
_LUA_BUFFER = """
redis.call('RPUSH', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[2])
local won = redis.call('SET', KEYS[2], '1', 'NX', 'EX', ARGV[3])
if won then return 1 else return 0 end
"""

# LPUSH variant for requeue_aborted_text. The aborted text arrived
# chronologically BEFORE the message that triggered the abort, so it
# belongs at the head of the buffer — not the tail. Otherwise the merge
# loop in _flush() inverts the customer's words ("una barracuda" then
# "mejor dos" → "mejor dos\nuna barracuda" → planner can't infer that
# "mejor dos" was a quantity modifier on the prior message).
_LUA_BUFFER_PREPEND = """
redis.call('LPUSH', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[2])
local won = redis.call('SET', KEYS[2], '1', 'NX', 'EX', ARGV[3])
if won then return 1 else return 0 end
"""

# Atomically drain all buffered messages and release the flusher lock.
_LUA_DRAIN = """
local msgs = redis.call('LRANGE', KEYS[1], 0, -1)
redis.call('DEL', KEYS[1], KEYS[2])
return msgs
"""


def _flush(phone: str, to_number: str, flask_app) -> None:
    """
    Background thread: sleep for the debounce window, then atomically
    drain the buffer and hand off to process_whatsapp_message.

    Business lookup happens here — *after* the debounce sleep — so the
    webhook thread can return immediately and the 3 s quiet window
    starts on message arrival, not after a ~3–5 s Supabase round trip.
    One lookup per flush instead of one per message.

    flask_app must be the actual Flask app object (not a proxy), captured
    in the request thread via current_app._get_current_object() and passed
    here so we can push an app context — background threads don't inherit one.
    """
    # Unique ID so we can trace which drain belongs to which flusher.
    fid = uuid.uuid4().hex[:6]
    t0 = time.time()

    r = _get_redis()
    if r is None:
        return

    # Key by (to_number, phone) — each business owns a unique Twilio
    # number, so this preserves cross-tenant isolation without needing
    # a business_id lookup on the hot path.
    key_msgs = f"debounce:msgs:{to_number}:{phone}"
    key_flusher = f"debounce:flusher:{to_number}:{phone}"
    proc_key = _processing_key(to_number, phone)

    # Adaptive debounce window:
    #   (b) Initial window grows exponentially with the recent
    #       requeue count (DEBOUNCE_SECONDS * 2^count, capped).
    #   (a) Each new arrival during the wait extends the window by
    #       _EXTENSION_SECONDS, capped at _MAX_TOTAL_WAIT_SECONDS
    #       (absolute deadline anchored on flusher start).
    requeue_count = _read_requeue_count(to_number, phone)
    initial_window = _compute_initial_window(requeue_count)
    deadline = t0 + initial_window
    hard_deadline = t0 + _MAX_TOTAL_WAIT_SECONDS
    last_buffer_size = -1
    try:
        last_buffer_size = int(r.llen(key_msgs) or 0)
    except Exception:
        pass
    extensions = 0
    logging.warning(
        "[DEBOUNCE] %s fid=%s: window_start=%.2fs requeue_count=%d cap=%.1fs "
        "(pid=%d t=%.3f)",
        phone, fid, initial_window, requeue_count, _MAX_TOTAL_WAIT_SECONDS,
        os.getpid(), t0,
    )
    while True:
        now = time.time()
        if now >= deadline or now >= hard_deadline:
            break
        # Sleep to the next decision point: whichever comes first —
        # the deadline, the hard deadline, or the next poll tick.
        sleep_for = min(deadline - now, hard_deadline - now, _BACKOFF_POLL_INTERVAL)
        if sleep_for <= 0:
            break
        time.sleep(sleep_for)
        # Did a new message arrive during this poll interval? If so,
        # push the deadline out (capped at hard_deadline).
        try:
            current_size = int(r.llen(key_msgs) or 0)
        except Exception:
            current_size = last_buffer_size
        if last_buffer_size >= 0 and current_size > last_buffer_size:
            new_deadline = min(time.time() + _EXTENSION_SECONDS, hard_deadline)
            if new_deadline > deadline:
                deadline = new_deadline
                extensions += 1
                logging.warning(
                    "[DEBOUNCE] %s fid=%s: window extended (msgs=%d→%d, "
                    "extension=%.1fs, total_extensions=%d)",
                    phone, fid, last_buffer_size, current_size,
                    _EXTENSION_SECONDS, extensions,
                )
        last_buffer_size = current_size

    t1 = time.time()
    logging.warning(
        "[DEBOUNCE] %s fid=%s: woke after %.3fs (extensions=%d, pid=%d t=%.3f)",
        phone, fid, t1 - t0, extensions, os.getpid(), t1,
    )

    # Wait for any in-flight turn to finish before draining. The in-flight
    # turn may be about to requeue its text (because we already set the
    # ABORT signal at message-arrival time). If we drain now, the requeue
    # arrives too late and our drain processes only the newer message —
    # producing two disconnected replies for what was meant to be one
    # coalesced thread (e.g. "La barracuda" then "Que valor?").
    wait_started = time.time()
    waited = False
    while True:
        try:
            if not r.exists(proc_key):
                break
        except Exception:
            # Redis blip — give up the wait, drain best-effort.
            break
        if (time.time() - wait_started) >= _INFLIGHT_WAIT_MAX_SECONDS:
            logging.warning(
                "[DEBOUNCE] %s fid=%s: in-flight wait exceeded %.1fs, draining anyway",
                phone, fid, _INFLIGHT_WAIT_MAX_SECONDS,
            )
            break
        waited = True
        time.sleep(_INFLIGHT_POLL_INTERVAL)
    if waited:
        logging.warning(
            "[DEBOUNCE] %s fid=%s: waited %.3fs for in-flight turn to finish",
            phone, fid, time.time() - wait_started,
        )

    try:
        # ── Drain atomically via Lua (LRANGE + DEL in one round trip) ─
        drain = r.register_script(_LUA_DRAIN)
        raw_msgs = drain(keys=[key_msgs, key_flusher])

        logging.warning("[DEBOUNCE] %s fid=%s: drained %d message(s)", phone, fid, len(raw_msgs) if raw_msgs else 0)
        if not raw_msgs:
            return

        logging.warning("[DEBOUNCE] %s: coalescing %d message(s)", phone, len(raw_msgs))

        entries = []
        for raw in raw_msgs:
            try:
                entries.append(json.loads(raw))
            except Exception:
                pass

        if not entries:
            return

        # ── Merge: concat text bodies into a base payload that has full
        # identity (contacts.wa_id, messages.from, metadata). When abort
        # carry-forward is in play, entries[0] may be a stripped requeue
        # payload missing those fields — picking it as base would yield
        # an empty wa_id and a Twilio "whatsapp:+" send error. So we
        # pick the first entry whose contacts.wa_id is populated, and
        # fall back to entries[0] only if none qualify.
        def _has_full_identity(entry: dict) -> bool:
            try:
                contacts = (
                    entry["normalized_body"]["entry"][0]["changes"][0]
                    ["value"].get("contacts") or []
                )
                return bool(contacts and contacts[0].get("wa_id"))
            except (KeyError, IndexError, TypeError):
                return False

        base_entry = next(
            (e for e in entries if _has_full_identity(e)), entries[0]
        )
        combined_body = base_entry["normalized_body"]
        if len(entries) > 1:
            texts = []
            for entry in entries:
                try:
                    text = (
                        entry["normalized_body"]
                        ["entry"][0]["changes"][0]["value"]
                        ["messages"][0]["text"]["body"]
                    )
                except (KeyError, IndexError, TypeError):
                    text = ""
                if text.strip():
                    texts.append(text.strip())

            # Dedupe adjacent byte-identical texts before joining. A user
            # who taps send twice (or whose client double-fires) produces
            # two identical buffered entries; joining them with "\n" makes
            # the planner read "X\nX" as two separate items and add 2× to
            # cart. Strict equality only — case/whitespace differences are
            # user signal we don't erase at the transport layer; semantic
            # dedup is the planner's job.
            deduped: list[str] = []
            for t in texts:
                if deduped and deduped[-1] == t:
                    continue
                deduped.append(t)
            if len(deduped) < len(texts):
                logging.info(
                    "[DEBOUNCE] %s: collapsed %d duplicate adjacent message(s)",
                    phone, len(texts) - len(deduped),
                )
            texts = deduped

            if texts:
                try:
                    combined_body["entry"][0]["changes"][0]["value"][
                        "messages"
                    ][0]["text"]["body"] = "\n".join(texts)
                except (KeyError, IndexError, TypeError) as exc:
                    logging.warning(
                        "[DEBOUNCE] %s: merge failed (%s); using first message only",
                        phone, exc,
                    )

        # ── Resolve business context inside the flusher ──────────────
        # Done here (not in the webhook thread) so the debounce window
        # isn't eaten by the ~3–5 s Supabase round trip. One lookup
        # serves the whole coalesced batch — used for both per-entry
        # persist and the agent dispatch below.
        from ..utils.twilio_utils import resolve_twilio_business_context
        business_context = resolve_twilio_business_context(to_number)

        # ── Per-entry persist (Option A, 2026-05-11) ─────────────────
        # Achieves 1:1 between Twilio inbound webhook and
        # conversations.role='user' row even when this flush coalesces
        # multiple entries. The flusher then merges them for the agent
        # so the bot still replies once on the full thread.
        #
        # Why not at webhook time (Option C)? Tried 2026-05-10 and
        # reverted: synchronous business lookup + persist in the
        # webhook thread hung the handler for >130s when Supabase's
        # pooler had connection-timeout blips, breaking prod. Doing
        # it here keeps DB writes off the hot path.
        #
        # Entries with ``_skip_persist=True`` are the requeue path —
        # they were already persisted on their original webhook's
        # first flush. Re-persisting would re-create the dup.
        from ..handlers.whatsapp_handler import (
            persist_buffered_entry,
            process_whatsapp_message,
        )  # avoid circular
        from .turn_lock import wa_id_turn_lock

        # Set processing flag so new arrivals can signal an abort.
        proc_key = _processing_key(to_number, phone)
        ab_key = _abort_key(to_number, phone)
        try:
            r.set(proc_key, "1", ex=_PROCESSING_TTL)
        except Exception:
            pass

        persisted_count = 0
        skipped_count = 0
        for entry in entries:
            if entry.get("_skip_persist"):
                skipped_count += 1
                continue
            try:
                persist_buffered_entry(
                    entry.get("normalized_body") or {},
                    business_context=business_context,
                    abort_key=ab_key,
                )
                persisted_count += 1
            except Exception as exc:
                logging.warning(
                    "[DEBOUNCE] %s: per-entry persist failed: %s",
                    phone, exc,
                )
        if persisted_count or skipped_count:
            logging.warning(
                "[DEBOUNCE] %s fid=%s: persisted=%d skipped=%d (entries=%d)",
                phone, fid, persisted_count, skipped_count, len(entries),
            )

        was_aborted = False
        with flask_app.app_context():
            with wa_id_turn_lock(phone) as lock_result:
                # skip_persist=True always — per-entry persist above is
                # the only place we write user rows from the flush path.
                was_aborted = bool(process_whatsapp_message(
                    combined_body,
                    business_context=business_context,
                    abort_key=ab_key,
                    stale_turn=lock_result.waited,
                    skip_persist=True,
                ))

        # Counter decay is intentionally TTL-only (not reset on first
        # clean turn). Eager reset turned out to be too aggressive in
        # production: a single thrash bumped the counter, the next
        # turn cleared it, and the third turn was back to a 1.5s
        # window — so a chatty conversation kept seeing rapid replies
        # during follow-up messages even though it had just thrashed
        # seconds ago. With TTL-only decay, the backoff persists for
        # ``_REQUEUE_COUNT_TTL`` seconds (60s default) of inactivity
        # before resetting; bursts within that window stay in the
        # backed-off regime. The log line below makes it visible.
        if was_aborted:
            logging.warning(
                "[DEBOUNCE] %s fid=%s: turn aborted — requeue_count preserved (backoff stays)",
                phone, fid,
            )
        else:
            try:
                preserved = _read_requeue_count(to_number, phone) if r else 0
            except Exception:
                preserved = 0
            if preserved > 0:
                logging.warning(
                    "[DEBOUNCE] %s fid=%s: clean turn — requeue_count=%d preserved (TTL %ds)",
                    phone, fid, preserved, _REQUEUE_COUNT_TTL,
                )

    except Exception as exc:
        logging.error("[DEBOUNCE] flush error for %s: %s", phone, exc, exc_info=True)
    finally:
        # Clean up processing flag only. Do NOT delete key_flusher here —
        # _LUA_DRAIN already deleted it at drain time, and a newer
        # flusher may have re-created it since then. Deleting it here
        # would destroy the newer flusher's lock, causing the next
        # message to start a duplicate flusher instead of coalescing.
        try:
            r = _get_redis()
            if r:
                r.delete(_processing_key(to_number, phone))
        except Exception:
            pass


def debounce_message(
    phone: str,
    to_number: str,
    normalized_body: dict,
    flask_app,
) -> bool:
    """
    Buffer a webhook message and start a flusher thread if needed.

    Called *before* business context resolution so the quiet window
    starts on message arrival, not after a Supabase round trip. The
    flusher resolves business context once, after the window expires.

    Args:
        phone: Caller's E.164 phone number (e.g. "+573001234567").
               Used as part of the Redis key and as the turn_lock key.
        to_number: The Twilio `To` number (business-owned WhatsApp line).
               Scopes the debounce key so messages to different
               businesses from the same phone don't get coalesced.
        normalized_body: Meta-format payload (from normalize_twilio_to_meta
               for Twilio, or the raw Meta body for the Meta path).
        flask_app: Actual Flask app object captured via
               current_app._get_current_object() in the request thread.
               Passed to the flusher so it can push an app context.

    Returns:
        True  – message buffered; caller must return 200 immediately
                WITHOUT calling process_whatsapp_message.
        False – Redis unavailable; caller falls back to synchronous
                processing via the existing turn_lock path.
    """
    if not phone:
        return False

    r = _get_redis()
    if r is None:
        return False

    key_msgs = f"debounce:msgs:{to_number}:{phone}"
    key_flusher = f"debounce:flusher:{to_number}:{phone}"

    payload = json.dumps({"normalized_body": normalized_body})

    try:
        # Atomic Lua: RPUSH + SET NX in one script so no other client can
        # slip between them. Returns 1 if this caller won the flusher lock.
        buf = r.register_script(_LUA_BUFFER)
        lua_result = buf(
            keys=[key_msgs, key_flusher],
            args=[payload, _MSG_TTL, _FLUSHER_TTL],
        )
        is_flusher = bool(lua_result)
        if is_flusher:
            t = threading.Thread(
                target=_flush,
                args=(phone, to_number, flask_app),
                daemon=True,
                name=f"debounce-{phone}",
            )
            t.start()
            logging.warning(
                "[DEBOUNCE] %s: NEW flusher lua=%s pid=%d t=%.3f",
                phone, lua_result, os.getpid(), time.time(),
            )
        else:
            logging.warning(
                "[DEBOUNCE] %s: COALESCED lua=%s pid=%d t=%.3f",
                phone, lua_result, os.getpid(), time.time(),
            )

        # If a previous flusher already drained and is processing
        # (not just sleeping), signal it to abort before executor —
        # this new message supersedes the in-flight turn.
        # Runs for ALL messages (not just coalesced) because _LUA_DRAIN
        # releases the flusher lock at drain time, so new messages
        # arriving during processing get lua_result=1 (own flusher).
        #
        # Bump the requeue counter HERE (at signal time) instead of in
        # requeue_aborted_text (at abort-handling time). Reason: the
        # new flusher we just spawned will read the counter BEFORE the
        # in-flight turn finishes its abort path. If we wait for the
        # abort handler to bump, every new flusher sees the stale
        # pre-thrash value and commits to the base window — defeating
        # backoff (b). Setting the signal IS the moment we know a
        # thrash just started; bump now.
        proc_key = _processing_key(to_number, phone)
        try:
            if r.exists(proc_key):
                ab_key = _abort_key(to_number, phone)
                r.set(ab_key, "1", ex=_ABORT_TTL)
                requeue_count = _bump_requeue_count(to_number, phone)
                logging.warning(
                    "[DEBOUNCE] %s: ABORT signal set (processing in-flight) "
                    "requeue_count=%d t=%.3f",
                    phone, requeue_count, time.time(),
                )
        except Exception as exc:
            logging.warning("[DEBOUNCE] %s: failed to set abort: %s", phone, exc)

        return True

    except Exception as exc:
        logging.warning("[DEBOUNCE] Redis error, falling back to sync for %s: %s", phone, exc)
        return False
