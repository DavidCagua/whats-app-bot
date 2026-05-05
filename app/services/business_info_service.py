"""
Business info service: read business.settings fields by canonical key.

Shared capability invoked by the customer service agent. Designed so the
router can ALSO call it directly in later phases (fast-path for simple
info questions like "a qué hora abren" without full agent dispatch).

Supported fields map a canonical key to a settings path + optional
formatter. Unknown keys return None so callers can produce a safe
"no tengo ese dato" fallback.
"""

import logging
from typing import Optional, Callable, Any, Dict, List


logger = logging.getLogger(__name__)


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
