"""
Promotion matcher and applicator.

Two surfaces:

- `list_active_promos(business_id, when=None)` — read API used by the
  customer service agent to answer "qué promos hay". Filters by
  schedule (day-of-week, time window, date window).

- `match_and_apply(business_id, cart_items, when=None)` — pricing API
  called by `product_order_service.place_order` when an order is
  written. Decides which promo(s) apply, returns line-level pricing
  decisions and a list of applications for the audit table.

Matching rules (Phase 1):

- A promo's components must ALL be present in the cart in at least the
  required quantity for the promo to "apply once". If the cart has more,
  the promo applies multiple times (greedy multi-apply).

- When multiple promos qualify, we pick the one that yields the BIGGEST
  per-application discount. Don't stack. Apply it as many times as
  possible, then re-run the matcher on what's left in the cart.

- Bindings the agent set at add-time (`promotion_id`/`promo_group_id`
  on cart items) are honored as-is: those items get the promo's price
  and aren't re-matched. Unbound items go through the matcher fresh.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import joinedload

from ..database.models import Promotion, get_db_session


logger = logging.getLogger(__name__)


PRICING_FIXED_PRICE = "fixed_price"
PRICING_DISCOUNT_AMOUNT = "discount_amount"
PRICING_DISCOUNT_PCT = "discount_pct"


def list_active_promos(
    business_id: str,
    when: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Return active promos for a business that are valid at `when`
    (default: now, UTC). Each item is `Promotion.to_dict()` shape.
    """
    if not business_id:
        return []
    when = when or datetime.now(timezone.utc)

    session = get_db_session()
    try:
        rows = (
            session.query(Promotion)
            .options(joinedload(Promotion.components))
            .filter(
                Promotion.business_id == uuid.UUID(business_id),
                Promotion.is_active.is_(True),
            )
            .all()
        )
        valid = [r for r in rows if _schedule_matches(r, when)]
        return [r.to_dict() for r in valid]
    except Exception as exc:
        logger.error("[PROMO] list_active_promos failed: %s", exc, exc_info=True)
        return []
    finally:
        session.close()


def get_promotion(business_id: str, promotion_id: str) -> Optional[Dict[str, Any]]:
    """Fetch one promo by id, scoped to business. Returns None if absent."""
    if not business_id or not promotion_id:
        return None
    session = get_db_session()
    try:
        row = (
            session.query(Promotion)
            .options(joinedload(Promotion.components))
            .filter(
                Promotion.id == uuid.UUID(promotion_id),
                Promotion.business_id == uuid.UUID(business_id),
            )
            .first()
        )
        return row.to_dict() if row else None
    except Exception as exc:
        logger.error("[PROMO] get_promotion failed: %s", exc, exc_info=True)
        return None
    finally:
        session.close()


def match_and_apply(
    *,
    business_id: str,
    cart_items: List[Dict[str, Any]],
    when: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Decide which promos apply to a cart and return pricing decisions.

    `cart_items` shape (matching what product_order_service.place_order
    builds from the session cart):
        [
          {
            "product_id": str,
            "quantity": int,
            "unit_price": float | Decimal,
            "promotion_id": str | None,    # set by ADD_PROMO_TO_CART
            "promo_group_id": str | None,  # set by ADD_PROMO_TO_CART
          },
          ...
        ]

    Returns:
        {
          "items": [<cart_item with line_total set>...],
          "subtotal_before_promos": float,
          "promo_discount_total": float,
          "subtotal_after_promos": float,
          "applications": [
            {
              "promotion_id": str,
              "promotion_name": str,
              "pricing_mode": str,
              "discount_applied": float,
              "promo_group_id": str,    # tags the consumed lines
              "consumed": [{"product_id", "quantity"}],
            }, ...
          ],
        }
    """
    when = when or datetime.now(timezone.utc)

    # Pre-compute base line totals; we'll re-write them as promos consume items.
    items = [_normalize_item(it) for it in cart_items]
    subtotal_before = sum(it["unit_price"] * it["quantity"] for it in items)

    applications: List[Dict[str, Any]] = []

    # Step 1: honor bindings the agent already attached at add-time.
    # Group bound items by (promotion_id, promo_group_id) and price each
    # group as one promo application.
    bound_groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for it in items:
        if it["promotion_id"] and it["promo_group_id"]:
            bound_groups[(it["promotion_id"], it["promo_group_id"])].append(it)

    for (promo_id, group_id), group_items in bound_groups.items():
        promo = get_promotion(business_id, promo_id)
        if not promo or not promo.get("is_active"):
            # Promo got disabled/deleted between add-time and place. Drop the
            # binding from those items so they price at base.
            for it in group_items:
                it["promotion_id"] = None
                it["promo_group_id"] = None
            continue
        # Don't re-validate schedule here: the customer was promised this
        # price when they added it. Honor the commitment.
        app = _apply_promo_to_items(promo, group_items)
        if app:
            applications.append(app)

    # Step 2: matcher runs on the remaining unbound items.
    unbound_items = [it for it in items if not it["promotion_id"]]
    available_promos = list_active_promos(business_id, when=when)
    # Promos with zero components (cart-wide discounts) require special
    # handling — defer those past Phase 1.
    component_promos = [p for p in available_promos if p.get("components")]

    while True:
        best_app, best_promo = _pick_best_application(component_promos, unbound_items)
        if best_app is None:
            break
        applications.append(best_app)
        # Mark consumed items so subsequent rounds don't double-count.
        _consume_for_application(best_promo, unbound_items, best_app)

    # Sum discount.
    promo_discount_total = sum(Decimal(str(a["discount_applied"])) for a in applications)
    subtotal_after = subtotal_before - promo_discount_total

    return {
        "items": items,
        "subtotal_before_promos": float(subtotal_before),
        "promo_discount_total": float(promo_discount_total),
        "subtotal_after_promos": float(subtotal_after),
        "applications": applications,
    }


# ── internals ────────────────────────────────────────────────────────


def _normalize_item(it: Dict[str, Any]) -> Dict[str, Any]:
    """Defensive copy with consistent types and a placeholder line_total."""
    return {
        "product_id": str(it["product_id"]),
        "quantity": int(it.get("quantity") or 0),
        "unit_price": Decimal(str(it.get("unit_price") or 0)),
        "notes": it.get("notes"),
        "promotion_id": (str(it["promotion_id"]) if it.get("promotion_id") else None),
        "promo_group_id": (str(it["promo_group_id"]) if it.get("promo_group_id") else None),
        "line_total": Decimal(str(it.get("unit_price") or 0)) * int(it.get("quantity") or 0),
    }


def _schedule_matches(promo: Promotion, when: datetime) -> bool:
    """True if `promo` is valid at `when` per its schedule columns."""
    when_local = when  # business_timezone handling deferred to Phase 1.5
    today = when_local.date()

    if promo.starts_on and today < (promo.starts_on.date() if isinstance(promo.starts_on, datetime) else promo.starts_on):
        return False
    if promo.ends_on and today > (promo.ends_on.date() if isinstance(promo.ends_on, datetime) else promo.ends_on):
        return False

    if promo.days_of_week:
        # Postgres ISO: 1=Monday..7=Sunday. Python isoweekday() matches.
        if when_local.isoweekday() not in promo.days_of_week:
            return False

    now_t = when_local.time()
    if promo.start_time and now_t < promo.start_time:
        return False
    if promo.end_time and now_t > promo.end_time:
        return False

    return True


def _apply_promo_to_items(
    promo: Dict[str, Any],
    group_items: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Compute discount for an explicit (already-bound) promo group.
    Doesn't validate components — trusts what the agent set at add-time.
    """
    if not group_items:
        return None
    base_total = sum(it["unit_price"] * it["quantity"] for it in group_items)
    discount, mode = _compute_discount(promo, base_total)

    if discount <= 0:
        return None

    # Re-write line_total per item proportionally so receipts add up.
    if mode == PRICING_FIXED_PRICE:
        # Distribute the fixed_price across lines proportional to base.
        fixed_price = Decimal(str(promo["fixed_price"]))
        if base_total > 0:
            for it in group_items:
                share = (it["unit_price"] * it["quantity"]) / base_total
                it["line_total"] = (fixed_price * share).quantize(Decimal("0.01"))
    else:
        # discount_amount / discount_pct — apply proportionally.
        for it in group_items:
            share = (it["unit_price"] * it["quantity"]) / base_total if base_total > 0 else Decimal(0)
            it["line_total"] = (it["unit_price"] * it["quantity"] - discount * share).quantize(Decimal("0.01"))

    promo_group_id = group_items[0]["promo_group_id"]
    return {
        "promotion_id": str(promo["id"]),
        "promotion_name": promo["name"],
        "pricing_mode": mode,
        "discount_applied": float(discount),
        "promo_group_id": promo_group_id,
        "consumed": [{"product_id": it["product_id"], "quantity": it["quantity"]} for it in group_items],
    }


def _pick_best_application(
    candidate_promos: List[Dict[str, Any]],
    unbound_items: List[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Among `candidate_promos`, find the (promo, application) pair that
    yields the largest discount given the current unbound items.
    Returns (None, None) when no promo qualifies.
    """
    best_app: Optional[Dict[str, Any]] = None
    best_promo: Optional[Dict[str, Any]] = None
    best_discount = Decimal(0)

    cart_qty = _cart_qty_map(unbound_items)

    for promo in candidate_promos:
        components = promo.get("components") or []
        if not components or not _components_satisfied(components, cart_qty):
            continue

        # Build the consumed-item slice for ONE application of this promo.
        consumed_items = _slice_consumed_items(components, unbound_items)
        if not consumed_items:
            continue
        base_total = sum(it["unit_price"] * it["quantity"] for it in consumed_items)
        discount, mode = _compute_discount(promo, base_total)

        if discount > best_discount:
            best_discount = discount
            best_promo = promo
            promo_group_id = str(uuid.uuid4())
            best_app = {
                "promotion_id": str(promo["id"]),
                "promotion_name": promo["name"],
                "pricing_mode": mode,
                "discount_applied": float(discount),
                "promo_group_id": promo_group_id,
                "consumed": [{"product_id": it["product_id"], "quantity": it["quantity"]} for it in consumed_items],
                "_consumed_refs": consumed_items,  # internal: for line_total rewrite
                "_base_total": base_total,         # internal
            }

    return best_app, best_promo


def _consume_for_application(
    promo: Dict[str, Any],
    unbound_items: List[Dict[str, Any]],
    app: Dict[str, Any],
) -> None:
    """
    Reduce qty / mark items as bound to record that this application
    consumed them. Re-prices the consumed items proportionally so the
    sum equals the promo price.
    """
    consumed_refs: List[Dict[str, Any]] = app["_consumed_refs"]
    base_total: Decimal = app["_base_total"]
    promo_group_id = app["promo_group_id"]
    mode = app["pricing_mode"]

    if mode == PRICING_FIXED_PRICE:
        fixed_price = Decimal(str(promo["fixed_price"]))
        for it in consumed_refs:
            share = (it["unit_price"] * it["quantity"]) / base_total if base_total > 0 else Decimal(0)
            it["line_total"] = (fixed_price * share).quantize(Decimal("0.01"))
            it["promotion_id"] = str(promo["id"])
            it["promo_group_id"] = promo_group_id
    else:
        discount = Decimal(str(app["discount_applied"]))
        for it in consumed_refs:
            share = (it["unit_price"] * it["quantity"]) / base_total if base_total > 0 else Decimal(0)
            it["line_total"] = (it["unit_price"] * it["quantity"] - discount * share).quantize(Decimal("0.01"))
            it["promotion_id"] = str(promo["id"])
            it["promo_group_id"] = promo_group_id

    # Drop the internal helpers so callers don't see them.
    app.pop("_consumed_refs", None)
    app.pop("_base_total", None)


def _cart_qty_map(items: List[Dict[str, Any]]) -> Dict[str, int]:
    """product_id -> total available qty among items not yet bound to a promo."""
    qty: Dict[str, int] = defaultdict(int)
    for it in items:
        if it["promotion_id"] is None:
            qty[it["product_id"]] += it["quantity"]
    return qty


def _components_satisfied(components: List[Dict[str, Any]], cart_qty: Dict[str, int]) -> bool:
    for c in components:
        if cart_qty.get(str(c["product_id"]), 0) < int(c["quantity"]):
            return False
    return True


def _slice_consumed_items(
    components: List[Dict[str, Any]],
    unbound_items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Pick which actual cart-line items represent the consumption of ONE
    promo application. We split lines if needed (e.g. cart has 4 burgers
    on one line, promo wants 2 — we split to a 2-line + 2-line so the
    promo's items are independently price-able).

    Returns references into `unbound_items` (mutating list).
    """
    needed: Dict[str, int] = {str(c["product_id"]): int(c["quantity"]) for c in components}
    out: List[Dict[str, Any]] = []

    for product_id, qty_needed in needed.items():
        remaining = qty_needed
        for idx, it in enumerate(unbound_items):
            if remaining <= 0:
                break
            if it["promotion_id"] is not None:
                continue
            if it["product_id"] != product_id:
                continue
            if it["quantity"] <= remaining:
                # consume the whole line
                remaining -= it["quantity"]
                out.append(it)
            else:
                # split: keep the leftover as a new line, take the consumed slice
                consumed_slice = dict(it)
                consumed_slice["quantity"] = remaining
                consumed_slice["line_total"] = consumed_slice["unit_price"] * remaining
                it["quantity"] -= remaining
                it["line_total"] = it["unit_price"] * it["quantity"]
                # Insert the slice into the working list so downstream re-pricing
                # sees it as a real item.
                unbound_items.insert(idx, consumed_slice)
                out.append(consumed_slice)
                remaining = 0
        if remaining > 0:
            # Should never happen — _components_satisfied already verified.
            return []
    return out


def _compute_discount(promo: Dict[str, Any], base_total: Decimal) -> Tuple[Decimal, str]:
    """Return (discount_amount, pricing_mode_label)."""
    if promo.get("fixed_price") is not None:
        fixed = Decimal(str(promo["fixed_price"]))
        return (max(Decimal(0), base_total - fixed), PRICING_FIXED_PRICE)
    if promo.get("discount_amount") is not None:
        amt = Decimal(str(promo["discount_amount"]))
        return (min(amt, base_total), PRICING_DISCOUNT_AMOUNT)
    if promo.get("discount_pct") is not None:
        pct = Decimal(str(promo["discount_pct"]))
        return ((base_total * pct / Decimal(100)).quantize(Decimal("0.01")), PRICING_DISCOUNT_PCT)
    return (Decimal(0), "")
