"""
Regression evals — end-to-end scenarios that run the real OrderAgent
pipeline (planner LLM + executor + response LLM) and assert via the
LangChain `agentevals` trajectory evaluators plus a response-text
layer for prose-level guardrails.

Each scenario pins a user-facing behavior that we've seen regress in
production. Trajectory matching (`create_trajectory_match_evaluator`
in superset mode) catches planner routing drift early; the
response-text regex catches response-generator prose drift; LLM-as-judge
is reserved for cases where prose nuance genuinely matters.

Running:
    pytest -m eval                        # all (needs OPENAI_API_KEY)
    pytest -m eval -k pizza               # single scenario
    pytest -m eval -k confirm             # the Biela abandonment class

If pass rate drops below 100% on any of these, do NOT ship — the fix
they're pinning has regressed.
"""

import pytest

from tests.evals._harness import (
    AgentScenario,
    assert_scenario,
    expected_planner_call,
    product,
    run_scenario,
)


pytestmark = pytest.mark.eval


# ---------------------------------------------------------------------------
# Retrieval false-positive regressions (fix: commit ac2a6a3)
# ---------------------------------------------------------------------------

def test_hay_pizza_at_biela_says_no_pizza():
    """
    The Biela false-positive: user asks 'hay pizza?' at a burger shop.
    Before the retrieval hardening, embedding fallback returned burgers
    and the LLM narrated them as "las siguientes pizzas". After the fix:
    - Planner must route to LIST_PRODUCTS (category question) or
      SEARCH_PRODUCTS (product query) — NOT GREET or CHAT.
    - Response must tell the user we don't have pizza, not list burgers
      as if they were pizzas.

    Deterministic layer: superset trajectory match — the planner must
    have called a retrieval intent with "pizza" in the args.
    Prose layer: response must not pretend pizza exists.
    """
    scenario = AgentScenario(
        name="hay_pizza_at_biela_says_no_pizza",
        user_message="hay pizza?",
        initial_order_context={"state": "GREETING"},
        stub_search_products=lambda biz, q: [],
        stub_list_products_with_fallback=lambda biz, cat: [],
        stub_list_categories=lambda biz: ["BURGERS", "HOT DOGS", "BEBIDAS"],
        reference_trajectory=expected_planner_call(
            user_message="hay pizza?",
            intent="LIST_PRODUCTS",
            params={"category": "pizza"},
        ),
        must_not_contain=[
            r"(?<!no\s)tenemos pizza",
            r"(?<!no\s)contamos con.*pizza",
            r"\blas siguientes pizzas\b",
            r"\bnuestras pizzas\b",
            r"PICADA.*pizza|pizza.*PICADA",
            r"PEGORETTI.*pizza|pizza.*PEGORETTI",
            r"ARRABBIATA.*pizza|pizza.*ARRABBIATA",
        ],
        must_contain_any=[
            r"\bno tenemos\b.*pizza",
            r"\bno contamos\b.*pizza",
            r"\bno hay\b.*pizza",
            r"\bsin pizza\b",
        ],
    )
    run = run_scenario(scenario)
    assert_scenario(scenario, run)


def test_hay_sushi_at_biela_says_no_sushi():
    """Same class as pizza — no sushi at a burger shop."""
    scenario = AgentScenario(
        name="hay_sushi_at_biela_says_no_sushi",
        user_message="hay sushi?",
        initial_order_context={"state": "GREETING"},
        stub_search_products=lambda biz, q: [],
        stub_list_products_with_fallback=lambda biz, cat: [],
        stub_list_categories=lambda biz: ["BURGERS", "HOT DOGS", "BEBIDAS"],
        reference_trajectory=expected_planner_call(
            user_message="hay sushi?",
            intent="LIST_PRODUCTS",
            params={"category": "sushi"},
        ),
        must_not_contain=[
            r"(?<!no\s)tenemos sushi",
            r"(?<!no\s)contamos con.*sushi",
            r"\bnuestro sushi\b",
            r"Manhattan.*sushi|sushi.*Manhattan",
            r"Barracuda.*sushi|sushi.*Barracuda",
        ],
        must_contain_any=[
            r"\bno tenemos\b.*sushi",
            r"\bno contamos\b.*sushi",
            r"\bno hay\b.*sushi",
        ],
    )
    run = run_scenario(scenario)
    assert_scenario(scenario, run)


def test_perro_caliente_denver_returns_only_denver():
    """
    "un perro caliente denver" — Biela has a "Perro Caliente Denver".
    Before the fix, embedding additively appended NIJABOU/NAIROBI and
    unrelated drinks. After the fix, only Denver should surface.

    Deterministic layer: planner must route to ADD_TO_CART (user named
    a specific product and used a quantity article "un"). Prose layer:
    response must name Denver and must NOT mention the bleed products.
    """
    denver = product(
        "Perro Caliente Denver", 27000,
        category="HOT DOGS",
        description="Perro caliente estilo Denver con salsa BBQ y tocineta.",
        tags=["perro", "hot dog"],
        matched_by="exact",
    )
    scenario = AgentScenario(
        name="perro_caliente_denver_returns_only_denver",
        user_message="un perro caliente denver",
        initial_order_context={"state": "GREETING"},
        stub_search_products=lambda biz, q: [denver],
        stub_list_products_with_fallback=lambda biz, cat: [denver],
        # No reference_trajectory: the planner can validly classify this
        # as ADD_TO_CART, SEARCH_PRODUCTS, or GET_PRODUCT depending on
        # prompt tuning — all three are acceptable because the user is
        # naming a specific product. The important assertion is the
        # response-text layer: Denver shows up, bleed products don't.
        must_not_contain=[
            r"\bNIJABOU\b",
            r"\bNAIROBI\b",
            r"\bhervidos\b",
            r"\bMora y Maracuy[aá]\b",
        ],
        must_contain_any=[
            r"Perro Caliente Denver",
            r"\bDenver\b",
        ],
    )
    run = run_scenario(scenario)
    assert_scenario(scenario, run)


def test_algo_con_picante_returns_arrabbiata_preservation():
    """
    Preservation case: "algo con picante" must still surface ARRABBIATA.
    ARRABBIATA has 'picante' in its tags AND its description (both
    lexical signals), so the Phase 2 pure-embedding filter should NOT
    drop it.

    We stub all retrieval paths because the planner occasionally routes
    attribute queries to LIST_PRODUCTS or GET_MENU_CATEGORIES instead of
    SEARCH_PRODUCTS. That routing variability is tracked as a capability
    xfail, not a regression — this test only pins the preservation:
    whichever path the planner picks, ARRABBIATA must appear in the reply.
    """
    def _arrabbiata_matches(*_args, **_kwargs):
        return [
            product(
                "ARRABBIATA", 27000,
                description="Pan, carne, mozzarella, salsa arrabbiata picante, rúgula, papas.",
                tags=["hamburguesa", "burger", "picante"],
                matched_by="lexical",
            )
        ]
    scenario = AgentScenario(
        name="algo_con_picante_returns_arrabbiata_preservation",
        user_message="tienen algo con picante?",
        initial_order_context={"state": "GREETING"},
        stub_search_products=_arrabbiata_matches,
        stub_list_products_with_fallback=_arrabbiata_matches,
        stub_list_products=_arrabbiata_matches,
        stub_list_categories=lambda biz: ["BURGERS", "HOT DOGS", "BEBIDAS"],
        must_contain_any=[r"ARRABBIATA", r"arrabbiata"],
    )
    run = run_scenario(scenario)
    assert_scenario(scenario, run)


# ---------------------------------------------------------------------------
# CONFIRM intent regression (fix: commit ae05da7)
# ---------------------------------------------------------------------------

def test_procedemos_in_ready_to_place_places_order():
    """
    The Biela abandonment bug: user says "Procedemos" with cart + full
    delivery info, state=READY_TO_PLACE.

    Deterministic layer: the planner MUST emit CONFIRM (new intent),
    not PROCEED_TO_CHECKOUT or PLACE_ORDER. The executor is what
    translates CONFIRM → PLACE_ORDER based on state — that translation
    is exercised in unit tests. This trajectory check is the canary
    for the PLANNER prompt: if someone weakens the confirmation-verb
    rule in PLANNER_SYSTEM_TEMPLATE, this test fails first.

    Prose layer: the final response must confirm the order and must
    not contain the old rejection-recovery phrasing.
    """
    scenario = AgentScenario(
        name="procedemos_in_ready_to_place_places_order",
        user_message="Procedemos",
        initial_order_context={
            "state": "READY_TO_PLACE",
            "items": [
                {"product_id": "prod-honey", "name": "HONEY BURGER", "quantity": 1, "price": 28000},
                {"product_id": "prod-lim", "name": "Limonada de cereza", "quantity": 1, "price": 12000},
            ],
            "total": 40000,
            "delivery_info": {
                "name": "Tatiana",
                "address": "Calle 30 #19-30",
                "phone": "+573151234567",
                "payment_method": "Efectivo",
            },
        },
        conversation_history=[
            {"role": "user", "content": "hola una honey burger y una limonada de cereza"},
            {"role": "assistant", "content": "Listo, 1x HONEY BURGER + 1x Limonada de cereza. Para continuar necesito tus datos."},
            {"role": "user", "content": "Tatiana. Calle 30 #19-30. +573151234567. Efectivo."},
            {"role": "assistant", "content": "Tengo dirección Calle 30 #19-30, teléfono +573151234567 y pago Efectivo. ¿Procedemos?"},
        ],
        stub_place_order_tool_result="✅ Pedido confirmado #ABC1234",
        reference_trajectory=expected_planner_call(
            user_message="Procedemos",
            intent="CONFIRM",
            params={},
        ),
        must_not_contain=[
            r"no podemos proceder",
            r"no se puede hacer",
            r"esa acci[oó]n",
            r"en este momento no",
        ],
        must_contain_any=[
            r"pedido confirmado",
            r"\bconfirmad[oa]\b",
            r"\blisto\b",
            r"\bgracias\b.*pedido",
            r"pedido.*\bgracias\b",
            r"#ABC1234",
        ],
    )
    run = run_scenario(scenario)
    assert_scenario(scenario, run)


# ---------------------------------------------------------------------------
# LLM-as-judge scenario — prose quality check
# ---------------------------------------------------------------------------

def test_hay_pizza_llm_judge_full_semantic_check():
    """
    Same 'pizza' case, but graded by the tuned TRAJECTORY_ACCURACY_PROMPT
    (no-reference variant) plus our additional rubric. Catches wording
    that technically avoids forbidden phrases but still implies we have
    pizza (e.g. "for pizza we have: PICADA..."). Slower than regex —
    reserved for scenarios where prose nuance matters.

    Deliberately does NOT set a reference_trajectory: the planner routes
    "tenes pizza?" (informal "do you have X") to SEARCH_PRODUCTS whereas
    "hay pizza?" routes to LIST_PRODUCTS. Both classifications are
    reasonable given the phrasing, and this scenario is about response
    quality, not routing. Routing is pinned by the deterministic
    `test_hay_pizza_at_biela_says_no_pizza` above.
    """
    scenario = AgentScenario(
        name="hay_pizza_llm_judge_full_semantic_check",
        user_message="tenes pizza?",
        initial_order_context={"state": "GREETING"},
        stub_search_products=lambda biz, q: [],
        stub_list_products_with_fallback=lambda biz, cat: [],
        stub_list_categories=lambda biz: ["BURGERS", "HOT DOGS", "BEBIDAS"],
        llm_judge_rubric=(
            "The restaurant does NOT sell pizza. The reply PASSES if it "
            "clearly tells the customer that pizza is not available, without "
            "suggesting or implying that any listed product IS a pizza or a "
            "replacement for pizza. The reply may invite the customer to "
            "explore other categories (burgers, drinks). "
            "The reply FAILS if it: (a) lists any product as if it were a pizza, "
            "(b) says 'our pizzas are...' or similar, (c) pretends pizza exists, "
            "(d) is apologetic with 'lo siento' (against tone rules), or "
            "(e) is completely off-topic."
        ),
    )
    run = run_scenario(scenario)
    assert_scenario(scenario, run)
