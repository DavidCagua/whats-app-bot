"""
Customer service flow: handlers that back the CS agent's tools.

Read-only by design except for cancel_order — no cart mutation, no
order creation. Each handler returns a small result dict that the
tool wrapper turns into a FINAL or HANDOFF sentinel for the CS
agent's dispatch loop.

Each ``_handle_*`` function is invoked from a ``@tool`` wrapper in
``app/services/cs_tools.py``. The wrapper extracts per-turn context
(wa_id, business_id, business_context, session), calls the handler,
and renders the returned dict into the FINAL/HANDOFF sentinel the
agent dispatch loop understands.

Result dict shape (returned to the calling tool):
    {
      "result_kind": str,
      "success": bool,
      "business_id": str,
      "wa_id": str,
      # plus handler-specific payload fields
    }
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..database import order_lookup_service
from ..database import order_modification_service
from ..database.conversation_agent_service import (
    conversation_agent_service,
    HANDOFF_REASON_DELIVERY,
)
from ..services import business_info_service
from ..services import promotion_service
from ..services.order_eta import estimate_remaining_minutes, PICKUP_RANGE_TEXT
from ..services.order_modification_policy import (
    can_customer_cancel,
    CANCEL_REASON_CUSTOMER_WHATSAPP,
)
from ..services.order_status_machine import InvalidStatusTransition


logger = logging.getLogger(__name__)


# Delivery handoff: when a customer asks for order status this many
# minutes (or more) after placing the order and the order is still
# in-flight, we apologize, hand off to a human, and disable the bot
# for this conversation. Override via env for local testing.
def _delivery_handoff_threshold_min() -> int:
    raw = os.getenv("DELIVERY_HANDOFF_THRESHOLD_MIN")
    if raw is None:
        return 50
    try:
        value = int(raw)
        return value if value > 0 else 50
    except (TypeError, ValueError):
        return 50


# Order statuses that count as "in-flight" — i.e. the customer is
# legitimately worried because the food hasn't arrived (or hasn't been
# picked up) yet. Terminal states (completed, cancelled) never trigger
# a handoff. `ready_for_pickup` is included so a "is my order ready?"
# question >50min after placement still escalates — staff often want
# to know when a customer is running late on a counter pickup.
_IN_FLIGHT_STATUSES = frozenset({
    "pending", "confirmed", "out_for_delivery", "ready_for_pickup",
})


# Result kinds — handlers tag their return dicts so the tool wrapper
# in app/services/cs_tools.py can render the right FINAL/HANDOFF sentinel.
RESULT_KIND_BUSINESS_INFO = "business_info"
RESULT_KIND_INFO_MISSING = "info_missing"
RESULT_KIND_ORDER_STATUS = "order_status"
RESULT_KIND_NO_ORDER = "no_order"
RESULT_KIND_ORDER_HISTORY = "order_history"
RESULT_KIND_ORDER_CANCELLED = "order_cancelled"
RESULT_KIND_CANCEL_NOT_ALLOWED = "cancel_not_allowed"
RESULT_KIND_PROMOS_LIST = "promos_list"
RESULT_KIND_NO_PROMOS = "no_promos"
RESULT_KIND_PROMO_NOT_RESOLVED = "promo_not_resolved"
RESULT_KIND_PROMO_AMBIGUOUS = "promo_ambiguous"
RESULT_KIND_CHAT_FALLBACK = "cs_chat_fallback"
RESULT_KIND_INTERNAL_ERROR = "cs_internal_error"
# Customer asked for order status >= 50 min after placing an in-flight
# order. The agent renders a deterministic apology and disables itself
# for this conversation; only staff can re-enable from the admin console.
RESULT_KIND_DELIVERY_HANDOFF = "delivery_handoff"
# Signal that the agent should hand off mid-turn. The agent's execute()
# translates this into the `handoff` field of its AgentOutput so the
# dispatcher picks up the next agent.
RESULT_KIND_HANDOFF = "cs_handoff"


def _base_result(
    wa_id: str,
    business_id: str,
    result_kind: str,
    success: bool = True,
    **extra: Any,
) -> Dict[str, Any]:
    result = {
        "result_kind": result_kind,
        "success": success,
        "business_id": business_id,
        "wa_id": wa_id,
    }
    result.update(extra)
    return result


def _parse_iso_to_utc(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp string into a tz-aware UTC datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _should_trigger_delivery_handoff(order: Dict[str, Any]) -> bool:
    """
    True iff the order is in-flight AND was placed >= threshold minutes
    ago. Used to escalate "where is my food?" questions to human staff.
    """
    if not order:
        return False
    if order.get("status") not in _IN_FLIGHT_STATUSES:
        return False
    created_at = _parse_iso_to_utc(order.get("created_at"))
    if created_at is None:
        return False
    elapsed_min = (datetime.now(timezone.utc) - created_at).total_seconds() / 60.0
    return elapsed_min >= _delivery_handoff_threshold_min()


def _session_is_pickup(session: Optional[Dict[str, Any]]) -> bool:
    """True iff the customer's in-flight session is in pickup mode."""
    if not isinstance(session, dict):
        return False
    order_context = session.get("order_context") or {}
    raw = (order_context.get("fulfillment_type") or "").strip().lower()
    return raw == "pickup"


def _settings_from_context(
    business_context: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return ``business.settings`` dict from a turn business_context, or None."""
    if not business_context:
        return None
    biz = business_context.get("business") or {}
    settings = biz.get("settings")
    return settings if isinstance(settings, dict) else None


def _clean_order_for_response(
    order: Dict[str, Any],
    business_settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Strip internal-only fields before passing to the response generator.

    Adds `eta_minutes`: an approximate remaining wait time in minutes,
    derived from the current state, (for `confirmed`) the elapsed time
    since `confirmed_at`, and the active delivery-ETA override on the
    business settings (delivery orders only — pickup is unaffected).
    None for terminal states. The agent surfaces this only when the
    customer asks about timing.
    """
    return {
        "id": order.get("id"),
        "status": order.get("status"),
        "total_amount": order.get("total_amount"),
        "delivery_address": order.get("delivery_address"),
        "payment_method": order.get("payment_method"),
        "notes": order.get("notes"),
        "cancellation_reason": order.get("cancellation_reason"),
        "created_at": order.get("created_at"),
        "eta_minutes": estimate_remaining_minutes(
            {
                "status": order.get("status"),
                "confirmed_at": order.get("confirmed_at"),
                "fulfillment_type": order.get("fulfillment_type"),
            },
            business_settings=business_settings,
        ),
        "items": order.get("items") or [],
    }


# ── Handlers (invoked by app/services/cs_tools.py tool wrappers) ──────

def _handle_business_info(
    wa_id: str,
    business_id: str,
    business_context: Optional[Dict[str, Any]],
    params: Dict[str, Any],
    session: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    field = (params.get("field") or "").strip().lower()
    if not field:
        return _base_result(
            wa_id, business_id,
            RESULT_KIND_INFO_MISSING,
            field=None,
            available_fields=business_info_service.supported_fields(),
        )

    # Per-order ETA swap: "cuánto se demora la entrega?" is policy by
    # default, but if the customer has a placed order with a meaningful
    # remaining time, the per-order answer is more useful than a generic
    # "40 a 50 minutos". Returns the order_status path so the existing
    # ETA-aware response template handles it.
    if field == "delivery_time":
        latest = order_lookup_service.get_latest_order(wa_id, business_id)
        if latest:
            settings = _settings_from_context(business_context)
            if estimate_remaining_minutes(
                {
                    "status": latest.get("status"),
                    "confirmed_at": latest.get("confirmed_at"),
                    "fulfillment_type": latest.get("fulfillment_type"),
                },
                business_settings=settings,
            ) is not None:
                return _base_result(
                    wa_id, business_id,
                    RESULT_KIND_ORDER_STATUS,
                    order=_clean_order_for_response(latest, business_settings=settings),
                )

        # No placed order yet but the customer is mid-checkout in pickup
        # mode — return the pickup-specific range ("15 a 20 minutos")
        # instead of the generic delivery_time (45 min). Production
        # observation 2026-05-17: pickup user asked "en cuánto puedo
        # pasar?" while the cart was in ready_to_confirm and got
        # "45 minutos" — the delivery-mode number.
        if _session_is_pickup(session):
            return _base_result(
                wa_id, business_id,
                RESULT_KIND_BUSINESS_INFO,
                field="pickup_time",
                value=PICKUP_RANGE_TEXT,
            )

    value = business_info_service.get_business_info(business_context, field)
    if value is None:
        return _base_result(
            wa_id, business_id,
            RESULT_KIND_INFO_MISSING,
            field=field,
            available_fields=business_info_service.supported_fields(),
        )

    # Hours: prepend a live open-now sentence so questions like
    # "hay servicio?" / "están abiertos?" get an unambiguous answer
    # for the current Bogotá time, not just the schedule. The
    # schedule still follows on a new line so customers also see
    # the full window.
    if field == "hours":
        try:
            status = business_info_service.compute_open_status(business_id)
            sentence = business_info_service.format_open_status_sentence(status)
            if sentence:
                value = f"{sentence}\n{value}"
        except Exception as exc:
            logger.warning("[CS_FLOW] open-status compute failed: %s", exc)

    return _base_result(
        wa_id, business_id,
        RESULT_KIND_BUSINESS_INFO,
        field=field,
        value=value,
    )


def _handle_order_status(
    wa_id: str,
    business_id: str,
    session: Optional[Dict[str, Any]],
    business_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    # Ambiguity guard: "qué tengo en mi pedido?" / "dónde está mi pedido?"
    # might have been routed here by the language-only classifier, but if
    # the user has an active in-progress cart the correct interpretation
    # is VIEW_CART on the order agent. Emit a handoff instead of answering.
    order_context = (session or {}).get("order_context") or {}
    active_cart_items = order_context.get("items") or []
    if active_cart_items:
        logger.info(
            "[CS_FLOW] order_status routed to CS but user has active cart (n=%d) "
            "— handing off to order/VIEW_CART",
            len(active_cart_items),
        )
        return _base_result(
            wa_id, business_id,
            RESULT_KIND_HANDOFF,
            handoff={
                "to": "order",
                "segment": "qué tengo en mi pedido",
                "context": {"reason": "mi_pedido_active_cart"},
            },
        )

    order = order_lookup_service.get_latest_order(wa_id, business_id)
    if not order:
        return _base_result(
            wa_id, business_id,
            RESULT_KIND_NO_ORDER,
        )

    # Per-order ask counter. The handoff only fires on the SECOND (or
    # later) "where is my food?" question for the same order — staff
    # don't want every customer who happens to ask once past minute 50
    # to be hard-handed off to a human. Tracked in
    # customer_service_context so it's per-conversation and resets when
    # a new order is placed (different order id → counter resets).
    agent_contexts = (session or {}).get("agent_contexts") or {}
    cs_ctx = agent_contexts.get("customer_service") or {}
    tracked_order_id = cs_ctx.get("last_status_order_id")
    prior_count_raw = cs_ctx.get("last_status_ask_count") or 0
    try:
        prior_count = int(prior_count_raw)
    except (TypeError, ValueError):
        prior_count = 0
    # Counter only carries forward if the user is still asking about the
    # same order. Once the latest order id changes (new placement), this
    # is the first ask for the new order.
    effective_prior = prior_count if tracked_order_id == order.get("id") else 0
    ask_number = effective_prior + 1
    is_repeat_ask = effective_prior >= 1
    new_state = {
        "last_status_order_id": order.get("id"),
        "last_status_ask_count": ask_number,
    }

    # Delivery handoff: only when this is at least the 2nd status ask
    # AND the order has been in flight past the patience threshold. The
    # first ask always gets a normal status reply so the customer hears
    # something useful before we escalate to a human.
    if is_repeat_ask and _should_trigger_delivery_handoff(order):
        threshold = _delivery_handoff_threshold_min()
        try:
            conversation_agent_service.set_agent_enabled(
                business_id, wa_id, False,
                handoff_reason=HANDOFF_REASON_DELIVERY,
            )
            logger.warning(
                "[CS_FLOW] delivery handoff triggered wa_id=%s business_id=%s "
                "order_id=%s status=%s ask_number=%d threshold_min=%d — bot disabled",
                wa_id, business_id, order.get("id"), order.get("status"),
                ask_number, threshold,
            )
        except Exception as exc:
            logger.error(
                "[CS_FLOW] delivery handoff: failed to disable agent "
                "wa_id=%s business_id=%s: %s",
                wa_id, business_id, exc, exc_info=True,
            )
        return _base_result(
            wa_id, business_id,
            RESULT_KIND_DELIVERY_HANDOFF,
            order=_clean_order_for_response(order, business_settings=_settings_from_context(business_context)),
            state_patch=new_state,
        )

    return _base_result(
        wa_id, business_id,
        RESULT_KIND_ORDER_STATUS,
        order=_clean_order_for_response(order, business_settings=_settings_from_context(business_context)),
        state_patch=new_state,
    )


def _handle_order_history(
    wa_id: str,
    business_id: str,
    params: Dict[str, Any],
    business_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    limit_raw = params.get("limit") or 5
    try:
        limit = max(1, min(int(limit_raw), 20))
    except (TypeError, ValueError):
        limit = 5

    orders = order_lookup_service.get_order_history(wa_id, business_id, limit=limit)
    if not orders:
        return _base_result(
            wa_id, business_id,
            RESULT_KIND_NO_ORDER,
        )

    settings = _settings_from_context(business_context)
    return _base_result(
        wa_id, business_id,
        RESULT_KIND_ORDER_HISTORY,
        orders=[_clean_order_for_response(o, business_settings=settings) for o in orders],
    )


def _handle_cancel_order(
    wa_id: str,
    business_id: str,
    business_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Cancel the customer's most recent order, gated by the customer
    modification policy. We only cancel the *latest* order — if the
    customer has multiple in-flight (rare on WhatsApp), they'd need
    to specify, but for the MVP we don't model that.
    """
    order = order_lookup_service.get_latest_order(wa_id, business_id)
    if not order:
        return _base_result(wa_id, business_id, RESULT_KIND_NO_ORDER)

    settings = _settings_from_context(business_context)
    current_status = order.get("status")
    if not can_customer_cancel(current_status):
        return _base_result(
            wa_id, business_id,
            RESULT_KIND_CANCEL_NOT_ALLOWED,
            order=_clean_order_for_response(order, business_settings=settings),
        )

    try:
        updated = order_modification_service.cancel_order(
            order_id=order["id"],
            reason=CANCEL_REASON_CUSTOMER_WHATSAPP,
        )
    except InvalidStatusTransition:
        # Race: status changed between read and write (admin moved it).
        # Re-read to give the customer an accurate explanation.
        latest = order_lookup_service.get_order_by_id(order["id"]) or order
        return _base_result(
            wa_id, business_id,
            RESULT_KIND_CANCEL_NOT_ALLOWED,
            order=_clean_order_for_response(latest, business_settings=settings),
        )

    return _base_result(
        wa_id, business_id,
        RESULT_KIND_ORDER_CANCELLED,
        order=_clean_order_for_response(updated, business_settings=settings),
    )


# ── Promo handlers ───────────────────────────────────────────────────


def _summarize_promo_for_listing(promo: Dict[str, Any]) -> Dict[str, Any]:
    """
    Trim a Promotion.to_dict() down to what the response generator needs
    to render a one-line summary, plus what the agent needs to remember
    in `last_listed_promos` for ordinal/anaphora resolution next turn.
    """
    if promo.get("fixed_price") is not None:
        price_label = f"${int(float(promo['fixed_price'])):,}".replace(",", ".")
        price_kind = f"precio promo {price_label}"
    elif promo.get("discount_amount") is not None:
        amt = f"${int(float(promo['discount_amount'])):,}".replace(",", ".")
        price_kind = f"descuento de {amt}"
    elif promo.get("discount_pct") is not None:
        price_kind = f"descuento del {int(promo['discount_pct'])}%"
    else:
        price_kind = ""

    days = promo.get("days_of_week") or []
    schedule_label = _spanish_days_label(days) if days else None

    return {
        "id": promo.get("id"),
        "name": promo.get("name"),
        "description": promo.get("description"),
        "price_kind": price_kind,
        "schedule_label": schedule_label,
    }


_DAY_NAMES_ES = {
    1: "lunes", 2: "martes", 3: "miércoles", 4: "jueves",
    5: "viernes", 6: "sábado", 7: "domingo",
}


def _spanish_days_label(days: List[int]) -> str:
    """[1,5] → 'lunes y viernes'. Sorted, oxford-style join."""
    names = [_DAY_NAMES_ES[d] for d in sorted(set(days)) if d in _DAY_NAMES_ES]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " y " + names[-1]


def _handle_get_promos(
    wa_id: str,
    business_id: str,
    business_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    List promos in two buckets — active right now and upcoming this
    week — so the response can say "hoy no tenemos, pero el viernes
    sale X". Schedule math runs in the business's local timezone.

    Persists the active set into `customer_service_context.
    last_listed_promos` so a follow-up "dame esa" / "la primera" can
    resolve back to a concrete promo_id. Upcoming promos aren't
    persisted because the customer can't add a promo that isn't active.
    """
    tz_name = promotion_service.timezone_from_business_context(business_context)
    buckets = promotion_service.list_promos_for_listing(
        business_id, timezone_name=tz_name,
    )
    active = buckets.get("active_now") or []
    upcoming = buckets.get("upcoming") or []

    if not active and not upcoming:
        return _base_result(wa_id, business_id, RESULT_KIND_NO_PROMOS)

    return _base_result(
        wa_id, business_id,
        RESULT_KIND_PROMOS_LIST,
        promos=[_summarize_promo_for_listing(p) for p in active],
        upcoming_promos=[_summarize_promo_for_listing(p) for p in upcoming],
    )


def _handle_select_listed_promo(
    wa_id: str,
    business_id: str,
    params: Dict[str, Any],
    session: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Resolve a promo reference into a concrete `promo_id` and hand off
    to the order agent for cart-add. Two surfaces feed in here:

    A) Anaphora after a list: the planner saw "dame esa" / "la primera"
       / "la del honey" and we lean on `last_listed_promos`.
    B) Cold ask: planner ambiguates a fresh "dame una promo de honey"
       as SELECT_LISTED_PROMO too. There's no list to select from, so
       fall through to a query against ALL active promos via the shared
       matcher.

    Resolution priority:
      1. params.promo_id — exact id (rare; planner could pass it).
      2. params.selector — ordinal against `last_listed_promos`.
      3. params.query against `last_listed_promos` (anaphora pass).
      4. params.query against ALL active promos (cold-ask pass).

    Outcomes:
      - 1 match → RESULT_KIND_HANDOFF with concrete promo_id.
      - 2+ matches → RESULT_KIND_PROMO_AMBIGUOUS (asks the customer
        to be more specific).
      - 0 matches → RESULT_KIND_PROMO_NOT_RESOLVED.
    """
    agent_contexts = (session or {}).get("agent_contexts") or {}
    cs_ctx = agent_contexts.get("customer_service") or {}
    listed = cs_ctx.get("last_listed_promos") or []

    promo_id: Optional[str] = None

    # 1) explicit id
    raw_id = (params.get("promo_id") or "").strip()
    if raw_id:
        if any(p.get("id") == raw_id for p in listed):
            promo_id = raw_id

    # 2) ordinal selector against the listed set
    if promo_id is None:
        selector = (params.get("selector") or "").strip().lower()
        idx = _ordinal_to_index(selector) if selector else None
        if idx is not None and 0 <= idx < len(listed):
            promo_id = listed[idx].get("id")

    # 3) fuzzy query against the listed set (anaphora pass).
    raw_query = (params.get("query") or "").strip()
    if promo_id is None and raw_query and listed:
        q = raw_query.lower()
        matches = [p for p in listed if q in (p.get("name") or "").lower()]
        if len(matches) == 1:
            promo_id = matches[0].get("id")

    # 4) Cold-ask pass: query against ALL active promos via shared matcher.
    # Triggered when the planner picked SELECT_LISTED_PROMO but there's no
    # listed set (or the anaphora pass missed). This is the bridge that
    # lets a fresh "me das una promo de honey" still resolve correctly.
    if promo_id is None and raw_query:
        all_matches = promotion_service.find_promo_by_query(business_id, raw_query)
        if len(all_matches) == 1:
            promo_id = all_matches[0].get("id")
        elif len(all_matches) >= 2:
            return _base_result(
                wa_id, business_id,
                RESULT_KIND_PROMO_AMBIGUOUS,
                query=raw_query,
                candidates=[
                    {"id": p.get("id"), "name": p.get("name")}
                    for p in all_matches[:5]
                ],
            )

    if promo_id is None:
        # Safety net: when the planner emitted SELECT_LISTED_PROMO but
        # there was no recent promo listing AND the user's query has no
        # promo-y keywords, the message is almost certainly a PRODUCT
        # reference the LLM mis-classified as a promo selection (Biela
        # 2026-05-06 / 3177000722: "buenas tiene la del concurso?" →
        # SELECT_LISTED_PROMO → "no tengo una promo activa con ese
        # nombre", when the user meant a product). Hand off to the
        # order agent so SEARCH_PRODUCTS runs the lookup with its
        # fuzzy + semantic + tag matchers — it knows how to resolve
        # descriptive references and typos.
        promo_keywords = (
            "promo", "promos", "promocion", "promoción", "promociones",
            "oferta", "ofertas", "combo", "combos",
            "descuento", "descuentos",
            "2x1", "2 x 1",
        )
        q_lower = raw_query.lower()
        has_promo_keyword = any(kw in q_lower for kw in promo_keywords)
        if raw_query and not listed and not has_promo_keyword:
            logger.info(
                "[CS_FLOW] SELECT_LISTED_PROMO unresolved with no listed set "
                "and no promo keyword in query=%r — handing off to order/SEARCH",
                raw_query,
            )
            return _base_result(
                wa_id, business_id,
                RESULT_KIND_HANDOFF,
                handoff={
                    "to": "order",
                    "segment": raw_query,
                    "context": {"reason": "promo_query_no_match_search_fallback"},
                },
            )
        return _base_result(
            wa_id, business_id,
            RESULT_KIND_PROMO_NOT_RESOLVED,
            listed_count=len(listed),
            query=raw_query or None,
        )

    # Hand off to the order agent. The order agent's fast-path consumes
    # context.promo_id and synthesizes ADD_PROMO_TO_CART directly.
    return _base_result(
        wa_id, business_id,
        RESULT_KIND_HANDOFF,
        handoff={
            "to": "order",
            "segment": "agregar promo seleccionada",
            "context": {"promo_id": promo_id},
        },
    )


_ORDINAL_WORDS_ES = {
    "primer": 0, "primero": 0, "primera": 0, "1": 0, "uno": 0, "una": 0,
    "segundo": 1, "segunda": 1, "2": 1, "dos": 1,
    "tercero": 2, "tercera": 2, "3": 2, "tres": 2,
    "cuarto": 3, "cuarta": 3, "4": 3, "cuatro": 3,
    "quinto": 4, "quinta": 4, "5": 4, "cinco": 4,
}


def _ordinal_to_index(selector: str) -> Optional[int]:
    """'la primera' → 0, '2' → 1, 'tercero' → 2. Returns None on miss."""
    if not selector:
        return None
    s = selector.lower().strip()
    # Strip leading articles / common framing tokens.
    for prefix in ("la ", "el ", "los ", "las ", "esa ", "ese ", "esos ", "esas "):
        if s.startswith(prefix):
            s = s[len(prefix):]
    for token in s.split():
        if token in _ORDINAL_WORDS_ES:
            return _ORDINAL_WORDS_ES[token]
    return _ORDINAL_WORDS_ES.get(s)
