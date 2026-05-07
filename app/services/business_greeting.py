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
# Remove once every business has settings.menu_url.
_LEGACY_DEFAULT_BUSINESS_NAME = "BIELA FAST FOOD"
_LEGACY_DEFAULT_MENU_URL = "https://gixlink.com/Biela"


def get_greeting(
    business_context: Optional[dict],
    customer_name: Optional[str],
) -> str:
    """
    Build the plain-text greeting reply — body matches the Twilio CTA
    template's `rendered_body`, with the menu URL appended on its own
    line as the button replacement (plain text has no clickable card).

    Used when the business has no Twilio CTA configured (Meta path or
    a Twilio business without `welcome_content_sid`). Reads name +
    menu_url from business_context.business.settings; falls back to the
    legacy Biela defaults.
    """
    business_name = _LEGACY_DEFAULT_BUSINESS_NAME
    menu_url = _LEGACY_DEFAULT_MENU_URL

    if business_context and business_context.get("business"):
        biz = business_context["business"]
        business_name = (biz.get("name") or business_name).strip()
        settings = biz.get("settings") or {}
        menu_url = (settings.get("menu_url") or menu_url).strip()

    first = _first_name(customer_name)
    has_real_name = first and first.lower() not in ("usuario", "cliente", "user")
    opener = f"Hola {first} " if has_real_name else "Hola "

    body = (
        f"{opener}👋 Bienvenido a {business_name} 🍔🔥\n"
        "¿Qué se te antoja hoy? Estamos listos para ayudarte"
    )
    if menu_url:
        body += f"\n\n{menu_url}"
    return body


def cta_welcome_payload(
    business_context: Optional[dict],
    customer_name: Optional[str],
) -> Optional[dict]:
    """
    Return CTA Content Template payload when this business should send the
    welcome via a button-styled card; None otherwise (caller falls back to
    the plain-text greeting).

    Activation: business.settings.welcome_content_sid set + provider=twilio.
    The Content Template at that SID must define exactly:
      {{1}} = business name
      {{2}} = name fragment of the opener — either "Hola <Name> " (with
              trailing space) or "Hola " when the contact's name is
              unknown. Carrying it as a variable lets us cleanly drop
              the name without breaking the rest of the sentence.
    The CTA URL is hardcoded inside the template (Twilio rejects a pure
    variable in the action.url field — URL is per-business anyway, so
    bake it into the template at creation time).

    Returns: {"content_sid", "variables", "rendered_body"}.
    rendered_body is the plain-text version we persist to conversation
    history; it must match what the customer sees on WhatsApp so the
    inbox UI is consistent.
    """
    if not business_context or business_context.get("provider") != "twilio":
        return None
    biz = business_context.get("business") or {}
    settings = biz.get("settings") or {}
    content_sid = (settings.get("welcome_content_sid") or "").strip()
    if not content_sid:
        return None
    business_name = (biz.get("name") or _LEGACY_DEFAULT_BUSINESS_NAME).strip()
    first = _first_name(customer_name)
    has_real_name = first and first.lower() not in ("usuario", "cliente", "user")
    opener = f"Hola {first} " if has_real_name else "Hola "
    variables = {"1": business_name, "2": opener}
    rendered_body = (
        f"{opener}👋 Bienvenido a {business_name} 🍔🔥\n"
        "¿Qué se te antoja hoy? Estamos listos para ayudarte"
    )
    return {
        "content_sid": content_sid,
        "variables": variables,
        "rendered_body": rendered_body,
    }
