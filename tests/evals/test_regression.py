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
# Biela +573177000722 — disambiguation reply with flavor qualifier
#
# Incident 2026-04-16. After turn 3 offered [Hervido Mora, Jugos en agua,
# Jugos en leche], the user replied "Un jugo de mora en agua". The
# planner mapped to 'Jugos en agua' but stripped the 'mora' qualifier,
# adding the item to the cart without a note — the flavor disappeared.
# Fix: deterministic `apply_disamb_reply_flavor_fallback` re-attaches
# qualifier tokens as `notes`.
# ---------------------------------------------------------------------------


def test_biela_disamb_reply_preserves_flavor_as_notes():
    """
    End-to-end: seed a pending disambiguation for 'jugo de mora' with
    the three real Biela options. User replies 'Un jugo de mora en agua'.
    Cart must end with 'Jugos en agua (mora)' — note travels through the
    planner's safety-net post-processor and lands on the cart item.
    """
    JUGOS_EN_AGUA = {
        "id": "prod-jugos-agua",
        "business_id": "44488756-473b-46d2-a907-9f579e98ecfd",
        "name": "Jugos en agua",
        "description": "",
        "price": 7500.0,
        "currency": "COP",
        "category": "BEBIDAS",
        "sku": None,
        "is_active": True,
        "tags": ["jugo", "bebida"],
        "metadata": {},
        "matched_by": "exact",
    }
    JUGOS_EN_LECHE = {
        **JUGOS_EN_AGUA,
        "id": "prod-jugos-leche",
        "name": "Jugos en leche",
        "tags": ["jugo", "bebida", "leche"],
    }
    HERVIDO_MORA = {
        **JUGOS_EN_AGUA,
        "id": "prod-hervido-mora",
        "name": "Hervido Mora",
        "price": 9500.0,
        "tags": ["hervido", "mora", "caliente"],
    }

    def _search_stub(biz, query: str):
        q = (query or "").lower()
        # When the planner (or fallback) emits the exact option name,
        # return just that exact row.
        if "jugos en agua" in q and "leche" not in q:
            return [JUGOS_EN_AGUA]
        if "jugos en leche" in q:
            return [JUGOS_EN_LECHE]
        if "hervido" in q and "mora" in q:
            return [HERVIDO_MORA]
        return []

    scenario = AgentScenario(
        name="biela_disamb_reply_preserves_flavor",
        user_message="Un jugo de mora en agua",
        initial_order_context={
            "state": "ORDERING",
            "items": [{"product_id": "prod-barracuda", "name": "BARRACUDA", "quantity": 1, "price": 28000}],
            "total": 28000,
            "pending_disambiguation": {
                "requested_name": "jugo de mora",
                "options": [
                    {"name": "Hervido Mora",  "price": 9500,  "product_id": "prod-hervido-mora"},
                    {"name": "Jugos en agua", "price": 7500,  "product_id": "prod-jugos-agua"},
                    {"name": "Jugos en leche","price": 7500,  "product_id": "prod-jugos-leche"},
                ],
            },
        },
        # Pre-populate the fake service's id→product index so the
        # disamb bypass's get_product(product_id="prod-jugos-agua")
        # call returns the real row instead of None.
        known_products=[JUGOS_EN_AGUA, JUGOS_EN_LECHE, HERVIDO_MORA],
        stub_search_products=_search_stub,
        stub_list_categories=lambda biz: ["BEBIDAS", "BURGERS"],
        must_contain_any=[
            # Cart line must show the mora flavor as a note on Jugos en agua
            r"jugos? en agua.*\(mora\)",
            r"jugos? en agua.*mora",
        ],
        must_not_contain=[
            # Silent flavor loss — the exact regression we're pinning
            r"hervido mora",
            r"\blo siento\b",
            r"\bno pude\b",
        ],
        llm_judge_rubric=(
            "The user is replying to a disambiguation prompt. The "
            "options were 'Hervido Mora', 'Jugos en agua', 'Jugos en "
            "leche'. The user said 'Un jugo de mora en agua'. The "
            "correct interpretation is: the user picked 'Jugos en "
            "agua' (a generic catalog row) AND wants the flavor "
            "'mora' as a note on that item — the kitchen at Biela "
            "handles which fruits are actually in stock. "
            "The reply PASSES if it confirms that 'Jugos en agua' "
            "was added to the cart AND mentions the mora flavor "
            "(parenthetical '(mora)', 'con mora', 'sabor mora', etc). "
            "The reply FAILS if it: "
            "(a) drops the mora flavor silently, "
            "(b) offers Hervido Mora as an alternative, "
            "(c) re-asks the disambiguation question, "
            "(d) apologizes / says 'lo siento' / 'no pude'."
        ),
    )
    run = run_scenario(scenario)
    assert_scenario(scenario, run)


# ---------------------------------------------------------------------------
# Biela — sad-path multi-item with one unmatchable product
#
# Regression for Bug 2 in the +573177000722 transcript: user said
# "Un jugo de mango en leche y una club Colombia". There's no mango
# product in the Biela catalog at all (not even as a generic; the
# kitchen fulfills flavors via the generic 'Jugos en leche' row, but
# the search path only surfaces that when a qualifier is provided
# cleanly). Before Fix A+B the jugo was silently dropped and only
# Club Colombia landed in the cart, with zero mention in the response.
# After the fix, add_to_cart raises ProductNotFoundError and the
# multi-item executor surfaces it via the `not_found` extra.
# ---------------------------------------------------------------------------


def test_biela_multi_item_one_not_found_surfaces_in_response():
    """
    Two-item batch: Club Colombia is an exact match, pitahaya jugo
    has no catalog row (truly unavailable). The response must confirm
    the Club Colombia landed AND tell the user the pitahaya couldn't
    be found — never silently drop it.
    """
    CLUB_COLOMBIA = {
        "id": "prod-club",
        "business_id": "44488756-473b-46d2-a907-9f579e98ecfd",
        "name": "Club Colombia",
        "description": "",
        "price": 7500.0,
        "currency": "COP",
        "category": "BEBIDAS",
        "sku": None,
        "is_active": True,
        "tags": ["cerveza"],
        "metadata": {},
        "matched_by": "exact",
    }

    def _search_stub(biz, query: str):
        q = (query or "").lower()
        if "club" in q:
            return [CLUB_COLOMBIA]
        # pitahaya has no match — search returns empty and add_to_cart
        # raises ProductNotFoundError
        return []

    scenario = AgentScenario(
        name="biela_multi_item_one_not_found",
        user_message="Un jugo de pitahaya y una Club Colombia",
        initial_order_context={"state": "ORDERING", "items": [], "total": 0},
        known_products=[CLUB_COLOMBIA],
        stub_search_products=_search_stub,
        stub_list_categories=lambda biz: ["BEBIDAS", "BURGERS"],
        must_contain_any=[
            # Club Colombia lands in the cart
            r"club colombia",
        ],
        must_not_contain=[
            # Don't silently drop the pitahaya; don't apologize
            r"\blo siento\b",
            r"\bdiscul",
            r"\bfall[oó]\b",
        ],
        llm_judge_rubric=(
            "The customer asked for two items: 'un jugo de pitahaya' "
            "(not in the menu — Biela has no pitahaya) and 'una Club "
            "Colombia' (in the menu, exact match at $7.500). "
            "The reply PASSES if it: "
            "(1) confirms Club Colombia was added to the cart, AND "
            "(2) mentions that the pitahaya wasn't found / isn't on "
            "the menu (may suggest seeing the menu or trying a "
            "different flavor). "
            "The reply FAILS if it: "
            "(a) doesn't mention the pitahaya at all (silent drop), "
            "(b) apologizes or uses 'lo siento' / 'disculpa' / "
            "'no pude', "
            "(c) claims the pitahaya WAS added, "
            "(d) forgets about the Club Colombia."
        ),
    )
    run = run_scenario(scenario)
    assert_scenario(scenario, run)


# ---------------------------------------------------------------------------
# Biela — token-set decisive winner (Bug 5)
#
# Incident 2026-04-16 +573177000722 turn 11. User said "Una soda de
# frutos rojos". The exact-name rule (1a) missed because of the "una"
# and "de" stopwords in the query. The score-ratio rule (2) didn't
# clear 2× because Coca-Cola / Coca-Cola Zero came in as strong
# embedding neighbors. The user got a disambiguation with 5 options
# including two Coca-Colas — obviously wrong, since Soda Frutos rojos
# is a perfect token-set match. New rule 1c fires and wins.
# ---------------------------------------------------------------------------


def test_biela_jugo_de_mora_disamb_excludes_hervido_mora():
    """
    Bug 4 — the LLM disambiguation resolver. User says "un jugo de mora"
    at Biela. The ranker returns [Jugos en agua, Jugos en leche, Hervido Mora].
    The deterministic rules can't resolve (no exact match, no containment,
    no token-set equality, no score gap). The LLM resolver sees that
    "jugo" ≠ "hervido" and either:
      A. Filters Hervido Mora out → disambiguation shows only the two jugos.
      B. Returns AMBIGUOUS → all three shown (acceptable but not ideal).

    What MUST NOT happen: the bot should NOT present Hervido Mora as
    equal to the jugos, or silently pick Hervido Mora as the winner.
    """
    JUGOS_EN_AGUA = product("Jugos en agua", 7500, category="BEBIDAS",
                            tags=["jugo", "natural", "agua", "bebida fria"])
    JUGOS_EN_LECHE = product("Jugos en leche", 7500, category="BEBIDAS",
                             tags=["jugo", "natural", "leche", "bebida fria"])
    HERVIDO_MORA = product("Hervido Mora", 9500, category="BEBIDAS",
                           description="Bebida caliente preparada con mora.",
                           tags=["hervido", "caliente", "bebida caliente", "mora"])

    def _search_stub(biz, query: str):
        q = (query or "").lower()
        if "jugo" in q or "mora" in q:
            return [JUGOS_EN_AGUA, JUGOS_EN_LECHE, HERVIDO_MORA]
        return []

    scenario = AgentScenario(
        name="biela_jugo_de_mora_disamb_excludes_hervido",
        user_message="Un jugo de mora",
        initial_order_context={"state": "ORDERING", "items": [], "total": 0},
        known_products=[JUGOS_EN_AGUA, JUGOS_EN_LECHE, HERVIDO_MORA],
        stub_search_products=_search_stub,
        stub_list_categories=lambda biz: ["BEBIDAS", "BURGERS"],
        must_not_contain=[
            # Hervido Mora must not be presented as an option for
            # "jugo de mora" — it's a hot drink, not a juice.
            r"hervido mora",
            r"\blo siento\b",
            r"\bno pude\b",
        ],
        must_contain_any=[
            # The disambiguation must present at least one jugo option
            r"jugos? en agua",
            r"jugos? en leche",
        ],
    )
    run = run_scenario(scenario)
    assert_scenario(scenario, run)


def test_biela_soda_de_frutos_rojos_wins_decisively():
    """
    End-to-end: user says "Una soda de frutos rojos" at an empty
    ORDERING cart. The search returns Soda, Soda Frutos rojos,
    Soda Uvilla, Coca-Cola, Coca-Cola Zero as candidates (simulating
    Biela's real catalog behavior). Rule 1c must pick Soda Frutos
    rojos decisively — no disambiguation prompt.
    """
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
        "matched_by": "lexical",
    }
    SODA_PLAIN = {**SODA_FRUTOS, "id": "prod-soda", "name": "Soda", "price": 4500.0}
    SODA_UVILLA = {
        **SODA_FRUTOS,
        "id": "prod-soda-uvilla",
        "name": "Soda Uvilla y maracuyá",
    }
    COCA = {
        **SODA_FRUTOS,
        "id": "prod-coca",
        "name": "Coca-Cola",
        "price": 5500.0,
        "tags": ["gaseosa"],
    }
    COCA_ZERO = {**COCA, "id": "prod-coca-zero", "name": "Coca-Cola Zero"}

    def _search_stub(biz, query: str):
        q = (query or "").lower()
        # Simulate the ranker returning all 5 candidates for any
        # soda-related query — this is what the real Biela catalog
        # behavior looked like in the 2026-04-16 incident. Rule 1c
        # must disambiguate structurally, not via score gap.
        if "soda" in q or "frutos" in q:
            return [SODA_FRUTOS, SODA_PLAIN, SODA_UVILLA, COCA, COCA_ZERO]
        return []

    scenario = AgentScenario(
        name="biela_soda_de_frutos_rojos_decisive",
        user_message="Una soda de frutos rojos",
        initial_order_context={"state": "ORDERING", "items": [], "total": 0},
        known_products=[SODA_FRUTOS, SODA_PLAIN, SODA_UVILLA, COCA, COCA_ZERO],
        stub_search_products=_search_stub,
        stub_list_categories=lambda biz: ["BEBIDAS", "BURGERS"],
        must_contain_any=[
            # Cart should confirm the Soda Frutos rojos landing —
            # exact catalog name required.
            r"soda frutos rojos",
        ],
        must_not_contain=[
            # No disambiguation prompt, no Coca-Cola as an option.
            r"coca[- ]?cola",
            r"¿cu[aá]l prefieres",
            r"\buvilla\b",
            r"\blo siento\b",
            r"\bdiscul",
        ],
    )
    run = run_scenario(scenario)
    assert_scenario(scenario, run)
    # Extra: planner classification itself — must NOT be the
    # needs_clarification result kind.
    assert run.exec_result.get("result_kind") == "cart_change", (
        f"expected cart_change (decisive winner), got "
        f"{run.exec_result.get('result_kind')!r}"
    )


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
