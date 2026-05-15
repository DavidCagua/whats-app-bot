"""Payment configuration helpers.

Reads the per-method context structure stored in ``businesses.settings``:

    payment_methods:
      - { name: "Efectivo",      contexts: ["delivery_on_fulfillment", "on_site_on_fulfillment"] }
      - { name: "Tarjeta",       contexts: ["on_site_on_fulfillment"] }
      - { name: "Nequi",         contexts: ["delivery_pay_now", "delivery_on_fulfillment",
                                            "on_site_pay_now", "on_site_on_fulfillment"] }
      - { name: "Transferencia", contexts: ["delivery_pay_now", "on_site_pay_now"] }
      - { name: "Llave BreB",    contexts: ["delivery_pay_now", "on_site_pay_now"] }
    payment_destinations:
      Nequi: "300 123 4567 (a nombre de Biela SAS)"
      Transferencia: "Bancolombia 123-456789-00 (ahorros, Biela SAS)"

Context is the cartesian product of (fulfillment × timing):
    delivery_pay_now, delivery_on_fulfillment,
    on_site_pay_now,  on_site_on_fulfillment

on_site covers both pickup and dine-in.
"""

from __future__ import annotations

from typing import Dict, List, Optional


# ── Context constants ─────────────────────────────────────────────────────────

CONTEXT_DELIVERY_PAY_NOW = "delivery_pay_now"
CONTEXT_DELIVERY_ON_FULFILLMENT = "delivery_on_fulfillment"
CONTEXT_ON_SITE_PAY_NOW = "on_site_pay_now"
CONTEXT_ON_SITE_ON_FULFILLMENT = "on_site_on_fulfillment"

ALL_CONTEXTS: List[str] = [
    CONTEXT_DELIVERY_PAY_NOW,
    CONTEXT_DELIVERY_ON_FULFILLMENT,
    CONTEXT_ON_SITE_PAY_NOW,
    CONTEXT_ON_SITE_ON_FULFILLMENT,
]

_DELIVERY_CONTEXTS = (CONTEXT_DELIVERY_PAY_NOW, CONTEXT_DELIVERY_ON_FULFILLMENT)
_ON_SITE_CONTEXTS = (CONTEXT_ON_SITE_PAY_NOW, CONTEXT_ON_SITE_ON_FULFILLMENT)


def contexts_for_fulfillment(fulfillment_type: Optional[str]) -> List[str]:
    """Map a fulfillment_type to the payment contexts that apply.

    - ``"delivery"`` → both delivery contexts
    - ``"pickup"`` / ``"dine_in"`` / ``"on_site"`` → both on-site contexts
    - anything else / None → all four (caller doesn't know fulfillment yet)
    """
    if fulfillment_type == "delivery":
        return list(_DELIVERY_CONTEXTS)
    if fulfillment_type in ("pickup", "dine_in", "on_site"):
        return list(_ON_SITE_CONTEXTS)
    return list(ALL_CONTEXTS)


# ── Readers ───────────────────────────────────────────────────────────────────


def get_payment_methods(settings: Optional[Dict]) -> List[Dict]:
    """Return the raw payment_methods list from settings, defensively normalized.

    Each entry is expected to be ``{"name": str, "contexts": List[str]}``. Entries
    missing a name or contexts list are dropped.
    """
    raw = (settings or {}).get("payment_methods") or []
    out: List[Dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("name") or "").strip()
        contexts = entry.get("contexts") or []
        if not name or not isinstance(contexts, list):
            continue
        out.append({"name": name, "contexts": [str(c) for c in contexts if c]})
    return out


def get_payment_methods_for(context: str, settings: Optional[Dict]) -> List[str]:
    """Return the names of payment methods accepted in the given context.

    Order follows the order of the methods in settings. Method names are
    surfaced verbatim (no canonicalization).
    """
    return [m["name"] for m in get_payment_methods(settings) if context in m["contexts"]]


def get_payment_methods_for_any(
    contexts: List[str], settings: Optional[Dict]
) -> List[str]:
    """Names of methods accepted in ANY of the given contexts. Deduplicated."""
    wanted = set(contexts)
    seen: set = set()
    out: List[str] = []
    for m in get_payment_methods(settings):
        if not wanted.intersection(m["contexts"]):
            continue
        if m["name"] in seen:
            continue
        seen.add(m["name"])
        out.append(m["name"])
    return out


def get_payment_destination(method: str, settings: Optional[Dict]) -> Optional[str]:
    """Return the destination string for a method (Nequi number, etc.), or None.

    Lookup is case-insensitive on the method name to be forgiving of LLM
    capitalization drift.
    """
    if not method:
        return None
    destinations = (settings or {}).get("payment_destinations") or {}
    if not isinstance(destinations, dict):
        return None
    target = method.strip().casefold()
    for key, value in destinations.items():
        if not isinstance(key, str):
            continue
        if key.strip().casefold() == target:
            v = (value or "").strip() if isinstance(value, str) else ""
            return v or None
    return None


def is_method_valid_for_context(
    method: str, context: str, settings: Optional[Dict]
) -> bool:
    """Whether ``method`` is configured to accept payments in ``context``.

    Case-insensitive on method name.
    """
    if not method or not context:
        return False
    target = method.strip().casefold()
    for m in get_payment_methods(settings):
        if m["name"].strip().casefold() == target and context in m["contexts"]:
            return True
    return False


def is_method_valid_for_fulfillment(
    method: str, fulfillment_type: Optional[str], settings: Optional[Dict]
) -> bool:
    """Whether ``method`` is valid for ANY timing within ``fulfillment_type``.

    Used by ``submit_delivery_info`` to reject combinations like
    ``Tarjeta + delivery`` that can never succeed regardless of when the
    customer pays. Empty / unknown ``fulfillment_type`` is treated as
    permissive (caller hasn't decided yet) — defers validation until the
    fulfillment is set.
    """
    if not method:
        return False
    if not fulfillment_type:
        return True
    contexts = contexts_for_fulfillment(fulfillment_type)
    return any(is_method_valid_for_context(method, c, settings) for c in contexts)
