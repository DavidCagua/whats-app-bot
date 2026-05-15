"""
Catalog service: shared read-only capability for menu/product queries.

Exposes a stable public interface for listing categories, listing products,
searching products, and looking up a single product. Used by the router
(menu fast-path, later phase), any agent that needs catalog data, and
future REST endpoints / admin console reads.

Scope and design:

- Returns normalized dicts with a fixed, documented shape (`Product` /
  `Category`). Callers never see raw ORM rows, raw cache internals, or
  source-specific formats.
- The current implementation reads from our Postgres catalog (via the
  existing `catalog_cache` + `product_order_service`). That's the only
  catalog source today.
- Future expansion point: per-business catalog sources (Square, Toast,
  Google Sheets, etc.) go BEHIND this interface. Add a `CatalogSource`
  ABC inside this module, dispatch based on `business.settings.catalog_source`,
  and the public functions don't change — callers are untouched.

Shared conventions:

- All functions take `business_id` as the first positional arg.
- Exceptions bubble up from the underlying service:
  `AmbiguousProductError`, `ProductNotFoundError`.
- Empty-state is represented by empty lists, not None. A `get_product`
  miss returns None (the only singular result in the interface).

Deliberate non-goals for the minimal version:
- No CatalogSource abstraction yet. Single source (Postgres).
- No write operations (catalog mutations happen through admin console).
- No inventory/stock tracking. `is_available` defaults to True.
"""

import logging
from typing import Any, Dict, List, Optional

from . import catalog_cache
from ..database.product_order_service import (
    product_order_service,
    AmbiguousProductError,  # noqa: F401 — re-exported for callers
    ProductNotFoundError,   # noqa: F401 — re-exported for callers
)

logger = logging.getLogger(__name__)


# ── Public types ────────────────────────────────────────────────────
# Minimal Product shape returned to callers. A TypedDict would be nicer
# but we want dict-compatible objects today for backward compat with the
# response generator that consumes these. Kept as plain dict on purpose.
#
# Product = {
#   "id":          str            (product UUID — may become namespaced later)
#   "name":        str
#   "price":       float          (minor units kept as-is; COP uses whole COP)
#   "currency":    str            (e.g. "COP")
#   "description": str | None
#   "category":    str | None
#   "matched_by":  str | None     ("exact" | "lexical" | "embedding")
#   "tags":        List[str]
# }


def _normalize_product(p: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize a raw product dict from any source into the public Product shape.

    Callers see stable field names and types regardless of the source's
    internal representation. Strip internal-only fields (business_id,
    embedding vector, metadata) that callers must not depend on.
    """
    return {
        "id": str(p.get("id") or ""),
        "name": p.get("name") or "",
        "price": float(p.get("price") or 0),
        "currency": p.get("currency") or "COP",
        "description": (p.get("description") or "").strip() or None,
        "category": p.get("category") or None,
        "matched_by": p.get("matched_by"),
        "tags": list(p.get("tags") or []),
    }


# ── Public interface ────────────────────────────────────────────────

def list_categories(business_id: str) -> List[str]:
    """
    Return the list of category names for a business. Empty list if
    no categories are defined (caller falls back to listing all products).
    """
    if not business_id:
        return []
    return list(catalog_cache.list_categories(business_id) or [])


def list_products(
    business_id: str,
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Return products for a business, optionally filtered by category.

    Uses `list_products_with_fallback` which returns the category match
    when available, else falls back to the full catalog. Empty category
    string is treated as "all categories."
    """
    if not business_id:
        return []
    cat = (category or "").strip() or ""
    raw = catalog_cache.list_products_with_fallback(
        business_id=business_id, category=cat,
    ) or []
    return [_normalize_product(p) for p in raw]


def search_products(
    business_id: str,
    query: str,
    *,
    limit: int = 20,
    unique: bool = False,
) -> List[Dict[str, Any]]:
    """
    Hybrid search (lexical + semantic) across the catalog. Returns top
    results as normalized Product dicts with a `matched_by` tag.

    Raises AmbiguousProductError when unique=True and the top-1 isn't
    decisively ahead (same contract as product_order_service.search_products).
    """
    if not business_id:
        return []
    q = (query or "").strip()
    if not q:
        return []
    raw = product_order_service.search_products(
        business_id=business_id, query=q, limit=limit, unique=unique,
    ) or []
    return [_normalize_product(p) for p in raw]


def get_product(
    business_id: str,
    *,
    product_id: Optional[str] = None,
    product_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Fetch a single product by id or name. Returns None if not found.
    At least one of product_id / product_name must be provided.

    Name resolution goes through the same hybrid search as `search_products`
    for fuzzy matching. Exact lookups by UUID go through the DB directly.
    """
    if not business_id:
        return None
    pid = (product_id or "").strip() or None
    pname = (product_name or "").strip() or None
    if not pid and not pname:
        return None
    raw = product_order_service.get_product(
        product_id=pid,
        product_name=pname,
        business_id=business_id,
    )
    if not raw:
        return None
    return _normalize_product(raw)
