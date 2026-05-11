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
from datetime import date as _date, datetime as _datetime, time as _time, timedelta as _timedelta
from typing import Optional, Callable, Any, Dict, List, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9 — should not happen in our deploys
    ZoneInfo = None  # type: ignore


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


# ── Open-now status ─────────────────────────────────────────────────────
# Live "are we open right now?" check. Reads the same
# business_availability rows the schedule formatter uses, but compares
# against the current Bogotá time and surfaces whichever transition
# (closes_at today / opens_at today / next_open weekday) is most
# relevant for a Spanish customer-facing reply.
#
# Production observation 2026-05-05 (Biela / 3177000722): user asked
# "Buenas hay servicio" at 00:42 Bogotá and the bot answered "Sí,
# estamos abiertos" — the LLM hallucinated the open status because
# nothing in the flow actually checked current time vs schedule. This
# helper closes that gap.

_BUSINESS_TIMEZONE_NAME = "America/Bogota"


def _business_now(now: Optional[_datetime] = None) -> _datetime:
    """Return ``now`` in the business timezone (Bogotá). Pure for tests."""
    if now is not None:
        if now.tzinfo is None and ZoneInfo is not None:
            now = now.replace(tzinfo=ZoneInfo(_BUSINESS_TIMEZONE_NAME))
        return now
    if ZoneInfo is not None:
        return _datetime.now(tz=ZoneInfo(_BUSINESS_TIMEZONE_NAME))
    return _datetime.utcnow() - _timedelta(hours=5)


def _load_active_availability_rows(business_id: str) -> List[Dict[str, Any]]:
    """Load every active availability row for the business. Returns []
    on DB failure or unknown business."""
    if not business_id:
        return []
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
                .all()
            )
            return [r.to_dict() for r in rows]
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[BUSINESS_INFO] active availability load failed: %s", exc)
        return []


def _row_window_for_dow(rows: List[Dict[str, Any]], dow: int) -> Optional[Tuple[_time, _time]]:
    """Pick the widest open/close window across rows for ``dow``."""
    chosen: Optional[Tuple[_time, _time]] = None
    for r in rows:
        if r.get("day_of_week") != dow:
            continue
        if not r.get("is_active", True):
            continue
        ot = r.get("open_time")
        ct = r.get("close_time")
        if ot is None or ct is None:
            continue
        if chosen is None:
            chosen = (ot, ct)
        else:
            new_ot = ot if ot < chosen[0] else chosen[0]
            new_ct = ct if ct > chosen[1] else chosen[1]
            chosen = (new_ot, new_ct)
    return chosen


_DAY_NAMES_LONG = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"]


def _next_open_lookup(rows: List[Dict[str, Any]], starting_dow: int) -> Optional[Tuple[int, _time]]:
    """
    Find the next (day_of_week, open_time) window strictly AFTER
    today (within the next 7 days). Used to render
    "Volvemos a abrir el lunes a las 5:00 PM".
    """
    for offset in range(1, 8):
        dow = (starting_dow + offset) % 7
        window = _row_window_for_dow(rows, dow)
        if window is not None:
            return (dow, window[0])
    return None


def compute_open_status(
    business_id: str,
    now: Optional[_datetime] = None,
) -> Dict[str, Any]:
    """
    Return a structured open-now status for ``business_id``.

    Result fields:
      - ``is_open`` (bool)
      - ``has_data`` (bool) — False when we couldn't load any rows;
        callers should NOT render an open-now sentence in that case.
      - ``opens_at`` (time | None) — today's open time, when the
        business is closed but will open today.
      - ``closes_at`` (time | None) — today's close time, when the
        business is open right now.
      - ``next_open_dow`` / ``next_open_time`` — the next weekday +
        time the business reopens (used when closed for the rest of
        today, or closed today entirely).
      - ``now_local`` — the timezone-aware Bogotá datetime the
        decision was made against (handy for tests).
    """
    base: Dict[str, Any] = {
        "is_open": False,
        "has_data": False,
        "opens_at": None,
        "closes_at": None,
        "next_open_dow": None,
        "next_open_time": None,
        "now_local": None,
    }
    if not business_id:
        return base

    rows = _load_active_availability_rows(business_id)
    if not rows:
        return base

    now_local = _business_now(now)
    base["now_local"] = now_local
    base["has_data"] = True

    # Python's weekday() is 0=Mon..6=Sun. The DB schema uses
    # 0=Sun..6=Sat (matches Postgres extract(dow)). Convert.
    dow_today = (now_local.weekday() + 1) % 7
    today_window = _row_window_for_dow(rows, dow_today)
    cur_time = now_local.time().replace(microsecond=0)

    if today_window is not None:
        ot, ct = today_window
        if cur_time < ot:
            # Closed but opens later today.
            base["opens_at"] = ot
            base["next_open_dow"] = dow_today
            base["next_open_time"] = ot
            return base
        if ot <= cur_time < ct:
            # Open now.
            base["is_open"] = True
            base["closes_at"] = ct
            return base

    # Either no row for today (closed all day) or already past today's
    # close. Look forward for the next open weekday.
    nxt = _next_open_lookup(rows, dow_today)
    if nxt is not None:
        base["next_open_dow"] = nxt[0]
        base["next_open_time"] = nxt[1]
    return base


def _format_time_lower(t: _time) -> str:
    """5:30 PM (matches the schedule formatter style)."""
    return _format_time_12h(t)


def is_taking_orders_now(
    business_id: str,
    now: Optional[_datetime] = None,
) -> Dict[str, Any]:
    """
    Decide whether the order agent should be accepting cart-mutating
    intents at this moment.

    Wraps ``compute_open_status``. When the business has no
    availability rows configured (``has_data=False``), default to
    accepting orders — the gate is opt-in via the presence of
    ``business_availability`` data.

    Returns a dict::

        {
          "can_take_orders": bool,
          "reason": "open" | "closed" | "no_data",
          "opens_at": time | None,            # today's open, when closed-but-opens-today
          "next_open_dow": int | None,        # 0=Sun..6=Sat
          "next_open_time": time | None,
          "now_local": datetime | None,       # Bogotá tz
        }

    Browse intents (menu / product details / search) should be
    permitted even when ``can_take_orders`` is False — the customer
    can still read the menu while the shop is closed. The gate is
    applied per-intent inside the order agent's dispatch loop; this
    helper just answers the yes/no question.
    """
    status = compute_open_status(business_id, now=now)
    base: Dict[str, Any] = {
        "can_take_orders": True,
        "reason": "no_data",
        "opens_at": None,
        "next_open_dow": None,
        "next_open_time": None,
        "now_local": status.get("now_local"),
    }
    if not status.get("has_data"):
        return base
    if status.get("is_open"):
        base["reason"] = "open"
        return base
    return {
        "can_take_orders": False,
        "reason": "closed",
        "opens_at": status.get("opens_at"),
        "next_open_dow": status.get("next_open_dow"),
        "next_open_time": status.get("next_open_time"),
        "now_local": status.get("now_local"),
    }


def is_fully_closed_today(status: Dict[str, Any]) -> bool:
    """
    True iff today is a "no service" day — the business has no opening
    window at all today. Distinguishes from a mid-day break (closed now
    but opens later today, ``opens_at`` set) where customers will be
    served in a few hours.

    Used to decide when to append the alt-branch contact line and to
    drop the menu URL / Twilio welcome card on the greeting.
    """
    if not status or not status.get("has_data"):
        return False
    if status.get("is_open"):
        return False
    # ``opens_at`` is set ONLY in the "closed but opens later today"
    # branch of compute_open_status. When today has no schedule row at
    # all (e.g. Sunday for a Mon-Sat business), opens_at stays None.
    if status.get("opens_at") is not None:
        return False
    return True


def format_closed_alt_contact_suffix(business: Optional[Dict[str, Any]]) -> str:
    """
    Render the "if you need to order today, contact <sibling branch>"
    suffix used on fully-closed-today greetings and order_closed handoffs.

    Reads ``business.settings.closed_day_alt_contact = {name, phone}``.
    Returns an empty string when the contact isn't configured.
    """
    if not business:
        return ""
    settings = (business.get("settings") or {}) if isinstance(business, dict) else {}
    alt = settings.get("closed_day_alt_contact") or {}
    if not isinstance(alt, dict):
        return ""
    name = (alt.get("name") or "").strip()
    phone = (alt.get("phone") or "").strip()
    if not name or not phone:
        return ""
    return f" Si necesitas pedir hoy, escríbele a {name} al {phone}."


def format_open_status_sentence(status: Dict[str, Any]) -> str:
    """
    One-liner Spanish sentence summarizing whether the business is
    currently open. Empty string when ``status['has_data']`` is
    False — callers should fall back to the schedule alone.
    """
    if not status.get("has_data"):
        return ""
    if status.get("is_open") and status.get("closes_at") is not None:
        return f"Sí, estamos abiertos. Cerramos hoy a las {_format_time_lower(status['closes_at'])}."
    # Closed cases.
    now_local = status.get("now_local")
    today_dow = ((now_local.weekday() + 1) % 7) if now_local else None
    next_dow = status.get("next_open_dow")
    next_time = status.get("next_open_time")
    if next_dow is not None and next_time is not None:
        if today_dow is not None and next_dow == today_dow:
            return (
                "Por ahora estamos cerrados. "
                f"Hoy abrimos a las {_format_time_lower(next_time)}."
            )
        # Different day — render the day name.
        day_name = _DAY_NAMES_LONG[next_dow] if 0 <= next_dow < 7 else ""
        if day_name:
            return (
                "Por ahora estamos cerrados. "
                f"Volvemos a abrir el {day_name} a las {_format_time_lower(next_time)}."
            )
    return "Por ahora estamos cerrados."


# Canonical field keys. Keep in sync with the customer service agent
# planner prompt — the planner emits these as params to GET_BUSINESS_INFO.
FIELD_HOURS = "hours"
FIELD_ADDRESS = "address"
FIELD_PHONE = "phone"
FIELD_DELIVERY_FEE = "delivery_fee"
FIELD_DELIVERY_TIME = "delivery_time"
FIELD_MENU_URL = "menu_url"
FIELD_PAYMENT_METHODS = "payment_methods"
FIELD_PAYMENT_DETAILS = "payment_details"

ALL_FIELDS = (
    FIELD_HOURS,
    FIELD_ADDRESS,
    FIELD_PHONE,
    FIELD_DELIVERY_FEE,
    FIELD_DELIVERY_TIME,
    FIELD_MENU_URL,
    FIELD_PAYMENT_METHODS,
    FIELD_PAYMENT_DETAILS,
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
    FIELD_PAYMENT_DETAILS: {
        # Free-text instruction for HOW/WHERE the customer pays — Nequi
        # number, account, or "contra entrega al domiciliario". Distinct
        # from `payment_methods` (the list of accepted methods) and from
        # `phone` (the business's general contact number, which the LLM
        # used to grab for payment-account questions). Production
        # 2026-05-06 (Biela): customer asked "A qué número se realiza el
        # pago?" and got the contact phone — operator's manual reply was
        # "El pago es directo con el domiciliario".
        # Default: contra-entrega is the standard for small Colombian
        # delivery restaurants, so when the operator hasn't configured
        # a Nequi/account the safest answer is the contra-entrega
        # message — never the business contact phone.
        "keys": ("payment_details",),
        "format": _plain,
        "default": "El pago es contra entrega, directo con el domiciliario.",
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


def format_business_info_for_prompt(business_context: Optional[Dict]) -> str:
    """Format address, phone, hours, payment methods, fulfillment rules,
    and out-of-zone redirects for the order agent's system prompt and
    the response renderer's business-voice block.

    Lives in this module (not on the agent) because both the order
    agent and the response renderer need it, and it reads from the
    same business settings + availability tables this module already
    owns. Moved here from the legacy order_agent module during v1
    removal so the helper outlives the planner/executor pipeline.
    """
    if not business_context or not business_context.get("business"):
        return "Información del negocio: (no configurada)."
    raw_settings = business_context["business"].get("settings")
    # Support both dict and None; JSONB can sometimes be dict-like
    settings = dict(raw_settings) if raw_settings is not None else {}
    if not isinstance(settings, dict):
        settings = {}
    address = (settings.get("address") or settings.get("Address") or "").strip()
    phone = (settings.get("phone") or "").strip()
    city = (settings.get("city") or "").strip()
    state = (settings.get("state") or "").strip()
    country = (settings.get("country") or "").strip()
    business_id = business_context.get("business_id")
    parts: List[str] = []
    if address:
        parts.append(f"Dirección: {address}")
    if city or state or country:
        loc = ", ".join(filter(None, [city, state, country]))
        if loc:
            parts.append(f"Ciudad/país: {loc}")
    if phone:
        parts.append(f"Teléfono: {phone}")
    if business_id:
        try:
            # Lazy import to avoid a circular dep with the database
            # layer at module-load time.
            from ..database.booking_service import booking_service
            rules = booking_service.get_availability(str(business_id))
            if rules:
                day_names = {
                    0: "Domingo",
                    1: "Lunes",
                    2: "Martes",
                    3: "Miércoles",
                    4: "Jueves",
                    5: "Viernes",
                    6: "Sábado",
                }
                hour_lines = []
                for rule in sorted(rules, key=lambda x: x.get("day_of_week", 0)):
                    day_label = day_names.get(rule.get("day_of_week", -1), "Día")
                    if not rule.get("is_active", True):
                        hour_lines.append(f"  {day_label}: cerrado")
                        continue
                    hour_lines.append(
                        f"  {day_label}: {rule.get('open_time', '')} - {rule.get('close_time', '')}"
                    )
                if hour_lines:
                    parts.append("Horarios:\n" + "\n".join(hour_lines))
        except Exception:
            pass
    # One clear line for location questions: address if set, else city/state/country
    location_parts: List[str] = []
    if address:
        location_parts.append(address)
    if city or state or country:
        location_parts.append(", ".join(filter(None, [city, state, country])))
    if location_parts:
        parts.append("Ubicación (para preguntas 'dónde están'): " + " ".join(location_parts))
    payment_methods = settings.get("payment_methods") or []
    if isinstance(payment_methods, list):
        cleaned_methods = [str(m).strip() for m in payment_methods if str(m).strip()]
        if cleaned_methods:
            parts.append(
                "Métodos de pago aceptados: "
                + ", ".join(cleaned_methods)
                + ". Cuando el cliente indique su método de pago, EMPAREJA su "
                "texto contra esta lista de forma flexible (case-insensitive, "
                "fragmentos, abreviaturas, errores de tipeo razonables) y pasa "
                "el NOMBRE CANÓNICO de la lista al campo `payment_method` de "
                "submit_delivery_info. Ejemplos: 'breb' o 'bre' → 'Llave BreB'; "
                "'efe' o 'cash' → 'efectivo'; 'transf' o 'transferencia bancaria' "
                "→ 'transferencia'; 'nequi' (cualquier capitalización) → 'Nequi'. "
                "Solo OMITE el campo `payment_method` cuando el cliente mencione "
                "EXPLÍCITAMENTE un método que claramente NO está en la lista "
                "(ej. PayPal, tarjeta de crédito) y no haya overlap razonable con "
                "ningún canónico — entonces emite delivery_info_collected con la "
                "lista canónica en facts para que el cliente elija."
            )
    ai_prompt = (settings.get("ai_prompt") or "").strip()
    if ai_prompt:
        parts.append("IMPORTANTE: Reglas y contexto del negocio (usa para preguntas sobre combos, hamburguesas con papas, etc.):\n" + ai_prompt)
    # Universal pickup-vs-delivery rules. Surfaced for every business —
    # not behind a per-business flag — so the model has one consistent
    # mental model. Default is delivery; pickup is set only when the
    # customer explicitly signals it. Switching is symmetric.
    parts.append(
        "Modos de cumplimiento disponibles:\n"
        "- 🛵 Domicilio (default): se requiere nombre, dirección, teléfono y medio de pago.\n"
        "- 🏃 Recoger en local (pickup): se requiere SOLO el nombre. El número de WhatsApp "
        "cubre el teléfono y el pago se hace en el local.\n"
        "El modo activo está visible en \"Modo:\" del bloque ESTADO Y HISTORIAL DEL TURNO. "
        "El cliente comienza en domicilio por default. Cambia a pickup ÚNICAMENTE cuando el "
        "cliente lo indique explícitamente con frases como: \"lo recojo\", \"paso a recoger\", "
        "\"para recoger\", \"en sitio\", \"en el local\", \"para llevar\", \"recogida\". "
        "En ese caso llama submit_delivery_info(fulfillment_type='pickup', name=<si lo dijo>) "
        "— NO pidas dirección ni medio de pago. Cambia de vuelta a domicilio si el cliente "
        "dice \"no, mejor domicilio\", \"envíenmelo\", \"para domicilio\" pasando "
        "submit_delivery_info(fulfillment_type='delivery')."
    )
    out_of_zone = settings.get("out_of_zone_delivery_contacts") or []
    if isinstance(out_of_zone, list) and out_of_zone:
        rows: List[str] = []
        for entry in out_of_zone:
            if not isinstance(entry, dict):
                continue
            city = (entry.get("city") or "").strip()
            phone = (entry.get("phone") or "").strip()
            if city and phone:
                rows.append(f"- {city} → contacto: {phone}")
        if rows:
            parts.append(
                "Zonas FUERA de cobertura de domicilio (NO atendemos pedidos a estas ciudades; "
                "el cliente debe escribir al número listado):\n"
                + "\n".join(rows)
                + "\n\nSi el cliente pide hacer un pedido o domicilio a una de estas "
                "ciudades (lo dice en la dirección, en el destino, o explícitamente "
                "'a Ipiales', 'para X', 'envíen a Y'), NO recolectes datos de entrega "
                "ni intentes crear el pedido. Llama "
                "respond(kind='out_of_scope', summary='out_of_zone:<ciudad>', "
                "facts=['city:<ciudad>', 'phone:<numero>']) — el sistema redirigirá "
                "al cliente al número correspondiente."
            )
    if not parts:
        return "Información del negocio: (no configurada)."
    return "Información del negocio:\n" + "\n".join(parts)
