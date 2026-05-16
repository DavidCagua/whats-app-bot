"""
Deterministic pre-classifier for cart-mutation intents.

Detects user message patterns that map unambiguously to a set_cart_items
call. When a confident match is produced, the order agent bypasses the
planner LLM for the cart-mutation decision and calls set_cart_items
directly with the computed target list. Other messages fall through to
the normal planner path.

The classifier exists to lift the reliability of the patterns that flap
under the mini LLM — full multi-product restatement and single-product
quantity correction with totals claim. Both have unambiguous Spanish
shapes the model handles probabilistically; deterministic regex makes
them 100% on the patterns we cover.

Design contract:
- Favor false negatives over false positives. When in doubt, return None
  and let the LLM handle it. Silent miscarts are far worse than letting
  the LLM run.
- Only resolve product names against the CURRENT CART. Never resolve
  against the catalog here — that's set_cart_items' job. If a fragment
  doesn't substring-match exactly one cart line, fall through.
- Never emit an empty items list. set_cart_items refuses that anyway.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Spanish number words for 1-10 (covers practical order sizes).
_NUM_WORDS = {
    "un": 1, "una": 1, "uno": 1,
    "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
}


# Restatement openers — match at the very start of the message. Order
# matters (longest first) so "el pedido es" wins over "es".
_RESTATEMENT_OPENERS = [
    "el pedido es",
    "lo que quiero es",
    "lo que pido es",
    "son",
    "es",
]


# Single-product quantity-correction openers.
_PARTIAL_OPENERS = [
    "solo son",
    "sólo son",
    "que sean",
    "déjame con",
    "dejame con",
    "déjamelo en",
    "dejamelo en",
    "solo",
    "sólo",
]


# Decrement / removal openers. "quita N X" → decrement by N. "quita la X" /
# "elimina X" / "saca X" / "ya no quiero X" → remove entirely.
_REMOVE_OPENERS = [
    "quitame",
    "quítame",
    "quita",
    "elimina",
    "elimíname",
    "elimina la",
    "elimina el",
    "saca la",
    "saca el",
    "saca",
    "borra la",
    "borra el",
    "borra",
    "ya no quiero",
]


# "quita la X" — full removal markers between the verb and product name.
_FULL_REMOVE_DETERMINERS = {"la", "el", "los", "las"}


def _parse_quantity(token: str) -> Optional[int]:
    """Return positive int from '2', 'dos', 'TRES', etc. Otherwise None."""
    if not token:
        return None
    t = token.strip().lower()
    if t in _NUM_WORDS:
        return _NUM_WORDS[t]
    try:
        n = int(t)
    except ValueError:
        return None
    return n if 1 <= n <= 99 else None


def _strip_trailing_totals_claim(text: str) -> str:
    """Drop trailing ', solo dos en total' / 'solo dos' / 'solo dos hamburguesas'
    fragments that don't carry product info but might confuse splitting."""
    cleaned = re.sub(
        r",?\s*(solo|sólo)\s+\w+(\s+en\s+total)?(\s+\w+)?\s*\.?$",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    )
    return cleaned.strip().rstrip(",.")


def _split_items(text: str) -> List[str]:
    """Split 'N X y M Y, P Z' into ['N X', 'M Y', 'P Z'].

    Normalizes ' y ' → ', ' first so a single split handles both separators.
    """
    normalized = re.sub(r"\s+y\s+", ", ", text, flags=re.IGNORECASE)
    return [s.strip() for s in normalized.split(",") if s.strip()]


def _parse_item_fragment(fragment: str) -> Optional[Tuple[int, str]]:
    """Parse 'N PRODUCT_NAME' (e.g. '1 al pastor', '2 Mexican burger').

    Returns (qty, name) or None if the fragment doesn't start with a
    parseable quantity. Product name is whatever follows the quantity,
    stripped.
    """
    parts = fragment.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None
    qty = _parse_quantity(parts[0])
    if qty is None:
        return None
    name = parts[1].strip().strip(".,;")
    return (qty, name) if name else None


def _resolve_in_cart(
    name_fragment: str,
    cart_items: List[Dict],
) -> Optional[Dict]:
    """Substring-match a product-name fragment against cart lines.

    Returns the cart item ONLY if exactly one cart line matches.
    No match or multiple matches → None (the classifier falls through;
    the LLM gets a chance to disambiguate).
    """
    if not name_fragment:
        return None
    needle = name_fragment.lower().strip()
    matches = [
        it for it in cart_items
        if needle in (it.get("name") or "").lower()
    ]
    if len(matches) == 1:
        return matches[0]
    # Try the reverse direction: the cart name being a substring of
    # the fragment ("Mexican burger" fragment, cart has "MEXICAN BURGER" —
    # already handled above; but also "mexicanas" fragment, cart has
    # "MEXICAN" — handle by token-prefix match).
    first_token = needle.split()[0] if needle.split() else ""
    if first_token and len(first_token) >= 4:
        # 4 chars min so we don't false-match on "es", "una", etc.
        token_matches = [
            it for it in cart_items
            if (it.get("name") or "").lower().split()
            and (it.get("name") or "").lower().split()[0].startswith(first_token[:4])
        ]
        if len(token_matches) == 1:
            return token_matches[0]
    return None


def classify_cart_mutation(
    message: str,
    cart_items: List[Dict],
) -> Optional[List[Dict]]:
    """Return a target items list for set_cart_items, or None.

    Returns a list shaped like [{"product_id": ..., "product_name": ...,
    "quantity": N, "notes": ...}, ...] suitable for set_cart_items.invoke().
    Notes from existing cart lines are preserved.

    Patterns covered:
    - Multi-product restatement: starts with "es" / "son" / "el pedido es" /
      "lo que quiero es", followed by 2+ "N PRODUCT" segments joined with
      "y" or ",".
    - Single-product correction: "solo son N X", "solo una X", "que sean N X",
      where X is currently in the cart. The result keeps every other cart
      item at its current quantity.

    Returns None when:
    - No pattern opener matches.
    - Parsed item count is < 1 (restatement) or != 1 (partial).
    - Any named product can't be resolved against the cart (for partial)
      or against the cart on a single line (for restatement, when the
      product is already in cart we use that line's price/id/notes).
    - Cart resolution is ambiguous (multiple lines with the same name).
    """
    if not message or not isinstance(message, str):
        return None
    msg = message.strip()
    if not msg:
        return None
    msg_lower = msg.lower()

    # ── Removal / decrement ─────────────────────────────────────────
    # "quita la X" / "elimina X" → drop X entirely from the cart.
    # "quita N X" → decrement X by N (drop if N >= current_qty).
    # In both cases, every OTHER cart line is preserved with its
    # current quantity (set_cart_items would otherwise wipe them).
    for opener in _REMOVE_OPENERS:
        if not msg_lower.startswith(opener + " "):
            continue
        rest = msg[len(opener):].strip().strip(".,;!?")
        if not rest:
            continue
        # Parse "N X" (decrement) vs "<determiner> X" (full removal) vs "X" (full).
        tokens = rest.split(maxsplit=1)
        decrement_qty: Optional[int] = None
        product_fragment = rest
        if len(tokens) >= 2:
            head, tail = tokens[0], tokens[1]
            qty_guess = _parse_quantity(head)
            if qty_guess is not None:
                decrement_qty = qty_guess
                product_fragment = tail
            elif head.lower() in _FULL_REMOVE_DETERMINERS:
                product_fragment = tail
            # else: head is the start of the product name; leave product_fragment=rest
        product_fragment = product_fragment.strip().strip(".,;!?")
        if not product_fragment:
            continue
        target = _resolve_in_cart(product_fragment, cart_items)
        if target is None:
            # Product not in cart or ambiguous → fall through to LLM.
            logger.info(
                "[CART_INTENT] removal opener=%r matched but product %r not "
                "resolved in cart; falling through",
                opener, product_fragment,
            )
            return None
        # Build the items list: every OTHER cart line at its current
        # qty; the target either omitted (full removal) or decremented.
        items: List[Dict] = []
        for it in cart_items:
            if it is target:
                if decrement_qty is None:
                    # Full removal — omit from items.
                    continue
                current = int(it.get("quantity", 0) or 0)
                new_qty = current - decrement_qty
                if new_qty <= 0:
                    # Decrement equals or exceeds current — full removal.
                    continue
                new_item: Dict = {
                    "product_id": it.get("product_id") or "",
                    "product_name": it.get("name") or product_fragment,
                    "quantity": new_qty,
                }
                if it.get("notes"):
                    new_item["notes"] = it["notes"]
                items.append(new_item)
            else:
                keep: Dict = {
                    "product_id": it.get("product_id") or "",
                    "product_name": it.get("name") or "",
                    "quantity": int(it.get("quantity", 0) or 0),
                }
                if it.get("notes"):
                    keep["notes"] = it["notes"]
                items.append(keep)
        if not items:
            # The only cart line was removed entirely. set_cart_items
            # refuses empty lists, so emit None — the planner can handle
            # the "clear cart" path as chat.
            logger.info(
                "[CART_INTENT] removal would empty the cart (only line "
                "removed); falling through to LLM for chat-level handling"
            )
            return None
        action = "decrement" if decrement_qty is not None else "full_removal"
        logger.info(
            "[CART_INTENT] removal matched: opener=%r action=%s product=%r "
            "(remaining lines=%d)",
            opener, action,
            (target.get("name") or product_fragment),
            len(items),
        )
        return items

    # ── Partial single-product correction ────────────────────────────
    # Try longest openers first so "solo son N" doesn't get split by "solo".
    for opener in _PARTIAL_OPENERS:
        if msg_lower.startswith(opener + " "):
            rest = msg[len(opener):].strip()
            parsed = _parse_item_fragment(rest)
            if parsed is None:
                # e.g. "solo eso" — not a quantity correction.
                continue
            qty, name = parsed
            existing = _resolve_in_cart(name, cart_items)
            if existing is None:
                # Product not in cart or ambiguous — fall through to LLM.
                logger.info(
                    "[CART_INTENT] partial-correction matched opener=%r "
                    "but product %r not resolved in cart; falling through",
                    opener, name,
                )
                return None
            # Build the items list: the modified product + every OTHER
            # cart line at its current qty (preserves the rest of the cart).
            items: List[Dict] = []
            for it in cart_items:
                if it is existing:
                    new_item: Dict = {
                        "product_id": it.get("product_id") or "",
                        "product_name": it.get("name") or name,
                        "quantity": qty,
                    }
                    if it.get("notes"):
                        new_item["notes"] = it["notes"]
                    items.append(new_item)
                else:
                    keep: Dict = {
                        "product_id": it.get("product_id") or "",
                        "product_name": it.get("name") or "",
                        "quantity": int(it.get("quantity", 0) or 0),
                    }
                    if it.get("notes"):
                        keep["notes"] = it["notes"]
                    items.append(keep)
            logger.info(
                "[CART_INTENT] partial-correction matched: opener=%r "
                "product=%r qty=%d (preserving %d other line(s))",
                opener, name, qty, len(cart_items) - 1,
            )
            return items

    # ── Multi-product restatement ────────────────────────────────────
    for opener in _RESTATEMENT_OPENERS:
        if msg_lower.startswith(opener + " "):
            rest = msg[len(opener):].strip()
            # Drop trailing totals claim ("...solo dos en total") so it
            # doesn't pollute item parsing.
            rest_clean = _strip_trailing_totals_claim(rest)
            fragments = _split_items(rest_clean)
            parsed_items: List[Tuple[int, str]] = []
            for frag in fragments:
                p = _parse_item_fragment(frag)
                if p is None:
                    # If any fragment fails to parse, we can't be confident
                    # the message is a clean restatement — bail.
                    parsed_items = []
                    break
                parsed_items.append(p)
            # Restatement requires at least 2 items; one item is a partial
            # correction (handled above) or an add (handled by the LLM).
            if len(parsed_items) < 2:
                continue
            # Build items list. For each named product, try to resolve
            # against the cart to preserve price/id/notes; new products
            # are passed by name for set_cart_items to resolve.
            items = []
            for qty, name in parsed_items:
                existing = _resolve_in_cart(name, cart_items)
                if existing is not None:
                    new_item = {
                        "product_id": existing.get("product_id") or "",
                        "product_name": existing.get("name") or name,
                        "quantity": qty,
                    }
                    if existing.get("notes"):
                        new_item["notes"] = existing["notes"]
                    items.append(new_item)
                else:
                    items.append({
                        "product_name": name,
                        "quantity": qty,
                    })
            logger.info(
                "[CART_INTENT] restatement matched: opener=%r items=%s",
                opener,
                [(it["quantity"], it.get("product_name") or it.get("product_id")) for it in items],
            )
            return items

    return None
