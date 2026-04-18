"""
Per-wa_id turn serialization using Postgres advisory locks.

Background: two webhooks for the same WhatsApp ID arriving close together
(e.g. user types "hola" and "para hacer un pedido" 4 seconds apart) would
otherwise execute in parallel. The second pipeline pass loads the
pre-first-turn session state, generates a stale reply, and the customer
gets two assistant messages out of order — the second one ignoring the
existence of the first.

Fix: hold a Postgres advisory lock keyed on a hash of the wa_id for the
duration of the inbound handler. A second concurrent webhook for the
same user blocks until the first releases. The first turn commits its
session state; the second turn loads the post-first-turn state and
generates a coherent follow-up reply.

Why advisory locks specifically:
- No new infra (we already have Postgres).
- Per-key, not global: different users still process in parallel.
- Auto-released when the holding connection closes — even if the
  handler crashes mid-flight, the lock doesn't leak permanently.
- Bounded by lock_timeout — a stuck handler can't wedge a user forever.

Failure mode: if the lock can't be acquired (DB unreachable, timeout),
we log a warning and fall back to processing WITHOUT serialization.
Better to risk one stale message than to drop the customer's message
entirely. The fallback is observable via the [TURN_LOCK] warning tag.
"""

import hashlib
import logging
from contextlib import contextmanager
from typing import Iterator

from ..database.models import engine

logger = logging.getLogger(__name__)


def _wa_id_to_lock_key(wa_id: str) -> int:
    """
    Hash a wa_id to a 64-bit signed integer for pg_advisory_lock.
    Postgres advisory locks take a bigint key; we collapse the wa_id
    string to one via blake2b (collision-resistant, deterministic
    across processes, fast).
    """
    if not wa_id:
        return 0
    digest = hashlib.blake2b(wa_id.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


class TurnLockResult:
    """Yielded by wa_id_turn_lock; exposes whether the lock had to wait."""
    __slots__ = ("waited",)

    def __init__(self, waited: bool = False):
        self.waited = waited


@contextmanager
def wa_id_turn_lock(wa_id: str, *, timeout_seconds: float = 30.0) -> Iterator[TurnLockResult]:
    """
    Hold a per-wa_id Postgres advisory lock for the duration of the
    `with` block. A second concurrent caller for the same wa_id blocks
    until the first exits.

    Yields a ``TurnLockResult`` whose ``.waited`` flag is True when the
    lock was NOT immediately available (another turn was in-flight).
    Callers can use this to detect "stale turns" — messages the user
    sent before seeing the previous bot reply.

    Args:
        wa_id: WhatsApp ID (e.g. "+573001234567"). Empty/None disables
            the lock and yields immediately.
        timeout_seconds: Max time to wait for the lock before falling
            back to unsynchronized processing. Set high enough that a
            normal turn (planner LLM ~5s + response LLM ~5s + slack)
            fits, low enough that a stuck handler doesn't wedge users.

    Always yields exactly once. Releases the lock and returns the
    underlying DB-API connection to the engine on exit, success or
    failure. Failure to acquire is logged but does NOT raise — the
    block still runs without serialization in that case.
    """
    result = TurnLockResult()
    if not wa_id:
        yield result
        return

    key = _wa_id_to_lock_key(wa_id)
    conn = None
    locked = False
    try:
        try:
            conn = engine.raw_connection()
            cur = conn.cursor()

            # Probe: try to acquire without blocking to detect contention.
            cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
            got_it = cur.fetchone()[0]

            if got_it:
                # Lock was free — no other turn in-flight.
                locked = True
                cur.close()
                logger.info(f"[TURN_LOCK] acquired (no wait) wa_id={wa_id} key={key}")
            else:
                # Another turn holds the lock — this is a queued message.
                result.waited = True
                cur.close()
                logger.warning(
                    f"[TURN_LOCK] wa_id={wa_id} key={key}: lock held, waiting (stale turn)"
                )
                cur = conn.cursor()
                cur.execute(f"SET lock_timeout = '{int(timeout_seconds * 1000)}ms'")
                cur.execute("SELECT pg_advisory_lock(%s)", (key,))
                locked = True
                cur.close()
                logger.info(f"[TURN_LOCK] acquired (after wait) wa_id={wa_id} key={key}")

        except Exception as e:
            # Acquisition failed (timeout, DB unreachable, …). Fall back
            # to running without the lock so the user's message is not
            # dropped. One stale reply is preferable to no reply.
            logger.warning(
                f"[TURN_LOCK] failed to acquire wa_id={wa_id} key={key}: {e}; "
                "proceeding without serialization"
            )

        yield result
    finally:
        if locked and conn is not None:
            try:
                cur = conn.cursor()
                cur.execute("SELECT pg_advisory_unlock(%s)", (key,))
                cur.close()
                logger.info(f"[TURN_LOCK] released wa_id={wa_id} key={key}")
            except Exception as e:
                logger.warning(
                    f"[TURN_LOCK] failed to release wa_id={wa_id} key={key}: {e}"
                )
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
