"""
Floor guard for destructive cancel intents.

`has_explicit_cancel_keyword` is the deterministic backstop that
cs_tools.cancel_order checks before deleting an order. It exists
specifically to catch model hallucinations — the LLM has emitted
cancel_order on bare affirmations ("Si\\nGracias") and on payment
questions where the customer used "cancelar" in the Colombian
"pagar" sense (Nicolás Bolaños, May 17 2026 prod; local repro
"Puedo cancelar de una vez el pedido?" May 17 2026).

Tests document:
  - Unambiguous cancel forms (anular family, clitic forms, phrasal
    "no quiero el pedido") always pass.
  - The bare cancelar family passes only when no payment vocab
    co-occurs.
  - Non-cancel messages (greetings, affirmations) never pass.
"""

from __future__ import annotations

import pytest

from app.services.cancel_keywords import (
    CANCEL_KEYWORDS,
    has_explicit_cancel_keyword,
)


class TestUnambiguousFormsAlwaysPass:
    """Forms that never mean 'pay' in Colombian Spanish."""

    @pytest.mark.parametrize(
        "msg",
        [
            "anula el pedido",
            "anúlalo",
            "anular el pedido por favor",
            "cancélalo",
            "cancélala por favor",
            "borralo",
            "borra el pedido",
            "no quiero el pedido",
            "ya no quiero el pedido",
            "olvídalo",
            "mejor no",
            "cancelar todo",
        ],
    )
    def test_passes(self, msg):
        assert has_explicit_cancel_keyword(msg) is True, (
            f"Unambiguous cancel form failed the guard: {msg!r}"
        )


class TestAmbiguousFormsPassWithoutPaymentVocab:
    """Bare cancelar-family — fine when no payment co-occurrence."""

    @pytest.mark.parametrize(
        "msg",
        [
            "cancela",
            "cancelar",
            "cancela mi pedido",
            "cancelar el pedido por favor",
            "quiero cancelar",
        ],
    )
    def test_passes(self, msg):
        assert has_explicit_cancel_keyword(msg) is True


class TestAmbiguousFormsRejectedWithPaymentVocab:
    """
    Regression: 'Puedo cancelar de una vez el pedido?' (May 17 2026
    local repro) — bot deleted order #116B2DF9 because the floor
    accepted bare 'cancelar' as sufficient. With the payment-veto,
    the bare form is rejected when 'de una vez' co-occurs.
    """

    @pytest.mark.parametrize(
        "msg",
        [
            "Puedo cancelar de una vez el pedido?",
            "puedo cancelar la cuenta?",
            "cancelar de una vez",
            "le cancelo al domiciliario",
            "cancelo con Nequi?",
            "cancelar contraentrega",
            "puedo cancelar antes de que llegue",
            "cancelo con tarjeta?",
            "voy a cancelar la cuenta",
            "cancelar el domicilio en efectivo",
        ],
    )
    def test_rejected(self, msg):
        assert has_explicit_cancel_keyword(msg) is False, (
            f"Ambiguous form passed despite payment vocab: {msg!r} — "
            "the floor would let cs_tools.cancel_order delete an order "
            "the customer was asking how to pay for."
        )


class TestUnambiguousFormsBeatPaymentVeto:
    """
    Veto applies only to AMBIGUOUS forms. If the message has both an
    unambiguous cancel verb AND payment vocab, the cancel intent stands.
    Example: 'anula el pedido, prefiero pagar otra cosa' → still cancel.
    """

    @pytest.mark.parametrize(
        "msg",
        [
            "anula el pedido, prefiero pagar otra cosa",
            "ya no quiero el pedido, no me alcanza para Nequi",
            "cancélalo, no tengo efectivo",
        ],
    )
    def test_passes(self, msg):
        assert has_explicit_cancel_keyword(msg) is True


class TestNonCancelMessages:
    """Prior incidents and benign affirmations — never pass."""

    @pytest.mark.parametrize(
        "msg",
        [
            "",
            None,
            "Si\nGracias",  # 2026-05-04 incident — bare affirmation.
            "hola buenas",
            "una hamburguesa por favor",
            "cuanto es el domicilio",
            "puedes mandar el número de nequi",
        ],
    )
    def test_rejected(self, msg):
        assert has_explicit_cancel_keyword(msg) is False


class TestPublicAPIShape:
    """CANCEL_KEYWORDS is exported for compatibility — confirm it
    still contains both ambiguous and unambiguous forms (some callers
    iterate the full set)."""

    def test_canonical_keywords_present(self):
        assert "cancelar" in CANCEL_KEYWORDS  # ambiguous
        assert "anular" in CANCEL_KEYWORDS    # unambiguous
        assert "cancelalo" in CANCEL_KEYWORDS  # clitic
        assert "no quiero el pedido" in CANCEL_KEYWORDS  # phrasal
