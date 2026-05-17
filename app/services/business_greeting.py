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

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# Window after which the welcome CTA is allowed to fire again for the
# same customer. Set to 3 hours: a Biela shift is ~5 hours and an order
# resolves in at most ~2 hours, so 3h reliably covers "same visit"
# without re-greeting customers who come back hours later.
GREETING_REPEAT_WINDOW = timedelta(hours=3)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def stamp_welcome_sent(wa_id: str, business_id: Optional[str]) -> None:
    """Record that the welcome was dispatched to this customer.

    Writes ``last_welcome_sent_at`` (ISO-8601 UTC) into session
    order_context. Both the welcome CTA path and the plain-text
    fallback should call this on success so the router's
    ``was_recently_greeted`` check is consistent across paths.
    """
    if not wa_id or not business_id:
        return
    try:
        from ..database.session_state_service import session_state_service
        session_state_service.save(
            wa_id, str(business_id),
            {"order_context": {"last_welcome_sent_at": _now_utc().isoformat()}},
        )
    except Exception as exc:
        logger.warning("[GREETING] stamp_welcome_sent failed: %s", exc)


def was_recently_greeted(
    wa_id: str,
    business_id: Optional[str],
    *,
    window: timedelta = GREETING_REPEAT_WINDOW,
) -> bool:
    """Return True if the bot dispatched the welcome to this customer
    within ``window``. Used by the router to suppress repeat welcomes
    when the conversation is still active.

    Failure-open: if the session can't be loaded, returns False (treats
    it as "no prior greeting"). The cost of an extra welcome on the
    cold-start path is small; the cost of suppressing a legit welcome
    on a stale-session read is larger.
    """
    if not wa_id or not business_id:
        return False
    try:
        from ..database.session_state_service import session_state_service
        result = session_state_service.load(wa_id, str(business_id))
        order_ctx = (result or {}).get("session", {}).get("order_context") or {}
        ts_str = order_ctx.get("last_welcome_sent_at")
        if not ts_str:
            return False
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (_now_utc() - ts) < window
    except Exception as exc:
        logger.warning("[GREETING] was_recently_greeted check failed: %s", exc)
        return False


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


# Biela restaurant tenants keep the legacy restaurant greeting (🍔🔥 +
# "¿Qué se te antoja hoy?" + Biela menu URL fallback). Everyone else
# gets a neutral booking-friendly opener and skips the legacy defaults.
_BIELA_BUSINESS_IDS = frozenset({
    "4144785f-154d-4b4d-8ff5-a10dac356862",
    "44488756-473b-46d2-a907-9f579e98ecfd",
})


def _is_biela_business(business_context: Optional[dict]) -> bool:
    if not business_context:
        return False
    return str(business_context.get("business_id") or "") in _BIELA_BUSINESS_IDS


def _closed_sentence_from_gate(
    gate: Optional[dict],
    business: Optional[dict] = None,
) -> str:
    """
    Render the live "we're closed, opens at X" sentence from the gate
    payload returned by ``business_info_service.is_taking_orders_now``.
    Returns "" when the gate is None / open / no_data — caller should
    treat empty as "no override".

    Covers two not-taking-orders shapes:
      - ``reason == 'closed'``: outside business hours, render the
        same prose used by the CS ``order_closed`` handoff branch.
      - ``reason == 'delivery_paused'``: operator flipped the
        delivery-paused switch on the orders page. Render a
        deterministic "estamos llenos por ahora" sentence — no
        scheduled reopen time, the operator decides when.

    When ``business`` is provided AND today is fully closed (no opening
    window at all) AND ``business.settings.alt_branch_contact`` is
    configured, appends "Si necesitas pedir hoy, escríbele a <name> al
    <phone>." — same suffix used by the CS order_closed handoff.
    """
    if not gate or gate.get("can_take_orders"):
        return ""
    reason = gate.get("reason")
    if reason == "delivery_paused":
        return (
            "Por ahora estamos al tope de pedidos y no estamos tomando "
            "más por el momento."
        )
    if reason != "closed":
        return ""
    try:
        from . import business_info_service as _bi_svc
        synthesized = _status_from_gate(gate)
        sentence = _bi_svc.format_open_status_sentence(synthesized)
        if business is not None and _bi_svc.is_fully_closed_today(synthesized):
            sentence = sentence + _bi_svc.format_alt_branch_suffix(business, "closed")
        return sentence
    except Exception:
        return "Por ahora estamos cerrados."


def _high_demand_notice_from_gate(
    gate: Optional[dict],
    business: Optional[dict] = None,
) -> str:
    """
    Build the "alta demanda" warning when the operator set a delivery-ETA
    override. Returns "" when no override is active or the shop is
    closed / paused (other gate messages already convey the situation).

    Format: "⚠️ Hoy tenemos alta demanda — el tiempo de entrega está
    en <range_text>." The range_text matches the dashboard dropdown
    label phrasing (e.g. "1 hora 10 minutos a 1 hora 20 minutos") so
    the operator and the customer see the same numbers.

    When ``business`` is provided AND
    ``business.settings.alt_branch_contact`` is configured, appends
    "Si necesitas que sea más rápido, nuestra sede <name> está más
    descargada — escribe al <phone>." so customers in a hurry have
    an out — same data field that powers the closed-day suffix.
    """
    if not gate:
        return ""
    if not gate.get("can_take_orders"):
        return ""
    override = gate.get("delivery_eta_override")
    if not override or not isinstance(override, dict):
        return ""
    range_text = (override.get("range_text") or "").strip()
    if not range_text:
        return ""
    notice = (
        f"⚠️ Hoy tenemos alta demanda — el tiempo de entrega está "
        f"en {range_text}."
    )
    if business is not None:
        try:
            from . import business_info_service as _bi_svc
            notice = notice + _bi_svc.format_alt_branch_suffix(business, "high_demand")
        except Exception:
            pass
    return notice


def _status_from_gate(gate: dict) -> dict:
    """
    Synthesize the ``compute_open_status`` shape from a gate dict.

    Shared by ``_closed_sentence_from_gate`` and
    ``_is_fully_closed_today_from_gate`` so the two can't drift on
    which fields they thread through. Production 2026-05-11 / Biela:
    the two helpers used to inline-build their dicts and one was
    updated for ``today_had_window`` while the other wasn't — the
    closed CTA's `{{3}}` variable then incorrectly carried the
    alt-branch contact line for past-close-with-window cases. DRYing
    here removes the drift surface.

    Caller MUST have already verified ``gate`` is the closed payload
    (``can_take_orders=False`` AND ``reason='closed'``).
    """
    return {
        "is_open": False,
        "has_data": True,
        "opens_at": gate.get("opens_at"),
        "closes_at": None,
        "next_open_dow": gate.get("next_open_dow"),
        "next_open_time": gate.get("next_open_time"),
        # Distinguishes past-close-but-had-slot (Monday 9am-10pm shop,
        # message at 10:20pm — NOT fully closed) from no-slot-today
        # (Sunday for a Mon-Sat shop — fully closed). is_taking_orders_now
        # populates this field; gates from older code paths may not.
        "today_had_window": gate.get("today_had_window", False),
        "now_local": gate.get("now_local"),
    }


def _is_fully_closed_today_from_gate(gate: Optional[dict]) -> bool:
    """Lightweight wrapper so callers don't have to synthesize the status shape."""
    if not gate or gate.get("can_take_orders") or gate.get("reason") != "closed":
        return False
    try:
        from . import business_info_service as _bi_svc
        return _bi_svc.is_fully_closed_today(_status_from_gate(gate))
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
    is_biela = _is_biela_business(business_context)
    business_name = _LEGACY_DEFAULT_BUSINESS_NAME if is_biela else ""
    menu_url = _LEGACY_DEFAULT_MENU_URL if is_biela else ""

    if business_context and business_context.get("business"):
        biz = business_context["business"]
        business_name = (biz.get("name") or business_name).strip()
        settings = biz.get("settings") or {}
        menu_url = (settings.get("menu_url") or menu_url).strip()

    first = _first_name(customer_name)
    has_real_name = first and first.lower() not in ("usuario", "cliente", "user")
    opener = f"Hola {first} " if has_real_name else "Hola "

    # Brand-flavored copy only for Biela; everyone else gets a neutral
    # booking-friendly opener that works for clinics, salons, etc.
    welcome_line = (
        f"{opener}👋 Bienvenido a {business_name} 🍔🔥"
        if is_biela
        else f"{opener}👋 Bienvenido a {business_name}".rstrip()
    )
    cta_line = (
        "¿Qué se te antoja hoy? Estamos listos para ayudarte"
        if is_biela
        else "¿En qué te puedo ayudar hoy?"
    )

    business_for_suffix = (
        (business_context or {}).get("business") if business_context else None
    )
    closed_sentence = _closed_sentence_from_gate(gate, business=business_for_suffix)
    high_demand_notice = _high_demand_notice_from_gate(gate, business=business_for_suffix)
    if closed_sentence:
        body = (
            f"{welcome_line}\n"
            f"\n"
            f"{closed_sentence}\n"
            f"\n"
            "Mientras tanto puedo contarte del menú o resolverte cualquier duda."
        )
    elif high_demand_notice:
        # Operator-flagged high demand: warn up front so the customer
        # decides before committing. Notice goes BEFORE the CTA prompt
        # so it's the first thing they read.
        body = (
            f"{welcome_line}\n"
            f"\n"
            f"{high_demand_notice}\n"
            f"\n"
            f"{cta_line}"
        )
    else:
        body = (
            f"{welcome_line}\n"
            f"{cta_line}"
        )
    # Suppress the menu URL on ANY closed-state greeting (fully closed,
    # mid-day break, past today's close). Surfacing it encourages a
    # multi-step order path that ends in disappointment at submit time
    # — same rationale that retired the Twilio CTA on closed states.
    # Customers can still request the menu via CS while closed.
    # Production incident: +573172908887 on 2026-05-11; extended to
    # all closed states 2026-05-12.
    if menu_url and not closed_sentence:
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

    Three templates are supported per business:

    - ``welcome_content_sid`` — open-state greeting. Body uses
      ``{{1}}`` (business name) and ``{{2}}`` (opener fragment).
    - ``welcome_closed_content_sid`` — closed-state greeting. Same
      ``{{1}}/{{2}}`` plus ``{{3}}`` carrying the live closed
      sentence. Selected when ``gate`` says ``can_take_orders=False``
      and ``reason == 'closed'``. Currently we prefer the plain-text
      fallback so this branch returns None.
    - ``welcome_high_demand_content_sid`` — open-shop-with-ETA-override
      greeting. Same ``{{1}}/{{2}}`` plus ``{{3}}`` carrying the
      "alta demanda" warning sentence. Selected when the operator
      set ``businesses.settings.delivery_eta_minutes`` from the
      orders page AND this SID is configured. When the SID is NOT
      configured, returns None so plain-text greeting (which also
      surfaces the warning) takes over.

    Selection rules:
      - Open shop, no override → ``welcome_content_sid`` CTA.
      - Open shop, ETA override active, high-demand SID configured →
        ``welcome_high_demand_content_sid`` CTA with warning baked in.
      - Open shop, ETA override active, SID missing → None.
      - Any closed state (fully closed OR closed-for-a-period) → None.
      - delivery_paused (operator switch on orders page) → None.

    The "Ver carta" button on either card encourages a browse-and-build
    loop that ends in disappointment when submit-time hits the closed
    gate. Plain text + a clear closed sentence is the safer surface
    while closed — same reasoning that already retired the card on
    fully-closed days (production incident +573172908887 / 2026-05-11),
    now extended to past-close and mid-day break.

    Returns: ``{"content_sid", "variables", "rendered_body", "kind"}``.
    ``rendered_body`` is the plain-text version persisted to
    conversation history; must match what the customer sees on
    WhatsApp so the inbox UI stays consistent. ``kind`` is
    ``"open_cta"`` for log/trace.
    """
    if not business_context or business_context.get("provider") != "twilio":
        return None
    biz = business_context.get("business") or {}
    settings = biz.get("settings") or {}
    business_name = (biz.get("name") or _LEGACY_DEFAULT_BUSINESS_NAME).strip()
    first = _first_name(customer_name)
    has_real_name = first and first.lower() not in ("usuario", "cliente", "user")
    opener = f"Hola {first} " if has_real_name else "Hola "

    # Closed in ANY shape → no CTA. Plain-text greeting via get_greeting()
    # handles the closed sentence (and alt-branch on fully-closed days).
    is_closed = bool(gate and not gate.get("can_take_orders") and gate.get("reason") == "closed")
    if is_closed:
        return None
    # Same rationale for delivery-paused: the CS pause copy is plain-text
    # only, so fall through.
    is_paused = bool(gate and gate.get("reason") == "delivery_paused")
    if is_paused:
        return None
    # High-demand override active: prefer the dedicated CTA template
    # (operator pre-provisioned it in Twilio + saved the SID). The
    # warning sentence lands in {{3}}, customer still gets the
    # "Ver carta" button so they can place a delayed order. If the
    # SID is missing, fall back to plain-text greeting so the
    # warning still reaches the customer. When alt_branch_contact is
    # configured the suffix is appended into {{3}} so the same Twilio
    # template renders the demand sentence + sibling-branch fallback
    # without needing a fourth variable.
    high_demand_notice = _high_demand_notice_from_gate(gate, business=biz)
    if high_demand_notice:
        high_demand_sid = (
            settings.get("welcome_high_demand_content_sid") or ""
        ).strip()
        if not high_demand_sid:
            return None
        # Body template carries the "⚠️" prefix in literal text, so the
        # {{3}} variable should be just the demand sentence (and the
        # optional alt-branch suffix) without the warning emoji to
        # avoid double-rendering. The plain-text notice from
        # _high_demand_notice_from_gate prepends "⚠️ "; strip it.
        notice_for_cta = high_demand_notice
        if notice_for_cta.startswith("⚠️ "):
            notice_for_cta = notice_for_cta[len("⚠️ "):]
        variables = {"1": business_name, "2": opener, "3": notice_for_cta}
        rendered_body = (
            f"{opener}👋 Bienvenido a {business_name} 🍔🔥\n"
            f"\n"
            f"{high_demand_notice}\n"
            f"\n"
            "¿Qué se te antoja hoy? Estamos listos para ayudarte"
        )
        return {
            "content_sid": high_demand_sid,
            "variables": variables,
            "rendered_body": rendered_body,
            "kind": "high_demand_cta",
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
