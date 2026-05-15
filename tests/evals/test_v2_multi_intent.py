"""
Real-LLM regression evals for the v2 (tool-calling) order agent.

Catches prompt-fidelity bugs that mock-based tests can't:

- Multi-intent: when one user message combines a product order with a
  delivery signal (pickup/domicilio mode, name, etc.), the model must
  process BOTH intents in the same turn. Production trace 2026-05-09
  showed the model handling only the pickup half and dropping the
  product when the name was missing — this eval guards against it.

Marked ``eval`` — deselected by default; runs with ``pytest -m eval``.
Requires ``OPENAI_API_KEY``. Each scenario is one real LLM round-trip,
so this file should stay small and high-signal.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.eval


# Hermetic Biela-shaped business context. Same shape production sends.
BIELA_CONTEXT = {
    "business_id": "biela-eval-v2",
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


# Catalog stub — search_products / list_products / etc. need to resolve
# "denver" / "perro denver" to a real product so add_to_cart can succeed.
DENVER_PRODUCT = {
    "id": "00000000-0000-0000-0000-000000000001",
    "name": "DENVER",
    "price": 27000,
    "currency": "COP",
    "category": "PERROS",
    "description": "Perro caliente con tocineta y queso cheddar",
    "is_active": True,
}


def _patch_catalog_and_services(fake_session):
    """Stitch together the patches the v2 agent needs to run hermetically.

    Returns a context-manager-stack-aware list of patches the caller
    enters with ExitStack. Catalog matchers resolve any "denver"-ish
    query to DENVER; customer DB returns no prior record (so address /
    phone / pago must come from the session); session is the in-memory
    fake.
    """
    from app.services import order_tools as ot
    from app.services import catalog_cache

    def _search(business_id, query, limit=20, unique=False):
        if not query:
            return []
        q = query.lower()
        if "denver" in q or "perro" in q:
            return [DENVER_PRODUCT]
        return []

    def _list_products(business_id, category=None):
        if category and category.lower().startswith("perro"):
            return [DENVER_PRODUCT]
        return [DENVER_PRODUCT]

    def _list_categories(business_id):
        return ["HAMBURGUESAS", "PERROS", "BEBIDAS"]

    fake_product_svc = MagicMock()
    fake_product_svc.search_products.side_effect = _search
    fake_product_svc.list_products.side_effect = _list_products
    fake_product_svc.list_products_with_fallback.side_effect = _list_products

    return [
        patch.object(ot, "session_state_service", fake_session),
        patch(
            "app.database.session_state_service.session_state_service",
            fake_session,
        ),
        patch.object(catalog_cache, "search_products", side_effect=_search),
        patch.object(
            catalog_cache, "list_products", side_effect=_list_products,
        ),
        patch.object(
            catalog_cache, "list_categories", side_effect=_list_categories,
        ),
        patch.object(
            ot, "product_order_service", fake_product_svc,
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


def _run_v2_turn(
    fake_session,
    user_message: str,
    *,
    initial_order_context: dict = None,
    conversation_history: list = None,
):
    """Drive one turn through the real v2 OrderAgent.

    The LLM is NOT mocked — this calls OpenAI. Returns the AgentOutput
    plus the post-turn order_context so assertions can read both the
    user-facing reply and the resulting state.
    """
    from app.agents.order_agent import OrderAgent
    from contextlib import ExitStack

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


# ---------------------------------------------------------------------------
# Multi-intent: product + pickup signal in one message (no name)
# ---------------------------------------------------------------------------

class TestMultiIntentProductPlusPickup:
    """The production-bug regression. User says "para pedir un perro denver
    para recoger" — a SINGLE message with two intents. The model MUST
    process both: add the product AND switch to pickup mode. Before the
    rule 1 carve-out + rule 15 multi-intent guidance landed, the model
    would handle only the pickup half and silently drop the product."""

    def test_product_plus_pickup_no_name_adds_product_and_switches_mode(self, fake_session):
        out, oc = _run_v2_turn(
            fake_session,
            "Hola para pedir un perro denver para recoger",
        )

        items = oc.get("items") or []
        item_names = [(it.get("name") or "").upper() for it in items]
        assert any("DENVER" in n for n in item_names), (
            "model failed to add the product (perro denver) — multi-intent "
            f"regression. Cart: {item_names}. Reply: {out.get('message')!r}"
        )

        assert oc.get("fulfillment_type") == "pickup", (
            "model failed to switch to pickup mode — multi-intent regression. "
            f"Got fulfillment_type={oc.get('fulfillment_type')!r}. "
            f"Reply: {out.get('message')!r}"
        )

        # The reply should NOT pretend the order is confirmable yet —
        # we still don't have a name. Either it asks for the name, or
        # confirms the items added; either way no "¿confirmamos?".
        reply = (out.get("message") or "").lower()
        assert "¿confirmamos" not in reply, (
            "premature confirmation prompt despite missing name. "
            f"Reply: {out.get('message')!r}"
        )

    def test_product_plus_pickup_with_name_adds_product_saves_all(self, fake_session):
        """When name is also in the same message, the model has every
        piece — must add the product AND save name AND switch mode."""
        out, oc = _run_v2_turn(
            fake_session,
            "Hola para pedir un perro denver para recoger\n\nA nombre de David Zambrano",
        )

        items = oc.get("items") or []
        item_names = [(it.get("name") or "").upper() for it in items]
        assert any("DENVER" in n for n in item_names), (
            f"product not added. Cart: {item_names}"
        )
        assert oc.get("fulfillment_type") == "pickup"
        assert (oc.get("delivery_info") or {}).get("name", "").lower().startswith(
            "david zambrano".lower()[: len("david zambrano")][:5]
        ), f"name not captured. delivery_info={oc.get('delivery_info')}"


# ---------------------------------------------------------------------------
# Single-intent pickup switch (no product) — should still work
# ---------------------------------------------------------------------------

class TestSingleIntentPickupSwitch:
    """Sanity: a turn that contains ONLY a pickup signal (no product)
    — say, after the cart is already populated — must still switch
    mode without trying to add anything from the catalog."""

    def test_pickup_only_after_cart_populated(self, fake_session):
        out, oc = _run_v2_turn(
            fake_session,
            "asi esta bien es para recoger",
            initial_order_context={
                "items": [{
                    "product_id": DENVER_PRODUCT["id"],
                    "name": "DENVER",
                    "price": 27000,
                    "quantity": 1,
                }],
                "total": 27000,
                "state": "ORDERING",
            },
        )

        # Cart shouldn't have been mutated (no duplicate adds).
        items = oc.get("items") or []
        assert len(items) == 1, f"cart unexpectedly mutated: {items}"
        assert oc.get("fulfillment_type") == "pickup"
