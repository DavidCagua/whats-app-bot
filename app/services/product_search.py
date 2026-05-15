"""
Hybrid product search: lexical + tag + semantic.

Replaces the three legacy ILIKE functions in product_order_service with
one parameterizable entry point:

    search_products(business_id, query, *, limit=20, unique=False)

Pipeline:
    1. Normalize query: lowercase, strip accents, tokenize.
    2. Stem tokens (Snowball Spanish).
    3. Expand with per-business synonyms (business.settings.search_synonyms).
    4. Lexical pass: ILIKE on name / description / category, token and
       phrase level — fetches candidates.
    5. Tag pass: GIN containment on products.tags.
    6. Semantic pass (optional): pgvector cosine on query embedding —
       only runs if OPENAI_API_KEY is set and products have embeddings.
    7. Merge candidates, compute a weighted score per product, sort.
    8. For unique=True: return top-1 if score ratio vs top-2 is > 2x,
       otherwise raise AmbiguousProductError.

Score weights (additive per product):
    exact name match         100
    tag match (per tag)       40
    name substring            30
    category match            20
    description substring     15
    embedding cosine       alpha * 50 (default alpha=1.0)
    stem variant bonus         5
"""

import json
import logging
import os
import re
import unicodedata
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text as sql_text

from ..database.models import Product, Business, get_db_session
from . import catalog_cache
from .embeddings import embed_text, format_vector_literal

logger = logging.getLogger(__name__)


# ── LLM disambiguation resolver ──────────────────────────────────────────
# When the deterministic rules (exact-name, generic-containment,
# token-set equality, score-ratio) can't pick a decisive winner, this
# cheap LLM call resolves the ambiguity with semantic understanding
# rather than more heuristic rules. Replaces the "always raise
# AmbiguousProductError" fallback.
#
# Three possible outcomes:
#   WINNER  — one clear best match, optionally with derived notes
#             (e.g. "jugo de mora en leche" → Jugos en leche + notes=mora)
#   FILTERED — genuinely ambiguous, but some candidates are wrong
#             category and should be excluded from the options shown
#             to the user (e.g. Hervido Mora excluded from a "jugo"
#             disambiguation)
#   AMBIGUOUS — all candidates are plausible, show them all

_LLM_RESOLVER_SYSTEM = """You are a product-matching resolver for a Colombian restaurant's WhatsApp ordering bot.

Given the customer's query and a numbered list of candidate products from the catalog, decide:

1. **WINNER** — if exactly one candidate is clearly what the customer wants, return it.
   If the customer mentioned a flavor/ingredient/detail not in the winning product's name,
   include it in "notes" (the kitchen uses notes to fulfill flavor requests).

2. **FILTERED** — if the query is genuinely ambiguous between SOME candidates (e.g. customer
   said "jugo" but didn't specify water or milk) but other candidates are clearly the wrong
   category/type (e.g. a hot drink when the customer asked for a juice), exclude the wrong ones.
   Return the indices of candidates to KEEP.

3. **AMBIGUOUS** — if all candidates are plausible matches, return all indices.

Rules:
- "jugo" (juice) ≠ "hervido" (hot fruit drink). They are different product categories.
- "soda" (Italian soda) ≠ "gaseosa" / "Coca-Cola". Different product types.
- Generic products like "Jugos en leche" accept any flavor — the flavor goes in "notes".
- CRITICAL: when two or more candidates differ only by a sub-type the customer did NOT specify
  (water vs milk, size, color, material), ALWAYS return FILTERED — never auto-pick a default.
  Example: query "jugo de mora" with candidates [Jugos en agua, Jugos en leche] → the customer
  did NOT say water or milk, so return FILTERED with both. Do NOT guess that "jugo" defaults to
  "en agua". The customer must choose.
- Respond ONLY with valid JSON, no markdown, no explanation.

Response format — exactly one of:
  {"result": "WINNER", "index": <0-based>, "notes": "<flavor or empty string>"}
  {"result": "FILTERED", "keep": [<0-based indices to show user>]}
  {"result": "AMBIGUOUS"}
"""

_llm_resolver = None


def _get_llm_resolver():
    global _llm_resolver
    if _llm_resolver is not None:
        return _llm_resolver
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from langchain_openai import ChatOpenAI
        _llm_resolver = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            max_tokens=100,
            api_key=api_key,
        )
    except Exception as exc:
        logger.warning("[PRODUCT_SEARCH] LLM resolver init failed: %s", exc)
    return _llm_resolver


def _llm_resolve_disambiguation(
    query: str,
    candidates: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Ask a fast LLM to resolve product disambiguation.

    Args:
        query: The user's original search query.
        candidates: Scored product dicts (name, price, tags, description).

    Returns:
        A dict with one of three shapes:
          {"result": "WINNER", "product": <product_dict>, "notes": "..."}
          {"result": "FILTERED", "products": [<product_dicts to keep>]}
          {"result": "AMBIGUOUS"}  (or None on failure → caller falls back)
    """
    llm = _get_llm_resolver()
    if llm is None:
        return None

    # Build a compact candidate list for the prompt
    lines = []
    for i, c in enumerate(candidates):
        tags = ", ".join(c.get("tags") or [])
        desc = (c.get("description") or "")[:80]
        price = int(c.get("price") or 0)
        line = f"{i}. {c.get('name')} (${price:,}) — {desc}".replace(",", ".")
        if tags:
            line += f" [tags: {tags}]"
        lines.append(line)

    user_msg = f"Query: \"{query}\"\n\nCandidates:\n" + "\n".join(lines)

    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        response = llm.invoke(
            [
                SystemMessage(content=_LLM_RESOLVER_SYSTEM),
                HumanMessage(content=user_msg),
            ],
            config={"run_name": "product_disambiguator"},
        )
        text = (response.content if hasattr(response, "content") else str(response)).strip()

        # Parse JSON — try raw first, then extract from markdown fences
        parsed = None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{[^{}]*\}", text)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass

        if not parsed or not isinstance(parsed, dict):
            logger.warning("[PRODUCT_SEARCH] LLM resolver returned unparseable: %r", text)
            return None

        result_type = (parsed.get("result") or "").upper()

        if result_type == "WINNER":
            idx = parsed.get("index")
            if isinstance(idx, int) and 0 <= idx < len(candidates):
                winner = dict(candidates[idx])
                notes = (parsed.get("notes") or "").strip()
                if notes:
                    winner["_derived_notes"] = notes
                logger.info(
                    "[PRODUCT_SEARCH] LLM resolver: WINNER query=%r → %r notes=%r",
                    query, winner.get("name"), notes,
                )
                return {"result": "WINNER", "product": winner}

        if result_type == "FILTERED":
            keep = parsed.get("keep") or []
            if isinstance(keep, list) and len(keep) >= 1:
                filtered = []
                for idx in keep:
                    if isinstance(idx, int) and 0 <= idx < len(candidates):
                        filtered.append(candidates[idx])
                if filtered and len(filtered) < len(candidates):
                    logger.info(
                        "[PRODUCT_SEARCH] LLM resolver: FILTERED query=%r keep=%s",
                        query, [p.get("name") for p in filtered],
                    )
                    return {"result": "FILTERED", "products": filtered}

        # AMBIGUOUS or unrecognized → return AMBIGUOUS explicitly
        logger.info("[PRODUCT_SEARCH] LLM resolver: AMBIGUOUS for query=%r", query)
        return {"result": "AMBIGUOUS"}

    except Exception as exc:
        logger.warning("[PRODUCT_SEARCH] LLM resolver failed: %s", exc)
        return None


# ── LLM zero-result fallback ────────────────────────────────────────────
# When both lexical, semantic, AND trigram phases return nothing, this
# sends the query + full catalog to a fast LLM as a last resort. Handles
# cases where the typo is too severe for trigram (e.g. phonetic
# misspellings, creative abbreviations) but a language model can still
# infer the intent from context.

_LLM_ZERO_RESULT_SYSTEM = """You are a product-matching fallback for a Colombian restaurant's WhatsApp ordering bot.

The customer typed a product name that didn't match anything in our catalog via normal search.
This usually means a typo, misspelling, or phonetic approximation.

Given the customer's query and the full product catalog, decide:

1. **MATCH** — if exactly one product is clearly what the customer meant (typo, misspelling,
   phonetic similarity), return its index. Examples: "vitoria" → VITTORIA, "aravbiata" → ARRABBIATA.

2. **NO_MATCH** — if no product is a reasonable match for the query, or if multiple products
   could match equally well. Do NOT guess.

Rules:
- Only return MATCH if you are highly confident (>90%) this is a typo/misspelling of that product.
- Never match unrelated products just because they share a letter or two.
- Respond ONLY with valid JSON, no markdown, no explanation.

Response format — exactly one of:
  {"result": "MATCH", "index": <0-based>}
  {"result": "NO_MATCH"}
"""


def _llm_zero_result_fallback(
    query: str,
    all_products: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Last-resort LLM call when all search phases returned nothing.

    Only sends product names + categories to the LLM (minimal tokens).
    Returns the matched product dict or None.
    """
    llm = _get_llm_resolver()
    if llm is None:
        return None
    if not all_products:
        return None

    # Send only name + category — minimal tokens, enough for typo matching
    lines = []
    for i, p in enumerate(all_products):
        cat = p.get("category") or ""
        lines.append(f"{i}. {p.get('name')} ({cat})")

    user_msg = f'Query: "{query}"\n\nCatalog:\n' + "\n".join(lines)

    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        response = llm.invoke(
            [
                SystemMessage(content=_LLM_ZERO_RESULT_SYSTEM),
                HumanMessage(content=user_msg),
            ],
            config={"run_name": "product_zero_result_fallback"},
        )
        text = (response.content if hasattr(response, "content") else str(response)).strip()

        parsed = None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{[^{}]*\}", text)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass

        if not parsed or not isinstance(parsed, dict):
            logger.warning("[PRODUCT_SEARCH] LLM zero-result fallback unparseable: %r", text)
            return None

        result_type = (parsed.get("result") or "").upper()
        if result_type == "MATCH":
            idx = parsed.get("index")
            if isinstance(idx, int) and 0 <= idx < len(all_products):
                winner = dict(all_products[idx])
                winner["matched_by"] = "llm_fallback"
                logger.info(
                    "[PRODUCT_SEARCH] LLM zero-result fallback: query=%r → %r",
                    query, winner.get("name"),
                )
                return winner

        logger.info("[PRODUCT_SEARCH] LLM zero-result fallback: NO_MATCH for query=%r", query)
        return None

    except Exception as exc:
        logger.warning("[PRODUCT_SEARCH] LLM zero-result fallback failed: %s", exc)
        return None


class AmbiguousProductError(Exception):
    """Raised when unique=True but the search result has no clear winner."""
    def __init__(self, query: str, matches: List[Dict[str, Any]]):
        self.query = query
        self.matches = matches
        names = ", ".join(m.get("name", "?") for m in matches)
        super().__init__(f"Multiple products match '{query}': {names}")


class ProductNotFoundError(Exception):
    """
    Raised when add_to_cart / get_product cannot find ANY product matching
    the user's request. Distinct from AmbiguousProductError (which means
    multiple candidates exist and we need the user to choose).

    Adding this as a raised exception instead of a returned "❌ not found"
    string lets the multi-item executor loop distinguish "the batch
    partially failed because one item is missing" from "the batch
    partially failed because the user needs to clarify a variant". The
    two cases need different response prompts.
    """
    def __init__(self, query: str):
        self.query = query
        super().__init__(f"No product matches '{query}'")


# ---------- normalization ----------

_SPANISH_STOPWORDS = frozenset({
    "una", "un", "la", "el", "de", "con", "para", "por", "y", "e", "o", "u",
    "del", "al", "los", "las", "unos", "unas", "que", "en", "lo", "le", "se",
    "da", "algo", "uno", "como", "mas", "pero", "sus", "este", "esta", "eso",
    "me", "mi", "tu", "su", "si", "no", "ya", "muy", "quiero", "dame", "pasame",
    "dos", "tres",  # quantity words — safe to drop for search tokens
})


def _strip_accents(s: str) -> str:
    nfkd = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def _normalize(query: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    if not query:
        return ""
    s = _strip_accents(query.lower())
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _tokenize(query_norm: str) -> List[str]:
    return [
        w for w in query_norm.split()
        if len(w) > 1 and w not in _SPANISH_STOPWORDS
    ]


_stemmer = None


def _get_stemmer():
    global _stemmer
    if _stemmer is None:
        try:
            import snowballstemmer
            _stemmer = snowballstemmer.stemmer("spanish")
        except Exception as e:
            logger.warning("[PRODUCT_SEARCH] snowballstemmer unavailable: %s", e)
            _stemmer = False
    return _stemmer if _stemmer is not False else None


def _stem(token: str) -> str:
    s = _get_stemmer()
    if s is None:
        return token
    try:
        return s.stemWord(token)
    except Exception:
        return token


# ---------- synonym expansion ----------

def _load_business_synonyms(db_session, business_id: str) -> Dict[str, List[str]]:
    try:
        business = (
            db_session.query(Business)
            .filter(Business.id == uuid.UUID(business_id))
            .first()
        )
        if not business or not business.settings:
            return {}
        settings = business.settings if isinstance(business.settings, dict) else {}
        syns = settings.get("search_synonyms") or {}
        if not isinstance(syns, dict):
            return {}
        # Normalize keys: lowercase, accent-stripped
        out: Dict[str, List[str]] = {}
        for k, v in syns.items():
            if not isinstance(k, str):
                continue
            key = _normalize(k)
            if not isinstance(v, list):
                continue
            vals = [_normalize(x) for x in v if isinstance(x, str)]
            vals = [x for x in vals if x]
            if key and vals:
                out[key] = vals
        return out
    except Exception as e:
        logger.warning("[PRODUCT_SEARCH] Failed to load synonyms for %s: %s", business_id, e)
        return {}


def _expand_tokens(tokens: List[str], synonyms: Dict[str, List[str]]) -> List[str]:
    """Add synonyms for each token. De-duplicated, original order preserved."""
    seen = set()
    out: List[str] = []
    for tok in tokens:
        for variant in [tok] + synonyms.get(tok, []):
            if variant not in seen:
                seen.add(variant)
                out.append(variant)
    # Also expand the full phrase as a synonym key (e.g. "cerveza rubia")
    full = " ".join(tokens)
    for variant in synonyms.get(full, []):
        if variant not in seen:
            seen.add(variant)
            out.append(variant)
    return out


# ---------- lexical + tag + semantic search ----------

_SCORE_EXACT_NAME = 100
_SCORE_TAG = 40
_SCORE_NAME_SUBSTRING = 30
_SCORE_CATEGORY = 20
_SCORE_DESCRIPTION = 15
_SCORE_EMBEDDING_MAX = 50  # cosine 0..1 → 0..50
_SCORE_STEM_BONUS = 5

# Phrase-level boosts. A multi-word phrase from the query (e.g. "burguer master")
# matching verbatim inside a product's tag / description / category is intrinsically
# more selective than the sum of single-token hits, so it earns a separate boost.
# Without these, a campaign-style tag like "burguer master" loses to a generic
# tag like "hamburguesa" because the latter is a single token that lands the full
# _SCORE_TAG on every burger in the catalog.
_SCORE_PHRASE_TAG = 80
_SCORE_PHRASE_DESCRIPTION = 35
_SCORE_PHRASE_CATEGORY = 25

# Minimum cosine similarity for a semantic hit to count. Below this, the
# match is near-random noise from nearest-neighbor search — e.g. "pizza" at
# a burger shop returning burgers because they're the closest vectors in
# embedding space, even though nothing actually matches. Tune based on
# [ORDER_TURN] log signal: raise if noise leaks through, drop if legitimate
# typos/near-matches get killed. 0.55 is a conservative starting point.
_EMBEDDING_SIMILARITY_FLOOR = 0.55

# Minimum pg_trgm similarity for a trigram fuzzy match to qualify.
# 0.3 is Postgres's default; 0.35 tightens it slightly to avoid noise
# while still catching single-char typos like "vitoria" → "vittoria".
_TRIGRAM_SIMILARITY_THRESHOLD = 0.35


def _trigram_candidates(
    db_session,
    business_id: str,
    query_norm: str,
    limit: int = 10,
    *,
    include_unavailable: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """
    Fuzzy fallback using pg_trgm similarity on product names.

    Only called when both lexical and semantic phases returned zero
    candidates — i.e. the user's query has a typo or misspelling that
    breaks substring matching but is close enough for trigram overlap.

    When ``include_unavailable`` is True, ``is_active=False`` and
    ``promo_only=True`` rows are included in the result set. The caller
    is expected to inspect the returned dicts' ``is_active`` / ``promo_only``
    fields and decide what to do (typically: mark them with an availability
    tag so the LLM can surface the right user-facing message and the
    add-to-cart tool can refuse).

    Returns {product_id: product_row_dict}, same shape as _lexical_candidates.
    """
    if not query_norm:
        return {}

    business_uuid = uuid.UUID(business_id)
    avail_filter = "" if include_unavailable else "AND is_active = TRUE AND promo_only = FALSE"
    sql = sql_text(f"""
        SELECT id, business_id, name, description, price, currency, category, sku,
               is_active, promo_only, tags, metadata,
               similarity(lower(name), :query) AS sim
        FROM products
        WHERE business_id = :business_id
          {avail_filter}
          AND similarity(lower(name), :query) > :threshold
        ORDER BY sim DESC
        LIMIT :lim
    """)
    try:
        rows = db_session.execute(sql, {
            "business_id": str(business_uuid),
            "query": query_norm,
            "threshold": _TRIGRAM_SIMILARITY_THRESHOLD,
            "lim": limit,
        }).mappings().all()
    except Exception as e:
        logger.debug("[PRODUCT_SEARCH] trigram search failed (pg_trgm not available?): %s", e)
        try:
            db_session.rollback()
        except Exception:
            pass
        return {}

    candidates: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        pid = str(row["id"])
        candidates[pid] = {
            "id": pid,
            "business_id": str(row["business_id"]),
            "name": row["name"],
            "description": row["description"],
            "price": float(row["price"]) if row["price"] is not None else 0.0,
            "currency": row["currency"],
            "category": row["category"],
            "sku": row["sku"],
            "is_active": row["is_active"],
            "promo_only": bool(row["promo_only"]),
            "tags": list(row["tags"] or []),
            "metadata": dict(row["metadata"] or {}),
        }
    if candidates:
        logger.info(
            "[PRODUCT_SEARCH] trigram fallback: query=%r found %d candidate(s): %s",
            query_norm,
            len(candidates),
            [c.get("name") for c in candidates.values()],
        )
    return candidates


def _lexical_candidates(
    db_session,
    business_id: str,
    tokens_expanded: List[str],
    full_query_norm: str,
    *,
    include_unavailable: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """
    Pull candidate products via a single SQL query that ORs all lexical
    conditions. Returns {product_id: product_row_dict} — dedup at the app
    layer so we don't double-count scores.

    ``include_unavailable=True`` drops the ``is_active=TRUE AND
    promo_only=FALSE`` filter so the caller can surface unavailable
    products (with markers) for info questions while still blocking
    them at add-to-cart.
    """
    if not tokens_expanded and not full_query_norm:
        return {}

    business_uuid = uuid.UUID(business_id)
    like_params: Dict[str, str] = {}
    or_clauses: List[str] = []
    idx = 0

    def add_like(field_sql: str, term: str):
        nonlocal idx
        key = f"p{idx}"
        idx += 1
        like_params[key] = f"%{term}%"
        or_clauses.append(f"unaccent(lower({field_sql})) ILIKE unaccent(:{key})")

    # Full phrase on name/desc/category
    if full_query_norm:
        add_like("coalesce(name,'')", full_query_norm)
        add_like("coalesce(description,'')", full_query_norm)
        add_like("coalesce(category,'')", full_query_norm)

    # Each expanded token on name/desc/category/tags-as-text
    for tok in tokens_expanded:
        add_like("coalesce(name,'')", tok)
        add_like("coalesce(description,'')", tok)
        add_like("coalesce(category,'')", tok)
        # Tags as text: catches stemmed tokens that don't exact-match
        # any tag element but substring-match (e.g. stem "perr" in tag "perro")
        add_like("coalesce(array_to_string(tags, ' '),'')", tok)

    # Tag containment (native array &&)
    tag_clause = ""
    if tokens_expanded:
        tag_clause = "tags && CAST(:tag_list AS text[])"

    all_clauses = or_clauses[:]
    if tag_clause:
        all_clauses.append(tag_clause)
    if not all_clauses:
        return {}

    where_sql = " OR ".join(all_clauses)
    avail_filter = "" if include_unavailable else "AND is_active = TRUE AND promo_only = FALSE"
    sql = f"""
        SELECT id, business_id, name, description, price, currency, category, sku,
               is_active, promo_only, tags, metadata, created_at, updated_at
        FROM products
        WHERE business_id = :business_id
          {avail_filter}
          AND ({where_sql})
        LIMIT 100
    """
    params = {**like_params, "business_id": str(business_uuid)}
    if tag_clause:
        params["tag_list"] = "{" + ",".join(tokens_expanded) + "}"

    try:
        rows = db_session.execute(sql_text(sql), params).mappings().all()
    except Exception as e:
        # unaccent() extension is missing — rollback (transaction is aborted)
        # and retry the same query with plain lower() (query-side accents are
        # already stripped in _normalize, so we only lose matches when a PRODUCT
        # name contains accents; rare, and the caller can still fall back to
        # embedding / tag search).
        logger.warning("[PRODUCT_SEARCH] unaccent failed (%s), retrying without it", e)
        try:
            db_session.rollback()
        except Exception:
            pass
        or_clauses_plain = []
        for clause in or_clauses:
            m = re.match(r"unaccent\(lower\((.+?)\)\) ILIKE unaccent\(:(\w+)\)", clause)
            if m:
                or_clauses_plain.append(f"lower({m.group(1)}) ILIKE :{m.group(2)}")
            else:
                or_clauses_plain.append(clause)
        all_plain = or_clauses_plain + ([tag_clause] if tag_clause else [])
        sql = f"""
            SELECT id, business_id, name, description, price, currency, category, sku,
                   is_active, promo_only, tags, metadata, created_at, updated_at
            FROM products
            WHERE business_id = :business_id
              {avail_filter}
              AND ({" OR ".join(all_plain)})
            LIMIT 100
        """
        try:
            rows = db_session.execute(sql_text(sql), params).mappings().all()
        except Exception as e2:
            logger.error("[PRODUCT_SEARCH] lexical fallback also failed: %s", e2)
            try:
                db_session.rollback()
            except Exception:
                pass
            return {}

    candidates: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        pid = str(row["id"])
        candidates[pid] = {
            "id": pid,
            "business_id": str(row["business_id"]),
            "name": row["name"],
            "description": row["description"],
            "price": float(row["price"]) if row["price"] is not None else 0.0,
            "currency": row["currency"],
            "category": row["category"],
            "sku": row["sku"],
            "is_active": row["is_active"],
            "promo_only": bool(row["promo_only"]),
            "tags": list(row["tags"] or []),
            "metadata": dict(row["metadata"] or {}),
        }
    return candidates


def _semantic_candidates(
    db_session,
    business_id: str,
    query: str,
    limit: int,
    *,
    include_unavailable: bool = False,
) -> Dict[str, Tuple[Dict[str, Any], float]]:
    """
    Fetch top-K nearest neighbors by embedding cosine distance.
    Returns {product_id: (product_dict, similarity_0_to_1)}.
    Empty dict if embeddings are unavailable.

    ``include_unavailable=True`` drops the ``is_active=TRUE AND
    promo_only=FALSE`` filter — see ``_lexical_candidates`` for rationale.
    """
    vec = embed_text(query)
    if not vec:
        return {}

    vec_lit = format_vector_literal(vec)
    avail_filter = "" if include_unavailable else "AND is_active = TRUE AND promo_only = FALSE"
    sql = sql_text(f"""
        SELECT id, business_id, name, description, price, currency, category, sku,
               is_active, promo_only, tags, metadata,
               1 - (embedding <=> CAST(:qvec AS vector)) AS similarity
        FROM products
        WHERE business_id = :business_id
          {avail_filter}
          AND embedding IS NOT NULL
        ORDER BY embedding <=> CAST(:qvec AS vector)
        LIMIT :k
    """)
    try:
        rows = db_session.execute(sql, {
            "business_id": str(uuid.UUID(business_id)),
            "qvec": vec_lit,
            "k": limit,
        }).mappings().all()
    except Exception as e:
        logger.debug("[PRODUCT_SEARCH] semantic search failed: %s", e)
        return {}

    out: Dict[str, Tuple[Dict[str, Any], float]] = {}
    for row in rows:
        sim = float(row["similarity"]) if row["similarity"] is not None else 0.0
        sim = max(0.0, min(1.0, sim))
        # Floor: near-zero neighbors are semantic noise, not matches. Skip.
        if sim < _EMBEDDING_SIMILARITY_FLOOR:
            continue
        pid = str(row["id"])
        prod = {
            "id": pid,
            "business_id": str(row["business_id"]),
            "name": row["name"],
            "description": row["description"],
            "price": float(row["price"]) if row["price"] is not None else 0.0,
            "currency": row["currency"],
            "category": row["category"],
            "sku": row["sku"],
            "is_active": row["is_active"],
            "promo_only": bool(row["promo_only"]),
            "tags": list(row["tags"] or []),
            "metadata": dict(row["metadata"] or {}),
        }
        out[pid] = (prod, sim)
    return out


def _score_product(
    product: Dict[str, Any],
    full_query_norm: str,
    tokens: List[str],
    tokens_expanded: List[str],
    stems: List[str],
    semantic_sim: float,
    alpha: float,
) -> Dict[str, Any]:
    """
    Compute the aggregate match score for a single product.

    Returns a dict: {score, exact_name_match, has_lexical_hit}
    where has_lexical_hit is True if any non-embedding signal fired
    (used to filter pure-embedding matches out of disambiguation).
    """
    name_norm = _normalize(product.get("name") or "")
    desc_norm = _normalize(product.get("description") or "")
    cat_norm = _normalize(product.get("category") or "")
    tags_norm = [_normalize(t) for t in (product.get("tags") or [])]

    # Build the "core" query (stopwords removed, joined) so that "la michelada"
    # → "michelada" → matches the product "Michelada" as an exact name.
    core_query = " ".join(tokens) if tokens else full_query_norm

    score = 0.0
    has_lexical_hit = False

    # Exact name match (highest signal) — against full query, core query,
    # or any single distinctive token.
    exact_match = False
    if full_query_norm and name_norm == full_query_norm:
        exact_match = True
    elif core_query and name_norm == core_query:
        exact_match = True
    elif len(tokens) == 1 and name_norm == tokens[0]:
        exact_match = True
    elif " " not in name_norm and name_norm and name_norm in tokens:
        # Multi-token query where a single-word product name appears as
        # one of the tokens, and every OTHER token is a descriptor of
        # this same product's category or tags. Handles Spanish
        # "[category] [name]" phrasings:
        #
        #   "un perro caliente denver"  → DENVER (category: HOT DOGS,
        #                                 tags include "perro caliente")
        #   "una hamburguesa barracuda" → BARRACUDA (category: BURGERS)
        #
        # Without this rule the scorer falls through to decisive rule 2,
        # which can't distinguish DENVER from NAIROBI/PEGORETTI/SPECIAL
        # DOG because all four share the "perro caliente" tag — so tag
        # hits fire equally on all of them and DENVER's name-substring
        # lead (~30) isn't enough to clear the 2× ratio threshold.
        #
        # Gating on "other tokens fit category/tags" prevents this from
        # firing on coincidental mentions (e.g. "denver hamburguesa"
        # where the user is asking for a burger named Denver but DENVER
        # is actually a hot dog — the rule won't fire because
        # "hamburguesa" is not in DENVER's tags).
        other_query_tokens = [t for t in tokens if t != name_norm]
        if other_query_tokens:
            tag_blob = " ".join(tags_norm) + " " + (cat_norm or "")
            if all(tok in tag_blob for tok in other_query_tokens):
                exact_match = True
    if exact_match:
        score += _SCORE_EXACT_NAME
        has_lexical_hit = True

    # Name substring (full query) — applies for a fuzzy name match that isn't exact
    if not exact_match and core_query and core_query in name_norm:
        score += _SCORE_NAME_SUBSTRING
        has_lexical_hit = True

    # Per-token lexical contributions
    for tok in tokens_expanded:
        if not tok:
            continue
        if tok in name_norm:
            score += _SCORE_NAME_SUBSTRING
            has_lexical_hit = True
        if tok in desc_norm:
            score += _SCORE_DESCRIPTION
            has_lexical_hit = True
        if tok in cat_norm:
            score += _SCORE_CATEGORY
            has_lexical_hit = True

    # Phrase-level boosts. A distinctive multi-word phrase from the query
    # ("burguer master") matching verbatim inside tag / description / category
    # is much more selective than the sum of its single-token hits — campaign
    # tags would otherwise lose to generic single-word tags like "hamburguesa".
    #
    # We build the phrase set from the post-stopword token stream so common
    # filler ("la del...") doesn't generate noise bigrams. Each phrase fires
    # at most once per field per product.
    phrases: List[str] = []
    if len(tokens) >= 2:
        core = " ".join(tokens)
        phrases.append(core)
        for i in range(len(tokens) - 1):
            bg = f"{tokens[i]} {tokens[i + 1]}"
            if bg != core:
                phrases.append(bg)

    if phrases:
        seen_tag = False
        seen_desc = False
        seen_cat = False
        for phrase in phrases:
            if not seen_tag and any(phrase == t or phrase in t for t in tags_norm if " " in t):
                score += _SCORE_PHRASE_TAG
                has_lexical_hit = True
                seen_tag = True
            if not seen_desc and phrase in desc_norm:
                score += _SCORE_PHRASE_DESCRIPTION
                has_lexical_hit = True
                seen_desc = True
            if not seen_cat and phrase in cat_norm:
                score += _SCORE_PHRASE_CATEGORY
                has_lexical_hit = True
                seen_cat = True
            if seen_tag and seen_desc and seen_cat:
                break

    # Tag hits (exact and substring)
    for tag in tags_norm:
        if not tag:
            continue
        if tag in tokens_expanded or tag == full_query_norm:
            score += _SCORE_TAG
            has_lexical_hit = True
            continue
        for tok in tokens_expanded:
            if tok and (tok in tag or tag in tok):
                score += _SCORE_TAG * 0.7
                has_lexical_hit = True
                break

    # Stem-based bonus: if any original-query stem matches a product token/tag stem
    if stems:
        prod_tokens = set()
        for field in (name_norm, desc_norm, cat_norm):
            prod_tokens.update(field.split())
        prod_tokens.update(tags_norm)
        prod_stems = {_stem(t) for t in prod_tokens if t}
        for s in stems:
            if s in prod_stems:
                score += _SCORE_STEM_BONUS
                has_lexical_hit = True

    # Semantic contribution
    if semantic_sim > 0:
        score += alpha * _SCORE_EMBEDDING_MAX * semantic_sim

    return {
        "score": score,
        "exact_name_match": exact_match,
        "has_lexical_hit": has_lexical_hit,
    }


# ---------- public API ----------

def search_products(
    business_id: str,
    query: str,
    *,
    limit: int = 20,
    unique: bool = False,
    alpha: float = 1.0,
    include_unavailable: bool = False,
) -> List[Dict[str, Any]]:
    """
    Hybrid lexical + tag + semantic product search.

    Args:
        business_id: UUID string of the business to search within.
        query: Free-form user query ("cerveza", "coca cola zero", "algo con queso azul").
        limit: Max results to return (after scoring).
        unique: If True, return a list with a single best match OR raise
            AmbiguousProductError when no clear winner exists. Used by
            add_to_cart / get_product_details paths.
        alpha: Weight on the semantic signal (0.0 disables embeddings entirely).
        include_unavailable: If True, include products that are
            ``is_active=False`` or ``promo_only=True``. The caller is
            expected to read each row's ``is_active`` / ``promo_only``
            flags and decide what to do (typically: tag with an
            availability marker so the LLM can surface the right
            user-facing message; refuse at add-to-cart). Default False
            preserves the legacy filter (browse + cart paths).

    Returns:
        Ranked list of product dicts (high score first). Empty list if nothing matches.

    Raises:
        AmbiguousProductError: if unique=True and top-1 is not decisively ahead.
    """
    if not query or not query.strip():
        return []
    if not business_id:
        return []

    query_norm = _normalize(query)
    tokens = _tokenize(query_norm)
    if not tokens:
        tokens = [query_norm]
    stems = [_stem(t) for t in tokens]

    db_session = get_db_session()
    try:
        synonyms = catalog_cache.get_or_fetch(
            business_id,
            "search_synonyms",
            (),
            lambda: _load_business_synonyms(db_session, business_id),
        )
        tokens_expanded = _expand_tokens(tokens, synonyms)
        # Also add stems as expansion tokens (so ILIKE can catch partial morphology)
        for s in stems:
            if s and s not in tokens_expanded:
                tokens_expanded.append(s)

        try:
            lexical = _lexical_candidates(
                db_session, business_id, tokens_expanded, query_norm,
                include_unavailable=include_unavailable,
            )
        except Exception as e:
            logger.warning("[PRODUCT_SEARCH] lexical phase failed: %s", e)
            try:
                db_session.rollback()
            except Exception:
                pass
            lexical = {}

        # Skip the expensive embedding API call when lexical already found
        # an exact product name match — the semantic phase only adds latency
        # (and its results get filtered out at line ~800 anyway).
        has_exact_lexical = any(
            _normalize(p.get("name") or "") == query_norm
            for p in lexical.values()
        )

        semantic_map: Dict[str, Tuple[Dict[str, Any], float]] = {}
        if alpha > 0 and not has_exact_lexical:
            semantic_map = _semantic_candidates(
                db_session, business_id, query, limit * 2,
                include_unavailable=include_unavailable,
            )

        merged: Dict[str, Dict[str, Any]] = dict(lexical)
        semantic_sim_by_id: Dict[str, float] = {}
        for pid, (prod, sim) in semantic_map.items():
            semantic_sim_by_id[pid] = sim
            if pid not in merged:
                merged[pid] = prod

        if not merged:
            # ── Fallback chain: trigram → LLM ────────────────────────
            # Both lexical and semantic returned nothing. Try fuzzy
            # matching via pg_trgm, then an LLM last resort.

            # Fallback 1: pg_trgm similarity on product names
            trigram_hits = _trigram_candidates(
                db_session, business_id, query_norm, limit=5,
                include_unavailable=include_unavailable,
            )
            if trigram_hits:
                # Trigram already ranked by similarity. For unique=True
                # with a single hit, return it directly — the normal
                # scorer would give it score=0 because the typo that
                # brought us here also breaks substring matching.
                for prod in trigram_hits.values():
                    prod["matched_by"] = "trigram"
                if unique and len(trigram_hits) == 1:
                    return list(trigram_hits.values())
                if unique and len(trigram_hits) > 1:
                    # Multiple fuzzy matches — let the LLM disambiguator
                    # pick, or raise AmbiguousProductError.
                    candidates = list(trigram_hits.values())
                    llm_result = _llm_resolve_disambiguation(query, candidates)
                    if llm_result and llm_result["result"] == "WINNER":
                        return [llm_result["product"]]
                    raise AmbiguousProductError(query=query, matches=candidates)
                # unique=False: return all trigram hits
                return list(trigram_hits.values())[:limit]
            else:
                # Fallback 2: LLM zero-result fallback — send the query
                # + cached catalog (names+category only) to a fast LLM.
                all_prods = catalog_cache.list_products(business_id) or []
                llm_match = _llm_zero_result_fallback(query, all_prods)
                if llm_match:
                    return [llm_match]

                return []

        # scored: list of (score, exact_match, has_lexical, product)
        # Each product dict gains a "matched_by" tag so downstream layers
        # (order_flow → response generator) can distinguish authoritative
        # lexical matches from pure-embedding neighbors without re-running
        # the scorer.
        scored: List[Tuple[float, bool, bool, Dict[str, Any]]] = []
        for pid, product in merged.items():
            sem_sim = semantic_sim_by_id.get(pid, 0.0)
            s = _score_product(
                product=product,
                full_query_norm=query_norm,
                tokens=tokens,
                tokens_expanded=tokens_expanded,
                stems=stems,
                semantic_sim=sem_sim,
                alpha=alpha,
            )
            if s["score"] <= 0:
                continue
            if s["exact_name_match"]:
                product["matched_by"] = "exact"
            elif s["has_lexical_hit"]:
                product["matched_by"] = "lexical"
            else:
                product["matched_by"] = "embedding"
            scored.append((s["score"], s["exact_name_match"], s["has_lexical_hit"], product))

        # Sort: exact-name first, then by score, then by name
        scored.sort(key=lambda x: (-int(x[1]), -x[0], x[3].get("name") or ""))

        # Pure-embedding filter (applied to ALL paths, not just unique=True).
        #
        # Policy: embedding is allowed to RE-RANK and TIE-BREAK, never to add
        # results on its own when other real matches exist. Two consequences:
        #
        #   1. If any result has a lexical/tag/exact hit, drop every
        #      embedding-only result. Kills the "Denver + NIJABOU/NAIROBI"
        #      bleed where the exact name match wins but the semantic lane
        #      appends unrelated products alongside it.
        #
        #   2. If NO result has any lexical/tag/exact hit, return empty.
        #      Kills the "una pizza" → burgers and "un sushi" → burgers
        #      fallthrough at Biela (a restaurant with no pizzas or sushi;
        #      embedding was pivoting to the nearest neighbors regardless).
        #
        # The unique=True path below has its own soft fallback for the
        # disambiguation UI — it reuses `scored` if lexical_only is empty.
        # That's intentional: disambig is rarely reached without at least
        # one partial hit, and the softer policy preserves fuzzy matches
        # like "mishelada" → "Michelada" that depend on semantic rescue.
        has_any_lexical = any(s[2] for s in scored)
        if has_any_lexical:
            scored_filtered = [s for s in scored if s[2]]
        else:
            scored_filtered = []

        # Decisive-winner trim: drop matches that score below half of the
        # top hit. Targets the "Burger Master" failure mode where
        # phrase-tag/description matches (Ramona, score ~145) co-exist with
        # incidental name-substring matches (Honey Burger, Mexican Burger,
        # score ~30). The low-score rivals share a word with the top
        # result by coincidence — listing them as "also participated"
        # is wrong. With cutoff=0.5×top, equal-quality clusters stay
        # together (a query like "queso" still surfaces every cheese
        # product) but a clear leader stops dragging weak siblings along.
        if len(scored_filtered) >= 2:
            top_score = scored_filtered[0][0]
            if top_score > 0:
                cutoff = top_score * 0.5
                scored_filtered = [s for s in scored_filtered if s[0] >= cutoff]

        ranked = [p for _, _, _, p in scored_filtered[:limit]]

        if unique:
            if not scored:
                return []

            # Decisive rule 1: exactly one product has an exact name match → win,
            # BUT only if no other scored candidate's name contains the full
            # normalized query as a token. Otherwise the query is a prefix of a
            # larger product name (e.g. "soda" matches "Soda" exactly but also
            # "Soda Frutos rojos", "Soda Uvilla y maracuyá") and the user
            # genuinely needs to disambiguate.
            exact_matches = [s for s in scored if s[1]]
            if len(exact_matches) == 1:
                winner_prod = exact_matches[0][3]
                winner_id = winner_prod.get("id")
                query_token = query_norm  # already normalized, e.g. "soda"
                has_prefix_rival = False
                if query_token:
                    for _score, _exact, _has_lex, other in scored:
                        if other.get("id") == winner_id:
                            continue
                        other_name_norm = _normalize(other.get("name") or "")
                        other_tokens = set(other_name_norm.split())
                        if query_token in other_tokens:
                            has_prefix_rival = True
                            break
                if not has_prefix_rival:
                    return [winner_prod]
                # Fall through to disambiguation below.

            # Decisive rule 1b: generic-product-with-qualifier match.
            #
            # When no exact-name winner fired and the query contains
            # strictly MORE content tokens than exactly one candidate —
            # and that candidate's tokens are all present in the query —
            # the user is specifying a flavor/variant of a generic
            # catalog entry. Example: Biela has "Jugos en leche" (one
            # generic row covering every flavor the kitchen stocks) and
            # the user types "jugo de mora en leche". The query stems
            # {jug, mora, lech} contain every stem of "Jugos en leche"
            # ({jug, lech}) plus one leftover ({mora}) — that leftover
            # is the flavor the human at the restaurant will fulfill.
            #
            # We promote the generic candidate to decisive winner and
            # attach the leftover original tokens as ``_derived_notes``
            # on the returned product dict so the add_to_cart path can
            # stash them on the cart item.
            #
            # Guard rails (all must hold, in this order):
            #   1. No exact-name match fired above.
            #   2. Query has STRICTLY more stemmed content tokens than
            #      the candidate (so "corona" → "Corona" and the Corona
            #      / Corona michelada prefix-rival case stay unaffected).
            #   3. The candidate's stemmed tokens are a subset of the
            #      query's stemmed tokens.
            #   4. EXACTLY ONE candidate qualifies (otherwise we don't
            #      know which generic the user meant — disambiguate).
            #   5. The winner is in the top 5 by score (prevents a
            #      low-ranked semantic surprise from hijacking the path).
            if not exact_matches:
                query_stem_set = set(s for s in stems if s)
                subset_matches: List[Tuple[Dict[str, Any], List[str]]] = []
                for _score, _exact, _has_lex, cand in scored:
                    cand_name = cand.get("name") or ""
                    cand_norm = _normalize(cand_name)
                    cand_tokens = _tokenize(cand_norm)
                    if not cand_tokens:
                        continue
                    cand_stems = [_stem(t) for t in cand_tokens if t]
                    cand_stem_set = set(s for s in cand_stems if s)
                    if not cand_stem_set:
                        continue
                    if len(query_stem_set) <= len(cand_stem_set):
                        continue
                    if cand_stem_set <= query_stem_set:
                        subset_matches.append((cand, cand_stems))
                if len(subset_matches) == 1:
                    winner_prod, winner_stems = subset_matches[0]
                    top5_ids = {
                        scored[i][3].get("id")
                        for i in range(min(5, len(scored)))
                    }
                    if winner_prod.get("id") in top5_ids:
                        used = set(winner_stems)
                        leftover_tokens: List[str] = []
                        for tok, st in zip(tokens, stems):
                            if st and st not in used:
                                leftover_tokens.append(tok)
                                used.add(st)
                        # Copy the product dict so we don't mutate any
                        # shared cache entry with per-call metadata.
                        result_prod = dict(winner_prod)
                        if leftover_tokens:
                            result_prod["_derived_notes"] = " ".join(leftover_tokens)
                        logger.info(
                            "[PRODUCT_SEARCH] generic-match: query=%r winner=%r derived_notes=%r",
                            query,
                            result_prod.get("name"),
                            result_prod.get("_derived_notes") or "",
                        )
                        return [result_prod]

            if len(scored) == 1:
                return [scored[0][3]]

            # Decisive rule 1c: token-set equality winner.
            #
            # The full-string exact-name rule (1a) compares the raw
            # normalized query against the product name, so stopwords
            # in the query ("una soda de frutos rojos" vs catalog row
            # "Soda Frutos rojos") make it miss. Score ratio (rule 2
            # below) also misses when the runner-up scores high enough
            # that the 2× margin collapses — exactly what happened with
            # `"una soda de frutos rojos"` at Biela: the ranker pulled
            # Coca-Cola / Coca-Cola Zero as semantic neighbors, and the
            # top/second ratio didn't clear 2× so disambiguation fired
            # with an obviously-wrong option list.
            #
            # This rule patches that: when no exact name match fired
            # and exactly one scored candidate's stemmed content tokens
            # form an EQUAL set to the query's stemmed content tokens,
            # promote it decisively. Stopwords are stripped on both
            # sides via `_tokenize`, so "una soda de frutos rojos" and
            # "Soda Frutos rojos" both become `{sod, frut, rojo}`.
            #
            # Guard — query must have ≥ 2 content tokens. The 1-token
            # case is already handled by rule 1a's prefix-rival check
            # (so "corona" still disambiguates when Corona michelada
            # exists; so "michelada" still disambiguates when
            # Corona michelada exists). Entering rule 1c on 1-token
            # queries would silently bypass that protection.
            if not exact_matches and len(query_stem_set) >= 2:
                equal_matches: List[Dict[str, Any]] = []
                for _score, _exact, _has_lex, cand in scored:
                    cand_name = cand.get("name") or ""
                    cand_stem_set = {
                        _stem(t)
                        for t in _tokenize(_normalize(cand_name))
                        if t
                    }
                    cand_stem_set.discard("")
                    if cand_stem_set and cand_stem_set == query_stem_set:
                        equal_matches.append(cand)
                if len(equal_matches) == 1:
                    winner = equal_matches[0]
                    logger.info(
                        "[PRODUCT_SEARCH] token-set-equal match: query=%r winner=%r",
                        query, winner.get("name"),
                    )
                    return [winner]
                # If 0 or ≥ 2 candidates have equal token sets, fall
                # through to the score-ratio rule below. Multiple
                # equal-set matches mean the query is genuinely
                # ambiguous at the token level (e.g. two products with
                # identical names after normalization).

            # Decisive rule 2: score ratio (top ≥ 2x second AND absolute ≥ 60)
            top_score = scored[0][0]
            second_score = scored[1][0]
            if top_score >= max(60.0, 2.0 * second_score):
                return [scored[0][3]]

            # Disambiguation — but filter out pure-embedding matches.
            # Only propose candidates that actually hit a lexical / tag signal;
            # otherwise we'd show the customer a list of semantically-adjacent
            # products they never asked about (e.g. malteadas for "michelada").
            lexical_only = [s for s in scored if s[2]]
            close = [p for _, _, _, p in (lexical_only or scored)[:5]]

            # ── LLM resolver: last chance before raising disambiguation ──
            # All deterministic rules failed. Ask a fast LLM to either
            # pick a winner, filter out wrong-category candidates, or
            # confirm that the list is genuinely ambiguous.
            llm_result = _llm_resolve_disambiguation(query, close)
            if llm_result is not None:
                if llm_result["result"] == "WINNER":
                    return [llm_result["product"]]
                if llm_result["result"] == "FILTERED":
                    close = llm_result["products"]
                # AMBIGUOUS → fall through to raise with (possibly filtered) close list

            raise AmbiguousProductError(query=query, matches=close)

        # Dominance trim (unique=False): when the top-1 has a decisive lead
        # over top-2, return only the winner. Same threshold as the
        # unique=True decisive rule (>= 2x AND absolute >= 60). Keeps
        # SEARCH_PRODUCTS focused on what the user asked about — without
        # this, the response generator gets the full ranked list and
        # enumerates alternatives even when the search has a clear answer
        # (e.g. campaign tags like "burguer master" lose to single-word
        # tags like "hamburguesa" because the latter inflates every burger
        # in the catalog into the runner-up slot).
        if len(scored_filtered) >= 2:
            top_score = scored_filtered[0][0]
            second_score = scored_filtered[1][0]
            if top_score >= max(60.0, 2.0 * second_score):
                return [scored_filtered[0][3]]

        return ranked
    finally:
        db_session.close()


def get_unique_product(business_id: str, query: str) -> Optional[Dict[str, Any]]:
    """Convenience wrapper: return single best match or None (no raise)."""
    try:
        results = search_products(business_id, query, limit=5, unique=True)
        return results[0] if results else None
    except AmbiguousProductError:
        raise
