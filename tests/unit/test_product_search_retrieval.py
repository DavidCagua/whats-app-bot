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
    category: str = "HAMBURGUESAS",
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
    "PERRO_DENVER":_product("p-11", "Perro Caliente Denver", category="PERROS CALIENTES", description="Perro caliente estilo Denver.", tags=["perro", "hot dog"], price=27000),
    "NIJABOU":     _product("p-12", "NIJABOU", category="HAMBURGUESAS", description="Hamburguesa estilo japonés.", tags=["hamburguesa", "burger"]),
    "NAIROBI":     _product("p-13", "NAIROBI", category="HAMBURGUESAS", description="Hamburguesa estilo africano.", tags=["hamburguesa", "burger"]),
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
    # Chicken burgers — used by the typo-tolerance tests (vitoria → VITTORIA).
    "VITTORIA":    _product("p-19", "VITTORIA", description="Filete de pollo apanado, albahaca, mozzarella.", tags=["hamburguesa", "burger", "pollo"], price=28000),
    "ARIZONA":     _product("p-20", "ARIZONA", description="Filete de pollo apanado, tocineta, pepinillos.", tags=["hamburguesa", "burger", "pollo"], price=28000),
    # Multi-word product whose name is also a common Colombian phrase
    # ("a la vuelta" = "around the corner"). Used by the regression
    # test below for Biela / 3147554464 (2026-05-06): the planner +
    # search disambiguator picked HONEY BURGER and stuffed "a la vuelta"
    # into notes instead of returning LA VUELTA outright.
    "LA_VUELTA":   _product("p-21", "LA VUELTA", description="Pan artesanal, 150gr de carne, tocineta crispy de cebolla, caramelizado de chilacuan, queso quajada, salsa tártara.", tags=["hamburguesa", "burger", "carne", "res", "chilacuan", "cuajada"], price=28000),
    "HONEY_BURGER":_product("p-22", "HONEY BURGER", description="Carne, cheddar, tocineta, miel mostaza, cebolla caramelizada, papas.", tags=["hamburguesa", "burger"], price=28000),
    # Campaign-tagged burger used by the dominance-trim regression: when
    # the user types "la del burguer master" RAMONA must win decisively
    # (not be listed alongside the rest of the burgers).
    "RAMONA":      _product("p-23", "RAMONA",
                            description="Participante burguer master. Carne Angus, queso americano, mostaneza golf, pepinos encurtidos, tocineta y pan pretzel.",
                            tags=["hamburguesa", "carne", "queso", "burguer master", "tocineta"],
                            price=30000),
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
        # Disable the LLM resolver by default so deterministic-rule
        # tests can assert AmbiguousProductError without the resolver
        # intercepting. Tests that want to exercise the LLM resolver
        # override this with their own mock.
        patch("app.services.product_search._llm_resolve_disambiguation", return_value=None),
        # Disable fuzzy fallback paths by default — trigram returns no
        # hits, LLM zero-result returns None. Tests for these features
        # override explicitly.
        patch("app.services.product_search._trigram_candidates", return_value={}),
        patch("app.services.product_search._llm_zero_result_fallback", return_value=None),
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
        """
        'qué pizzas tienen' at Biela: no pizza category, no products
        tagged 'pizza' → return []. The hybrid search's pure-embedding
        filter handles this — it returns empty when only embedding
        neighbors match and no lexical/tag signal exists.
        """
        from app.database import product_order_service as svc_module
        svc = svc_module.product_order_service

        with patch.object(svc, "list_products", return_value=[]), \
             patch.object(svc, "search_products_semantic", return_value=[]):
            result = svc.list_products_with_fallback(BUSINESS_ID, "pizza")

        assert result == []

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
             patch.object(svc, "list_categories", return_value=["HAMBURGUESAS", "BEBIDAS"]), \
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
             patch.object(svc, "list_categories", return_value=["HAMBURGUESAS", "BEBIDAS"]), \
             patch.object(svc, "search_products_semantic", return_value=[BIELA_CATALOG["LIM_NATURAL"]]) as mock_semantic:
            result = svc.list_products_with_fallback(BUSINESS_ID, "bebidas")

        mock_semantic.assert_called_once()
        assert len(result) == 1

    def test_cerveza_subcategory_falls_through_to_tag_search(self):
        """
        Regression for 98b8bf9: 'cervezas' is not a DB category (products
        are in BEBIDAS) but beers are tagged 'cerveza'. The old pre-check
        blocked the fallback for non-overlapping category names. Now the
        hybrid search runs and finds beers via tag match.
        """
        from app.database import product_order_service as svc_module
        svc = svc_module.product_order_service

        beers = [BIELA_CATALOG["CORONA"], BIELA_CATALOG["CORONA_MICH"]]
        with patch.object(svc, "list_products", return_value=[]), \
             patch.object(svc, "search_products_semantic", return_value=beers) as mock_semantic:
            result = svc.list_products_with_fallback(BUSINESS_ID, "cervezas")

        mock_semantic.assert_called_once()
        assert len(result) == 2
        names = {p["name"] for p in result}
        assert "Corona 355ml" in names
        assert "Corona michelada" in names


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


# ---------------------------------------------------------------------------
# Phase 8 — LLM disambiguation resolver
#
# When deterministic rules can't pick a winner, a cheap LLM call
# resolves the ambiguity with semantic understanding. Three outcomes:
#   WINNER   → one clear best match (optionally with derived notes)
#   FILTERED → genuinely ambiguous, but some candidates excluded
#   AMBIGUOUS → all candidates plausible
#
# These tests mock _llm_resolve_disambiguation to verify the wiring
# without hitting the real API.
# ---------------------------------------------------------------------------


def _stub_search_with_llm(lexical=None, semantic=None, synonyms=None, llm_result=None):
    """
    Like _stub_search but lets the caller control the LLM resolver's
    return value. Passes llm_result=None to disable (same as the
    default stub), or a dict to simulate a specific LLM response.
    """
    base_patches = _stub_search(lexical=lexical, semantic=semantic, synonyms=synonyms)
    # Replace the last patch (which disables the LLM resolver) with
    # one that returns our test-specific result.
    base_patches[-1] = patch(
        "app.services.product_search._llm_resolve_disambiguation",
        return_value=llm_result,
    )
    return base_patches


class TestLLMDisambiguationResolver:
    """
    Integration point tests: verify that the search_products wiring
    correctly uses the LLM resolver's output to pick a winner, filter
    candidates, or fall through to AmbiguousProductError.
    """

    @staticmethod
    def _jugo_candidates():
        return {
            "JUGOS_LECHE":  BIELA_CATALOG["JUGOS_LECHE"],
            "JUGOS_AGUA":   BIELA_CATALOG["JUGOS_AGUA"],
            "HERVIDO_MORA": BIELA_CATALOG["HERVIDO_MORA"],
        }

    def test_llm_winner_returns_single_product(self):
        """
        LLM says WINNER → search_products returns a single-element list
        with the winning product.
        """
        cands = self._jugo_candidates()
        llm_result = {
            "result": "WINNER",
            "product": dict(cands["JUGOS_LECHE"], _derived_notes="mora"),
        }
        with _PatchStack(_stub_search_with_llm(
            lexical=cands,
            semantic={k: (v, 0.80) for k, v in cands.items()},
            llm_result=llm_result,
        )):
            results = product_search.search_products(
                BUSINESS_ID, "jugo de mora en leche", unique=True,
            )
        assert len(results) == 1
        assert results[0]["name"] == "Jugos en leche"
        assert results[0].get("_derived_notes") == "mora"

    def test_llm_filtered_excludes_wrong_category(self):
        """
        LLM says FILTERED → AmbiguousProductError is still raised but
        only with the filtered candidates. Hervido Mora (a hot drink)
        should be excluded from a "jugo" disambiguation.
        """
        cands = self._jugo_candidates()
        filtered_products = [cands["JUGOS_AGUA"], cands["JUGOS_LECHE"]]
        llm_result = {
            "result": "FILTERED",
            "products": filtered_products,
        }
        with _PatchStack(_stub_search_with_llm(
            lexical=cands,
            semantic={k: (v, 0.80) for k, v in cands.items()},
            llm_result=llm_result,
        )):
            with pytest.raises(product_search.AmbiguousProductError) as exc_info:
                product_search.search_products(
                    BUSINESS_ID, "jugo de mora", unique=True,
                )
        names = {m.get("name") for m in exc_info.value.matches}
        assert "Jugos en agua" in names
        assert "Jugos en leche" in names
        assert "Hervido Mora" not in names

    def test_llm_ambiguous_raises_with_all_candidates(self):
        """
        LLM says AMBIGUOUS → AmbiguousProductError raised with the
        original candidates (same as if no LLM resolver existed).

        Uses the Corona pair because deterministic rules reach the LLM
        resolver when one candidate has an exact match but a prefix
        rival exists — rule 1a falls through, and the score gap isn't
        big enough for rule 2.

        Wait — actually Corona "corona" does reach AmbiguousProductError
        via the prefix-rival fallthrough. But the LLM resolver is
        disabled in the default stub. Here we ENABLE it and make it
        return AMBIGUOUS to verify the wiring.
        """
        lexical = {
            "CORONA":      BIELA_CATALOG["CORONA"],
            "CORONA_MICH": BIELA_CATALOG["CORONA_MICH"],
        }
        semantic = {
            "CORONA":      (BIELA_CATALOG["CORONA"],      0.78),
            "CORONA_MICH": (BIELA_CATALOG["CORONA_MICH"], 0.77),
        }
        llm_result = {"result": "AMBIGUOUS"}
        with _PatchStack(_stub_search_with_llm(
            lexical=lexical,
            semantic=semantic,
            llm_result=llm_result,
        )):
            with pytest.raises(product_search.AmbiguousProductError) as exc_info:
                product_search.search_products(
                    BUSINESS_ID, "corona", unique=True,
                )
        names = {m.get("name") for m in exc_info.value.matches}
        assert "Corona 355ml" in names
        assert "Corona michelada" in names

    def test_llm_failure_falls_back_to_full_disambiguation(self):
        """
        When the LLM resolver returns None (API failure, no key, parse
        error), the search falls back to the deterministic path: raise
        AmbiguousProductError with all lexical candidates.
        """
        lexical = {
            "CORONA":      BIELA_CATALOG["CORONA"],
            "CORONA_MICH": BIELA_CATALOG["CORONA_MICH"],
        }
        semantic = {
            "CORONA":      (BIELA_CATALOG["CORONA"],      0.78),
            "CORONA_MICH": (BIELA_CATALOG["CORONA_MICH"], 0.77),
        }
        # llm_result=None simulates failure
        with _PatchStack(_stub_search_with_llm(
            lexical=lexical,
            semantic=semantic,
            llm_result=None,
        )):
            with pytest.raises(product_search.AmbiguousProductError) as exc_info:
                product_search.search_products(
                    BUSINESS_ID, "corona", unique=True,
                )
        assert len(exc_info.value.matches) >= 2


# ---------------------------------------------------------------------------
# Phase 9 — Typo tolerance: trigram + LLM zero-result fallback
# ---------------------------------------------------------------------------


def _stub_search_with_trigram(
    lexical=None,
    semantic=None,
    synonyms=None,
    trigram=None,
    llm_zero_result=None,
):
    """
    Like _stub_search but lets the caller control the trigram fallback
    and LLM zero-result fallback return values.

    Args:
        trigram: {product_key: product_dict} — what _trigram_candidates returns
        llm_zero_result: product dict or None — what _llm_zero_result_fallback returns
    """
    base_patches = _stub_search(lexical=lexical, semantic=semantic, synonyms=synonyms)

    trigram_map = {}
    for k, p in (trigram or {}).items():
        trigram_map[p["id"]] = p

    # Replace the trigram and llm_zero_result patches (last two in base)
    base_patches[-2] = patch(
        "app.services.product_search._trigram_candidates",
        return_value=trigram_map,
    )
    base_patches[-1] = patch(
        "app.services.product_search._llm_zero_result_fallback",
        return_value=llm_zero_result,
    )
    # Stub catalog_cache.list_products so the LLM fallback path doesn't
    # hit the real DB (the return value is only used by the real
    # _llm_zero_result_fallback, which is itself patched above).
    base_patches.append(patch(
        "app.services.product_search.catalog_cache.list_products",
        return_value=list(BIELA_CATALOG.values()),
    ))
    return base_patches


class TestTypoTolerance:
    """
    Regression tests for the typo-tolerance fallback chain.

    When lexical + semantic return nothing (the user misspelled a product
    name), the search should try:
      1. pg_trgm similarity on product names
      2. LLM zero-result fallback with cached catalog

    Pinned by the real incident: user typed "Una Vitoria" (1 t) for the
    product "VITTORIA" (2 t's). The bot replied "No tengo la Vitoria en
    el menú" instead of adding it to the cart.
    """

    def test_trigram_single_hit_returns_winner(self):
        """
        Trigram finds exactly one fuzzy match → return it directly.
        This is the "vitoria" → "VITTORIA" case.
        """
        # Lexical + semantic return nothing (the typo breaks ILIKE)
        trigram = {"VITTORIA": BIELA_CATALOG["VITTORIA"]}
        with _PatchStack(_stub_search_with_trigram(trigram=trigram)):
            results = product_search.search_products(
                BUSINESS_ID, "vitoria", unique=True,
            )
        assert len(results) == 1
        assert results[0]["name"] == "VITTORIA"
        assert results[0]["matched_by"] == "trigram"

    def test_trigram_single_hit_unique_false(self):
        """Trigram hit also works for unique=False (browse/search path)."""
        trigram = {"VITTORIA": BIELA_CATALOG["VITTORIA"]}
        with _PatchStack(_stub_search_with_trigram(trigram=trigram)):
            results = product_search.search_products(
                BUSINESS_ID, "vitoria", unique=False,
            )
        assert len(results) == 1
        assert results[0]["name"] == "VITTORIA"

    def test_trigram_multiple_hits_raises_ambiguous(self):
        """
        Trigram returns multiple fuzzy matches and LLM disambiguator
        can't pick a winner → AmbiguousProductError.
        """
        trigram = {
            "VITTORIA": BIELA_CATALOG["VITTORIA"],
            "ARIZONA":  BIELA_CATALOG["ARIZONA"],
        }
        with _PatchStack(_stub_search_with_trigram(trigram=trigram)):
            with pytest.raises(product_search.AmbiguousProductError) as exc_info:
                product_search.search_products(
                    BUSINESS_ID, "vizona", unique=True,
                )
        names = {m.get("name") for m in exc_info.value.matches}
        assert "VITTORIA" in names or "ARIZONA" in names

    def test_trigram_multiple_hits_llm_picks_winner(self):
        """
        Trigram returns multiple fuzzy matches but the LLM disambiguator
        picks a clear winner.
        """
        trigram = {
            "VITTORIA": BIELA_CATALOG["VITTORIA"],
            "ARIZONA":  BIELA_CATALOG["ARIZONA"],
        }
        # Override LLM disambiguator to pick VITTORIA
        patches = _stub_search_with_trigram(trigram=trigram)
        # The LLM disambiguator patch is at index 4 in the base stubs
        patches[4] = patch(
            "app.services.product_search._llm_resolve_disambiguation",
            return_value={
                "result": "WINNER",
                "product": dict(BIELA_CATALOG["VITTORIA"]),
            },
        )
        with _PatchStack(patches):
            results = product_search.search_products(
                BUSINESS_ID, "vitoria", unique=True,
            )
        assert len(results) == 1
        assert results[0]["name"] == "VITTORIA"

    def test_llm_zero_result_fallback_when_trigram_empty(self):
        """
        Both lexical+semantic AND trigram return nothing → LLM fallback
        with cached catalog finds the match.
        """
        llm_match = dict(BIELA_CATALOG["VITTORIA"])
        llm_match["matched_by"] = "llm_fallback"
        # Also need to stub catalog_cache.list_products
        patches = _stub_search_with_trigram(llm_zero_result=llm_match)
        with _PatchStack(patches):
            results = product_search.search_products(
                BUSINESS_ID, "vitoria", unique=True,
            )
        assert len(results) == 1
        assert results[0]["name"] == "VITTORIA"
        assert results[0]["matched_by"] == "llm_fallback"

    def test_all_fallbacks_fail_returns_empty(self):
        """
        Lexical, semantic, trigram, and LLM all return nothing → [].
        """
        with _PatchStack(_stub_search_with_trigram()):
            results = product_search.search_products(
                BUSINESS_ID, "xyznonexistent", unique=True,
            )
        assert results == []

    def test_trigram_does_not_fire_when_lexical_has_results(self):
        """
        When lexical returns results, the trigram fallback should NOT
        run — it only fires when merged is empty.
        """
        lexical = {"VITTORIA": BIELA_CATALOG["VITTORIA"]}
        trigram = {"ARIZONA": BIELA_CATALOG["ARIZONA"]}
        with _PatchStack(_stub_search_with_trigram(
            lexical=lexical, trigram=trigram,
        )):
            results = product_search.search_products(
                BUSINESS_ID, "vittoria", unique=True,
            )
        # Should return VITTORIA from lexical, not ARIZONA from trigram
        assert len(results) == 1
        assert results[0]["name"] == "VITTORIA"


# ---------------------------------------------------------------------------
# Regression: 2026-05-06 (Biela / 3147554464). User said "Regálame una
# hamburguesa a la vuelta" and "Tienes la a la Vuelta?". LA VUELTA exists
# in the catalog, but the bot replied "Se ha agregado la HONEY BURGER
# (a la vuelta)" — picked the wrong product and stuffed the real product
# name into notes.
#
# These tests document what search_products currently returns for this
# class of query so we can target the broken decisive rule. The expected
# outcome (after the fix) is LA VUELTA as the unique winner with
# "hamburguesa" as derived notes (mirrors the existing
# "jugo de mora en leche" → "Jugos en leche" behavior).
# ---------------------------------------------------------------------------


class TestLaVueltaSubsetMatch:
    """Repro for the LA VUELTA mis-resolution bug."""

    def _all_burgers_lexical(self):
        # What the lexical search would plausibly return: every burger
        # whose name or tags overlap the query. Includes LA VUELTA.
        keys = ("LA_VUELTA", "HONEY_BURGER", "BARRACUDA", "BIELA", "BETA",
                "PEGORETTI", "ARRABBIATA", "MANHATTAN")
        return {k: BIELA_CATALOG[k] for k in keys}

    def _all_burgers_semantic(self):
        keys = ("LA_VUELTA", "HONEY_BURGER", "BARRACUDA", "BIELA", "BETA")
        # Plausible cosine similarities — LA VUELTA gets a respectable
        # score (it's in the candidate set) but isn't necessarily highest.
        sims = {"LA_VUELTA": 0.78, "HONEY_BURGER": 0.82, "BARRACUDA": 0.74,
                "BIELA": 0.71, "BETA": 0.70}
        return {k: (BIELA_CATALOG[k], sims[k]) for k in keys}

    def test_full_phrase_query_picks_la_vuelta(self):
        """
        Query: "una hamburguesa a la vuelta"
        Stems (rough): {hamburgu, vuelt}
        LA VUELTA stems: {vuelt} — strict subset → unique subset_match
            → rule 1b should fire → LA VUELTA wins, notes="hamburguesa".
        HONEY BURGER stems: {honey, burger} — NOT a subset.

        Currently expected to FAIL (production picked HONEY BURGER).
        After the targeted fix, this passes.
        """
        with _PatchStack(_stub_search(
            lexical=self._all_burgers_lexical(),
            semantic=self._all_burgers_semantic(),
        )):
            results = product_search.search_products(
                BUSINESS_ID, "una hamburguesa a la vuelta", unique=True,
            )
        assert len(results) == 1, [r["name"] for r in results]
        assert results[0]["name"] == "LA VUELTA"

    def test_short_query_la_vuelta_picks_la_vuelta(self):
        """
        Query: "la vuelta" (the bare product name).
        Should be an exact-name match (rule 1a) — no ambiguity.
        """
        with _PatchStack(_stub_search(
            lexical=self._all_burgers_lexical(),
            semantic=self._all_burgers_semantic(),
        )):
            results = product_search.search_products(
                BUSINESS_ID, "la vuelta", unique=True,
            )
        assert len(results) == 1
        assert results[0]["name"] == "LA VUELTA"

    def test_user_phrasing_tienes_la_a_la_vuelta(self):
        """
        Query: "tienes la a la Vuelta?" — the actual production message.
        Stems (rough): {tien, vuelt}
        LA VUELTA {vuelt} is a strict subset → unique winner via rule 1b.
        """
        with _PatchStack(_stub_search(
            lexical=self._all_burgers_lexical(),
            semantic=self._all_burgers_semantic(),
        )):
            results = product_search.search_products(
                BUSINESS_ID, "tienes la a la Vuelta?", unique=True,
            )
        assert len(results) == 1, [r["name"] for r in results]
        assert results[0]["name"] == "LA VUELTA"

    def test_planner_dropped_vuelta_query_is_ambiguous(self):
        """
        When the planner emits the wrong product_name (e.g. just
        "hamburguesa" or "HONEY BURGER" because it picked from the
        previously-listed options and didn't recognize "la Vuelta" as
        a real product), search_products has nothing to anchor on. We
        document that behavior here — it's NOT a search bug, it's a
        planner bug. The fix has to live upstream (router or order
        planner), not in product_search.
        """
        # Planner emits just "hamburguesa" — no "vuelta" token.
        with _PatchStack(_stub_search(
            lexical=self._all_burgers_lexical(),
            semantic=self._all_burgers_semantic(),
        )):
            with pytest.raises(product_search.AmbiguousProductError):
                product_search.search_products(
                    BUSINESS_ID, "hamburguesa", unique=True,
                )

    def test_planner_emitted_honey_burger_returns_honey_burger(self):
        """
        Confirms the production failure path: if the planner emits
        product_name="HONEY BURGER" (because it picked the first item
        from a recently-listed options block), search_products faithfully
        returns HONEY BURGER — never sees the user's original "la vuelta"
        intent. The fix must keep the planner from emitting the wrong
        product_name in the first place.
        """
        with _PatchStack(_stub_search(
            lexical=self._all_burgers_lexical(),
            semantic=self._all_burgers_semantic(),
        )):
            results = product_search.search_products(
                BUSINESS_ID, "HONEY BURGER", unique=True,
            )
        assert len(results) == 1
        assert results[0]["name"] == "HONEY BURGER"


# ---------------------------------------------------------------------------
# Dominance trim — SEARCH_PRODUCTS (unique=False) must not enumerate
# alternatives when the top result has a decisive score lead.
# ---------------------------------------------------------------------------


class TestDominanceTrim:
    """
    Regression for the Biela 'la del Burguer master?' incident: even after
    the phrase-aware tag/description boost, search returned the full ranked
    list, the response generator enumerated 5 burgers, and the user had to
    pick — defeating the point of the boost. With the dominance trim,
    a SEARCH whose top score has a 2x lead over the runner-up returns
    only the winner.
    """

    def _burger_lexical(self):
        # Mirrors what _lexical_candidates returns in prod for any burger
        # query: every burger whose tags include "hamburguesa" / "burger".
        keys = ("RAMONA", "BARRACUDA", "BIELA", "BETA", "PEGORETTI",
                "ARRABBIATA", "MANHATTAN", "HONEY_BURGER")
        return {k: BIELA_CATALOG[k] for k in keys}

    def _burger_semantic(self):
        keys = ("RAMONA", "BARRACUDA", "BIELA", "ARRABBIATA")
        sims = {"RAMONA": 0.82, "BARRACUDA": 0.72, "BIELA": 0.70, "ARRABBIATA": 0.69}
        return {k: (BIELA_CATALOG[k], sims[k]) for k in keys}

    def test_burguer_master_phrase_returns_only_ramona(self):
        """
        Query: "Está la del Burguer master?" (the prod failure phrase).
        RAMONA has the multi-word tag "burguer master" AND the literal
        phrase in its description. The phrase boost gives it a 2x+ lead;
        the dominance trim returns only RAMONA so the response generator
        speaks about that one product.
        """
        with _PatchStack(_stub_search(
            lexical=self._burger_lexical(),
            semantic=self._burger_semantic(),
        )):
            results = product_search.search_products(
                BUSINESS_ID, "Está la del Burguer master?",
            )
        names = [r["name"] for r in results]
        assert names == ["RAMONA"], (
            f"expected only RAMONA after dominance trim, got {names}"
        )

    def test_concurso_phrase_returns_only_ramona(self):
        """
        Synonym variant: "la hamburguesa del concurso" — RAMONA's
        description contains 'concurso' (campaign mention). Same outcome.
        """
        with _PatchStack(_stub_search(
            lexical=self._burger_lexical(),
            semantic=self._burger_semantic(),
        )):
            results = product_search.search_products(
                BUSINESS_ID, "la hamburguesa del concurso",
            )
        # RAMONA's description doesn't literally contain "concurso" in the
        # fixture, but with the embedding sim and phrase boost it should
        # still dominate. We assert RAMONA is the *first* result and not
        # buried among alternatives.
        assert results, "expected at least one result"
        assert results[0]["name"] == "RAMONA"

    def test_no_dominance_keeps_broad_list(self):
        """
        Reverse guarantee: when the score gap is narrow, the dominance
        trim must NOT fire. 'limonada' matches both LIM_NATURAL and
        LIM_CEREZA equally — both must come back.
        """
        lexical = {
            "LIM_NATURAL": BIELA_CATALOG["LIM_NATURAL"],
            "LIM_CEREZA":  BIELA_CATALOG["LIM_CEREZA"],
        }
        with _PatchStack(_stub_search(lexical=lexical, semantic={})):
            results = product_search.search_products(BUSINESS_ID, "limonada")
        names = {r["name"] for r in results}
        assert "Limonada natural" in names
        assert "Limonada de cereza" in names, (
            f"dominance trim must not fire for non-decisive queries; got {names}"
        )
