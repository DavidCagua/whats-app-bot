"""
LLM-backed router classification eval.

Asserts that the real router LLM (no mocks) classifies a curated set
of phrases into the expected domain. Catches the regression class
where the prompt rule exists but the model still picks the wrong
domain — the kind of bug prompt-content unit tests can't catch.

Each scenario is one phrase + one expected domain. Failures here
mean either:
  - The prompt rule is too weak / ambiguous for the model.
  - A recent prompt edit accidentally moved the boundary.
  - The model upgraded and changed behavior.

Marked `eval` — deselected by default; runs with `pytest -m eval`.
Needs OPENAI_API_KEY to actually call the model.
"""

from unittest.mock import patch

import pytest

from app.orchestration import router
from app.orchestration.router import (
    DOMAIN_ORDER,
    DOMAIN_CUSTOMER_SERVICE,
    DOMAIN_CHAT,
)


pytestmark = pytest.mark.eval


BIELA_CONTEXT = {
    "business_id": "biela-eval",
    "business": {
        "name": "Biela",
        "settings": {
            "menu_url": "https://gixlink.com/Biela/menu.html",
            "delivery_fee": 5000,
        },
    },
}


# Production has a populated catalog cache for every business — the
# router's deterministic price-of-product short-circuit reads it via
# catalog_cache.get_router_lookup_set. The eval uses the fake business_id
# "biela-eval" which has no catalog rows, so without this patch the
# lookup-set is empty and the deterministic check can't fire — every
# "cuánto vale la barracuda" call falls through to the LLM, which is
# flaky on the unfamiliar product name. Pin a realistic Biela token set
# so we exercise the deterministic path the way production does.
_BIELA_CATALOG_LOOKUP = frozenset({
    # burger names
    "barracuda", "honey", "burger", "bimota", "beta", "biela", "americana",
    "arrabbiata", "montesa", "ramona", "pastor", "denver", "nairobi",
    # other catalog products / categories
    "picada", "pegoretti", "salchipapa", "salchipapas", "fries",
    "cheese", "special", "perro", "perros",
    # drinks
    "coca", "cocacola", "soda", "michelada", "corona",
    "jugos", "hervido", "malteada",
    # tags / attributes
    "picante", "queso", "azul", "mozzarella", "cheddar", "tocineta",
})


@pytest.fixture(autouse=True)
def _stub_router_catalog_lookup_set():
    """Populate the deterministic price-of-product short-circuit's input."""
    with patch(
        "app.orchestration.router.catalog_cache.get_router_lookup_set",
        return_value=_BIELA_CATALOG_LOOKUP,
    ):
        yield


# (phrase, expected_first_segment_domain, why)
# Curated to cover the discriminator cases that have caused real
# production bugs — not exhaustive of every router decision.
ROUTING_CASES = [
    # ─── Ordering openers (intent: start an order, not a question) ────
    ("para un domicilio", DOMAIN_ORDER, "opener — no question, no product"),
    ("un domicilio por favor", DOMAIN_ORDER, "opener phrasing"),
    ("quiero pedir", DOMAIN_ORDER, "explicit ordering verb"),
    ("para hacer un pedido", DOMAIN_ORDER, "opener phrasing"),
    ("buenas, un domicilio por favor", DOMAIN_ORDER, "greeting + opener"),

    # ─── Delivery as a POLICY question (intent: ask price/info) ───────
    ("cuánto vale el domicilio", DOMAIN_CUSTOMER_SERVICE, "price question, no product"),
    ("cuánto cobran de domicilio", DOMAIN_CUSTOMER_SERVICE, "price question variant"),
    ("tienen domicilio?", DOMAIN_CUSTOMER_SERVICE, "policy question — do you offer delivery"),

    # ─── Named-product price questions (intent: menu navigation) ──────
    ("cuánto vale la barracuda", DOMAIN_ORDER, "named product price → menu navigation"),
    ("una picada que valor?", DOMAIN_ORDER, "product + price question, no order verb"),
    ("qué precio tiene la honey burger", DOMAIN_ORDER, "named product price"),

    # ─── Promo discovery vs. cart action ──────────────────────────────
    ("qué promos tienen?", DOMAIN_CUSTOMER_SERVICE, "discovery, no specific promo"),
    ("tienes alguna promo?", DOMAIN_CUSTOMER_SERVICE, "discovery"),
    ("dame la promo del honey", DOMAIN_ORDER, "cart action with named promo"),

    # ─── Other CS classics (sanity — these have always worked) ────────
    ("a qué hora abren?", DOMAIN_CUSTOMER_SERVICE, "hours question"),
    ("cuál es la dirección?", DOMAIN_CUSTOMER_SERVICE, "address question"),

    # ─── Delivery TIME questions (info, not order) ────────────────────
    ("cuánto se demora la entrega?", DOMAIN_CUSTOMER_SERVICE, "delivery-time policy"),
    ("cuánto tardan en entregar?", DOMAIN_CUSTOMER_SERVICE, "delivery-time variant"),

    # ─── Browsing + adding (sanity for order) ─────────────────────────
    ("qué hamburguesas tienen?", DOMAIN_ORDER, "category browsing"),
    ("dame una barracuda", DOMAIN_ORDER, "explicit cart add"),
    ("qué trae la barracuda", DOMAIN_ORDER, "product details"),
]


@pytest.mark.parametrize(
    "phrase, expected_domain, why",
    [pytest.param(p, d, w, id=p) for (p, d, w) in ROUTING_CASES],
)
def test_router_classifies_phrase_to_expected_domain(phrase, expected_domain, why):
    result = router.route(
        message_body=phrase,
        business_context=BIELA_CONTEXT,
        customer_name="David",
    )
    # Pure greetings short-circuit before the LLM. None of our test
    # phrases are pure greetings, so we expect classifier output.
    assert result.direct_reply is None, (
        f"unexpected greeting fast-path hit for {phrase!r}; "
        f"only pure greetings should match"
    )
    assert result.segments, (
        f"router returned no segments for {phrase!r} ({why}); "
        f"classifier may have failed — check logs"
    )
    actual_domain = result.segments[0][0]
    assert actual_domain == expected_domain, (
        f"phrase={phrase!r} expected={expected_domain!r} "
        f"got={actual_domain!r} (reason: {why}). "
        f"all_segments={result.segments}"
    )
