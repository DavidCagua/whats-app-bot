"""
Process-memory TTL cache for product catalog reads.

Problem: every order turn hits the DB for the same catalog data —
  - product_order_service.list_categories(business_id)
  - product_order_service.list_products(business_id, category=...)
  - product_order_service.list_products_with_fallback(business_id, category)

The product catalog is effectively immutable on the webhook hot path.
Admins add/remove products through a separate Next.js admin console
(writes go via Prisma), so the Python process never sees a mutation.
For the bot-facing read path, caching these results for a few minutes
is essentially free — and saves one round trip per turn per read.

Staleness contract
------------------
The only invalidation mechanism is TTL (``_TTL_SECONDS``). Catalog
writes from the admin console cannot cross into this process, and the
Python-side CLI script that regenerates metadata
(``scripts/generate_product_metadata.py``) does not currently call us.

Consequence: menu changes from admins can take up to ``_TTL_SECONDS``
to reflect in the bot. If that becomes a problem, the next step is a
Redis-backed version stamp (``GET catalog:version:{business_id}`` on
every read, bumped by admin writes) — see the caching-strategy plan in
conversation history for the design sketch. Keeping it simple until we
hit a real complaint.

Design
------
``_cache`` is a flat ``{(business_id, method, args_tuple): (expiry, value)}``
dict protected by ``_lock``. ``get_or_fetch`` is the single entry point:
it checks the entry, calls the loader on a miss (or expiry), stores,
and returns.

``invalidate(business_id)`` scans the dict and drops every key matching
that tenant — O(n) in total cached entries, but n is small (one per
(business, method, args) combination) and invalidation is rare.
"""

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple


logger = logging.getLogger(__name__)

_TTL_SECONDS = 300.0  # 5 min — matches business_service cache
_cache: Dict[Tuple[str, str, tuple], Tuple[float, Any]] = {}
_lock = threading.Lock()


def get_or_fetch(
    business_id: str,
    method: str,
    args: tuple,
    loader: Callable[[], Any],
    ttl_seconds: float = _TTL_SECONDS,
) -> Any:
    """
    Return the cached value for (business_id, method, args), calling
    ``loader`` on miss or expiry.

    ``method`` is a short string tag used to differentiate cached data
    for the same business across the methods we wrap (e.g.
    ``"list_products"``, ``"list_categories"``).

    ``args`` is a tuple of the remaining arguments used to build the
    result (e.g. ``(category,)``). Must be hashable.
    """
    key = (str(business_id), method, args)
    now = time.time()
    with _lock:
        cached = _cache.get(key)
        if cached and cached[0] > now:
            return cached[1]

    # Loader runs outside the lock so a slow DB call doesn't block
    # unrelated cache reads.
    value = loader()
    with _lock:
        _cache[key] = (now + ttl_seconds, value)
    return value


def invalidate(business_id: str) -> int:
    """
    Drop every cached entry for ``business_id``. Returns the number of
    entries removed (useful for logs / metrics).

    Callable from Python-side writes (e.g. the metadata generation
    script) so the next read after a mutation rebuilds the cache.
    """
    if not business_id:
        return 0
    target = str(business_id)
    removed = 0
    with _lock:
        keys = [k for k in _cache if k[0] == target]
        for k in keys:
            _cache.pop(k, None)
            removed += 1
    if removed:
        logger.info("[CATALOG_CACHE] invalidated %d entries for %s", removed, target)
    return removed


def invalidate_all() -> int:
    """Drop every cached entry. Useful for tests and emergency resets."""
    with _lock:
        n = len(_cache)
        _cache.clear()
    if n:
        logger.info("[CATALOG_CACHE] invalidated all %d entries", n)
    return n


# ── Convenience wrappers for the methods we actually cache ─────────────
# These live in this module (not product_order_service) so the caching
# layer is a discrete seam you can remove or reroute without touching
# the DB service internals.


def list_categories(business_id: str) -> List[str]:
    """Cached ``product_order_service.list_categories``."""
    from ..database.product_order_service import product_order_service
    return get_or_fetch(
        business_id,
        "list_categories",
        (),
        lambda: product_order_service.list_categories(business_id=business_id),
    )


def list_products(business_id: str, category: Optional[str] = None) -> List[Dict]:
    """Cached ``product_order_service.list_products``."""
    from ..database.product_order_service import product_order_service
    # Normalize category to a stable cache key (None vs "" vs "  " must collapse).
    cat_key = (category or "").strip().lower() or None
    return get_or_fetch(
        business_id,
        "list_products",
        (cat_key,),
        lambda: product_order_service.list_products(
            business_id=business_id, category=category
        ),
    )


def list_products_with_fallback(business_id: str, category: str) -> List[Dict]:
    """Cached ``product_order_service.list_products_with_fallback``."""
    from ..database.product_order_service import product_order_service
    cat_key = (category or "").strip().lower()
    return get_or_fetch(
        business_id,
        "list_products_with_fallback",
        (cat_key,),
        lambda: product_order_service.list_products_with_fallback(
            business_id=business_id, category=category
        ),
    )
