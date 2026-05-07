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


CANCEL_KEYWORDS: tuple[str, ...] = (
    # cancelar (+ imperative + clitic forms)
    "cancela", "cancelalo", "cancelala", "cancelalos", "cancelalas",
    "cancelar", "cancelarlo", "cancelarla",
    "cancelo", "cancele", "cancelen", "celenlo",
    "cancelacion",
    "cancelado", "cancelados", "cancelada",
    # anular (+ imperative + clitic forms)
    "anula", "anulalo", "anulala", "anulalos", "anulalas",
    "anular", "anularlo", "anularla",
    "anulo", "anulen",
    "anulada", "anulado",
    # English form sometimes used
    "cancel",
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


def has_explicit_cancel_keyword(message: Optional[str]) -> bool:
    """
    Return True iff ``message`` contains an explicit cancel verb or phrase.
    Used as a hard precondition for any destructive cancel/abandon intent.
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
    for kw in CANCEL_KEYWORDS:
        if " " in kw:
            if kw in cleaned:
                return True
        else:
            if kw in tokens:
                return True
    return False
