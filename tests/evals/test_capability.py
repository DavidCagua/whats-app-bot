"""
Capability evals — stretch tests that push the agent's limits.
Expected to have LOW pass rate initially. As the agent improves,
passing tests graduate to test_regression.py.

These test scenarios drawn from real Biela failures and edge cases
that the planner/agent doesn't reliably handle yet.
"""

import pytest

from tests.evals._harness import (
    AgentScenario,
    assert_scenario,
    product,
    run_scenario,
)


pytestmark = pytest.mark.eval


# ---------------------------------------------------------------------------
# Known planner routing gap: attribute-only queries
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    reason=(
        "Planner unstable on attribute-only queries. 'tienen algo picante?' "
        "gets routed to GET_MENU_CATEGORIES or CHAT depending on the run "
        "because the prompt rule for SEARCH_PRODUCTS expects a named product "
        "or ingredient, not a bare attribute/adjective. Graduate to the "
        "regression suite once PLANNER_SYSTEM_TEMPLATE adds an explicit rule "
        "for attribute queries (picante, dulce, sin gluten, vegetariano, etc.)."
    ),
    strict=False,
)
def test_attribute_only_query_picante_routes_to_search():
    """
    "tienen algo picante?" (bare attribute, no 'con X' preposition) should
    route to SEARCH_PRODUCTS and surface ARRABBIATA via the picante tag.
    Today the planner picks CHAT or GET_MENU_CATEGORIES inconsistently and
    retrieval never runs.

    The regression suite has a sibling test
    `test_algo_picante_returns_arrabbiata_preservation` that uses the
    explicit "algo con picante" phrasing — that path works today. This
    xfail pins the broken shorter phrasing so we notice when/if it
    starts passing.
    """
    def _arrabbiata(*_args, **_kwargs):
        return [
            product(
                "ARRABBIATA", 27000,
                description="Pan, carne, mozzarella, salsa arrabbiata picante, rúgula, papas.",
                tags=["hamburguesa", "burger", "picante"],
                matched_by="lexical",
            )
        ]
    scenario = AgentScenario(
        name="attribute_only_query_picante_routes_to_search",
        user_message="tienen algo picante?",
        initial_order_context={"state": "GREETING"},
        stub_search_products=_arrabbiata,
        stub_list_products_with_fallback=_arrabbiata,
        stub_list_products=_arrabbiata,
        stub_list_categories=lambda biz: ["BURGERS", "HOT DOGS", "BEBIDAS"],
        must_contain_any=[r"ARRABBIATA", r"arrabbiata"],
    )
    run = run_scenario(scenario)
    assert_scenario(scenario, run)


# ---------------------------------------------------------------------------
# Planned capability evals (not yet implemented)
# ---------------------------------------------------------------------------
#
# Case: "dame lo mismo de siempre" — requires order history lookup
# Case: "una barracuda sin cebolla y la dirección es calle 19" — multi-intent in a single message
# Case: "no espera, cambia la coca cola por una limonada" — mid-flow correction
# Case: "parce mándame dos combos al barrio" — heavy Colombian slang
# Case: "quiero 3 barracudas, no, 2, bueno sí 3" — self-correction within same message
# Case: "lo de siempre pero sin la coca cola" — reference to past order + modification
# Case: User sends voice note transcription with typos/fragments
# Case: User sends image of menu item (multimodal — not supported yet)
