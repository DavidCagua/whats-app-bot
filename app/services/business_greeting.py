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


# Pure greeting detection: message is ONLY a sequence of one or more
# greeting tokens (no product, no question, no substantive content).
# Compound greetings ("hola buenas noches", "buenas qué más", "hola
# qué tal") count — production trace 2026-05-09 showed the LLM-only
# fallback misclassifying these into ``order``, so the deterministic
# path now handles compounds too instead of relying on prompt fidelity.
#
# Order of alternation matters: longer multi-token tokens
# (``buenas tardes``, ``buenos días``) MUST come before the shorter
# single-word tokens that prefix them (``buenas``, ``buenos``), or
# Python's leftmost-first matching would consume just the prefix.
_GREETING_TOKEN = (
    r"(?:"
    r"hola+|"
    r"buenas\s+tardes?|"
    r"buenas\s+noches?|"
    r"buenos?\s+d[ií]as?|"
    r"buen\s+d[ií]a|"
    r"qu[eé]\s+m[aá]s|"
    r"qu[eé]\s+tal|"
    r"qu[eé]\s+hubo|"
    r"c[oó]mo\s+est[aá](?:s|n)?|"
    r"buenas|"
    r"hey+|"
    r"ey+|"
    r"saludos"
    r")"
)
_PURE_GREETING_RE = re.compile(
    r"^\s*"
    + _GREETING_TOKEN
    + r"(?:[\s,!¡.?¿;:]+" + _GREETING_TOKEN + r"){0,3}"  # up to 3 more tokens
    + r"[\s!¡.,?¿;:]*$",
    re.IGNORECASE,
)


def is_pure_greeting(message: Optional[str]) -> bool:
    """Return True when the message is nothing but greeting token(s).

    Single ("hola", "buenas tardes") and compound ("hola buenas noches",
    "hey qué tal") forms both match. Anything substantive after the
    greeting (a product, a question with content words, "un domicilio")
    falls through to the LLM classifier as before.
    """
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


def _closed_sentence_from_gate(
    gate: Optional[dict],
    business: Optional[dict] = None,
) -> str:
    """
    Render the live "we're closed, opens at X" sentence from the gate
    payload returned by ``business_info_service.is_taking_orders_now``.
    Returns "" when the gate is None / open / no_data — caller should
    treat empty as "no override".

    Mirrors ``business_info_service.format_open_status_sentence`` so
    the closed prose is identical across the welcome greeting and the
    CS ``order_closed`` handoff branch.

    When ``business`` is provided AND today is fully closed (no opening
    window at all) AND ``business.settings.closed_day_alt_contact`` is
    configured, appends "Si necesitas pedir hoy, escríbele a <name> al
    <phone>." — same suffix used by the CS order_closed handoff.
    """
    if not gate or gate.get("can_take_orders") or gate.get("reason") != "closed":
        return ""
    try:
        from . import business_info_service as _bi_svc
        # format_open_status_sentence expects the compute_open_status
        # shape; gate's fields are a strict superset, so we can pass it
        # as-is for the closed branch.
        synthesized = {
            "is_open": False,
            "has_data": True,
            "opens_at": gate.get("opens_at"),
            "closes_at": None,
            "next_open_dow": gate.get("next_open_dow"),
            "next_open_time": gate.get("next_open_time"),
            "now_local": gate.get("now_local"),
        }
        sentence = _bi_svc.format_open_status_sentence(synthesized)
        if business is not None and _bi_svc.is_fully_closed_today(synthesized):
            sentence = sentence + _bi_svc.format_closed_alt_contact_suffix(business)
        return sentence
    except Exception:
        return "Por ahora estamos cerrados."


def _is_fully_closed_today_from_gate(gate: Optional[dict]) -> bool:
    """Lightweight wrapper so callers don't have to synthesize the status shape."""
    if not gate or gate.get("can_take_orders") or gate.get("reason") != "closed":
        return False
    try:
        from . import business_info_service as _bi_svc
        return _bi_svc.is_fully_closed_today({
            "is_open": False,
            "has_data": True,
            "opens_at": gate.get("opens_at"),
            "closes_at": None,
            "next_open_dow": gate.get("next_open_dow"),
            "next_open_time": gate.get("next_open_time"),
            "now_local": gate.get("now_local"),
        })
    except Exception:
        return False


def get_greeting(
    business_context: Optional[dict],
    customer_name: Optional[str],
    gate: Optional[dict] = None,
) -> str:
    """
    Build the plain-text greeting reply — body matches the Twilio CTA
    template's `rendered_body`, with the menu URL appended on its own
    line as the button replacement (plain text has no clickable card).

    Used when the business has no Twilio CTA configured (Meta path or
    a Twilio business without `welcome_content_sid`). Reads name +
    menu_url from business_context.business.settings; falls back to the
    legacy Biela defaults.

    When ``gate`` indicates the business is closed, the greeting
    announces it inline ("Por ahora estamos cerrados…") so customers
    don't have to send a product to discover the shop is closed.
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

    business_for_suffix = (
        (business_context or {}).get("business") if business_context else None
    )
    closed_sentence = _closed_sentence_from_gate(gate, business=business_for_suffix)
    fully_closed = _is_fully_closed_today_from_gate(gate)
    if closed_sentence:
        body = (
            f"{opener}👋 Bienvenido a {business_name} 🍔🔥\n"
            f"\n"
            f"{closed_sentence}\n"
            f"\n"
            "Mientras tanto puedo contarte del menú o resolverte cualquier duda."
        )
    else:
        body = (
            f"{opener}👋 Bienvenido a {business_name} 🍔🔥\n"
            "¿Qué se te antoja hoy? Estamos listos para ayudarte"
        )
    # On a fully-closed-today greeting, do NOT append the menu URL.
    # The customer can still request it via CS later; surfacing it here
    # encourages a multi-step order path that ends in disappointment
    # (production incident: +573172908887 on 2026-05-11).
    if menu_url and not fully_closed:
        body += f"\n\n{menu_url}"
    return body


def cta_welcome_payload(
    business_context: Optional[dict],
    customer_name: Optional[str],
    gate: Optional[dict] = None,
) -> Optional[dict]:
    """
    Return CTA Content Template payload when this business should send the
    welcome via a button-styled card; None otherwise (caller falls back to
    the plain-text greeting).

    Two templates are supported per business:

    - ``welcome_content_sid`` — open-state greeting (existing). Body uses
      ``{{1}}`` (business name) and ``{{2}}`` (opener fragment).
    - ``welcome_closed_content_sid`` — closed-state greeting (new).
      Same {{1}}/{{2}} plus ``{{3}}`` carrying the live closed
      sentence. Selected when ``gate`` says ``can_take_orders=False``.

    Selection rules when ``gate`` indicates closed:
      1. ``welcome_closed_content_sid`` set → render closed CTA.
      2. Otherwise → return None so the caller falls back to the
         plain-text greeting (which will include the closed sentence
         via ``get_greeting`` + ``gate``).

    Returns: ``{"content_sid", "variables", "rendered_body", "kind"}``.
    ``rendered_body`` is the plain-text version persisted to
    conversation history; must match what the customer sees on
    WhatsApp so the inbox UI stays consistent. ``kind`` is one of
    ``"open_cta"`` / ``"closed_cta"`` for log/trace.
    """
    if not business_context or business_context.get("provider") != "twilio":
        return None
    biz = business_context.get("business") or {}
    settings = biz.get("settings") or {}
    business_name = (biz.get("name") or _LEGACY_DEFAULT_BUSINESS_NAME).strip()
    first = _first_name(customer_name)
    has_real_name = first and first.lower() not in ("usuario", "cliente", "user")
    opener = f"Hola {first} " if has_real_name else "Hola "

    is_closed = bool(gate and not gate.get("can_take_orders") and gate.get("reason") == "closed")
    fully_closed_today = _is_fully_closed_today_from_gate(gate)

    if is_closed:
        # On a fully-closed-today greeting, intentionally skip the Twilio
        # CTA. The card's "Ver carta" button entices customers to browse
        # and build a cart only to discover at submit time that the shop
        # is closed (production incident: +573172908887 on 2026-05-11).
        # Plain-text greeting handles closed days; it renders the closed
        # sentence + alt-branch contact line inline via get_greeting.
        if fully_closed_today:
            return None
        closed_sid = (settings.get("welcome_closed_content_sid") or "").strip()
        if not closed_sid:
            # No closed-state template configured — caller falls back
            # to the plain-text greeting, which renders the closed
            # sentence inline via get_greeting(gate=...).
            return None
        closed_sentence = _closed_sentence_from_gate(gate, business=biz) or "Por ahora estamos cerrados."
        variables = {"1": business_name, "2": opener, "3": closed_sentence}
        rendered_body = (
            f"{opener}👋 Bienvenido a {business_name} 🍔🔥\n"
            f"\n"
            f"{closed_sentence}\n"
            f"\n"
            "Mientras tanto puedo contarte del menú o resolverte cualquier duda."
        )
        return {
            "content_sid": closed_sid,
            "variables": variables,
            "rendered_body": rendered_body,
            "kind": "closed_cta",
        }

    # Open-state path: requires the open-template SID.
    content_sid = (settings.get("welcome_content_sid") or "").strip()
    if not content_sid:
        return None
    variables = {"1": business_name, "2": opener}
    rendered_body = (
        f"{opener}👋 Bienvenido a {business_name} 🍔🔥\n"
        "¿Qué se te antoja hoy? Estamos listos para ayudarte"
    )
    return {
        "content_sid": content_sid,
        "variables": variables,
        "rendered_body": rendered_body,
        "kind": "open_cta",
    }
