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

DEBOUNCE_SECONDS = 3.0
_FLUSHER_TTL = 90   # safety expiry on flusher lock: sleep(3) + max LLM time
_MSG_TTL = 120      # safety expiry on buffered messages (seconds)

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



_LUA_DRAIN = """
local msgs = redis.call('LRANGE', KEYS[1], 0, -1)
redis.call('DEL', KEYS[1], KEYS[2])
return msgs
"""


def _flush(phone: str, business_context: dict, flask_app) -> None:
    """
    Background thread: sleep for the debounce window, then atomically
    drain the buffer and hand off to process_whatsapp_message.

    Uses a fixed sleep (not a sliding window) to avoid key_last races.
    The Lua drain (LRANGE + DEL in one round trip) ensures no message
    can slip between reading the list and clearing it.

    flask_app must be the actual Flask app object (not a proxy), captured
    in the request thread via current_app._get_current_object() and passed
    here so we can push an app context — background threads don't inherit one.
    """
    # Sleep for the full debounce window before draining.
    # All messages that arrive within this window will be in the list.
    time.sleep(DEBOUNCE_SECONDS)

    r = _get_redis()
    if r is None:
        return

    key_msgs = f"debounce:msgs:{phone}"
    key_flusher = f"debounce:flusher:{phone}"

    try:
        # ── Drain atomically via Lua (LRANGE + DEL in one round trip) ─
        drain = r.register_script(_LUA_DRAIN)
        raw_msgs = drain(keys=[key_msgs, key_flusher])

        logging.warning("[DEBOUNCE] %s: drained %d message(s) from Redis", phone, len(raw_msgs) if raw_msgs else 0)
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

        # ── Hand off through existing turn_lock inside an app context ─
        # Background threads don't inherit Flask's app context; push one
        # explicitly so current_app / current_app.config work in send_message.
        from ..handlers.whatsapp_handler import process_whatsapp_message  # avoid circular
        from .turn_lock import wa_id_turn_lock

        with flask_app.app_context():
            with wa_id_turn_lock(phone):
                process_whatsapp_message(combined_body, business_context=business_context)

    except Exception as exc:
        logging.error("[DEBOUNCE] flush error for %s: %s", phone, exc, exc_info=True)
    finally:
        # Belt-and-suspenders: release flusher lock even if Lua drain or
        # processing crashed before it could delete it.
        try:
            r = _get_redis()
            if r:
                r.delete(key_flusher)
        except Exception:
            pass


def debounce_message(
    phone: str,
    normalized_body: dict,
    business_context: dict,
    flask_app,
) -> bool:
    """
    Buffer a webhook message and start a flusher thread if needed.

    Args:
        phone: Caller's E.164 phone number (e.g. "+573001234567").
               Used as the Redis key and turn_lock key.
        normalized_body: Meta-format payload (from normalize_twilio_to_meta
               for Twilio, or the raw Meta body for the Meta path).
        business_context: The resolved business context dict for this number.
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

    key_msgs = f"debounce:msgs:{phone}"
    key_flusher = f"debounce:flusher:{phone}"

    payload = json.dumps({
        "normalized_body": normalized_body,
        "business_context": business_context,
    })

    try:
        pipe = r.pipeline()
        pipe.rpush(key_msgs, payload)
        pipe.expire(key_msgs, _MSG_TTL)
        # NX + EX: only one thread becomes the flusher per debounce window.
        # TTL covers the sleep + processing time so the lock doesn't expire mid-flight.
        pipe.set(key_flusher, "1", nx=True, ex=_FLUSHER_TTL)
        results = pipe.execute()

        is_flusher = bool(results[2])  # True if this call won the NX race
        if is_flusher:
            t = threading.Thread(
                target=_flush,
                args=(phone, business_context, flask_app),
                daemon=True,
                name=f"debounce-{phone}",
            )
            t.start()
            logging.warning(
                "[DEBOUNCE] %s: buffered + flusher started (window=%.1fs)",
                phone, DEBOUNCE_SECONDS,
            )
        else:
            logging.warning("[DEBOUNCE] %s: buffered (flusher already running)", phone)

        return True

    except Exception as exc:
        logging.warning("[DEBOUNCE] Redis error, falling back to sync for %s: %s", phone, exc)
        return False
