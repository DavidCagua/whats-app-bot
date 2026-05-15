"""
Integration tests for the router — domain classification with real LLM calls.
Run with: pytest -m integration tests/integration/test_router.py

Uses VCR cassettes (pytest-recording) to record/replay HTTP calls.
Delete cassettes/ and rerun when the router prompt changes.

These tests guard the router LAYER. The bug they exist for: the
multi-agent merge added a stateless router that classified ambiguous
negatives ("no más") as customer_service, which then emitted
CANCEL_ORDER and silently cancelled in-progress carts. Fix was to make
the router state-aware via TurnContext.
"""

import pytest

from app.orchestration.router import (
    DOMAIN_ORDER,
    DOMAIN_CUSTOMER_SERVICE,
    _classify_with_llm,
)
from app.orchestration.turn_context import TurnContext


pytestmark = pytest.mark.integration


def _classify(
    message: str,
    *,
    order_state: str = "GREETING",
    cart_summary: str = "",
    has_active_cart: bool = False,
    last_assistant_message: str = "",
    has_recent_cancellable_order: bool = False,
):
    ctx = TurnContext(
        order_state=order_state,
        has_active_cart=has_active_cart,
        cart_summary=cart_summary,
        last_assistant_message=last_assistant_message,
        has_recent_cancellable_order=has_recent_cancellable_order,
    )
    segments = _classify_with_llm(message, business_context=None, ctx=ctx)
    assert segments, f"router returned no segments for {message!r}"
    return segments


# ---------------------------------------------------------------------------
# Negative confirmations during ORDERING — must stay in `order`.
# Regression for the production bug on 2026-04-27 (Biela / 3177000722):
# user said "No más" while ordering, router classified as customer_service,
# CS planner emitted CANCEL_ORDER, and the in-progress order was wiped.
# ---------------------------------------------------------------------------


class TestNegativeConfirmStaysInOrder:
    """
    With an active cart + the bot just having asked "¿algo más o procedemos?",
    short negatives must route to `order`. The order agent's tool-calling
    loop then resolves these to `place_order` / `respond`. We just need the
    router to deliver them.
    """

    LAST_BOT = "Tu pedido actual: 1x DENVER. Subtotal: $24.500. ¿Quieres agregar algo más o procedemos con el pedido?"

    @pytest.mark.parametrize("phrase", [
        "no más",
        "No más",
        "nada más",
        "que no",
        "eso es todo",
        "no, gracias",
        "así está bien",
        "ya no",
    ])
    def test_negative_confirm_during_ordering_routes_to_order(self, phrase):
        segments = _classify(
            phrase,
            order_state="ORDERING",
            cart_summary="1x DENVER. Subtotal: $24.500",
            has_active_cart=True,
            last_assistant_message=self.LAST_BOT,
        )
        domains = [d for d, _ in segments]
        assert DOMAIN_ORDER in domains, (
            f"expected `order` for {phrase!r} during ORDERING with "
            f"close-question; got {domains}"
        )


# ---------------------------------------------------------------------------
# Cancel-by-state matrix.
# - active cart, no placed order  → "cancela" routes to `order` (abandon cart)
# - no cart, placed cancellable   → "cancela mi pedido" routes to CS (post-venta)
# - no cart, no placed order      → "cancela" routes to CS (no-op response)
# ---------------------------------------------------------------------------


class TestCancelByState:

    def test_cancel_with_active_cart_routes_to_order(self):
        segments = _classify(
            "cancela el pedido",
            order_state="ORDERING",
            cart_summary="1x DENVER. Subtotal: $24.500",
            has_active_cart=True,
            last_assistant_message="¿Quieres agregar algo más o procedemos con el pedido?",
        )
        domains = [d for d, _ in segments]
        assert DOMAIN_ORDER in domains, (
            f"abandon-active-cart must go to order; got {domains}"
        )

    def test_cancel_during_collecting_delivery_routes_to_order(self):
        segments = _classify(
            "ya no quiero",
            order_state="COLLECTING_DELIVERY",
            cart_summary="1x DENVER. Subtotal: $24.500",
            has_active_cart=True,
            last_assistant_message="¿Cuál es la dirección?",
        )
        domains = [d for d, _ in segments]
        assert DOMAIN_ORDER in domains, (
            f"abandon-during-checkout must go to order; got {domains}"
        )

    def test_cancel_with_placed_order_routes_to_cs(self):
        segments = _classify(
            "cancela mi pedido",
            order_state="GREETING",
            cart_summary="",
            has_active_cart=False,
            has_recent_cancellable_order=True,
            last_assistant_message="",
        )
        domains = [d for d, _ in segments]
        assert DOMAIN_CUSTOMER_SERVICE in domains, (
            f"cancel-placed-order must go to CS; got {domains}"
        )

    def test_cancel_without_cart_or_order_routes_to_cs(self):
        # No cart, no placed order. Routing to CS is fine — CS planner
        # will then refuse CANCEL_ORDER via the deterministic guard and
        # respond with a friendly "no tienes pedidos por cancelar".
        segments = _classify(
            "cancela",
            order_state="GREETING",
            cart_summary="",
            has_active_cart=False,
            has_recent_cancellable_order=False,
        )
        domains = [d for d, _ in segments]
        assert DOMAIN_CUSTOMER_SERVICE in domains, (
            f"cancel-without-context defaults to CS; got {domains}"
        )


# ---------------------------------------------------------------------------
# Ordering verbs still route to `order` regardless of state.
# Lightweight sanity check that the new state-aware rules didn't regress
# the happy path.
# ---------------------------------------------------------------------------


class TestOrderingHappyPathStable:

    def test_add_to_cart_during_greeting(self):
        segments = _classify(
            "dame una hamburguesa",
            order_state="GREETING",
            has_active_cart=False,
        )
        assert any(d == DOMAIN_ORDER for d, _ in segments)

    def test_business_info_during_greeting(self):
        segments = _classify(
            "a qué hora abren?",
            order_state="GREETING",
            has_active_cart=False,
        )
        assert any(d == DOMAIN_CUSTOMER_SERVICE for d, _ in segments)
