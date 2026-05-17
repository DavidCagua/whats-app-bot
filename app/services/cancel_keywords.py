"""
Shared deterministic guard for destructive cancel/abandon intents.

Both the customer-service planner (CANCEL_ORDER, deletes a placed order) and
the order-flow executor (ABANDON_CART, wipes the in-progress cart) gate the
LLM-emitted intent on this matcher. The model has hallucinated cancel intent
on bare affirmations / clarifications in production — incidents:

  - 2026-05-04 Biela / 3108069647: customer said "Si\\nGracias" right after
    PLACE_ORDER → CS planner emitted CANCEL_ORDER → order #6A8D5250 deleted.
  - 2026-05-06 Biela / 3137112249: customer said "Hamburguesa" mid-checkout
    → order planner emitted ABANDON_CART → cart wiped, sale lost.

Match policy: accent-insensitive, case-insensitive. Single tokens match as
whole words (so "anu lar" doesn't match "anular"); multi-word phrases match
as a normalized substring.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional


# Ambiguous bare cancelar-family forms. In Colombia "cancelar" also means
# "pagar" ("cancelar la cuenta", "puedo cancelar de una vez?"). Accept these
# only when no payment vocabulary co-occurs in the message (see veto below).
_AMBIGUOUS_CANCEL_KEYWORDS: tuple[str, ...] = (
    "cancela", "cancelar",
    "cancelo", "cancele", "cancelen",
    "cancelacion",
    "cancelado", "cancelados", "cancelada",
    "cancel",  # English; also slang "cancelar" anglicism.
)

# Unambiguous cancel verbs — never mean "pay" in Colombian Spanish.
# - anular family: only ever means cancel/void.
# - clitic forms (cancélalo / cancélala): pronoun glued to verb makes the
#   destructive intent explicit.
# - phrasal forms with a direct object: "borra el pedido", "no quiero el
#   pedido", etc.
_UNAMBIGUOUS_CANCEL_KEYWORDS: tuple[str, ...] = (
    # cancelar with attached object pronoun
    "cancelalo", "cancelala", "cancelalos", "cancelalas",
    "cancelarlo", "cancelarla",
    "celenlo",
    # anular family (always means cancel)
    "anula", "anulalo", "anulala", "anulalos", "anulalas",
    "anular", "anularlo", "anularla",
    "anulo", "anulen",
    "anulada", "anulado",
    # destructive verbs scoped to "el/la/mi pedido / orden"
    "borra el pedido", "borrar el pedido", "borralo",
    "elimina el pedido", "eliminar el pedido",
    "descarta", "descartar",
    "no quiero el pedido", "no quiero la orden", "no quiero mi pedido",
    "ya no quiero el pedido", "ya no quiero la orden",
    "ya no quiero pedir", "ya no quiero pedir nada",
    "deja el pedido", "dejalo asi mejor no", "olvidalo", "olvidate",
    "mejor no", "mejor nada", "mejor nada gracias",
    "borrar todo", "cancelar todo",
)

# Backward-compat alias — some callers import the full set.
CANCEL_KEYWORDS: tuple[str, ...] = (
    _AMBIGUOUS_CANCEL_KEYWORDS + _UNAMBIGUOUS_CANCEL_KEYWORDS
)

# Payment co-occurrence vocabulary. When any of these appear alongside an
# ambiguous cancelar form, the message is a payment question, not a cancel
# request. Same list the upstream planner rule uses (kept in sync with
# customer_service_agent.py "AMBIGÜEDAD COLOMBIANA" section).
_PAYMENT_VETO_TOKENS: frozenset[str] = frozenset({
    "pago", "pagar", "pagarla", "pagarlo", "paga", "pagamos", "pagado",
    "efectivo", "nequi", "daviplata", "tarjeta", "transferencia",
    "transferir", "contraentrega", "domiciliario", "cuenta",
})

_PAYMENT_VETO_PHRASES: tuple[str, ...] = (
    "al domiciliario", "de una vez", "la cuenta", "antes de que llegue",
    "antes de recibir", "ya pague", "pago hecho",
    "numero de nequi", "llave bre",
)


def _has_payment_co_occurrence(cleaned: str, tokens: set[str]) -> bool:
    if tokens & _PAYMENT_VETO_TOKENS:
        return True
    for phrase in _PAYMENT_VETO_PHRASES:
        if phrase in cleaned:
            return True
    return False


def has_explicit_cancel_keyword(message: Optional[str]) -> bool:
    """
    Return True iff ``message`` contains an explicit cancel verb or phrase.
    Used as a hard precondition for any destructive cancel/abandon intent.

    The bare cancelar-family forms ("cancelar", "cancela", "cancelo") are
    ambiguous with the Colombian "pagar" sense. They count as an explicit
    cancel keyword ONLY when no payment vocabulary co-occurs in the same
    message. Unambiguous forms (anular family, clitic forms, phrasal
    "no quiero el pedido", etc.) always count.
    """
    if not message:
        return False
    nfkd = unicodedata.normalize("NFD", message.lower())
    cleaned = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return False
    tokens = set(cleaned.split())

    # Unambiguous forms always count.
    for kw in _UNAMBIGUOUS_CANCEL_KEYWORDS:
        if " " in kw:
            if kw in cleaned:
                return True
        else:
            if kw in tokens:
                return True

    # Ambiguous forms count only without payment co-occurrence.
    if _has_payment_co_occurrence(cleaned, tokens):
        return False
    for kw in _AMBIGUOUS_CANCEL_KEYWORDS:
        if kw in tokens:
            return True
    return False
