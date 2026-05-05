"""
Business info service: read business.settings fields by canonical key.

Shared capability invoked by the customer service agent. Designed so the
router can ALSO call it directly in later phases (fast-path for simple
info questions like "a qué hora abren" without full agent dispatch).

Supported fields map a canonical key to a settings path + optional
formatter. Unknown keys return None so callers can produce a safe
"no tengo ese dato" fallback.

Hours are sourced from the structured ``business_availability`` table
(per-day open/close rows), NOT from ``business.settings.hours_text`` —
that legacy free-text field is only consulted as a fallback for
businesses that haven't migrated yet.
"""

import logging
import threading
import time
from typing import Optional, Callable, Any, Dict, List, Tuple


logger = logging.getLogger(__name__)


# Process cache for the formatted hours string per business. Same TTL
# pattern as catalog_cache — admin edits will be picked up after at
# most _HOURS_TTL_SECONDS (5 min). Keyed on business_id only, since
# the formatter depends entirely on the per-business
# business_availability rows.
_HOURS_TTL_SECONDS = 300.0
_hours_cache: Dict[str, Tuple[float, Optional[str]]] = {}
_hours_lock = threading.Lock()


# Spanish day names matching BusinessAvailability.day_of_week:
# 0=Sunday, 1=Monday, ..., 6=Saturday.
_DAY_NAMES_SHORT = ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"]


def _format_time_12h(t) -> str:
    """Render a datetime.time as `5:30 PM` (Colombian convention)."""
    if t is None:
        return ""
    hour = t.hour
    minute = t.minute
    suffix = "AM" if hour < 12 else "PM"
    h12 = hour % 12
    if h12 == 0:
        h12 = 12
    if minute:
        return f"{h12}:{minute:02d} {suffix}"
    return f"{h12}:00 {suffix}"


def _condense_hours_rows(rows: List[Dict[str, Any]]) -> str:
    """
    Build the Spanish hours summary from a list of (active) availability
    rows. Groups consecutive weekdays that share the same open/close
    times into a range ("Lun a Vie: 5:30 PM - 10:00 PM"). One row per
    distinct schedule.
    """
    if not rows:
        return ""

    # Pick one open/close pair per day (when there are multiple rows
    # per day — e.g. per-staff — collapse to the widest window).
    by_day: Dict[int, Tuple] = {}
    for r in rows:
        dow = r.get("day_of_week")
        if dow is None:
            continue
        if not r.get("is_active", True):
            continue
        ot = r.get("open_time")
        ct = r.get("close_time")
        if ot is None or ct is None:
            continue
        prev = by_day.get(dow)
        if prev is None:
            by_day[dow] = (ot, ct)
            continue
        # Take widest window (earliest open, latest close).
        prev_ot, prev_ct = prev
        new_ot = ot if ot < prev_ot else prev_ot
        new_ct = ct if ct > prev_ct else prev_ct
        by_day[dow] = (new_ot, new_ct)

    if not by_day:
        return ""

    # Order with Monday first (week feels Monday-led in Colombia for
    # business hours), Sunday last.
    week_order = [1, 2, 3, 4, 5, 6, 0]
    ordered = [(d, by_day[d]) for d in week_order if d in by_day]

    # Group consecutive same-window days into ranges.
    groups: List[Tuple[List[int], Tuple]] = []
    for dow, window in ordered:
        if groups and groups[-1][1] == window and (
            week_order.index(dow) == week_order.index(groups[-1][0][-1]) + 1
        ):
            groups[-1][0].append(dow)
        else:
            groups.append(([dow], window))

    parts: List[str] = []
    for days, (ot, ct) in groups:
        if len(days) == 1:
            label = _DAY_NAMES_SHORT[days[0]]
        else:
            label = f"{_DAY_NAMES_SHORT[days[0]]} a {_DAY_NAMES_SHORT[days[-1]]}"
        parts.append(f"{label}: {_format_time_12h(ot)} - {_format_time_12h(ct)}")
    return ". ".join(parts)


def _load_hours_from_availability(business_id: str) -> Optional[str]:
    """
    Read active rows from ``business_availability`` for this business
    and format them. Returns None on DB failure or when no rows exist.
    """
    if not business_id:
        return None
    try:
        from ..database.models import BusinessAvailability, get_db_session
        import uuid as _uuid
        db = get_db_session()
        try:
            rows = (
                db.query(BusinessAvailability)
                .filter(
                    BusinessAvailability.business_id == _uuid.UUID(str(business_id)),
                    BusinessAvailability.is_active == True,
                )
                .order_by(BusinessAvailability.day_of_week)
                .all()
            )
            dicts = [r.to_dict() for r in rows]
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[BUSINESS_INFO] hours load from availability failed: %s", exc)
        return None
    return _condense_hours_rows(dicts) or None


def _get_hours_for_business(business_id: str) -> Optional[str]:
    """Cached wrapper over the availability loader."""
    if not business_id:
        return None
    key = str(business_id)
    now = time.time()
    with _hours_lock:
        cached = _hours_cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
    value = _load_hours_from_availability(key)
    with _hours_lock:
        _hours_cache[key] = (now + _HOURS_TTL_SECONDS, value)
    return value


def invalidate_hours_cache(business_id: Optional[str] = None) -> None:
    """Drop the cached hours string. ``None`` clears every entry."""
    with _hours_lock:
        if business_id is None:
            _hours_cache.clear()
            return
        _hours_cache.pop(str(business_id), None)


# Canonical field keys. Keep in sync with the customer service agent
# planner prompt — the planner emits these as params to GET_BUSINESS_INFO.
FIELD_HOURS = "hours"
FIELD_ADDRESS = "address"
FIELD_PHONE = "phone"
FIELD_DELIVERY_FEE = "delivery_fee"
FIELD_DELIVERY_TIME = "delivery_time"
FIELD_MENU_URL = "menu_url"
FIELD_PAYMENT_METHODS = "payment_methods"

ALL_FIELDS = (
    FIELD_HOURS,
    FIELD_ADDRESS,
    FIELD_PHONE,
    FIELD_DELIVERY_FEE,
    FIELD_DELIVERY_TIME,
    FIELD_MENU_URL,
    FIELD_PAYMENT_METHODS,
)


# Single source of truth for the delivery-fee fallback when a business
# hasn't configured `settings.delivery_fee`. Used by both the order side
# (order_tools, order_flow) and the customer service info lookup so the
# two surfaces agree on the same number — a customer asking the price
# never gets "no configurado" while orders silently apply $5.000.
DELIVERY_FEE_DEFAULT = 7000


def _format_cop(value: Any) -> str:
    """Colombian peso formatting used in the rest of the codebase."""
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return ""
    return f"${n:,}".replace(",", ".")


def _format_list(value: Any) -> str:
    """Format a list of strings as a human Spanish list."""
    if not value:
        return ""
    if isinstance(value, str):
        return value
    items = [str(v).strip() for v in value if str(v).strip()]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} y {items[1]}"
    return ", ".join(items[:-1]) + f" y {items[-1]}"


def _plain(value: Any) -> str:
    """Return value as stripped string, empty string for None."""
    if value is None:
        return ""
    return str(value).strip()


# Map canonical field → (settings key, formatter).
# Some fields accept multiple settings keys as aliases (legacy schemas).
_FIELD_MAP: Dict[str, Dict[str, Any]] = {
    FIELD_HOURS: {
        "keys": ("hours_text", "hours"),
        "format": _plain,
    },
    FIELD_ADDRESS: {
        "keys": ("address",),
        "format": _plain,
    },
    FIELD_PHONE: {
        "keys": ("phone", "contact_phone"),
        "format": _plain,
    },
    FIELD_DELIVERY_FEE: {
        "keys": ("delivery_fee",),
        "format": _format_cop,
        # Fall back to the same default the order side uses, so CS doesn't
        # say "no configurado" while orders silently apply this number.
        "default": DELIVERY_FEE_DEFAULT,
    },
    FIELD_DELIVERY_TIME: {
        # Operator-overridable as a free-text string ("30-60 minutos",
        # "Same-day", etc.). Falls back to the same NOMINAL_RANGE_TEXT
        # the order agent quotes at order placement so the answer to
        # "cuánto se demora la entrega?" matches what receipts promise.
        "keys": ("delivery_time_text",),
        "format": _plain,
        # Resolved lazily below to avoid a circular import at module load.
        "default_factory": lambda: _delivery_time_default(),
    },
    FIELD_MENU_URL: {
        "keys": ("menu_url",),
        "format": _plain,
    },
    FIELD_PAYMENT_METHODS: {
        "keys": ("payment_methods",),
        "format": _format_list,
    },
}


def get_business_info(
    business_context: Optional[Dict[str, Any]],
    field: str,
) -> Optional[str]:
    """
    Return the formatted business info string for the given field, or None.

    None means either:
    - Unknown field key.
    - Field is not populated in business.settings.

    Callers should produce a safe fallback reply ("no tengo ese dato exacto,
    te pongo en contacto con el equipo") when None is returned.

    For ``hours`` the structured ``business_availability`` table is the
    primary source; ``business.settings.hours_text`` is consulted only
    as a fallback when no availability rows exist.
    """
    if not business_context:
        return None
    field_key = (field or "").strip().lower()
    spec = _FIELD_MAP.get(field_key)
    if not spec:
        logger.debug("[BUSINESS_INFO] unknown field key: %r", field)
        return None

    biz = business_context.get("business") or {}
    settings = biz.get("settings") or {}

    # Hours: read from business_availability first; settings.hours_text
    # is only the fallback for businesses that haven't migrated.
    if field_key == FIELD_HOURS:
        bid = (business_context or {}).get("business_id")
        if bid:
            availability_text = _get_hours_for_business(str(bid))
            if availability_text:
                return availability_text

    value: Any = None
    for key in spec["keys"]:
        if key in settings and settings[key] not in (None, "", [], {}):
            value = settings[key]
            break

    if value in (None, "", [], {}):
        # Some fields opt into a default when the operator hasn't
        # configured a value — e.g. delivery_fee shares a default with
        # the order side so the two surfaces never disagree. Use
        # default_factory for defaults that need lazy resolution
        # (avoids import cycles).
        if "default" in spec:
            value = spec["default"]
        elif "default_factory" in spec:
            value = spec["default_factory"]()
        else:
            return None

    formatter: Callable[[Any], str] = spec["format"]
    formatted = formatter(value)
    return formatted or None


def _delivery_time_default() -> str:
    """Lazy import — order_eta lives in the same package and could
    otherwise create a circular dep at module-load time."""
    from .order_eta import NOMINAL_RANGE_TEXT
    return NOMINAL_RANGE_TEXT


def supported_fields() -> List[str]:
    """Return the list of canonical field keys the planner may emit."""
    return list(ALL_FIELDS)
