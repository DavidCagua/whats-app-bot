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

# Variant for the v2 (context-first) prompt — same business config,
# adds the per-business flag the router reads. Each eval test that
# wants to measure the v2 prompt uses this variant.
BIELA_CONTEXT_V2 = {
    **BIELA_CONTEXT,
    "business": {
        **BIELA_CONTEXT["business"],
        "settings": {
            **BIELA_CONTEXT["business"]["settings"],
            "router_prompt_mode": "context_first",
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


# ───────────────────────────────────────────────────────────────────────
# Compound-greeting cases.
#
# Pure single-token greetings ("hola") hit the regex fast-path before
# the LLM. Compound greetings ("hola buenas noches", "buenas qué más")
# miss the regex on purpose — the LLM router classifies them as the
# `greeting` domain and converts to `direct_reply`, matching the
# fast-path's output shape so conversation_manager dispatches the same
# welcome CTA. These cases assert that round-trip end-to-end.
# ───────────────────────────────────────────────────────────────────────


COMPOUND_GREETING_CASES = [
    ("hola buenas noches", "stacked greetings — common WhatsApp opener"),
    ("buenas qué más", "Colombian greeting + colloquialism"),
    ("hola qué tal", "greeting + small-talk filler"),
]


@pytest.mark.parametrize(
    "phrase, why",
    [pytest.param(p, w, id=p) for (p, w) in COMPOUND_GREETING_CASES],
)
def test_router_compound_greeting_returns_direct_reply(phrase, why):
    # Sanity: these MUST miss the regex so we're actually exercising
    # the LLM path. If a phrase here starts hitting the fast-path,
    # this assertion catches the regression and you can promote the
    # case to the regex test instead.
    from app.services import business_greeting
    assert not business_greeting.is_pure_greeting(phrase), (
        f"{phrase!r} now hits the regex fast-path — move this case to the "
        f"unit-level greeting tests"
    )

    result = router.route(
        message_body=phrase,
        business_context=BIELA_CONTEXT,
        customer_name="David",
    )
    assert result.direct_reply is not None, (
        f"phrase={phrase!r} ({why}) — expected LLM-classified greeting → "
        f"direct_reply, got segments={result.segments}"
    )
    assert "Biela" in result.direct_reply


# ───────────────────────────────────────────────────────────────────────
# Contextual routing cases.
#
# Same surface message routes to different domains depending on the
# turn context (last_assistant_message, order_state, has_active_cart,
# latest_order_status). These are the cases the current keyword-driven
# rules try to capture with explicit pattern lists like "DESPEDIDA
# POST-PEDIDO" / "CONTINUACIÓN DEL FLUJO DE PEDIDO" — but those lists
# are necessarily incomplete and miss novel phrasings.
#
# This eval is the regression gate for a "context-first" router
# redesign: the prompt should reason about the conversation flow
# (last bot reply + current message) rather than match keywords.
# ───────────────────────────────────────────────────────────────────────


from app.orchestration.turn_context import TurnContext


CONTEXTUAL_ROUTING_CASES = [
    # ── CS follow-up stickiness ─────────────────────────────────────
    # Bot just answered a CS question; user's reply is a follow-up
    # that doesn't introduce a new product / order verb. Should stay
    # in CS even when the user phrases it loosely.
    {
        "id": "cs_followup_payment_timing",
        "message": "Si puedo hacerlo de una vez mejor",
        "last_bot": "El pago se realiza al momento de recibir el domicilio. Si necesitas más información sobre medios de pago, házmelo saber.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": "pending",
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "follow-up to CS payment-timing answer (anaphoric 'hacerlo' = 'el pago')",
    },
    {
        "id": "cs_followup_short_thanks",
        "message": "ah ok gracias",
        "last_bot": "Abrimos de lunes a sábado de 12pm a 9pm.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "polite close to CS answer when no order context",
    },
    {
        "id": "cs_followup_clarifying_question",
        "message": "y a qué hora cierran los domingos?",
        "last_bot": "Abrimos de lunes a sábado de 12pm a 9pm.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "follow-up CS question after CS answer about hours",
    },

    # ── Despedida post-pedido (cart empty, just placed) ─────────────
    # After place_order clears the cart, polite-close messages should
    # hit the order agent's "thanks, enjoy your meal" path — not be
    # mistaken for a new order or a CS question.
    {
        "id": "despedida_gracias_post_order",
        "message": "gracias",
        "last_bot": "✅ ¡Pedido confirmado! #ABCD1234. Tiempo estimado de entrega: 40 a 50 minutos.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": "pending",
        "expected_domain": DOMAIN_ORDER,
        "why": "polite close right after place_order — order agent owns farewell",
    },
    {
        "id": "despedida_perfecto_post_order",
        "message": "perfecto muchas gracias",
        "last_bot": "✅ ¡Pedido confirmado! #ABCD1234. Tiempo estimado de entrega: 40 a 50 minutos.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": "pending",
        "expected_domain": DOMAIN_ORDER,
        "why": "longer despedida variant after placed order",
    },

    # ── Mid-checkout continuation (cart not empty) ──────────────────
    # Bot asked "¿algo más o procedemos?" — affirmations and short
    # answers are continuations of the order flow, not standalone
    # questions or chat.
    {
        "id": "continuation_dale_after_procedemos",
        "message": "dale",
        "last_bot": "Tu pedido: 1x BARRACUDA - $28.000. Subtotal: $28.000. ¿Te gustaría añadir algo más o procedemos con el pedido?",
        "order_state": "ORDERING",
        "has_active_cart": True,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "affirmation while bot has open 'procedemos?' question",
    },
    {
        "id": "continuation_typo_porfa",
        "message": "porfsvor",
        "last_bot": "¿Te gustaría añadir algo más o procedemos?",
        "order_state": "ORDERING",
        "has_active_cart": True,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "typo'd 'por favor' as continuation",
    },
    {
        "id": "continuation_negative_close_cart",
        "message": "no más así",
        "last_bot": "¿Quieres agregar algo más?",
        "order_state": "ORDERING",
        "has_active_cart": True,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "negative confirm during ORDERING ≠ cancel; means 'close the cart'",
    },
    {
        "id": "continuation_eso_es_todo",
        "message": "eso es todo",
        "last_bot": "¿Algo más?",
        "order_state": "ORDERING",
        "has_active_cart": True,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "explicit 'done adding items' — order owns this transition",
    },

    # ── Cancel intent depends on cart vs placed-order state ─────────
    {
        "id": "cancel_with_active_cart",
        "message": "cancela",
        "last_bot": "Tu pedido: 1x BARRACUDA. Subtotal: $28.000.",
        "order_state": "ORDERING",
        "has_active_cart": True,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "cancel during cart-build = abandon (order agent's job)",
    },
    {
        "id": "cancel_post_placed_order",
        "message": "cancela mi pedido",
        "last_bot": "✅ ¡Pedido confirmado! #ABCD1234.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": "pending",
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "cancel after placed order = post-sale cancellation (CS)",
    },

    # ── Topic shifts must NOT stick to prior domain ─────────────────
    # The redesign can't over-correct: a clear new product mention
    # must escape CS stickiness even if last bot reply was CS.
    {
        "id": "topic_shift_cs_to_order_named_product",
        "message": "dame una barracuda",
        "last_bot": "Abrimos de lunes a sábado de 12pm a 9pm.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "explicit cart-add overrides CS context",
    },
    {
        "id": "topic_shift_order_to_cs_address",
        "message": "cuál es la dirección del local?",
        "last_bot": "Tu pedido: 1x BARRACUDA. ¿Algo más?",
        "order_state": "ORDERING",
        "has_active_cart": True,
        "latest_order_status": None,
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "address question mid-cart = CS, not order",
    },

    # ── Product availability (browsing, not order verb) ─────────────
    {
        "id": "availability_named_product_cold_start",
        "message": "tienes la barracuda?",
        "last_bot": "Hola, ¿qué se te antoja hoy?",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "product-availability question → order (browsing)",
    },
    {
        "id": "availability_typo_product",
        "message": "tiene la vimota?",
        "last_bot": "Hola, ¿qué se te antoja?",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "typo of BIMOTA still routes to order; fuzzy match downstream",
    },
    {
        "id": "availability_descriptive_reference",
        "message": "tienen la del concurso?",
        "last_bot": "Hola.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "descriptive product reference → order",
    },

    # ── Service vs product 'tienen X?' boundary ─────────────────────
    {
        "id": "service_question_estacionamiento",
        "message": "tienen estacionamiento?",
        "last_bot": "Hola.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "service question (parking) ≠ product browsing",
    },
    {
        "id": "service_question_delivery_policy",
        "message": "tienen domicilio?",
        "last_bot": "Hola.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "do-you-offer-delivery is policy, not product",
    },

    # ── Promo discovery vs cart action ──────────────────────────────
    {
        "id": "promo_discovery_no_product",
        "message": "qué promos tienen hoy?",
        "last_bot": "Hola.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "asking what promos exist = info, not cart action",
    },
    {
        "id": "promo_cart_action_named",
        "message": "agrégame la promo del lunes",
        "last_bot": "Hola.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "named promo + cart verb = order",
    },

    # ── Affirmation interpretation depends on state + last bot ──────
    # Plain "si" with no order context could go either way; with cart
    # active and bot asking ¿procedemos? it's clearly order.
    {
        "id": "affirmation_after_cs_hours",
        "message": "ok perfecto",
        "last_bot": "Abrimos de lunes a sábado de 12pm a 9pm.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "ack to CS hours answer when no order context — stays in CS thread",
    },
    {
        "id": "affirmation_si_during_checkout",
        "message": "si",
        "last_bot": "¿Confirmamos el pedido?",
        "order_state": "READY_TO_PLACE",
        "has_active_cart": True,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "confirm during checkout — order agent owns this",
    },

    # ── Delivery info dump while cart is active ─────────────────────
    {
        "id": "delivery_info_dump_during_cart",
        "message": "Calle 18 #43 38 apto 208\n3104078032\nClaudia\nefectivo",
        "last_bot": "¿Me das tus datos de entrega?",
        "order_state": "ORDERING",
        "has_active_cart": True,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "multi-line delivery info during checkout = order agent",
    },

    # ── More CS follow-ups (the failure category) ───────────────────
    {
        "id": "cs_followup_pago_clarification",
        "message": "Ok el pago lo hago cuando llegue el domicilio o lo puedo hacer ya",
        "last_bot": "✅ ¡Pedido confirmado! #ABCD1234. Tiempo estimado de entrega: 40 a 50 minutos.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": "pending",
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "post-placement payment policy question (Biela / Diego, 2026-05-09)",
    },
    {
        "id": "cs_followup_uncertain_after_order",
        "message": "ya no estaba confirmado?",
        "last_bot": "Tengo estos datos para tu pedido: Nombre: ... ¿Confirmamos el pedido?",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": "pending",
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "user confused about order status after placement (Biela / 2026-05-09)",
    },
    {
        "id": "cs_followup_vale",
        "message": "vale",
        "last_bot": "El domicilio cuesta $5.000 dentro del barrio.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "ack to CS delivery-fee policy answer",
    },
    {
        "id": "cs_followup_address_request_after_hours_answer",
        "message": "y la dirección?",
        "last_bot": "Abrimos de lunes a sábado de 12pm a 9pm.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "second CS question piggybacking on the first",
    },
    {
        "id": "cs_followup_entiendo",
        "message": "uh entiendo",
        "last_bot": "Aceptamos efectivo, transferencia, Nequi y Llave BreB.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "verbal ack of CS payment-methods info",
    },

    # ── Order continuation: more affirmations during checkout ───────
    {
        "id": "continuation_listo_after_algo_mas",
        "message": "listo",
        "last_bot": "¿Algo más o procedemos?",
        "order_state": "ORDERING",
        "has_active_cart": True,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "common 'done adding' signal during cart-build",
    },
    {
        "id": "continuation_si_porfa_after_procedemos",
        "message": "si por favor",
        "last_bot": "¿Procedemos con el pedido?",
        "order_state": "READY_TO_PLACE",
        "has_active_cart": True,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "explicit affirmative confirm before place_order",
    },
    {
        "id": "continuation_solo_eso_after_algo_mas",
        "message": "solo eso por ahora",
        "last_bot": "¿Quieres añadir algo más?",
        "order_state": "ORDERING",
        "has_active_cart": True,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "novel phrasing, same 'close cart' intent",
    },

    # ── More despedida post-pedido variants ─────────────────────────
    {
        "id": "despedida_vale_gracias_post_order",
        "message": "vale gracias",
        "last_bot": "✅ ¡Pedido confirmado! #ABCD1234.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": "pending",
        "expected_domain": DOMAIN_ORDER,
        "why": "common Colombian 'vale gracias' close after placement",
    },
    {
        "id": "despedida_dale_post_order",
        "message": "dale, espero entonces",
        "last_bot": "✅ ¡Pedido confirmado! #ABCD1234. Tiempo estimado: 40 a 50 minutos.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": "pending",
        "expected_domain": DOMAIN_ORDER,
        "why": "longer despedida with implicit waiting acknowledgement",
    },

    # ── More cancel scenarios ───────────────────────────────────────
    {
        "id": "cancel_quita_todo_active_cart",
        "message": "quítalo todo, no quiero pedir nada",
        "last_bot": "Tu pedido: 1x BARRACUDA. ¿Algo más?",
        "order_state": "ORDERING",
        "has_active_cart": True,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "explicit cart-clear during build = order agent",
    },
    {
        "id": "cancel_anula_post_placed",
        "message": "anula mi pedido por favor",
        "last_bot": "✅ ¡Pedido confirmado! #ABCD1234.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": "pending",
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "post-placement cancel = customer_service",
    },
    {
        "id": "cancel_no_context",
        "message": "cancela",
        "last_bot": "Hola.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "cancel with nothing to cancel — CS handles 'no order found'",
    },

    # ── Topic shifts (both directions) ──────────────────────────────
    {
        "id": "topic_shift_order_to_cs_hours",
        "message": "a qué hora cierran?",
        "last_bot": "Tu pedido: 1x BARRACUDA. ¿Algo más o procedemos?",
        "order_state": "ORDERING",
        "has_active_cart": True,
        "latest_order_status": None,
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "hours question mid-cart shifts to CS",
    },
    {
        "id": "topic_shift_cs_to_order_browsing",
        "message": "qué hamburguesas tienen?",
        "last_bot": "Aceptamos efectivo, transferencia y Nequi.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "menu browsing after a CS payment answer = topic shift back to order",
    },

    # ── Price questions ─────────────────────────────────────────────
    {
        "id": "price_terse_named_product",
        "message": "cuánto la honey?",
        "last_bot": "Hola.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "terse price question for named product = menu navigation",
    },
    {
        "id": "price_delivery_policy_short",
        "message": "valor del domicilio?",
        "last_bot": "Hola.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "delivery-fee question = policy = CS",
    },
    {
        "id": "price_delivery_policy_paraphrase",
        "message": "cuánto sale el envío?",
        "last_bot": "Hola.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "paraphrased delivery-cost question = CS",
    },

    # ── Service vs product disambiguation ───────────────────────────
    {
        "id": "service_question_wifi",
        "message": "tienen wifi?",
        "last_bot": "Hola.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "wifi is a service, not a product",
    },
    {
        "id": "product_unknown_in_catalog",
        "message": "tienen veggie burger?",
        "last_bot": "Hola.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "product-shaped question even when not in catalog → order (downstream resolves)",
    },

    # ── Browsing edge phrasings ─────────────────────────────────────
    {
        "id": "browsing_drinks_loose",
        "message": "qué hay para tomar?",
        "last_bot": "Hola.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "drinks browsing in colloquial phrasing",
    },
    {
        "id": "browsing_starters",
        "message": "qué entradas tienen?",
        "last_bot": "Hola.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "category browsing for entradas",
    },

    # ── Production-derived: 'la del X' descriptive references ───────
    {
        "id": "availability_descriptive_burger_master",
        "message": "Está la del burger Master?",
        "last_bot": "Hola Yisela 👋 Bienvenido a Biela.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "'la del X' = product availability via descriptor (Biela / 2026-05-08)",
    },

    # ── Greeting + content combos ───────────────────────────────────
    {
        "id": "greeting_plus_intent_order",
        "message": "Hola buenos días, me regalan una hamburguesa",
        "last_bot": "",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "greeting prefix absorbed; intent is order",
    },
    {
        "id": "greeting_plus_intent_cs",
        "message": "Buenas, a qué hora abren los domingos?",
        "last_bot": "",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_CUSTOMER_SERVICE,
        "why": "greeting prefix absorbed; intent is CS hours question",
    },

    # ── Multi-domain segmentation ───────────────────────────────────
    # The router should produce 2 segments here; first segment domain
    # is what we assert. (Full multi-segment tests can come later.)
    {
        "id": "multi_segment_order_then_cs",
        "message": "dame una barracuda y a qué hora cierran",
        "last_bot": "Hola.",
        "order_state": "GREETING",
        "has_active_cart": False,
        "latest_order_status": None,
        "expected_domain": DOMAIN_ORDER,
        "why": "two intents — first is the cart action",
    },
]


def _build_ctx_for_case(case: dict) -> TurnContext:
    """Construct a TurnContext from an eval case dict."""
    return TurnContext(
        order_state=case["order_state"],
        has_active_cart=case["has_active_cart"],
        cart_summary=case.get("cart_summary", ""),
        last_assistant_message=case["last_bot"],
        recent_history=(
            (("assistant", case["last_bot"]),) if case["last_bot"] else ()
        ),
        has_recent_cancellable_order=bool(
            case.get("latest_order_status") in ("pending", "confirmed")
        ),
        recent_order_id=(
            "fake-order-id"
            if case.get("latest_order_status") in ("pending", "confirmed")
            else None
        ),
        latest_order_status=case.get("latest_order_status"),
        latest_order_id=(
            "fake-order-id" if case.get("latest_order_status") else None
        ),
    )


@pytest.mark.parametrize(
    "case",
    [pytest.param(c, id=c["id"]) for c in CONTEXTUAL_ROUTING_CASES],
)
def test_router_uses_context_to_route_correctly(case):
    """Each case should route to ``case['expected_domain']`` given its
    turn context, using the **v1** (default) prompt. Failures here
    mean the router is not using the conversation flow to disambiguate
    ambiguous-on-the-surface messages.

    Run with ``pytest -m eval -k contextual`` to focus.
    """
    ctx = _build_ctx_for_case(case)
    result = router.route(
        message_body=case["message"],
        business_context=BIELA_CONTEXT,
        customer_name="David",
        ctx=ctx,
    )
    assert result.direct_reply is None, (
        f"{case['id']}: unexpected greeting fast-path — "
        f"only pure greetings should match"
    )
    assert result.segments, (
        f"{case['id']}: classifier returned no segments. "
        f"reason={case['why']}"
    )
    actual = result.segments[0][0]
    expected = case["expected_domain"]
    assert actual == expected, (
        f"\nCASE: {case['id']}\n"
        f"MESSAGE: {case['message']!r}\n"
        f"LAST BOT: {case['last_bot']!r}\n"
        f"STATE: {case['order_state']} cart={case['has_active_cart']} "
        f"last_order={case['latest_order_status']}\n"
        f"EXPECTED: {expected!r}  GOT: {actual!r}\n"
        f"WHY: {case['why']}\n"
        f"ALL SEGMENTS: {result.segments}"
    )


@pytest.mark.parametrize(
    "case",
    [pytest.param(c, id=c["id"]) for c in CONTEXTUAL_ROUTING_CASES],
)
def test_router_v2_context_first_prompt(case):
    """Same eval cases run against the v2 (context-first) prompt.
    Used during the redesign to measure delta vs. the v1 baseline.

    Run with ``pytest -m eval -k context_first`` to focus on the v2
    prompt's behavior in isolation.
    """
    ctx = _build_ctx_for_case(case)
    result = router.route(
        message_body=case["message"],
        business_context=BIELA_CONTEXT_V2,
        customer_name="David",
        ctx=ctx,
    )
    assert result.direct_reply is None, (
        f"{case['id']} [v2]: unexpected greeting fast-path"
    )
    assert result.segments, (
        f"{case['id']} [v2]: classifier returned no segments. "
        f"reason={case['why']}"
    )
    actual = result.segments[0][0]
    expected = case["expected_domain"]
    assert actual == expected, (
        f"\n[V2] CASE: {case['id']}\n"
        f"MESSAGE: {case['message']!r}\n"
        f"LAST BOT: {case['last_bot']!r}\n"
        f"STATE: {case['order_state']} cart={case['has_active_cart']} "
        f"last_order={case['latest_order_status']}\n"
        f"EXPECTED: {expected!r}  GOT: {actual!r}\n"
        f"WHY: {case['why']}\n"
        f"ALL SEGMENTS: {result.segments}"
    )
