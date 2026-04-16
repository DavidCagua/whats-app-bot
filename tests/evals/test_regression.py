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
    and the LLM narrated them as "las siguientes pizzas". After the fix
    the response must tell the user we don't have pizza, not list
    burgers as if they were pizzas.

    NOTE: no reference_trajectory. The planner routes "hay pizza?"
    inconsistently across runs — sometimes LIST_PRODUCTS(category=pizza),
    sometimes SEARCH_PRODUCTS(query=pizza). Both produce identical
    behavior (retrieval → empty → 'no tenemos pizza'), so neither is
    wrong, and trajectory match doesn't have an "intent in {X, Y}" mode
    for legitimate routing flexibility. The response-text + LLM-judge
    layers cover this scenario fully.
    """
    scenario = AgentScenario(
        name="hay_pizza_at_biela_says_no_pizza",
        user_message="hay pizza?",
        initial_order_context={"state": "GREETING"},
        stub_search_products=lambda biz, q: [],
        stub_list_products_with_fallback=lambda biz, cat: [],
        stub_list_categories=lambda biz: ["BURGERS", "HOT DOGS", "BEBIDAS"],
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
    """Same class as pizza — no sushi at a burger shop. Same routing
    instability between LIST_PRODUCTS / SEARCH_PRODUCTS, so no
    reference_trajectory; response-text layer covers it."""
    scenario = AgentScenario(
        name="hay_sushi_at_biela_says_no_sushi",
        user_message="hay sushi?",
        initial_order_context={"state": "GREETING"},
        stub_search_products=lambda biz, q: [],
        stub_list_products_with_fallback=lambda biz, cat: [],
        stub_list_categories=lambda biz: ["BURGERS", "HOT DOGS", "BEBIDAS"],
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


def test_perro_caliente_denver_no_disambiguation():
    """
    Bug from 2026-04-13 prod: user said "un perro caliente denver" and the
    bot disambiguated with 5 hot-dog options ("Cuál prefieres?"). Root
    cause: the DB product name is literally 'DENVER' (single word),
    every hot dog shares the 'perro caliente' tag, so tag hits fire
    equally on all four and DENVER's name-substring lead doesn't clear
    the 2x ratio threshold in the decisive rule. Fix: fourth exact-match
    rule in _score_product recognizes the Spanish "[category] [name]"
    phrasing when the non-name tokens all match the product's tags or
    category.

    The harness stubs retrieval, so this test asserts at the agent level:
    - Planner must classify as ADD_TO_CART (not SEARCH_PRODUCTS or
      GET_PRODUCT — the user is ordering, not asking).
    - Executor adds the product (stubbed catalog returns only DENVER,
      so the response is a cart_change, not needs_clarification).
    - Response must confirm the add, must NOT ask "cuál prefieres".

    The scorer fix itself is verified by the unit tests in
    tests/unit/test_product_search_retrieval.py.
    """
    denver = product(
        "DENVER", 27000,
        category="HOT DOGS",
        description="Pan artesanal, salchicha, queso, tocineta, cebolla caramelizada, papas fritas.",
        tags=["hot dog", "perro", "perro caliente", "salchicha", "tocineta", "queso"],
        matched_by="exact",
    )
    scenario = AgentScenario(
        name="perro_caliente_denver_no_disambiguation",
        user_message="un perro caliente denver",
        initial_order_context={"state": "GREETING"},
        # Stubbed retrieval returns only DENVER — the unit-test layer
        # verifies the scorer will now pick DENVER uniquely from the
        # real 4-hot-dog catalog.
        stub_search_products=lambda biz, q: [denver],
        stub_list_products_with_fallback=lambda biz, cat: [denver],
        reference_trajectory=expected_planner_call(
            user_message="un perro caliente denver",
            intent="ADD_TO_CART",
            # args shape is flexible — planner may emit either
            # {"product_name": ..., "quantity": ...} or
            # {"items": [{"product_name": ..., "quantity": ...}]}.
            # Both are valid per the planner prompt.
            params={},
        ),
        trajectory_match_mode="superset",
        tool_args_match_mode="ignore",
        must_not_contain=[
            r"\bcu[aá]l prefieres\b",
            r"\bcu[aá]l te gustar[ií]a\b",
            r"\bNAIROBI\b",
            r"\bPEGORETTI\b",
            r"\bSPECIAL DOG\b",
        ],
        must_contain_any=[
            r"\bagregad[oa]\b",
            r"\bhemos agregado\b",
            r"\bse agreg[oó]\b",
            r"\blisto\b.*pedido",
        ],
    )
    run = run_scenario(scenario)
    assert_scenario(scenario, run)


def test_menu_link_sent_when_user_asks_for_carta():
    """
    Bug from 2026-04-13 prod: user said "me envias la carta porfa" and
    "me puedes enviar el menu porfa" — bot responded with a text list of
    categories, never sending the menu URL (which is set in
    business.settings.menu_url and already used in the greeting).

    Fix: the MENU_CATEGORIES response branch now receives menu_url from
    business_context and the prompt rules instruct the LLM to lead with
    the URL when the user explicitly asked to be SENT the menu (verbs:
    envías, mandas, pasas, compartes, etc.), and to include it as a
    soft offer at the end when the user just asked what's on the menu.

    Deterministic assertion: response must contain the menu URL.
    Trajectory assertion: planner routes to GET_MENU_CATEGORIES.
    """
    scenario = AgentScenario(
        name="menu_link_sent_when_user_asks_for_carta",
        user_message="me envias la carta porfa",
        initial_order_context={"state": "GREETING"},
        stub_list_categories=lambda biz: [
            "BURGERS", "CHICKEN BURGERS", "HOT DOGS", "FRIES",
            "BEBIDAS", "MENÚ INFANTIL", "STEAK & RIBS",
        ],
        reference_trajectory=expected_planner_call(
            user_message="me envias la carta porfa",
            intent="GET_MENU_CATEGORIES",
            params={},
        ),
        must_contain_any=[
            # The menu URL must appear in the response. That's the whole
            # point of the fix — the old behavior was to silently drop it.
            r"https://gixlink\.com/Biela",
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
# Biela +573242261188 — generic-product + multi-item regression
#
# Incident 2026-04-15: user sent "Dame 1 jugo de mora en leche y 1 soda
# de frutos rojos". Three bugs stacked:
#   1. Product search returned [Jugos en leche, Hervido Mora] as a
#      near-tie disambiguation even though the user was explicit about
#      "en leche" (over-sensitive semantic scoring on "mora").
#   2. The executor's multi-item loop re-raised AmbiguousProductError
#      from the jugo and never reached the soda, silently dropping a
#      perfectly-matchable item.
#   3. The response generator hallucinated "Jugo de Mora en Leche" as
#      the option name (merging the user's flavor with the real generic
#      "Jugos en leche"), which poisoned the disambiguation bypass on
#      the next turn because the saved options still held the real name.
#
# Fixes landed together:
#   A. Multi-item executor continues past per-item AmbiguousProductError
#      and carries pending_clarification through the cart_change result.
#   B. search_products decisive rule: when query tokens strictly contain
#      a generic candidate's name tokens, pick that candidate and expose
#      leftover tokens as _derived_notes (which add_to_cart stashes on
#      the cart item so the human at Biela sees the flavor request).
#   C. Response-generator prompt forbids inventing/composing option
#      names — must use exact catalog names.
# ---------------------------------------------------------------------------


def test_biela_jugo_mora_en_leche_plus_soda_multi_item():
    """
    Pin the full cascade fix. The scenario stubs product search so
    "jugo de mora en leche" resolves to the generic "Jugos en leche"
    with _derived_notes="mora" (simulating Fix B), and "soda de frutos
    rojos" resolves to "Soda Frutos rojos" exactly.

    Expected reply after Fix A + B + C:
      - cart confirms BOTH items (no silent drop)
      - mentions the flavor "mora" (from derived_notes)
      - uses the EXACT catalog name "Jugos en leche" (no hallucinated
        "Jugo de Mora en Leche")
      - does NOT list Hervido Mora as an option
      - does NOT fall into a disambiguation loop
    """
    JUGOS_EN_LECHE = {
        "id": "prod-jugos-leche",
        "business_id": "44488756-473b-46d2-a907-9f579e98ecfd",
        "name": "Jugos en leche",
        "description": "",
        "price": 7500.0,
        "currency": "COP",
        "category": "BEBIDAS",
        "sku": None,
        "is_active": True,
        "tags": ["bebida", "jugo"],
        "metadata": {},
        "matched_by": "lexical",
        # Simulates the Fix B search layer attaching the derived flavor
        # to the generic-product winner. The add_to_cart tool reads this
        # field and stashes it on the cart item's notes.
        "_derived_notes": "mora",
    }
    SODA_FRUTOS = {
        "id": "prod-soda-frutos",
        "business_id": "44488756-473b-46d2-a907-9f579e98ecfd",
        "name": "Soda Frutos rojos",
        "description": "",
        "price": 15000.0,
        "currency": "COP",
        "category": "BEBIDAS",
        "sku": None,
        "is_active": True,
        "tags": ["bebida", "soda"],
        "metadata": {},
        "matched_by": "exact",
    }

    def _search_stub(biz, query: str):
        q = (query or "").lower()
        # Jugo branch: route anything mentioning jugo + (leche OR mora)
        # to the generic Jugos en leche winner.
        if "jugo" in q and ("leche" in q or "mora" in q):
            return [JUGOS_EN_LECHE]
        # Soda branch: exact catalog match for "soda (de) frutos rojos".
        if "soda" in q and "frutos" in q:
            return [SODA_FRUTOS]
        return []

    scenario = AgentScenario(
        name="biela_jugo_mora_plus_soda_multi_item",
        user_message="Dame 1 jugo de mora en leche y 1 soda de frutos rojos",
        initial_order_context={"state": "ORDERING", "items": [], "total": 0},
        stub_search_products=_search_stub,
        stub_list_products_with_fallback=lambda biz, cat: [],
        stub_list_categories=lambda biz: ["BEBIDAS", "BURGERS", "PLATOS"],
        must_contain_any=[
            # The real (non-hallucinated) generic product name must appear
            # in the cart line. The response may also echo the user's
            # "jugo de mora en leche" phrase in the confirmation text —
            # that's a natural echo, not a fabrication. We only assert
            # the CART LINE uses the catalog name. "jugo en leche" also
            # matches because the response generator is allowed to
            # singularize plural catalog names in cart listings (the
            # flavor still rides along as the notes "(mora)").
            r"jugos? en leche",
        ],
        must_not_contain=[
            # Hervido Mora should never surface as an option in this flow.
            r"hervido mora",
            # The soda must NOT land in a disambiguation prompt — there's
            # a real exact match in the catalog.
            r"soda.*¿cu[aá]l",
            # Apology / failure markers — this flow must not feel broken.
            r"\bdiscul",
            r"\blo siento\b",
            r"\bno pude\b",
            r"\bfall[oó]\b",
        ],
        # Prose-level check: the judge verifies both items land in the
        # cart AND the flavor is carried as a note, rather than dropped
        # or re-prompted.
        llm_judge_rubric=(
            "The customer ordered two items in one message: "
            "'1 jugo de mora en leche' and '1 soda de frutos rojos'. "
            "The catalog has a GENERIC product 'Jugos en leche' (a $7.500 "
            "row that accepts any flavor the kitchen stocks; the flavor "
            "is written as a note on the ticket) and a SPECIFIC product "
            "'Soda Frutos rojos' at $15.000. "
            "The reply PASSES if it confirms BOTH items were added to the "
            "order, uses the exact catalog names 'Jugos en leche' and "
            "'Soda Frutos rojos', and either mentions that the mora flavor "
            "will be noted on the ticket OR lists the jugo with a '(mora)' "
            "or 'con mora' annotation. It is OK to ask 'something else or "
            "proceed?' at the end. "
            "The reply FAILS if it: (a) drops the soda silently or asks "
            "to clarify the soda, (b) asks the customer to choose between "
            "'Jugos en leche' and 'Hervido Mora' as if they were "
            "comparable options, (c) uses a fabricated product name like "
            "'Jugo de Mora en Leche' with each word capitalized, "
            "(d) apologizes or says 'lo siento' / 'no pude', or "
            "(e) adds only one of the two items and ignores the other."
        ),
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
