"""
Regression tests for product_search ranking and filtering.

Context: on 2026-04-13 a user asked Biela "una pizza" and the bot listed
PICADA/PEGORETTI/ARRABBIATA as pizzas. None are pizzas — they're burgers
and a meat platter. Root cause: the embedding lane was additive and had
no similarity floor, so near-zero neighbors polluted the ranking whenever
the lexical/tag lanes returned nothing. Same symptom on "un sushi" and on
"un perro caliente denver" (Denver won on exact name but NIJABOU/NAIROBI
were appended from embedding).

These tests pin the five fixes landed with the retrieval hardening:
1. Embedding similarity floor in _semantic_candidates (Phase 1).
2. Pure-embedding filter extended to SEARCH_PRODUCTS (Phase 2).
3. Category-existence pre-check in list_products_with_fallback (Phase 3).
4. matched_by signal propagated through results (Phase 4).
5. (Phase 5 — this file.)

Tests stub the retrieval helpers so no DB is required.
"""

from unittest.mock import patch

import pytest

from app.services import product_search


# ---------------------------------------------------------------------------
# Fixtures: a tiny Biela-shaped catalog
# ---------------------------------------------------------------------------

BUSINESS_ID = "44488756-473b-46d2-a907-9f579e98ecfd"  # matches biela_product_metadata.sql


def _product(
    pid: str,
    name: str,
    category: str = "BURGERS",
    description: str = "",
    tags=None,
    price: float = 27000.0,
):
    return {
        "id": pid,
        "business_id": BUSINESS_ID,
        "name": name,
        "description": description,
        "price": price,
        "currency": "COP",
        "category": category,
        "sku": None,
        "is_active": True,
        "tags": list(tags or []),
        "metadata": {},
    }


BIELA_CATALOG = {
    # name → product dict (lets tests look up by human name)
    "PICADA":      _product("p-01", "PICADA", category="PLATOS", description="Surtido de carnes y embutidos.", tags=["carne", "embutidos"], price=55000),
    "PEGORETTI":   _product("p-02", "PEGORETTI", description="Hamburguesa italiana.", tags=["hamburguesa", "burger"]),
    "ARRABBIATA":  _product("p-03", "ARRABBIATA", description="Pan, carne, mozzarella, salsa arrabbiata picante, rúgula, papas.", tags=["hamburguesa", "burger", "picante"]),
    "BARRACUDA":   _product("p-04", "BARRACUDA", description="Doble carne, cheddar, tocineta, cebolla caramelizada, papas.", tags=["hamburguesa", "burger", "doble"], price=28000),
    "BIELA":       _product("p-05", "BIELA", description="Carne, tocineta, huevo, cheddar, chipotle, papas.", tags=["hamburguesa", "burger"], price=28000),
    "BETA":        _product("p-06", "BETA", description="Carne, queso azul, champiñones salteados, cebolla crispy, papas.", tags=["hamburguesa", "burger", "queso azul"], price=28000),
    "MANHATTAN":   _product("p-07", "MANHATTAN", description="Hamburguesa con cheddar fundido.", tags=["hamburguesa", "burger"], price=28000),
    "MICHELADA":   _product("p-08", "Michelada", category="BEBIDAS", description="Cerveza preparada con limón y sal.", tags=["bebida", "cerveza"], price=12000),
    "LIM_NATURAL": _product("p-09", "Limonada natural", category="BEBIDAS", description="", tags=["bebida", "limonada"], price=6500),
    "LIM_CEREZA":  _product("p-10", "Limonada de cereza", category="BEBIDAS", description="", tags=["bebida", "limonada"], price=12000),
    "PERRO_DENVER":_product("p-11", "Perro Caliente Denver", category="HOT DOGS", description="Perro caliente estilo Denver.", tags=["perro", "hot dog"], price=27000),
    "NIJABOU":     _product("p-12", "NIJABOU", category="BURGERS", description="Hamburguesa estilo japonés.", tags=["hamburguesa", "burger"]),
    "NAIROBI":     _product("p-13", "NAIROBI", category="BURGERS", description="Hamburguesa estilo africano.", tags=["hamburguesa", "burger"]),
    # Beverage generics + siblings used by the generic-product-match tests.
    # "Jugos en leche" and "Jugos en agua" are catalog-level rows that
    # accept any flavor the kitchen stocks that day — the flavor is
    # passed to the staff as a note on the cart item.
    "JUGOS_LECHE": _product("p-14", "Jugos en leche", category="BEBIDAS", description="", tags=["bebida", "jugo"], price=7500),
    "JUGOS_AGUA":  _product("p-15", "Jugos en agua", category="BEBIDAS", description="", tags=["bebida", "jugo"], price=7500),
    "HERVIDO_MORA":_product("p-16", "Hervido Mora", category="BEBIDAS", description="Bebida caliente preparada con mora.", tags=["bebida", "caliente"], price=9500),
    # Corona siblings used by the regression guard that protects the
    # existing prefix-rival disambiguation behavior.
    "CORONA":      _product("p-17", "Corona 355ml", category="BEBIDAS", description="", tags=["bebida", "cerveza"], price=12000),
    "CORONA_MICH": _product("p-18", "Corona michelada", category="BEBIDAS", description="", tags=["bebida", "cerveza"], price=14500),
}


def _all_catalog():
    return dict(BIELA_CATALOG)


def _stub_search(lexical=None, semantic=None, synonyms=None):
    """
    Patch the three DB-hitting helpers so search_products() runs in-memory.

    Args:
        lexical: {product_key: product_dict} — what _lexical_candidates returns
        semantic: {product_key: (product_dict, similarity)} — what _semantic_candidates returns
        synonyms: business synonym map (rarely needed)

    Returns a context-manager-like tuple of patches to use in a `with` stack.
    """
    lexical_map = {}
    for k, p in (lexical or {}).items():
        lexical_map[p["id"]] = p
    semantic_map = {}
    for k, (p, sim) in (semantic or {}).items():
        semantic_map[p["id"]] = (p, sim)

    def fake_lexical(_db, _biz, _tokens, _qn):
        return lexical_map

    def fake_semantic(_db, _biz, _q, _limit):
        return semantic_map

    def fake_load_synonyms(_db, _biz):
        return synonyms or {}

    # get_db_session is a no-op context — just return a sentinel
    class _FakeSession:
        def close(self):
            pass
        def rollback(self):
            pass

    def fake_get_session():
        return _FakeSession()

    return [
        patch("app.services.product_search._lexical_candidates", side_effect=fake_lexical),
        patch("app.services.product_search._semantic_candidates", side_effect=fake_semantic),
        patch("app.services.product_search._load_business_synonyms", side_effect=fake_load_synonyms),
        patch("app.services.product_search.get_db_session", side_effect=fake_get_session),
    ]


class _PatchStack:
    """Tiny helper so tests can do `with _PatchStack(stubs):`."""
    def __init__(self, patches):
        self._patches = patches
    def __enter__(self):
        for p in self._patches:
            p.start()
        return self
    def __exit__(self, *a):
        for p in reversed(self._patches):
            p.stop()


# ---------------------------------------------------------------------------
# Phase 1 — embedding similarity floor
# ---------------------------------------------------------------------------

class TestEmbeddingFloor:
    """_EMBEDDING_SIMILARITY_FLOOR drops near-zero semantic neighbors."""

    def test_floor_constant_is_set(self):
        # The floor exists and is in a sensible range. If someone needs to
        # tune it, this test forces a re-review of the magic number.
        floor = product_search._EMBEDDING_SIMILARITY_FLOOR
        assert 0.3 <= floor <= 0.8, (
            f"Embedding floor {floor} outside the safe tuning band [0.3, 0.8]"
        )


# ---------------------------------------------------------------------------
# Phase 2 — pure-embedding filter extended to SEARCH_PRODUCTS
# ---------------------------------------------------------------------------

class TestPureEmbeddingFilter:
    """
    search_products(unique=False) must not return products that only
    matched via embedding when any lexical/tag result is present, and
    must return [] when zero lexical/tag hits exist.
    """

    def test_pizza_query_at_biela_returns_empty(self):
        """'una pizza' has no lexical/tag hits in the Biela catalog. The
        pre-fix behavior was to return the nearest embedding neighbors
        (burgers). The post-fix behavior is an empty list — the response
        generator then tells the user we don't have pizzas."""
        semantic = {
            # Embedding lane returns the nearest burger vectors — no lexical backing.
            "PICADA":     (BIELA_CATALOG["PICADA"],    0.62),
            "PEGORETTI":  (BIELA_CATALOG["PEGORETTI"], 0.61),
            "ARRABBIATA": (BIELA_CATALOG["ARRABBIATA"],0.60),
        }
        with _PatchStack(_stub_search(lexical={}, semantic=semantic)):
            results = product_search.search_products(BUSINESS_ID, "pizza")
        assert results == [], (
            f"'pizza' at Biela must return [] after the filter, got {[r['name'] for r in results]}"
        )

    def test_sushi_query_at_biela_returns_empty(self):
        """Same as pizza — no sushi in the catalog, no lexical hits."""
        semantic = {
            "BARRACUDA": (BIELA_CATALOG["BARRACUDA"], 0.58),
            "MANHATTAN": (BIELA_CATALOG["MANHATTAN"], 0.56),
        }
        with _PatchStack(_stub_search(lexical={}, semantic=semantic)):
            results = product_search.search_products(BUSINESS_ID, "sushi")
        assert results == []

    def test_denver_query_drops_embedding_neighbors(self):
        """
        Exact match on Denver should WIN, and NIJABOU/NAIROBI (pure
        embedding neighbors in the BURGERS category, no lexical backing
        for the word "denver") must be filtered out.
        """
        lexical = {
            "PERRO_DENVER": BIELA_CATALOG["PERRO_DENVER"],  # matches via name substring "denver"
        }
        semantic = {
            "PERRO_DENVER": (BIELA_CATALOG["PERRO_DENVER"], 0.95),
            "NIJABOU":      (BIELA_CATALOG["NIJABOU"],      0.62),
            "NAIROBI":      (BIELA_CATALOG["NAIROBI"],      0.61),
        }
        with _PatchStack(_stub_search(lexical=lexical, semantic=semantic)):
            results = product_search.search_products(BUSINESS_ID, "perro caliente denver")
        names = [r["name"] for r in results]
        assert "Perro Caliente Denver" in names
        assert "NIJABOU" not in names, "pure-embedding neighbor should be dropped"
        assert "NAIROBI" not in names, "pure-embedding neighbor should be dropped"


# ---------------------------------------------------------------------------
# Preservation — things that must keep working
# ---------------------------------------------------------------------------

class TestPreservation:
    """
    The filter must not regress the cases where retrieval was working.
    Every case below has at least one lexical/tag backing signal.
    """

    def test_picante_tag_query_returns_arrabbiata(self):
        """
        'algo picante' — ARRABBIATA has 'picante' in its tags AND in its
        description. Tag + description substring are lexical lanes, so
        the result survives the Phase 2 filter.
        """
        lexical = {
            "ARRABBIATA": BIELA_CATALOG["ARRABBIATA"],
        }
        semantic = {
            "ARRABBIATA": (BIELA_CATALOG["ARRABBIATA"], 0.8),
            # A near-miss burger with no "picante" backing — should be filtered.
            "BIELA":      (BIELA_CATALOG["BIELA"],      0.60),
        }
        with _PatchStack(_stub_search(lexical=lexical, semantic=semantic)):
            results = product_search.search_products(BUSINESS_ID, "algo picante")
        names = [r["name"] for r in results]
        assert "ARRABBIATA" in names
        assert "BIELA" not in names

    def test_queso_azul_description_hit_returns_beta(self):
        """
        'algo con queso azul' — BETA has 'queso azul' in description.
        Description substring is a lexical signal.
        """
        lexical = {
            "BETA": BIELA_CATALOG["BETA"],
        }
        semantic = {
            "BETA": (BIELA_CATALOG["BETA"], 0.78),
        }
        with _PatchStack(_stub_search(lexical=lexical, semantic=semantic)):
            results = product_search.search_products(BUSINESS_ID, "algo con queso azul")
        assert any(r["name"] == "BETA" for r in results)

    def test_limonada_name_substring_returns_all_limonadas(self):
        """'limonada' matches both limonada products via name substring."""
        lexical = {
            "LIM_NATURAL": BIELA_CATALOG["LIM_NATURAL"],
            "LIM_CEREZA":  BIELA_CATALOG["LIM_CEREZA"],
        }
        semantic = {}
        with _PatchStack(_stub_search(lexical=lexical, semantic=semantic)):
            results = product_search.search_products(BUSINESS_ID, "limonada")
        names = {r["name"] for r in results}
        assert "Limonada natural" in names
        assert "Limonada de cereza" in names

    def test_michelada_exact_name_still_wins(self):
        """
        'michelada' is the canonical example from commit 99cbc13 — it's an
        exact-name match and must not be killed by the filter.
        """
        lexical = {
            "MICHELADA": BIELA_CATALOG["MICHELADA"],
        }
        semantic = {
            "MICHELADA": (BIELA_CATALOG["MICHELADA"], 0.9),
        }
        with _PatchStack(_stub_search(lexical=lexical, semantic=semantic)):
            results = product_search.search_products(BUSINESS_ID, "michelada")
        assert any(r["name"] == "Michelada" for r in results)


# ---------------------------------------------------------------------------
# Phase 4 — matched_by signal
# ---------------------------------------------------------------------------

class TestMatchedBySignal:
    """Each returned product has a matched_by tag the response generator can use."""

    def test_exact_match_tagged_as_exact(self):
        lexical = {
            "MICHELADA": BIELA_CATALOG["MICHELADA"],
        }
        with _PatchStack(_stub_search(lexical=lexical, semantic={})):
            results = product_search.search_products(BUSINESS_ID, "michelada")
        assert results
        assert results[0].get("matched_by") == "exact"

    def test_lexical_substring_match_tagged_as_lexical(self):
        """ARRABBIATA matches 'picante' via tag/description, not by exact name."""
        lexical = {
            "ARRABBIATA": BIELA_CATALOG["ARRABBIATA"],
        }
        with _PatchStack(_stub_search(lexical=lexical, semantic={})):
            results = product_search.search_products(BUSINESS_ID, "picante")
        arr = next((r for r in results if r["name"] == "ARRABBIATA"), None)
        assert arr is not None
        assert arr.get("matched_by") == "lexical"


# ---------------------------------------------------------------------------
# Phase 3 — category existence pre-check (list_products_with_fallback)
# ---------------------------------------------------------------------------

class TestCategoryExistencePreCheck:
    """
    list_products_with_fallback must return [] when the requested category
    does not exist at this tenant — not pivot to semantic fallback.
    """

    def test_pizza_category_returns_empty_when_no_pizza_exists(self):
        """'qué pizzas tienen' at Biela: no pizza category → return []."""
        from app.database import product_order_service as svc_module
        svc = svc_module.product_order_service

        with patch.object(svc, "list_products", return_value=[]) as mock_list, \
             patch.object(svc, "list_categories", return_value=["BURGERS", "BEBIDAS", "HOT DOGS"]), \
             patch.object(svc, "search_products_semantic") as mock_semantic:
            result = svc.list_products_with_fallback(BUSINESS_ID, "pizza")

        assert result == []
        mock_semantic.assert_not_called(), (
            "Phase 3: semantic fallback must be skipped when the category doesn't exist"
        )

    def test_bebidas_category_falls_through_when_exists(self):
        """
        When the category exists (e.g. 'BEBIDAS'), direct lookup returns
        rows and no fallback runs. Sanity check that the happy path is
        untouched.
        """
        from app.database import product_order_service as svc_module
        svc = svc_module.product_order_service

        existing_drinks = [BIELA_CATALOG["LIM_NATURAL"], BIELA_CATALOG["MICHELADA"]]
        with patch.object(svc, "list_products", return_value=existing_drinks), \
             patch.object(svc, "list_categories", return_value=["BURGERS", "BEBIDAS"]), \
             patch.object(svc, "search_products_semantic") as mock_semantic:
            result = svc.list_products_with_fallback(BUSINESS_ID, "bebidas")

        assert len(result) == 2
        mock_semantic.assert_not_called()

    def test_existing_category_with_empty_rows_falls_back_to_search(self):
        """
        Edge case: category exists but direct lookup returns nothing (data
        mismatch). The fallback to hybrid search still runs — Phase 3 only
        skips the fallback when the category is *entirely unknown*.
        """
        from app.database import product_order_service as svc_module
        svc = svc_module.product_order_service

        with patch.object(svc, "list_products", return_value=[]), \
             patch.object(svc, "list_categories", return_value=["BURGERS", "BEBIDAS"]), \
             patch.object(svc, "search_products_semantic", return_value=[BIELA_CATALOG["LIM_NATURAL"]]) as mock_semantic:
            result = svc.list_products_with_fallback(BUSINESS_ID, "bebidas")

        mock_semantic.assert_called_once()
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Phase 6 — generic-product token-containment rule
#
# The Biela "jugo de mora en leche" transcript (wa_id +573242261188,
# 2026-04-15) uncovered a class of query where the user is specifying
# a flavor/variant of a generic catalog row. "Jugos en leche" is one
# DB entry that the kitchen fulfills with whichever flavor is in stock
# that day — the flavor ("mora") travels to the ticket as a note on
# the cart item. Before this rule the search would return both
# "Jugos en leche" and "Hervido Mora" as near-tied disambiguation
# candidates, trapping the conversation in an infinite "which would
# you like?" loop.
# ---------------------------------------------------------------------------


class TestGenericProductContainmentMatch:
    """
    The decisive token-containment rule: when the query has strictly
    more content tokens than exactly one candidate, and that candidate's
    tokens are all present in the query, the candidate wins with the
    leftover tokens exposed as ``_derived_notes``.
    """

    def test_jugo_de_mora_en_leche_picks_generic_jugos_en_leche(self):
        """
        Regression for Biela +573242261188. Query:
            "jugo de mora en leche"
        Lexical returns the generic "Jugos en leche" + sibling
        "Hervido Mora" (shares the 'mora' token, semantically close).
        Expected: Jugos en leche wins decisively with
        ``_derived_notes="mora"``. No AmbiguousProductError.
        """
        lexical = {
            "JUGOS_LECHE":  BIELA_CATALOG["JUGOS_LECHE"],
            "HERVIDO_MORA": BIELA_CATALOG["HERVIDO_MORA"],
        }
        semantic = {
            "JUGOS_LECHE":  (BIELA_CATALOG["JUGOS_LECHE"], 0.82),
            "HERVIDO_MORA": (BIELA_CATALOG["HERVIDO_MORA"], 0.79),
        }
        with _PatchStack(_stub_search(lexical=lexical, semantic=semantic)):
            results = product_search.search_products(
                BUSINESS_ID, "jugo de mora en leche", unique=True,
            )
        assert len(results) == 1
        assert results[0]["name"] == "Jugos en leche"
        assert results[0].get("_derived_notes") == "mora"

    def test_jugo_de_lulo_en_agua_picks_generic_jugos_en_agua(self):
        """
        Parallel case for the water-based variant. Query tokens
        {jugo, lulo, agua} → candidate {jugos, agua} after stemming
        normalizes jugo/jugos. Leftover is 'lulo'.
        """
        lexical = {
            "JUGOS_AGUA":  BIELA_CATALOG["JUGOS_AGUA"],
            "JUGOS_LECHE": BIELA_CATALOG["JUGOS_LECHE"],
        }
        semantic = {
            "JUGOS_AGUA":  (BIELA_CATALOG["JUGOS_AGUA"], 0.80),
            "JUGOS_LECHE": (BIELA_CATALOG["JUGOS_LECHE"], 0.65),
        }
        with _PatchStack(_stub_search(lexical=lexical, semantic=semantic)):
            results = product_search.search_products(
                BUSINESS_ID, "jugo de lulo en agua", unique=True,
            )
        assert len(results) == 1
        assert results[0]["name"] == "Jugos en agua"
        assert results[0].get("_derived_notes") == "lulo"

    def test_ambiguous_flavor_without_milk_or_water_still_disambiguates(self):
        """
        Negative: "jugo de mora" (no 'en leche'/'en agua' qualifier)
        must NOT collapse to a single winner — both generic candidates
        are viable, user genuinely needs to pick. Guard that our rule
        only fires when exactly one candidate is a token subset.
        """
        lexical = {
            "JUGOS_LECHE": BIELA_CATALOG["JUGOS_LECHE"],
            "JUGOS_AGUA":  BIELA_CATALOG["JUGOS_AGUA"],
        }
        semantic = {
            "JUGOS_LECHE": (BIELA_CATALOG["JUGOS_LECHE"], 0.80),
            "JUGOS_AGUA":  (BIELA_CATALOG["JUGOS_AGUA"], 0.80),
        }
        with _PatchStack(_stub_search(lexical=lexical, semantic=semantic)):
            with pytest.raises(product_search.AmbiguousProductError):
                product_search.search_products(
                    BUSINESS_ID, "jugo de mora", unique=True,
                )


class TestCoronaStillDisambiguates:
    """
    Regression guard: the new generic-match rule must NOT break the
    existing Corona / Corona michelada disambiguation. These three
    tests pin the behavior that single-token queries like "corona" and
    "soda" never collapse to a single winner when prefix-rival siblings
    exist in the catalog.
    """

    def test_corona_query_alone_still_forces_disambiguation(self):
        """
        Query "corona" has 1 content token, Corona 355ml has 2
        ("corona", "355ml"), Corona michelada has 2 ("corona",
        "michelada"). The generic-match rule's "query has strictly
        more tokens than the candidate" guard should stop the rule
        from firing, and the existing prefix-rival logic should kick
        in and raise AmbiguousProductError.
        """
        lexical = {
            "CORONA":      BIELA_CATALOG["CORONA"],
            "CORONA_MICH": BIELA_CATALOG["CORONA_MICH"],
        }
        semantic = {
            "CORONA":      (BIELA_CATALOG["CORONA"], 0.78),
            "CORONA_MICH": (BIELA_CATALOG["CORONA_MICH"], 0.77),
        }
        with _PatchStack(_stub_search(lexical=lexical, semantic=semantic)):
            with pytest.raises(product_search.AmbiguousProductError):
                product_search.search_products(
                    BUSINESS_ID, "corona", unique=True,
                )

    def test_corona_michelada_exact_name_still_wins(self):
        """
        Query "corona michelada" matches Corona michelada exactly.
        The generic-match rule should never run here because an exact
        name match fires first. Tests that the new rule is ordered
        AFTER the exact-match short-circuit.
        """
        lexical = {
            "CORONA":      BIELA_CATALOG["CORONA"],
            "CORONA_MICH": BIELA_CATALOG["CORONA_MICH"],
        }
        semantic = {
            "CORONA_MICH": (BIELA_CATALOG["CORONA_MICH"], 0.90),
        }
        with _PatchStack(_stub_search(lexical=lexical, semantic=semantic)):
            results = product_search.search_products(
                BUSINESS_ID, "corona michelada", unique=True,
            )
        assert len(results) == 1
        assert results[0]["name"] == "Corona michelada"
        # No derived notes on an exact name match
        assert "_derived_notes" not in results[0] or not results[0].get("_derived_notes")


# ---------------------------------------------------------------------------
# Phase 7 — token-set equality decisive rule (Bug 5)
#
# Biela incident 2026-04-16 +573177000722: "Una soda de frutos rojos"
# triggered a disambiguation with 5 candidates — including Coca-Cola
# and Coca-Cola Zero — instead of winning decisively on Soda Frutos
# rojos. Root cause: the full-string exact-name rule 1a sees the
# "una" / "de" stopwords in the query and doesn't match; the
# score-ratio rule 2 can't clear 2× because Coca-Cola is a strong
# embedding neighbor.
#
# Fix: new rule 1c that declares a decisive winner when exactly one
# scored candidate's stemmed content-token set EQUALS the query's.
# Gated on query having ≥ 2 content tokens so the single-token
# prefix-rival cases (corona, michelada) keep their existing
# disambiguation behavior.
# ---------------------------------------------------------------------------


class TestTokenSetEqualityDecisiveWinner:
    """
    Decisive rule 1c: when the user's content tokens (after stopword
    strip + stem) equal exactly one candidate's content tokens, pick
    that candidate without triggering disambiguation.
    """

    def _soda_lexical(self):
        return {
            "SODA":         _product("p-soda",   "Soda",         category="BEBIDAS", tags=["bebida", "soda"], price=4500),
            "SODA_FRUTOS":  _product("p-sodafr", "Soda Frutos rojos", category="BEBIDAS", tags=["bebida", "soda"], price=15000),
            "SODA_UVILLA":  _product("p-sodauv", "Soda Uvilla y maracuyá", category="BEBIDAS", tags=["bebida", "soda"], price=15000),
            "COCA":         _product("p-coca",   "Coca-Cola",    category="BEBIDAS", tags=["bebida", "gaseosa"], price=5500),
            "COCA_ZERO":    _product("p-cocaz",  "Coca-Cola Zero", category="BEBIDAS", tags=["bebida", "gaseosa"], price=5500),
        }

    def test_soda_de_frutos_rojos_query_picks_soda_frutos_rojos(self):
        """
        Regression for Biela +573177000722 turn 11. The stopwords
        'una' and 'de' in the query stopped rule 1a from firing on
        Soda Frutos rojos; Coca-Cola as a semantic neighbor prevented
        rule 2 from clearing 2×. Rule 1c is the surgical fix.
        """
        lexical = self._soda_lexical()
        semantic = {
            "SODA":         (lexical["SODA"],         0.80),
            "SODA_FRUTOS":  (lexical["SODA_FRUTOS"],  0.85),
            "SODA_UVILLA":  (lexical["SODA_UVILLA"],  0.70),
            "COCA":         (lexical["COCA"],         0.60),
            "COCA_ZERO":    (lexical["COCA_ZERO"],    0.55),
        }
        with _PatchStack(_stub_search(lexical=lexical, semantic=semantic)):
            results = product_search.search_products(
                BUSINESS_ID, "una soda de frutos rojos", unique=True,
            )
        assert len(results) == 1
        assert results[0]["name"] == "Soda Frutos rojos"

    def test_plain_exact_name_still_wins_without_stopwords(self):
        """
        Guard: "Soda Frutos rojos" as a bare query (no stopwords)
        must still resolve via rule 1a exact-match, not 1c. Both
        rules give the same answer here — this pins that rule 1a
        fires first (so nothing regresses if 1c is later disabled).
        """
        lexical = self._soda_lexical()
        with _PatchStack(_stub_search(lexical=lexical, semantic={})):
            results = product_search.search_products(
                BUSINESS_ID, "Soda Frutos rojos", unique=True,
            )
        assert len(results) == 1
        assert results[0]["name"] == "Soda Frutos rojos"

    def test_two_candidates_with_equal_token_set_falls_back(self):
        """
        Negative guard: if two candidates share the same token set as
        the query, rule 1c abstains and the search falls through to
        the score-ratio rule (or disambiguation). This protects
        against accidentally picking when the catalog has duplicate
        near-identical names.
        """
        dup_a = _product("p-dup-a", "Limonada natural", category="BEBIDAS", tags=["limonada"], price=6500)
        dup_b = _product("p-dup-b", "Limonada natural", category="BEBIDAS", tags=["limonada"], price=6500)
        lexical = {"DUP_A": dup_a, "DUP_B": dup_b}
        # Equal scores → score ratio rule won't fire → disambiguation
        with _PatchStack(_stub_search(lexical=lexical, semantic={})):
            with pytest.raises(product_search.AmbiguousProductError):
                product_search.search_products(
                    BUSINESS_ID, "limonada natural", unique=True,
                )

    def test_corona_single_token_still_disambiguates_after_rule_1c(self):
        """
        Regression guard for the Corona/Corona michelada prefix-rival
        case. Rule 1c's ≥2-tokens gate means a bare "corona" query
        never reaches this path, so the existing rule 1a prefix-rival
        disambiguation stays intact.
        """
        lexical = {
            "CORONA":      BIELA_CATALOG["CORONA"],
            "CORONA_MICH": BIELA_CATALOG["CORONA_MICH"],
        }
        semantic = {
            "CORONA":      (BIELA_CATALOG["CORONA"],      0.78),
            "CORONA_MICH": (BIELA_CATALOG["CORONA_MICH"], 0.77),
        }
        with _PatchStack(_stub_search(lexical=lexical, semantic=semantic)):
            with pytest.raises(product_search.AmbiguousProductError):
                product_search.search_products(
                    BUSINESS_ID, "corona", unique=True,
                )

    def test_michelada_single_token_still_disambiguates_after_rule_1c(self):
        """
        Parallel Corona guard with the other half of the prefix rival.
        "michelada" alone must stay in the disambiguation lane because
        it's a prefix token of "Corona michelada".
        """
        lexical = {
            "MICHELADA":   BIELA_CATALOG["MICHELADA"],
            "CORONA_MICH": BIELA_CATALOG["CORONA_MICH"],
        }
        semantic = {
            "MICHELADA":   (BIELA_CATALOG["MICHELADA"],   0.85),
            "CORONA_MICH": (BIELA_CATALOG["CORONA_MICH"], 0.80),
        }
        with _PatchStack(_stub_search(lexical=lexical, semantic=semantic)):
            with pytest.raises(product_search.AmbiguousProductError):
                product_search.search_products(
                    BUSINESS_ID, "michelada", unique=True,
                )
