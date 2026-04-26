"""
Business greeting template + pure-greeting detection.

Router invokes this when a user sends a pure greeting ("hola", "buenas"),
returning a templated welcome string directly without dispatching any agent.

No LLM call — it's a fixed template with variable substitutions
(business name, customer name, menu URL, hours).

Behavior preserves the prior order-agent GREET branch 1:1 for zero
regression. A later cleanup will migrate the hardcoded Biela hours
fallback into business.settings.hours_text.
"""

import re
from typing import Optional


# Pure greeting detection: message is ONLY a greeting token.
# Any extra content (product, question mark, etc.) falls through
# to the LLM router so the intent is classified normally.
_PURE_GREETING_RE = re.compile(
    r"^\s*"
    r"(hola+|buenas|buenos?\s+d[ií]as?|buen\s+d[ií]a|"
    r"buenas\s+tardes?|buenas\s+noches?|hey|ey|saludos)"
    r"[\s!¡.,]*$",
    re.IGNORECASE,
)


def is_pure_greeting(message: Optional[str]) -> bool:
    """Return True when the message is nothing but a greeting token."""
    if not message:
        return False
    return bool(_PURE_GREETING_RE.match(message.strip()))


def _first_name(full_name: Optional[str]) -> str:
    """First whitespace-split token, capitalized. Empty string when blank.

    'david caguazango' → 'David'. 'MARÍA JOSÉ' → 'María'. Greeting reads
    warmer with just the given name; full-name display belongs in formal
    surfaces (receipts, courier tickets), not conversational openers.
    """
    if not full_name:
        return ""
    tokens = full_name.strip().split()
    return tokens[0].capitalize() if tokens else ""


# Hardcoded Biela defaults preserved from the prior order-agent GREET
# branch so migrating this logic out doesn't change Biela's behavior.
# Remove once every business has settings.hours_text + settings.menu_url.
_LEGACY_DEFAULT_BUSINESS_NAME = "BIELA FAST FOOD"
_LEGACY_DEFAULT_MENU_URL = "https://gixlink.com/Biela"
_LEGACY_DEFAULT_HOURS_LINE = (
    "Recuerda que nuestro horario de atención al público es "
    "de 5:30 PM a 10:00 PM de lunes a viernes."
)


def get_greeting(
    business_context: Optional[dict],
    customer_name: Optional[str],
) -> str:
    """
    Build the business greeting reply.

    Reads name + menu_url + hours from business_context.business.settings.
    Falls back to the legacy Biela defaults so existing traffic sees no
    change until settings are populated per-business.
    """
    business_name = _LEGACY_DEFAULT_BUSINESS_NAME
    menu_url = _LEGACY_DEFAULT_MENU_URL
    hours_line = _LEGACY_DEFAULT_HOURS_LINE

    if business_context and business_context.get("business"):
        biz = business_context["business"]
        business_name = (biz.get("name") or business_name).strip()
        settings = biz.get("settings") or {}
        menu_url = (settings.get("menu_url") or menu_url).strip()
        custom_hours = (settings.get("hours_text") or "").strip()
        if custom_hours:
            hours_line = custom_hours

    customer_name = _first_name(customer_name)
    has_real_name = (
        customer_name
        and customer_name.lower() not in ("usuario", "cliente", "user")
    )
    opener = f"Hola {customer_name}.\n\n" if has_real_name else ""

    return (
        f"{opener}"
        f"Gracias por comunicarte con {business_name}. ¿Cómo podemos ayudarte?\n\n"
        "🍔🍟🔥😁\n\n"
        f"{hours_line}\n\n"
        f"{menu_url}"
    )
