"""
Real-LLM regression evals for the customer-service planner.

Covers the payment-disambiguation surface — turns where the user says
something that sounds like "cancel" or "pay" and the planner has to
pick the right tool (`get_payment_info`) instead of answering inline.

Regression baseline: Nicolás Bolaños's conversation (May 17 2026, +573153156770)
where the planner pattern-matched the in-prompt example for "cancelar la cuenta"
and produced four straight canned "pagar al domiciliario" replies, never
calling `get_payment_info`, before the customer had to spell out
"necesito pagarla antes de que llegue".

Each test is one real LLM turn through CustomerServiceAgent against a
stubbed business context. No mocks on the planner LLM itself — the
whole point is catching prompt drift that the unit tests can't see.

Marked ``eval`` — deselected by default; runs with ``pytest -m eval``.
Requires ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import logging
import re
from contextlib import ExitStack
from typing import Any, Dict, List, Tuple
from unittest.mock import patch

import pytest

from app.agents.customer_service_agent import CustomerServiceAgent
from app.orchestration.turn_context import TurnContext


pytestmark = pytest.mark.eval


# ---------------------------------------------------------------------------
# Hermetic business context
# ---------------------------------------------------------------------------

BIELA_CS_CONTEXT: Dict[str, Any] = {
    "business_id": "biela-eval-cs",
    "business": {
        "name": "Biela",
        "settings": {
            "menu_url": "https://example.com/menu",
            "delivery_fee": 7000,
            "products_enabled": True,
            # Per-context payment config so get_payment_info has data
            # to surface both "al recibir" and "por adelantado" options.
            "payment_methods": [
                {
                    "name": "Efectivo",
                    "contexts": [
                        "delivery_on_fulfillment",
                        "on_site_on_fulfillment",
                    ],
                },
                {
                    "name": "Nequi",
                    "contexts": [
                        "delivery_pay_now",
                        "delivery_on_fulfillment",
                        "on_site_pay_now",
                        "on_site_on_fulfillment",
                    ],
                },
                {
                    "name": "Llave BreB",
                    "contexts": ["delivery_pay_now", "on_site_pay_now"],
                },
            ],
            "payment_destinations": {
                "Nequi": "300 123 4567 (a nombre de Biela SAS)",
                "Llave BreB": "0090916751",
            },
        },
    },
}

WA_ID = "+573001234567"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

_CS_TURN_RE = re.compile(r"\[CS_TURN\][^[]*tools=\[([^\]]*)\]")


def _parse_tools_from_caplog(records: List[logging.LogRecord]) -> List[str]:
    """Pull the tools list logged by CustomerServiceAgent on turn end.

    The agent emits ``[CS_TURN] wa_id=... tools=[...]`` after the
    dispatch loop terminates. Returns the parsed list (may be empty).
    """
    for rec in records:
        m = _CS_TURN_RE.search(rec.getMessage())
        if m:
            inner = m.group(1).strip()
            if not inner:
                return []
            return [
                t.strip().strip("'\"")
                for t in inner.split(",")
                if t.strip()
            ]
    return []


def _run_cs_turn(
    message: str,
    *,
    recent_history: List[Tuple[str, str]],
    fulfillment_type: str = "delivery",
    has_recent_cancellable_order: bool = False,
    caplog: pytest.LogCaptureFixture,
    extra_patches: List[Any] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Drive one turn through the real CustomerServiceAgent.

    LLM is NOT mocked — calls OpenAI via the agent's configured client.
    Returns (agent_output, tools_called).
    """
    agent = CustomerServiceAgent()
    turn_ctx = TurnContext(
        order_state="GREETING",
        has_active_cart=False,
        cart_summary="",
        last_assistant_message=recent_history[-1][1] if recent_history else "",
        recent_history=tuple(recent_history),
        fulfillment_type=fulfillment_type,
        # CS planner reads latest_order_status to disambiguate
        # post-order closes; set to a confirmed status so the prompt
        # treats this as a placed-order context.
        latest_order_status="confirmed",
        latest_order_id="order-eval-1",
        has_recent_cancellable_order=has_recent_cancellable_order,
        recent_order_id="order-eval-1" if has_recent_cancellable_order else None,
    )

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="root"), ExitStack() as stack:
        stack.enter_context(patch(
            "app.agents.customer_service_agent.conversation_service.store_conversation_message",
        ))
        stack.enter_context(patch(
            "app.agents.customer_service_agent.tracer",
        ))
        # Pre-loop safety nets pull from the router; stub the two it
        # touches so we don't accidentally hand off out of CS.
        stack.enter_context(patch(
            "app.orchestration.router._deterministic_price_of_product",
            return_value=False,
        ))
        stack.enter_context(patch(
            "app.orchestration.router._expand_stuck_articles",
            side_effect=lambda m, _l: m,
        ))
        for cm in (extra_patches or []):
            stack.enter_context(cm)
        out = agent.execute(
            message_body=message,
            wa_id=WA_ID,
            name="Nicolás",
            business_context=BIELA_CS_CONTEXT,
            conversation_history=[
                {"role": role, "message": msg} for role, msg in recent_history
            ],
            turn_ctx=turn_ctx,
        )

    tools = _parse_tools_from_caplog(caplog.records)
    return out, tools


# ---------------------------------------------------------------------------
# Regression scenarios — Nicolás Bolaños loop (May 17 2026)
# ---------------------------------------------------------------------------


class TestPaymentDisambiguationCallsTool:
    """
    Every payment-flavoured turn must call `get_payment_info`, even when
    the message looks like the line-155 example. The bug: planner
    pattern-matched the example and answered inline with "pagar al
    domiciliario" — never surfaced prepay options.
    """

    def test_pagar_la_cuenta_after_disambiguation(self, caplog):
        """After the bot's own "anular o pagar la cuenta?" prompt,
        a bare 'Pagar la cuenta' must trigger get_payment_info.
        Previously: tools=[] and inline canned reply."""
        history = [
            (
                "user",
                "Puedo cancelar de una vez el pedido?",
            ),
            (
                "assistant",
                "Puedes pagar o cancelar el pedido; ¿quieres que lo "
                "anule o te refieres a pagar la cuenta?",
            ),
            ("user", "Ya que es un domicilio para alguien más"),
            (
                "assistant",
                "Entendido. ¿Quieres cancelar el pedido o solo te "
                "refieres a pagar el domicilio para esa persona?",
            ),
        ]
        _out, tools = _run_cs_turn(
            "Pagar la cuenta", recent_history=history, caplog=caplog,
        )
        assert "get_payment_info" in tools, (
            "Planner answered 'Pagar la cuenta' inline without calling "
            f"get_payment_info. Tools logged: {tools}. The prompt rule "
            "at customer_service_agent.py:149 ('NUNCA respondas inline "
            "sobre pago … sin llamar get_payment_info') has regressed."
        )

    def test_pero_es_regalo_is_prepay_signal(self, caplog):
        """'Pero es regalo' after the bot just promised pay-on-delivery
        must reverse course and call get_payment_info — 'regalo' is in
        the prepay signal list."""
        history = [
            ("user", "Pagar la cuenta"),
            (
                "assistant",
                "Puedes pagar la cuenta al domiciliario cuando llegue "
                "el pedido. Si quieres, también te puedo ayudar con "
                "otra duda.",
            ),
            ("user", "Pagar el domicilio"),
            (
                "assistant",
                "Puedes pagar el domicilio al domiciliario cuando te "
                "entregue el pedido. Si quieres, también te puedo "
                "ayudar con otra duda.",
            ),
        ]
        _out, tools = _run_cs_turn(
            "Pero es regalo", recent_history=history, caplog=caplog,
        )
        assert "get_payment_info" in tools, (
            "'Pero es regalo' (prepay signal — recipient shouldn't pay) "
            "did not trigger get_payment_info. Conversation-history "
            f"anchor won out over the prepay signal list. Tools: {tools}"
        )

    def test_antes_de_que_llegue_is_prepay_signal(self, caplog):
        """'necesito pagarla antes de que llegue' is the textbook prepay
        cue from the prompt's signal list — must trigger the tool."""
        history = [
            ("user", "Pero es regalo"),
            (
                "assistant",
                "Puedes pagar el domicilio al domiciliario cuando te "
                "entregue el pedido, aunque sea un regalo.",
            ),
        ]
        _out, tools = _run_cs_turn(
            "Ósea necesito pagarla antes de que llegue",
            recent_history=history,
            caplog=caplog,
        )
        assert "get_payment_info" in tools, (
            "'antes de que llegue' is the textbook prepay cue. Planner "
            f"did not call get_payment_info. Tools: {tools}"
        )


# ---------------------------------------------------------------------------
# Destructive-cancel guard — local repro 2026-05-17
# ---------------------------------------------------------------------------


_CANCELLATION_CONFIRMED_PHRASES = (
    "cancele tu pedido",   # "cancelé tu pedido"
    "cancelado",
    "anulé tu pedido",
    "anule tu pedido",
    "anulado",
    "tu pedido fue cancelado",
    "tu pedido ha sido cancelado",
    "pedido cancelado",
)


def _looks_like_cancellation_confirmation(message: str) -> bool:
    """Accent-insensitive check for 'I cancelled your order' replies."""
    import unicodedata
    nfkd = unicodedata.normalize("NFD", (message or "").lower())
    cleaned = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return any(phrase in cleaned for phrase in _CANCELLATION_CONFIRMED_PHRASES)


class TestCancelarDeUnaVezDoesNotDeleteOrder:
    """
    Regression: 2026-05-17 local repro. Customer placed order
    #116B2DF9, then asked 'Puedo cancelar de una vez el pedido?'
    which is a pay-the-bill question in Colombian Spanish. The bot
    called cancel_order and deleted the order.

    Two layers must hold:
      1. Floor guard (has_explicit_cancel_keyword) refuses the bare
         ambiguous 'cancelar' when payment vocab co-occurs.
      2. End-to-end, the reply must NOT confirm cancellation.
    """

    def test_puedo_cancelar_de_una_vez_does_not_confirm_cancellation(
        self, caplog,
    ):
        history = [
            (
                "assistant",
                "Tengo estos datos para tu pedido:\n\n"
                "Nombre: david | Dirección: calle 18 | "
                "Teléfono: 3177000722 | Pago: Nequi\n\n"
                "¿Confirmamos el pedido?",
            ),
            ("user", "si"),
            (
                "assistant",
                "✅ ¡Pedido confirmado! #116B2DF9\n\n"
                "• 1x BARRACUDA - $28.000\n\nSubtotal: $28.000\n"
                "🛵 Domicilio: $7.000\nTotal: $35.000\n"
                "⏱ Tiempo estimado de entrega: 40 a 50 minutos.",
            ),
        ]

        # Defense-in-depth: if cancel_order DID slip through the floor
        # guard, _handle_cancel_order would run. Patch it to a sentinel
        # so we'd see the call AND so no DB write is attempted.
        cancel_handler_calls: List[Dict[str, Any]] = []

        def _spy_cancel(*a, **kw):
            cancel_handler_calls.append({"args": a, "kwargs": kw})
            return {
                "result_kind": "order_cancelled",
                "order": {"display_number": 116, "id": "order-eval-1"},
            }

        out, tools = _run_cs_turn(
            "Puedo cancelar de una vez el pedido?",
            recent_history=history,
            has_recent_cancellable_order=True,
            caplog=caplog,
            extra_patches=[
                patch(
                    "app.services.cs_tools._handle_cancel_order",
                    side_effect=_spy_cancel,
                ),
            ],
        )

        reply = (out or {}).get("message") or ""

        assert not _looks_like_cancellation_confirmation(reply), (
            "Bot confirmed cancellation of an order the customer was "
            "asking how to pay for. Reply: " + repr(reply[:200]) +
            f" — tools: {tools}"
        )

        # The destructive handler must not have run at all (the floor
        # guard refused before reaching it). If this fires, the
        # cancel_keywords payment-veto has regressed.
        assert cancel_handler_calls == [], (
            "_handle_cancel_order was invoked despite payment vocab "
            f"in the user message — floor guard regressed. Tools: {tools}"
        )


# ---------------------------------------------------------------------------
# Explicit human-handoff scenarios
# ---------------------------------------------------------------------------


class TestRequestHumanHandoffTriggers:
    """
    When the customer explicitly asks to talk to a human / asesor /
    agente / persona, the planner must call ``request_human_handoff``.
    Common Colombian phrasings should all trigger — failures here mean
    the prompt signal list at customer_service_agent.py:161 has
    regressed and the bot will refuse to escalate.
    """

    @pytest.mark.parametrize(
        "message",
        [
            "Quisiera hablar con un asesor",
            "Quiero comunicarme con un asesor",
            "Comuníqueme con un humano por favor",
            "Necesito hablar con alguien del personal",
            "Páseme con una persona",
            "Ya no quiero hablar con un bot",
        ],
    )
    def test_explicit_human_request_calls_tool(self, message, caplog):
        # No prior history needed — these are direct requests on their own.
        history = [
            (
                "assistant",
                "Tu pedido va en camino, ya casi llega.",
            ),
        ]
        _out, tools = _run_cs_turn(
            message, recent_history=history, caplog=caplog,
        )
        assert "request_human_handoff" in tools, (
            f"Explicit human-request phrasing {message!r} did not trigger "
            f"request_human_handoff. Tools logged: {tools}. The prompt "
            "rule for the human-handoff tool has regressed."
        )

    def test_bare_gracias_does_not_trigger_handoff(self, caplog):
        # Conservative side: a plain "gracias" after the bot answered
        # must NOT trigger a human handoff. The prompt explicitly tells
        # the planner to skip this case.
        history = [
            (
                "user",
                "a qué hora abren mañana",
            ),
            (
                "assistant",
                "Abrimos mañana de 11am a 11pm.",
            ),
        ]
        _out, tools = _run_cs_turn(
            "gracias", recent_history=history, caplog=caplog,
        )
        assert "request_human_handoff" not in tools, (
            "Bare 'gracias' must not trigger a human handoff. "
            f"Tools logged: {tools}"
        )
