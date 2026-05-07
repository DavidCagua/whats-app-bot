"""
Twilio Content Template payload for the "confirm order" step.

Mirrors ``business_greeting.cta_welcome_payload``. When a business has
``settings.confirm_order_content_sid`` configured AND the provider is
Twilio AND we have all the delivery data, the order agent sends a
button-styled card with two quick replies (Confirm / Change) instead of
asking ``¿Procedemos o quieres cambiar algo?`` as plain text. The button
tap arrives as a normal inbound text message — Twilio fills ``Body``
with the button's title — so the existing planner's CONFIRMACIÓN rule
handles the affirmative path with no router/agent changes.

Twilio Content Template requirements
------------------------------------
Create a ``twilio/quick-reply`` Content Template with:

  Body: free-form text. Use ``{{1}}`` somewhere to render the recap
        block (multi-line, supports ``\\n``). Example:

            Tengo estos datos para tu pedido:

            {{1}}

            ¿Confirmamos el pedido?

  Buttons (two, exactly):
    - Title: "Confirmar pedido"   (planner reads this as CONFIRM)
    - Title: "Cambiar algo"       (planner reads this as CHAT — agent
                                   prompts "¿qué te gustaría cambiar?")

  No URL action; quick-reply only. The button title is what the customer
  sees AND what arrives in the inbound webhook ``Body``, so keep titles
  short, unambiguous, and aligned with the planner's CONFIRMACIÓN list.

Configure per business
----------------------
Set ``business.settings.confirm_order_content_sid = "<HX...>"`` for any
business that should use the CTA. Businesses without it keep the current
plain-text confirmation. No DB migration — settings is JSONB.
"""

from __future__ import annotations

from typing import Optional


def _format_money(amount) -> str:
    """Colombian peso formatting: ``$28000`` → ``$28.000``."""
    try:
        return f"${int(amount):,}".replace(",", ".")
    except (TypeError, ValueError):
        return ""


def _summary_block(delivery_status: dict) -> str:
    """
    One-shot recap rendered into the template's ``{{1}}`` variable.

    Includes: name, address, phone, payment method, total. Each on its
    own line with bold labels (WhatsApp markdown). Missing fields are
    omitted rather than printed as "(no registrado)" — the caller only
    triggers the CTA when ``all_present`` is True so this is mostly a
    belt-and-suspenders.
    """
    parts: list[str] = []
    name = (delivery_status.get("name") or "").strip()
    address = (delivery_status.get("address") or "").strip()
    phone = (delivery_status.get("phone") or "").strip()
    payment = (delivery_status.get("payment_method") or "").strip()
    total = delivery_status.get("total")
    if name:
        parts.append(f"*Nombre:* {name}")
    if address:
        parts.append(f"*Dirección:* {address}")
    if phone:
        parts.append(f"*Teléfono:* {phone}")
    if payment:
        parts.append(f"*Pago:* {payment}")
    if total:
        parts.append(f"*Total:* {_format_money(total)}")
    return "\n".join(parts)


def cta_confirm_order_payload(
    business_context: Optional[dict],
    delivery_status: Optional[dict],
) -> Optional[dict]:
    """
    Return a ``send_twilio_cta`` payload when this business should send
    the confirm-order step as a button card; ``None`` otherwise.

    Activation gate: provider == 'twilio' AND
    ``business.settings.confirm_order_content_sid`` set AND
    ``delivery_status['all_present']`` is True. Caller falls back to the
    LLM-generated plain-text prompt on any None return.

    Returns: ``{"content_sid", "variables", "rendered_body"}``.
    ``rendered_body`` is what we persist to ``conversations`` so the
    inbox UI and the planner's recent-history view both match what the
    customer actually sees on WhatsApp.
    """
    if not business_context or business_context.get("provider") != "twilio":
        return None
    if not delivery_status or not delivery_status.get("all_present"):
        return None
    biz = business_context.get("business") or {}
    settings = biz.get("settings") or {}
    content_sid = (settings.get("confirm_order_content_sid") or "").strip()
    if not content_sid:
        return None

    summary = _summary_block(delivery_status)
    if not summary:
        return None

    variables = {"1": summary}
    rendered_body = (
        "Tengo estos datos para tu pedido:\n\n"
        f"{summary}\n\n"
        "¿Confirmamos el pedido?"
    )
    return {
        "content_sid": content_sid,
        "variables": variables,
        "rendered_body": rendered_body,
    }
