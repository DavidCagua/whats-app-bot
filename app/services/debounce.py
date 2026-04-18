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

DEBOUNCE_SECONDS = 0.5
_FLUSHER_TTL = 90   # safety expiry on flusher lock: sleep(3) + max LLM time
_MSG_TTL = 120      # safety expiry on buffered messages (seconds)
_PROCESSING_TTL = 60  # safety expiry on processing flag (seconds)
_ABORT_TTL = 30       # safety expiry on abort signal (seconds)


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
    logging.warning("[DEBOUNCE] %s fid=%s: sleeping %.1fs (pid=%d)", phone, fid, DEBOUNCE_SECONDS, os.getpid())
    time.sleep(DEBOUNCE_SECONDS)
    logging.warning("[DEBOUNCE] %s fid=%s: woke up (pid=%d)", phone, fid, os.getpid())

    r = _get_redis()
    if r is None:
        return

    # Key by (to_number, phone) — each business owns a unique Twilio
    # number, so this preserves cross-tenant isolation without needing
    # a business_id lookup on the hot path.
    key_msgs = f"debounce:msgs:{to_number}:{phone}"
    key_flusher = f"debounce:flusher:{to_number}:{phone}"

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

        # ── Merge: concat text bodies into the first message's payload ─
        combined_body = entries[0]["normalized_body"]
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
        # serves the whole coalesced batch.
        from ..utils.twilio_utils import resolve_twilio_business_context
        business_context = resolve_twilio_business_context(to_number)

        # ── Hand off through existing turn_lock inside an app context ─
        # Background threads don't inherit Flask's app context; push one
        # explicitly so current_app / current_app.config work in send_message.
        from ..handlers.whatsapp_handler import process_whatsapp_message  # avoid circular
        from .turn_lock import wa_id_turn_lock

        # Set processing flag so new arrivals can signal an abort.
        proc_key = _processing_key(to_number, phone)
        ab_key = _abort_key(to_number, phone)
        try:
            r.set(proc_key, "1", ex=_PROCESSING_TTL)
        except Exception:
            pass

        with flask_app.app_context():
            with wa_id_turn_lock(phone) as lock_result:
                process_whatsapp_message(
                    combined_body,
                    business_context=business_context,
                    abort_key=ab_key,
                    stale_turn=lock_result.waited,
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
                "[DEBOUNCE] %s: buffered + flusher started (window=%.1fs) lua=%s pid=%d",
                phone, DEBOUNCE_SECONDS, lua_result, os.getpid(),
            )
        else:
            logging.warning(
                "[DEBOUNCE] %s: COALESCED (flusher lock held) lua=%s pid=%d",
                phone, lua_result, os.getpid(),
            )
            # If the previous flusher already drained and is processing
            # (not just sleeping), signal it to abort before executor —
            # this new message supersedes the in-flight turn.
            proc_key = _processing_key(to_number, phone)
            try:
                if r.exists(proc_key):
                    ab_key = _abort_key(to_number, phone)
                    r.set(ab_key, "1", ex=_ABORT_TTL)
                    logging.warning(
                        "[DEBOUNCE] %s: abort signal set (new message during processing)",
                        phone,
                    )
            except Exception as exc:
                logging.warning("[DEBOUNCE] %s: failed to set abort: %s", phone, exc)

        return True

    except Exception as exc:
        logging.warning("[DEBOUNCE] Redis error, falling back to sync for %s: %s", phone, exc)
        return False
