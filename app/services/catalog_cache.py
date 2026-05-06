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
import re
import threading
import time
import unicodedata
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple


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


# ── Router lookup-set ──────────────────────────────────────────────────
# A flat ``frozenset[str]`` of normalized tokens that signal "the user
# named something from the catalog". Used by the router's deterministic
# pre-classifier to route price questions about named products to the
# order agent without an LLM call. See app/orchestration/router.py.
#
# Built once per (business_id, TTL window) from the cached product list:
#   - product name tokens (post-normalize, post-stopword)
#   - tag tokens
#   - synonym keys + values (single-word entries)
# Words that would create false positives for policy questions (e.g.
# "cuánto vale el domicilio") are dropped — those must stay in CS.

_NON_PRODUCT_TOKENS: FrozenSet[str] = frozenset({
    "domicilio", "domicilios",
    "envio", "envios", "delivery",
    "propina", "propinas",
    "menu", "carta",
    "pedido", "pedidos", "orden", "ordenes",
    "factura", "facturas", "recibo", "recibos",
    "horario", "horarios",
    "direccion", "telefono", "ubicacion",
})

_TOKEN_STOPWORDS: FrozenSet[str] = frozenset({
    "el", "la", "los", "las", "un", "una", "unos", "unas",
    "de", "del", "y", "o", "u", "a", "al", "en", "con", "sin",
    "para", "por", "que", "mi", "tu", "su",
    "es", "son", "esta", "este", "esto", "esa", "ese", "eso",
    "muy", "mas", "menos", "ya", "no", "si",
})


def _normalize_token(s: str) -> str:
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFD", s.lower())
    cleaned = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w]", "", cleaned)


def _split_tokens(text: str) -> List[str]:
    if not text:
        return []
    nfkd = unicodedata.normalize("NFD", text.lower())
    cleaned = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    return [t for t in cleaned.split() if t]


def _build_router_lookup_set(business_id: str) -> FrozenSet[str]:
    """
    Build the router's product-token set for a business. Reads only
    from already-cached helpers so a warm cache pays nothing extra.
    """
    tokens: set = set()

    products = list_products(business_id) or []
    for p in products:
        for t in _split_tokens(p.get("name") or ""):
            tokens.add(t)
        for tag in (p.get("tags") or []):
            nt = _normalize_token(tag)
            if nt:
                tokens.add(nt)

    # Pull synonyms via a sibling cache entry — same TTL window, so
    # we don't re-query the DB on every router turn.
    try:
        synonyms = get_or_fetch(
            business_id,
            "router_synonyms",
            (),
            lambda: _load_synonyms_for_router(business_id),
        )
    except Exception as exc:
        logger.warning("[CATALOG_CACHE] router synonyms load failed: %s", exc)
        synonyms = {}

    for key, vals in (synonyms or {}).items():
        nt = _normalize_token(key)
        if nt:
            tokens.add(nt)
        for v in vals or []:
            nt = _normalize_token(v)
            if nt:
                tokens.add(nt)

    # Strip stopwords, denylist, and short noise.
    tokens = {
        t for t in tokens
        if len(t) >= 3
        and t not in _TOKEN_STOPWORDS
        and t not in _NON_PRODUCT_TOKENS
    }
    return frozenset(tokens)


def _load_synonyms_for_router(business_id: str) -> Dict[str, List[str]]:
    """Read business.settings.search_synonyms for the lookup-set build."""
    try:
        from ..database.models import Business, get_db_session
        import uuid as _uuid
        db = get_db_session()
        try:
            biz = db.query(Business).filter(Business.id == _uuid.UUID(business_id)).first()
            if not biz or not biz.settings:
                return {}
            settings = biz.settings if isinstance(biz.settings, dict) else {}
            raw = settings.get("search_synonyms") or {}
            if not isinstance(raw, dict):
                return {}
            return {str(k): list(v) for k, v in raw.items() if isinstance(v, list)}
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[CATALOG_CACHE] _load_synonyms_for_router failed: %s", exc)
        return {}


def get_router_lookup_set(business_id: str) -> FrozenSet[str]:
    """
    Return a frozenset of normalized tokens that mean "the user named
    something from the catalog" for ``business_id``.

    Cached with the same TTL as the underlying catalog reads. Cheap on
    a hit (one dict lookup); on a miss reuses the already-cached
    product list and pays one extra DB read for synonyms.
    """
    if not business_id:
        return frozenset()
    return get_or_fetch(
        business_id,
        "router_lookup_set",
        (),
        lambda: _build_router_lookup_set(business_id),
    )


# ── Multi-word product-name lookup ─────────────────────────────────
# Mapping from normalized multi-word product names → canonical
# (uppercase) catalog name. Used by the router's substring short-
# circuit so messages like "una hamburguesa a la vuelta" can be
# anchored to the actual catalog product LA VUELTA before the LLM
# planner sees them — same shape as the stuck-article splitter, for
# the multi-word case.
#
# Single-word product names (BARRACUDA, BIMOTA, ...) are NOT here —
# they're already covered by ``get_router_lookup_set`` and the
# stuck-article splitter. Including them would over-fire on single
# tokens that show up inside common phrases.
#
# Minimum length 5 to skip generics like "soda", "agua", "leche".


def _build_router_full_name_map(business_id: str) -> Dict[str, str]:
    """
    Return ``{normalized_name: canonical_catalog_name}`` for every
    multi-word active product (≥ 2 tokens after stopword filtering and
    ≥ 5 chars overall). Synonyms with multi-word values are included
    too so admin-defined aliases work.
    """
    out: Dict[str, str] = {}
    products = list_products(business_id) or []
    for p in products:
        canonical = (p.get("name") or "").strip()
        if not canonical:
            continue
        tokens = _split_tokens(canonical)
        if not tokens:
            continue
        # Skip names whose only words are stopwords or denied tokens.
        meaningful = [
            t for t in tokens
            if t not in _TOKEN_STOPWORDS and t not in _NON_PRODUCT_TOKENS
        ]
        if len(meaningful) < 1:
            continue
        normalized = " ".join(tokens)
        if len(normalized) < 5:
            continue
        if " " not in normalized:
            # Single-token product — handled by the existing token
            # lookup-set, skip here.
            continue
        out[normalized] = canonical

    # Multi-word synonym values map back to whichever product the key
    # implies. Without a key→product link we can't be precise, so we
    # only treat synonym VALUES as aliases when they themselves are
    # multi-word. The synonym KEY is then the canonical name we'll
    # surface (best-effort — matches the synonyms file convention).
    try:
        synonyms = get_or_fetch(
            business_id,
            "router_synonyms",
            (),
            lambda: _load_synonyms_for_router(business_id),
        )
    except Exception as exc:
        logger.warning("[CATALOG_CACHE] router synonyms load (full-name) failed: %s", exc)
        synonyms = {}

    for key, vals in (synonyms or {}).items():
        for v in vals or []:
            if not isinstance(v, str):
                continue
            normalized = " ".join(_split_tokens(v))
            if " " not in normalized or len(normalized) < 5:
                continue
            # Don't overwrite a real product name with a synonym alias.
            out.setdefault(normalized, str(key))
    return out


def get_router_full_name_map(business_id: str) -> Dict[str, str]:
    """
    Return cached ``{normalized_multi_word_name: canonical_name}``.
    Cached with the same TTL as ``get_router_lookup_set``.
    """
    if not business_id:
        return {}
    return get_or_fetch(
        business_id,
        "router_full_name_map",
        (),
        lambda: _build_router_full_name_map(business_id),
    )


# ── Single-token product-name lookup ───────────────────────────────
# Mapping from a single normalized token → canonical catalog name for
# products whose name is a single meaningful word (BARRACUDA, MONTESA,
# BIMOTA, BETA, ...). Used by the router to short-circuit messages like
# "Buenas tiene la barracuda?" / "tienen montesa?" to DOMAIN_ORDER
# without depending on the LLM classifier — the LLM gets biased toward
# customer_service when a greeting prefixes the question.
#
# Multi-word products are intentionally excluded; they're already
# covered by ``get_router_full_name_map``. Tokens shorter than 4 chars
# are skipped to avoid collisions on common short Spanish words.


def _build_router_single_token_map(business_id: str) -> Dict[str, str]:
    """
    Return ``{token: canonical_name}`` for active products whose name
    reduces to exactly one meaningful token (≥ 4 chars). Two products
    sharing the same token are dropped from the map — the router punts
    to the LLM rather than guess.
    """
    out: Dict[str, str] = {}
    ambiguous: set = set()
    products = list_products(business_id) or []
    for p in products:
        canonical = (p.get("name") or "").strip()
        if not canonical:
            continue
        tokens = _split_tokens(canonical)
        meaningful = [
            t for t in tokens
            if t not in _TOKEN_STOPWORDS and t not in _NON_PRODUCT_TOKENS
        ]
        if len(meaningful) != 1:
            continue
        token = meaningful[0]
        if len(token) < 4:
            continue
        if token in out and out[token] != canonical:
            ambiguous.add(token)
            continue
        out.setdefault(token, canonical)
    for tok in ambiguous:
        out.pop(tok, None)
    return out


def get_router_single_token_map(business_id: str) -> Dict[str, str]:
    """
    Return cached ``{token: canonical_name}`` map. Same TTL as the
    other router caches.
    """
    if not business_id:
        return {}
    return get_or_fetch(
        business_id,
        "router_single_token_map",
        (),
        lambda: _build_router_single_token_map(business_id),
    )
