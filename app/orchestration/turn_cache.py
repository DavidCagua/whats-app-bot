"""
Per-turn memoization cache for the order / booking flow.

Problem: one inbound message runs through:
  whatsapp_handler -> conversation_manager -> agent_executor -> order_agent
  -> order_flow -> order_tools -> product_order_service / customer_service

Multiple layers of that stack independently call:
  - session_state_service.load(wa_id, business_id)   [2-4x per turn]
  - customer_service.get_customer(wa_id)             [2-3x per turn]
  - product_order_service.search_products(...)       [1-2x per turn]

Each call is a fresh SQLAlchemy session to Supabase — ~150-300 ms each,
and they're effectively immutable within a single turn (except through
our own writes, which we can track). The raw inventory in the
caching-strategy research flagged this as the highest-ROI win: turn
session/customer/search fan-out from O(N) into O(1) per turn.

This module holds a per-request ``TurnCache`` keyed by
``contextvars.ContextVar`` so that:
  - gunicorn gthread workers get natural thread isolation
  - the debounce flusher thread gets its own fresh cache
  - test code can call ``begin_turn()`` between cases for clean state

Invalidation:
  - session caches MUST be dropped after any ``session_state_service.save``
    because save() merges state_update server-side; the cached dict will
    not reflect merged fields the caller didn't mutate in-place.
    Callers MUST call ``invalidate_session(wa_id, business_id)`` right
    after any save they issue. Helpers in order_tools already route all
    writes through ``_save_cart``, which is the single place to hook.
  - customer caches are dropped after ``customer_service.update_customer``
    / ``create_customer`` via the same explicit invalidation pattern.
  - product search results are not invalidated within a turn — the
    catalog can't change mid-turn and Tier 2 will handle cross-turn.
"""

import contextvars
import logging
from typing import Any, Callable, Dict, Optional, Tuple


logger = logging.getLogger(__name__)

_MISSING = object()  # sentinel: distinguishes "never looked up" from "cached None"


class TurnCache:
    """
    Single-turn memoization of expensive lookups.

    Each ``get_*`` method accepts an optional ``loader`` callable. When
    provided, the loader is called on a cache miss instead of the
    default import path. This lets callers pass their own module-level
    reference to the underlying service — which is exactly what unit
    tests do via ``unittest.mock.patch``. Without this indirection the
    cache would capture a fresh import reference that bypasses the
    test's monkey patch and hit a real DB.
    """

    def __init__(self) -> None:
        self._session: Dict[Tuple[str, str], Any] = {}
        self._customer: Dict[str, Any] = {}
        self._search: Dict[Tuple[str, str, tuple], Any] = {}

    # ── session ────────────────────────────────────────────────────

    def get_session(
        self,
        wa_id: str,
        business_id: str,
        loader: Optional[Callable[[], Any]] = None,
    ):
        """
        Memoized ``session_state_service.load`` for (wa_id, business_id).

        Returns the full load result dict (``{session, is_new, is_expired}``),
        same shape as the underlying service. ``is_new`` / ``is_expired``
        reflect the state at first-load time and should not be trusted
        after the first call in a turn — callers that care about those
        flags should be the first to load and capture them.
        """
        key = (wa_id, str(business_id))
        cached = self._session.get(key, _MISSING)
        if cached is not _MISSING:
            return cached
        if loader is not None:
            result = loader()
        else:
            from ..database.session_state_service import session_state_service
            result = session_state_service.load(wa_id, str(business_id))
        self._session[key] = result
        return result

    def invalidate_session(self, wa_id: str, business_id: str) -> None:
        """Drop the cached session after a write so the next read refetches."""
        self._session.pop((wa_id, str(business_id)), None)

    # ── customer ───────────────────────────────────────────────────

    def get_customer(
        self,
        wa_id: str,
        loader: Optional[Callable[[], Any]] = None,
    ):
        """
        Memoized ``customer_service.get_customer``. Returns None on
        lookup failure just like the underlying service. A cached None
        still counts as "looked up" — we won't retry within the turn.
        """
        if not wa_id:
            return None
        cached = self._customer.get(wa_id, _MISSING)
        if cached is not _MISSING:
            return cached
        try:
            if loader is not None:
                result = loader()
            else:
                from ..database.customer_service import customer_service
                result = customer_service.get_customer(wa_id)
        except Exception as exc:
            logger.warning("[TURN_CACHE] customer lookup failed for %s: %s", wa_id, exc)
            result = None
        self._customer[wa_id] = result
        return result

    def set_customer(self, wa_id: str, customer) -> None:
        """
        Pre-populate the customer slot from an earlier fetch. Called
        from ``whatsapp_handler._agent_gate_and_name`` which already
        loads the customer for the name display — no reason to reload
        it two levels down.
        """
        if not wa_id:
            return
        self._customer[wa_id] = customer

    def invalidate_customer(self, wa_id: str) -> None:
        """Drop the cached customer after an update."""
        if wa_id:
            self._customer.pop(wa_id, None)

    # ── product search ─────────────────────────────────────────────

    def get_product_search(
        self,
        business_id: str,
        query: str,
        **kwargs,
    ):
        """
        Memoized ``product_order_service.search_products``. Caches by
        (business_id, normalized query, kwargs) so different limit /
        unique / filter shapes don't collide.

        We only cache within a turn — catalog changes from the admin
        surface are handled by Tier 2 (not yet shipped). For now, a
        hybrid-search re-query inside the same turn is the main target:
        the planner searches, the tool re-searches on fallback, and
        they arrive at identical args.
        """
        normalized = (query or "").strip().lower()
        kw_key = tuple(sorted(kwargs.items()))
        key = (str(business_id), normalized, kw_key)
        cached = self._search.get(key, _MISSING)
        if cached is not _MISSING:
            return cached
        from ..database.product_order_service import product_order_service
        result = product_order_service.search_products(
            business_id=business_id, query=query, **kwargs
        )
        self._search[key] = result
        return result


# ── context plumbing ──────────────────────────────────────────────────

_turn_cache_var: contextvars.ContextVar[Optional[TurnCache]] = contextvars.ContextVar(
    "turn_cache", default=None
)


def begin_turn() -> TurnCache:
    """
    Reset the turn cache for the current context. Call exactly once at
    the top of every inbound message handler. Returns the fresh
    TurnCache so callers can pre-populate slots they already fetched.
    """
    cache = TurnCache()
    _turn_cache_var.set(cache)
    return cache


def current() -> TurnCache:
    """
    Return the current TurnCache, creating one if none exists.

    Creating on-demand keeps cold paths (admin scripts, unit tests,
    voice-only flows that skip the usual entry) safe — they get a
    single-use cache that dies with the thread context.
    """
    cache = _turn_cache_var.get()
    if cache is None:
        cache = TurnCache()
        _turn_cache_var.set(cache)
    return cache
