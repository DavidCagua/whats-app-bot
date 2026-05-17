"""
Real-LLM regression evals for the order agent planner.

Covers the cart-mutation surface the planner has to drive — add, set
exact qty, decrement, remove entirely, restatement, mid-checkout
correction, ambiguous-line refusal — plus the multi-intent
(product + delivery signal in one turn) cases that previously lived
in test_v2_multi_intent.py.

Each test is one real LLM turn through OrderAgent against a stubbed
catalog and in-memory session. No mocks on the planner LLM itself —
the whole point of these evals is catching prompt/tool drift the
unit tests can't see.

Marked ``eval`` — deselected by default; runs with ``pytest -m eval``.
Requires ``OPENAI_API_KEY``. Keep this file focused: each scenario
costs one LLM round-trip, so prefer additions that exercise a
distinct planner behavior.

Restatement scenarios (full restatement, partial restatement, mid-
checkout correction) document a known planner gap and are expected
to fail until the set_cart_items refactor lands. They're the
regression baseline that proves that refactor worked.
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.eval


# ---------------------------------------------------------------------------
# Hermetic business context + catalog stub
# ---------------------------------------------------------------------------

BIELA_CONTEXT = {
    "business_id": "biela-eval-planner",
    "wa_id": "+573001234567",
    "business": {
        "name": "Biela",
        "settings": {
            "menu_url": "https://example.com/menu",
            "delivery_fee": 5000,
            "products_enabled": True,
            "order_agent_mode": "tool_calling",
            "payment_methods": ["Efectivo", "Nequi", "Llave BreB", "Transferencia"],
        },
    },
}


CATALOG: List[Dict[str, Any]] = [
    {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": "BARRACUDA",
        "price": 28000,
        "currency": "COP",
        "category": "HAMBURGUESAS",
        "description": "Hamburguesa doble carne, queso cheddar, tocineta",
        "is_active": True,
    },
    {
        "id": "00000000-0000-0000-0000-000000000002",
        "name": "AL PASTOR",
        "price": 27000,
        "currency": "COP",
        "category": "HAMBURGUESAS",
        "description": "Hamburguesa estilo pastor con piña",
        "is_active": True,
    },
    {
        "id": "00000000-0000-0000-0000-000000000003",
        "name": "MEXICAN BURGER",
        "price": 27000,
        "currency": "COP",
        "category": "HAMBURGUESAS",
        "description": "Hamburguesa con jalapeños, guacamole y nachos",
        "is_active": True,
    },
    {
        "id": "00000000-0000-0000-0000-000000000004",
        "name": "MONTESA",
        "price": 27000,
        "currency": "COP",
        "category": "HAMBURGUESAS",
        "description": "Hamburguesa con queso azul, champiñones",
        "is_active": True,
    },
    {
        "id": "00000000-0000-0000-0000-000000000005",
        "name": "DENVER",
        "price": 27000,
        "currency": "COP",
        "category": "PERROS",
        "description": "Perro caliente con tocineta y queso cheddar",
        "is_active": True,
    },
]

_BY_ID = {p["id"]: p for p in CATALOG}


def _find_products(query: str) -> List[Dict[str, Any]]:
    """Token-overlap match — same shape product_order_service returns."""
    if not query:
        return []
    q = query.lower().strip()
    hits = []
    for p in CATALOG:
        name = p["name"].lower()
        desc = (p["description"] or "").lower()
        if q in name or any(tok in name for tok in q.split()):
            hits.append(p)
            continue
        if q in desc:
            hits.append(p)
    return hits


def _patch_catalog_and_services(fake_session):
    """Wire the order_tools module against in-memory catalog + session.

    Mirrors the production surface: order_tools reads through
    product_order_service (search_products, get_product) and through
    catalog_cache.list_products_with_fallback. Everything else
    (turn_cache, promotion_service) reads through these patched layers,
    so we don't have to stub them directly.
    """
    from app.services import order_tools as ot
    from app.services import catalog_cache

    def _search(business_id, query, limit=20, unique=False, include_unavailable=False):
        return _find_products(query)

    def _get_product(business_id, product_id=None, product_name=None, include_unavailable=False):
        if product_id and product_id in _BY_ID:
            return dict(_BY_ID[product_id])
        if product_name:
            hits = _find_products(product_name)
            if len(hits) == 1:
                return dict(hits[0])
            if len(hits) > 1:
                # Same shape production raises on multiple matches. The
                # real constructor takes `matches=` (not `candidates`) —
                # earlier signature mismatch made the harness crash on
                # legit ambiguous lookups instead of surfacing them.
                from app.database.product_order_service import AmbiguousProductError
                raise AmbiguousProductError(
                    query=product_name,
                    matches=[dict(p) for p in hits],
                )
        return None

    def _list_products_with_fallback(business_id, category=""):
        if not category:
            return [dict(p) for p in CATALOG]
        c = category.lower().strip()
        return [dict(p) for p in CATALOG if (p.get("category") or "").lower() == c]

    fake_svc = MagicMock()
    fake_svc.search_products.side_effect = _search
    fake_svc.get_product.side_effect = _get_product

    return [
        patch.object(ot, "session_state_service", fake_session),
        patch(
            "app.database.session_state_service.session_state_service",
            fake_session,
        ),
        patch.object(ot, "product_order_service", fake_svc),
        patch.object(
            catalog_cache, "list_products_with_fallback",
            side_effect=_list_products_with_fallback,
        ),
        patch.object(
            ot.customer_service, "get_customer", return_value=None,
        ),
        patch.object(
            ot.customer_service, "create_customer", return_value={"id": 1},
        ),
        patch.object(
            ot.customer_service, "update_customer", return_value=None,
        ),
        patch.object(
            ot.customer_service, "link_customer_to_business", return_value=None,
        ),
    ]


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _run_planner_turn(
    fake_session,
    user_message: str,
    *,
    initial_order_context: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[List[Dict]] = None,
):
    """Drive one turn through the real OrderAgent.

    LLM is NOT mocked — calls OpenAI. Returns (agent_output, post_turn_oc).
    """
    from app.agents.order_agent import OrderAgent

    if initial_order_context is not None:
        fake_session.save(
            BIELA_CONTEXT["wa_id"],
            BIELA_CONTEXT["business_id"],
            {"order_context": initial_order_context},
        )

    agent = OrderAgent()
    with ExitStack() as stack:
        for cm in _patch_catalog_and_services(fake_session):
            stack.enter_context(cm)
        stack.enter_context(patch(
            "app.agents.order_agent.conversation_service.store_conversation_message",
        ))
        stack.enter_context(patch(
            "app.agents.order_agent.tracer",
        ))
        out = agent.execute(
            message_body=user_message,
            wa_id=BIELA_CONTEXT["wa_id"],
            name="David",
            business_context=BIELA_CONTEXT,
            conversation_history=conversation_history or [],
        )

    state = fake_session.load(
        BIELA_CONTEXT["wa_id"], BIELA_CONTEXT["business_id"],
    )
    oc = (state.get("session") or {}).get("order_context") or {}
    return out, oc


def _cart_items(oc: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(oc.get("items") or [])


def _qty_of(items: List[Dict[str, Any]], name_fragment: str) -> int:
    """Sum quantities across all lines whose name contains the fragment."""
    frag = name_fragment.lower()
    return sum(
        int(it.get("quantity") or 0)
        for it in items
        if frag in (it.get("name") or "").lower()
    )


def _line_count(items: List[Dict[str, Any]], name_fragment: str) -> int:
    """Number of distinct lines whose name contains the fragment."""
    frag = name_fragment.lower()
    return sum(1 for it in items if frag in (it.get("name") or "").lower())


# ---------------------------------------------------------------------------
# Section 1 — Basic add behaviors
# ---------------------------------------------------------------------------

class TestAddBasics:
    def test_add_named_product(self, fake_session):
        out, oc = _run_planner_turn(fake_session, "dame una barracuda")
        items = _cart_items(oc)
        assert _qty_of(items, "barracuda") == 1, (
            f"expected 1x BARRACUDA in cart, got items={items}. "
            f"Reply: {out.get('message')!r}"
        )

    def test_add_with_notes(self, fake_session):
        out, oc = _run_planner_turn(fake_session, "una barracuda sin cebolla")
        items = _cart_items(oc)
        assert _qty_of(items, "barracuda") == 1, (
            f"BARRACUDA not added. items={items}"
        )
        # Find the line and check notes captured the modifier.
        line = next(
            (it for it in items if "barracuda" in (it.get("name") or "").lower()),
            None,
        )
        assert line is not None
        notes = (line.get("notes") or "").lower()
        assert "cebolla" in notes, (
            f"expected notes to capture 'sin cebolla', got notes={notes!r}"
        )

    def test_add_stacks_on_existing_line(self, fake_session):
        """Second add of the same product (no notes) increments the existing
        line's qty rather than creating a duplicate line."""
        out, oc = _run_planner_turn(
            fake_session,
            "agrégame otra barracuda",
            initial_order_context={
                "items": [{
                    "product_id": _BY_ID["00000000-0000-0000-0000-000000000001"]["id"],
                    "name": "BARRACUDA",
                    "price": 28000,
                    "quantity": 1,
                }],
                "total": 28000,
                "state": "ORDERING",
            },
        )
        items = _cart_items(oc)
        assert _qty_of(items, "barracuda") == 2, (
            f"expected qty 2 after stacking, got items={items}"
        )
        assert _line_count(items, "barracuda") == 1, (
            f"expected one stacked line, got {_line_count(items, 'barracuda')} lines"
        )


# ---------------------------------------------------------------------------
# Section 2 — Quantity edits and removals
# ---------------------------------------------------------------------------

class TestQuantityEdits:
    def test_set_exact_quantity_lower(self, fake_session):
        """Cart has 2x BARRACUDA, user says 'solo una' → cart should end at 1."""
        out, oc = _run_planner_turn(
            fake_session,
            "solo una barracuda",
            initial_order_context={
                "items": [{
                    "product_id": "00000000-0000-0000-0000-000000000001",
                    "name": "BARRACUDA",
                    "price": 28000,
                    "quantity": 2,
                }],
                "total": 56000,
                "state": "ORDERING",
            },
        )
        items = _cart_items(oc)
        assert _qty_of(items, "barracuda") == 1, (
            f"expected qty 1 after 'solo una', got items={items}. "
            f"Reply: {out.get('message')!r}"
        )

    def test_decrement_by_n(self, fake_session):
        """Cart has 3x BARRACUDA, user says 'quita dos' → cart should end at 1."""
        out, oc = _run_planner_turn(
            fake_session,
            "quita dos barracudas",
            initial_order_context={
                "items": [{
                    "product_id": "00000000-0000-0000-0000-000000000001",
                    "name": "BARRACUDA",
                    "price": 28000,
                    "quantity": 3,
                }],
                "total": 84000,
                "state": "ORDERING",
            },
        )
        items = _cart_items(oc)
        assert _qty_of(items, "barracuda") == 1, (
            f"expected qty 1 after decrement-2, got items={items}"
        )

    def test_remove_entirely(self, fake_session):
        """Cart has BARRACUDA + AL PASTOR, user says 'quita la barracuda' →
        only AL PASTOR remains."""
        out, oc = _run_planner_turn(
            fake_session,
            "quita la barracuda",
            initial_order_context={
                "items": [
                    {
                        "product_id": "00000000-0000-0000-0000-000000000001",
                        "name": "BARRACUDA",
                        "price": 28000,
                        "quantity": 1,
                    },
                    {
                        "product_id": "00000000-0000-0000-0000-000000000002",
                        "name": "AL PASTOR",
                        "price": 27000,
                        "quantity": 1,
                    },
                ],
                "total": 55000,
                "state": "ORDERING",
            },
        )
        items = _cart_items(oc)
        assert _qty_of(items, "barracuda") == 0, (
            f"BARRACUDA should be gone, got items={items}"
        )
        assert _qty_of(items, "al pastor") == 1, (
            f"AL PASTOR should remain, got items={items}"
        )


# ---------------------------------------------------------------------------
# Section 3 — Restatement / corrections
#
# These document the Katherin failure mode (2026-05-16, +573207505867).
# Today's planner reads "Es 1 X y 1 Y" through the additive rule and
# fails to set quantities. Expected to fail until set_cart_items lands.
# ---------------------------------------------------------------------------

class TestRestatement:
    def test_full_restatement_resets_existing_qty(self, fake_session):
        """Cart has 2x AL PASTOR, user restates 'Es 1 al pastor y 1 Mexican
        burger' → cart should be exactly 1 AL PASTOR + 1 MEXICAN BURGER."""
        out, oc = _run_planner_turn(
            fake_session,
            "Es 1 al pastor y 1 Mexican burger",
            initial_order_context={
                "items": [{
                    "product_id": "00000000-0000-0000-0000-000000000002",
                    "name": "AL PASTOR",
                    "price": 27000,
                    "quantity": 2,
                }],
                "total": 54000,
                "state": "ORDERING",
            },
        )
        items = _cart_items(oc)
        assert _qty_of(items, "al pastor") == 1, (
            f"AL PASTOR should be set to 1 (restatement), got items={items}. "
            f"Reply: {out.get('message')!r}"
        )
        assert _qty_of(items, "mexican") == 1, (
            f"MEXICAN BURGER should be 1, got items={items}"
        )

    def test_partial_restatement_only_touches_named_items(self, fake_session):
        """Cart has 3 MEXICAN + 2 BARRACUDA. User says 'solo son 2
        Mexican burger'. Only MEXICAN should drop to 2; BARRACUDA untouched.

        The key behavior tested: planner picks update_cart_item (single
        product), NOT set_cart_items (which would drop BARRACUDA). Using
        the canonical product name here so the stub catalog resolves
        cleanly — production's hybrid search would handle 'mexicanas',
        but the stub is name-substring-only by design (keeps test focus
        on planner tool-choice, not search ranking)."""
        out, oc = _run_planner_turn(
            fake_session,
            "solo son 2 Mexican burger",
            initial_order_context={
                "items": [
                    {
                        "product_id": "00000000-0000-0000-0000-000000000003",
                        "name": "MEXICAN BURGER",
                        "price": 27000,
                        "quantity": 3,
                    },
                    {
                        "product_id": "00000000-0000-0000-0000-000000000001",
                        "name": "BARRACUDA",
                        "price": 28000,
                        "quantity": 2,
                    },
                ],
                "total": 137000,
                "state": "ORDERING",
            },
        )
        items = _cart_items(oc)
        assert _qty_of(items, "mexican") == 2, (
            f"MEXICAN should be set to 2, got items={items}"
        )
        assert _qty_of(items, "barracuda") == 2, (
            f"BARRACUDA should stay at 2 (not mentioned in restatement), "
            f"got items={items}"
        )

    def test_mid_checkout_correction_reopens_cart(self, fake_session):
        """Cart is awaiting confirmation (post-ready_to_confirm) with 2x AL
        PASTOR. User restates 'Es 1 al pastor y 1 Mexican burger' instead of
        confirming. Cart should re-open and end with the corrected items.

        The meaningful checkout signal is ``awaiting_confirmation=True`` —
        the order agent appends an "ACCIÓN ESPERADA" system message in that
        state which tells the model to either place_order on a yes or
        handle changes without re-prompting. This test verifies cart
        mutation wins over the confirmation pending."""
        out, oc = _run_planner_turn(
            fake_session,
            "Es 1 al pastor y 1 Mexican burger",
            initial_order_context={
                "items": [{
                    "product_id": "00000000-0000-0000-0000-000000000002",
                    "name": "AL PASTOR",
                    "price": 27000,
                    "quantity": 2,
                }],
                "total": 54000,
                "state": "READY_TO_PLACE",
                "awaiting_confirmation": True,
            },
        )
        items = _cart_items(oc)
        assert _qty_of(items, "al pastor") == 1, (
            f"mid-checkout correction: AL PASTOR should be 1, got items={items}. "
            f"Reply: {out.get('message')!r}"
        )
        assert _qty_of(items, "mexican") == 1, (
            f"mid-checkout correction: MEXICAN should be 1, got items={items}"
        )


# ---------------------------------------------------------------------------
# Section 4 — Ambiguity guardrails
# ---------------------------------------------------------------------------

class TestAmbiguity:
    def test_ambiguous_multi_line_refuses_silent_pick(self, fake_session):
        """Two MONTESA lines with different notes. Removing 'una MONTESA'
        should not silently pick one — should either refuse or surface
        the variants. Today's update_cart_item refuses; we accept either
        a refusal message or that both lines remain intact."""
        out, oc = _run_planner_turn(
            fake_session,
            "quita una MONTESA",
            initial_order_context={
                "items": [
                    {
                        "product_id": "00000000-0000-0000-0000-000000000004",
                        "name": "MONTESA",
                        "price": 27000,
                        "quantity": 1,
                        "notes": "sin queso azul",
                    },
                    {
                        "product_id": "00000000-0000-0000-0000-000000000004",
                        "name": "MONTESA",
                        "price": 27000,
                        "quantity": 1,
                        "notes": "extra champiñones",
                    },
                ],
                "total": 54000,
                "state": "ORDERING",
            },
        )
        items = _cart_items(oc)
        # Either both lines are still there (refusal), or exactly one is
        # gone (cascade decrement). What we MUST NOT see is "both gone"
        # or "merged into one with wrong notes".
        total_qty = _qty_of(items, "montesa")
        assert total_qty in (1, 2), (
            f"ambiguous-remove produced unexpected state. total_qty={total_qty}, "
            f"items={items}. Reply: {out.get('message')!r}"
        )
        # If a line was removed, notes integrity check: remaining line(s)
        # should keep their original notes (not get merged/mangled).
        for it in items:
            if "montesa" in (it.get("name") or "").lower():
                notes = (it.get("notes") or "").lower()
                assert notes in ("sin queso azul", "extra champiñones"), (
                    f"remaining MONTESA line has unexpected notes={notes!r} — "
                    f"likely an incorrect silent pick"
                )


# ---------------------------------------------------------------------------
# Section 5 — Multi-intent (product + delivery signal in one turn)
#
# Inherited from the retired test_v2_multi_intent.py. The model must
# process BOTH intents in the same turn — handling only the pickup
# half and dropping the product was the 2026-05-09 regression these
# evals were written to catch.
# ---------------------------------------------------------------------------

class TestMultiIntentProductPlusDelivery:
    def test_product_plus_pickup_no_name(self, fake_session):
        out, oc = _run_planner_turn(
            fake_session,
            "Hola para pedir un perro denver para recoger",
        )
        items = _cart_items(oc)
        assert _qty_of(items, "denver") >= 1, (
            f"model failed to add DENVER while switching to pickup. "
            f"items={items}. Reply: {out.get('message')!r}"
        )
        assert oc.get("fulfillment_type") == "pickup", (
            f"fulfillment_type should be pickup, got {oc.get('fulfillment_type')!r}"
        )
        # Don't ask to confirm before name is collected.
        reply = (out.get("message") or "").lower()
        assert "¿confirmamos" not in reply, (
            f"premature confirmation prompt. Reply: {out.get('message')!r}"
        )

    def test_product_plus_pickup_plus_name(self, fake_session):
        out, oc = _run_planner_turn(
            fake_session,
            "Hola para pedir un perro denver para recoger\n\nA nombre de David Zambrano",
        )
        items = _cart_items(oc)
        assert _qty_of(items, "denver") >= 1, (
            f"DENVER not added. items={items}"
        )
        assert oc.get("fulfillment_type") == "pickup"
        name = (oc.get("delivery_info") or {}).get("name", "").lower()
        assert "david" in name, (
            f"name not captured. delivery_info={oc.get('delivery_info')}"
        )

    def test_pickup_only_with_existing_cart(self, fake_session):
        """Cart already has DENVER. User just signals pickup. Cart must
        not be re-mutated (no duplicate add)."""
        out, oc = _run_planner_turn(
            fake_session,
            "asi esta bien es para recoger",
            initial_order_context={
                "items": [{
                    "product_id": "00000000-0000-0000-0000-000000000005",
                    "name": "DENVER",
                    "price": 27000,
                    "quantity": 1,
                }],
                "total": 27000,
                "state": "ORDERING",
            },
        )
        items = _cart_items(oc)
        assert _qty_of(items, "denver") == 1, (
            f"cart was mutated (expected qty=1, got items={items})"
        )
        assert oc.get("fulfillment_type") == "pickup"


# ---------------------------------------------------------------------------
# Empty-cart guards — delivery-info tools must refuse before any product
# is in the cart. Production observation 2026-05-17 / Biela / +573177000722:
# opener "para un domicilio" with no cart led the model to call
# get_customer_info + respond(kind='delivery_info_collected'), asking for
# name/address/phone before the customer picked a product. Hard guards in
# get_customer_info and submit_delivery_info refuse in this state and tell
# the model to ask for the product first.
# ---------------------------------------------------------------------------


class TestEmptyCartDeliveryGuard:
    def test_opener_with_empty_cart_asks_for_product(self, fake_session):
        """Cart is empty. User says 'para un domicilio'. The agent must
        NOT ask for delivery info — it must ask what to order.

        Asserted via:
        - Cart stays empty (no products silently added).
        - No delivery_info written.
        - Reply does NOT mention name/address/phone (the delivery-info
          collection language).
        - Reply DOES mention ordering / menu / 'qué te gustaría' (the
          product-question redirect)."""
        out, oc = _run_planner_turn(
            fake_session,
            "para un domicilio",
        )
        items = _cart_items(oc)
        assert len(items) == 0, (
            f"cart unexpectedly populated on empty-cart opener: items={items}"
        )
        delivery = oc.get("delivery_info") or {}
        assert not delivery, (
            f"delivery_info written before product chosen: {delivery!r}"
        )
        reply = (out.get("message") or "").lower()
        # The buggy reply mentions name + address + phone (the four-tuple
        # ask). The fixed reply should ask about the product instead.
        delivery_words = ("nombre", "dirección", "direccion", "teléfono", "telefono")
        delivery_hits = [w for w in delivery_words if w in reply]
        assert len(delivery_hits) < 2, (
            f"reply still asks for delivery info on empty-cart opener: "
            f"hits={delivery_hits}. Full reply: {out.get('message')!r}"
        )
        product_words = ("qué te gustaría", "que te gustaria", "qué deseas", "menú", "menu", "ordenar", "pedir")
        product_hits = [w for w in product_words if w in reply]
        assert product_hits, (
            f"reply doesn't redirect to product/menu on empty-cart opener: "
            f"reply={out.get('message')!r}"
        )

    def test_recoger_with_empty_cart_asks_for_product(self, fake_session):
        """Same case in pickup mode. 'para recoger' with empty cart →
        ask for product, don't ask for name."""
        out, oc = _run_planner_turn(
            fake_session,
            "para recoger",
        )
        items = _cart_items(oc)
        assert len(items) == 0, (
            f"cart unexpectedly populated: items={items}"
        )
        reply = (out.get("message") or "").lower()
        # On pickup, the only required delivery field is name. So at
        # most one mention of "nombre" — but it shouldn't dominate.
        assert "dirección" not in reply and "direccion" not in reply, (
            f"reply asks for address on pickup empty-cart opener: {out.get('message')!r}"
        )

