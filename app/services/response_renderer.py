"""
Response renderer for the chained order-agent architecture.

The action agent emits a structured envelope (via the ``respond`` tool).
This module turns that envelope into a typed user-facing payload —
either plain text, a Twilio CTA Content Template (button card), or
later other channel primitives (interactive list, media, etc.).

Why this lives outside the agent
--------------------------------
- The action agent's job is "what should happen?" (call tools, mutate
  state, decide it's time to respond). It does not own how we talk to
  the user on this channel.
- This renderer's job is "how do we say it on WhatsApp, in this
  business's voice?" Voice rules, locale, channel-specific affordances
  (CTA cards), and length constraints all live here.
- Separation also bounds hallucination: the renderer sees only the
  envelope (kind + summary + facts), not the full reasoning context.
  The prompt forbids inventing names/numbers and forbids quoting any
  number that doesn't appear in ``facts``.

Output shape (``RenderedResponse``)
-----------------------------------
``type``:
    ``"text"`` — plain WhatsApp message body. Caller sends as text.
    ``"cta"``  — Twilio Content Template (quick-reply card). Caller
                 dispatches via ``send_twilio_cta(content_sid, variables, ...)``.
                 ``body`` carries the rendered text we persist for
                 conversation history (so the inbox UI matches what the
                 customer sees).

CTA short-circuit
-----------------
Only ``ready_to_confirm`` envelopes try the CTA path today. The renderer
re-reads delivery state from the canonical sources (session +
customers row) — same logic as ``order_tools.get_customer_info`` —
rather than trusting whatever the agent put in ``facts``. Mirrors the
v1 behaviour at ``app/agents/order_agent.py`` (confirm-order CTA branch).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, TypedDict

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage


class RenderedResponse(TypedDict, total=False):
    type: str
    body: str
    content_sid: Optional[str]
    variables: Optional[Dict[str, str]]


_RENDER_SYSTEM_PROMPT = """Eres el renderizador de respuestas para {business_name}, un asistente de pedidos por WhatsApp en español colombiano.

Tu trabajo es UNA cosa: redactar UNA línea corta de prefacio para acompañar el mensaje del cliente. El sistema agrega automáticamente el desglose del carrito y la pregunta de cierre — NO los escribas tú.

Reglas estrictas:
- UNA sola línea, 8-15 palabras máximo.
- Tono: cálido, breve, colombiano natural — como un mesero. NUNCA saludes (Hola/Buenas).
- NUNCA menciones precios, totales ni cantidades específicas — el desglose del carrito viene aparte.
- Para items_added: confirma brevemente lo que agregaste, e.g. "Listo, agregamos eso a tu pedido."
- Para items_removed: confirma que se quitó, e.g. "Listo, quitamos ese producto."
- Para cart_updated: confirma el cambio, e.g. "Listo, ajustamos el ítem."
- Para cart_view: una entrada como "Este es tu pedido actual:" o "Aquí va tu pedido:".
- NO termines con pregunta — el sistema agrega la pregunta de cierre.

Información del negocio (para tono / contexto):
{business_info}
"""

_RENDER_GENERIC_SYSTEM_PROMPT = """Eres el renderizador de respuestas para {business_name}, un asistente de pedidos por WhatsApp en español colombiano.

Tu trabajo: convertir el envelope estructurado del agente en un mensaje final breve y natural. NO ejecutas herramientas, solo redactas.

Reglas estrictas:
- Tono: cálido, breve, natural — como un mesero. 1-4 líneas máximo.
- NO saludes (Hola/Buenas). La conversación está en curso.
- NUNCA inventes nombres, precios ni IDs. Solo puedes citar valores que aparezcan en `facts` o en el `summary`.
- Si necesitas un número y NO está en facts/summary, reformula sin él.
- Para menu_info/info/chat: termina con una pregunta abierta breve.
- Para delivery_info_collected: confirma brevemente lo guardado y pide SOLO los campos que faltan (los facts indican qué falta). NUNCA preguntes "¿algo más a tu pedido?" — el cliente ya está en fase de checkout.
- Para order_placed: confirma con calidez, sin pregunta.
- Para error/out_of_scope: pide disculpas o reorienta sin pregunta forzada.
- Para disambiguation: lista las opciones del envelope y pregunta cuál prefiere.

Información del negocio:
{business_info}
"""


_RENDER_USER_TEMPLATE = """El cliente acaba de decir: "{last_user_message}"

Envelope del agente:
- kind: {kind}
- summary: {summary}
- facts: {facts}

Redacta la respuesta final al cliente."""


_renderer_llm: Optional[ChatOpenAI] = None


def _get_renderer_llm() -> ChatOpenAI:
    """Cheap, low-temperature renderer model. Lazy-init."""
    global _renderer_llm
    if _renderer_llm is None:
        _renderer_llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.4,
            api_key=os.getenv("OPENAI_API_KEY"),
        )
    return _renderer_llm


CART_BREAKDOWN_KINDS = frozenset({
    "items_added", "items_removed", "cart_updated", "cart_view",
})

# Kinds where the agent's prompt may close with "¿Quieres pedirla?" — fine
# when the cart is empty, wrong when the customer is asking a follow-up
# about an item already in their pedido. Post-processed below.
INFO_KINDS_WITH_UPSELL_RISK = frozenset({"product_info", "menu_info"})

# Matches the trailing "would you like to order it?" sentence variants the
# order agent tends to emit (per its system prompt). Anchored to end-of-body
# so we only strip the closing question, not info embedded mid-paragraph.
_UPSELL_CLOSER_RE = re.compile(
    r"\s*¿\s*(?:te\s+)?(?:gustar[íi]a|quieres|deseas|quisieras)\s+"
    r"(?:pedir(?:la|lo|las|los)?|"
    r"agregar(?:la|lo|las|los)?|"
    r"a[ñn]adir(?:la|lo|las|los)?|"
    r"ordenar(?:la|lo|las|los)?|"
    r"incluir(?:la|lo|las|los)?|"
    r"sumar(?:la|lo|las|los)?|"
    r"que\s+(?:te\s+)?la\s+agregue|"
    r"que\s+(?:te\s+)?lo\s+agregue)"
    r"[^?\n]*\?\s*$",
    re.IGNORECASE,
)

_ACTIVE_CART_CLOSER = "¿Te gustaría añadir algo más o procedemos con el pedido?"


def _cart_has_items(business_context: Optional[Dict], wa_id: str) -> bool:
    """True when the canonical session cart for this user has at least one item."""
    if not business_context or not wa_id:
        return False
    business_id = (business_context or {}).get("business_id") or ""
    if not business_id:
        return False
    try:
        from ..database.session_state_service import session_state_service
        state_result = session_state_service.load(wa_id, business_id) or {}
        session = state_result.get("session") or {}
        items = (session.get("order_context") or {}).get("items") or []
        return bool(items)
    except Exception as exc:
        logging.warning("[RENDERER] cart-has-items check failed: %s", exc)
        return False


def _swap_upsell_for_active_cart_closer(body: str) -> str:
    """Strip a trailing '¿Quieres pedirla?'-style closer and append the
    standard active-cart closer instead.

    Why: the order agent's prompt teaches it to close product_info answers
    with '¿Quieres pedirla?' (correct when the customer is discovering a
    product), but if the asked-about item is already in the cart that
    phrasing is confusing — it implies the item isn't ordered yet. When
    the cart has items, the right closer is the same one used after
    add_to_cart turns: '¿Te gustaría añadir algo más o procedemos con el pedido?'.

    Only swaps when an upsell-style closer is actually present — otherwise
    the body is returned unchanged so we don't double up legitimate
    follow-up questions like '¿Cuál prefieres?'.
    """
    cleaned, n = _UPSELL_CLOSER_RE.subn("", body)
    if n == 0:
        return body
    cleaned = cleaned.rstrip()
    if not cleaned:
        return _ACTIVE_CART_CLOSER
    if _ACTIVE_CART_CLOSER in cleaned:
        return cleaned
    return f"{cleaned}\n\n{_ACTIVE_CART_CLOSER}"


def render_response(
    envelope: Dict[str, Any],
    *,
    business_context: Optional[Dict],
    last_user_message: str,
    wa_id: str = "",
    tool_outputs: Optional[Dict[str, str]] = None,
) -> RenderedResponse:
    """
    Convert an action-agent envelope into a user-facing response.

    Routing per envelope kind:
      - ``ready_to_confirm``: build Twilio CTA Content Template payload
        when configured. Fall back to text rendering on failure or no SID.
      - ``order_placed``: emit the ``place_order`` tool's output verbatim
        from ``tool_outputs``. The tool already produced the canonical
        receipt (order ID, subtotal, delivery fee, total, ETA); the LLM
        cannot improve on it and must not be allowed to drop fields.
      - ``items_added`` / ``items_removed`` / ``cart_updated`` / ``cart_view``:
        deterministic cart breakdown (items + subtotal) from canonical
        session state + short LLM-generated prelude. Delivery + total
        are deliberately omitted — those depend on address/distance and
        are shown only at confirmation time.
      - All other kinds: small constrained LLM with verbatim-only rule.
    """
    kind = (envelope.get("kind") or "chat").strip()
    tool_outputs = tool_outputs or {}

    if kind == "ready_to_confirm":
        cta = _try_build_confirm_cta(business_context, wa_id)
        if cta is not None:
            return {
                "type": "cta",
                "body": cta["rendered_body"],
                "content_sid": cta["content_sid"],
                "variables": cta["variables"],
            }
        # CTA not configured (or build failed) — render the SAME
        # structured recap as plain text instead of dropping into
        # generic LLM rendering. Matches v1's "Tengo estos datos
        # para tu pedido: <multi-line recap>. ¿Confirmamos?" shape
        # so the customer experience is consistent across the CTA
        # and text paths.
        text_body = _build_confirm_text(business_context, wa_id)
        if text_body:
            return {
                "type": "text",
                "body": text_body,
                "content_sid": None,
                "variables": None,
            }
        # Last-ditch: no delivery state available. Fall through to
        # generic LLM render so the customer still gets something.

    if kind == "order_placed":
        body = _render_order_placed(
            envelope=envelope,
            tool_outputs=tool_outputs,
            business_context=business_context,
            last_user_message=last_user_message,
        )
        return {
            "type": "text",
            "body": body,
            "content_sid": None,
            "variables": None,
        }

    if kind in CART_BREAKDOWN_KINDS:
        body = _render_with_cart_breakdown(
            envelope=envelope,
            business_context=business_context,
            last_user_message=last_user_message,
            wa_id=wa_id,
        )
        return {
            "type": "text",
            "body": body,
            "content_sid": None,
            "variables": None,
        }

    body = _render_text(
        envelope=envelope,
        business_context=business_context,
        last_user_message=last_user_message,
    )
    if kind in INFO_KINDS_WITH_UPSELL_RISK and _cart_has_items(business_context, wa_id):
        body = _swap_upsell_for_active_cart_closer(body)
    return {
        "type": "text",
        "body": body,
        "content_sid": None,
        "variables": None,
    }


def _render_order_placed(
    envelope: Dict[str, Any],
    tool_outputs: Dict[str, str],
    business_context: Optional[Dict],
    last_user_message: str,
) -> str:
    """Verbatim ``place_order`` output if available, else fall back.

    The place_order tool returns the canonical receipt:
        ✅ ¡Pedido confirmado! #ABCD1234
        Subtotal: $28.000
        🛵 Domicilio: $7.000
        Total: $35.000
        Nos ponemos en contacto pronto para coordinar la entrega.
        ⏱ Tiempo estimado de entrega: 40 a 50 minutos.

    Letting the LLM rephrase this drops the order ID, subtotal, and
    delivery fee in practice — so we don't.
    """
    raw = (tool_outputs.get("place_order") or "").strip()
    if raw and "✅" in raw:
        return raw
    # No tool output captured (shouldn't happen if the agent followed
    # the flow). Fall back to LLM render so the user still gets a
    # confirmation message.
    return _render_text(
        envelope=envelope,
        business_context=business_context,
        last_user_message=last_user_message,
    )


def _render_with_cart_breakdown(
    envelope: Dict[str, Any],
    business_context: Optional[Dict],
    last_user_message: str,
    wa_id: str,
) -> str:
    """Cart-mutating turns: deterministic breakdown + short LLM prelude.

    Cart numbers come from canonical session state (via the same matcher
    ``view_cart`` uses) — the LLM only writes a one-line prelude. Removes
    the entire class of "model hallucinated the subtotal" bugs.
    """
    breakdown = _build_cart_breakdown(business_context, wa_id)
    if not breakdown:
        # No cart yet — fall through to plain LLM render. Happens on a
        # post-place_order session reset or empty-cart edge case.
        return _render_text(
            envelope=envelope,
            business_context=business_context,
            last_user_message=last_user_message,
        )

    prelude = _render_cart_prelude(
        envelope=envelope,
        business_context=business_context,
        last_user_message=last_user_message,
    )
    closer = "¿Te gustaría añadir algo más o procedemos con el pedido?"
    parts = [p for p in (prelude, breakdown, closer) if p]
    return "\n\n".join(parts)


def _render_cart_prelude(
    envelope: Dict[str, Any],
    business_context: Optional[Dict],
    last_user_message: str,
) -> str:
    biz = (business_context or {}).get("business") or {}
    biz_name = (biz.get("name") or "el restaurante").strip()
    try:
        from .business_info_service import format_business_info_for_prompt
        biz_info = format_business_info_for_prompt(business_context) or ""
    except Exception:
        biz_info = ""

    facts: List[str] = envelope.get("facts") or []
    facts_str = " | ".join(str(f) for f in facts) if facts else "(none)"

    system = _RENDER_SYSTEM_PROMPT.format(
        business_name=biz_name, business_info=biz_info
    )
    user = _RENDER_USER_TEMPLATE.format(
        last_user_message=last_user_message,
        kind=envelope.get("kind") or "chat",
        summary=envelope.get("summary") or "",
        facts=facts_str,
    )
    try:
        llm = _get_renderer_llm()
        msg = llm.invoke(
            [SystemMessage(content=system), HumanMessage(content=user)],
            config={"run_name": "order_response_prelude"},
        )
        body = (getattr(msg, "content", "") or "").strip()
        if not body:
            return _default_prelude_for_kind(envelope.get("kind") or "")
        # One line only — strip trailing question / extra paragraphs.
        return body.split("\n")[0].strip()
    except Exception as exc:
        logging.warning("[RENDERER] prelude render failed: %s", exc)
        return _default_prelude_for_kind(envelope.get("kind") or "")


def _default_prelude_for_kind(kind: str) -> str:
    return {
        "items_added": "Listo, agregamos eso a tu pedido.",
        "items_removed": "Listo, lo quitamos.",
        "cart_updated": "Listo, ajustamos el ítem.",
        "cart_view": "Este es tu pedido actual:",
    }.get(kind, "Actualicé tu pedido.")


def _build_cart_breakdown(
    business_context: Optional[Dict],
    wa_id: str,
    *,
    include_totals: bool = False,
) -> str:
    """Deterministic cart formatter — items + (optionally) delivery+total.

    Reads the cart from canonical session state, runs the promotion
    matcher (so promo bundles render correctly), and formats lines.

    ``include_totals=False`` (default for cart-mutation turns):
        Items + Subtotal only. Skips delivery fee and grand total
        because those depend on the customer's address / distance —
        showing them prematurely confuses customers when delivery is
        zone-dependent.

    ``include_totals=True`` (used by ready_to_confirm + order_placed):
        Full breakdown with promo discount, delivery fee, and total.
    """
    if not business_context or not wa_id:
        return ""
    business_id = (business_context or {}).get("business_id") or ""
    if not business_id:
        return ""
    try:
        from ..database.session_state_service import session_state_service
        from . import promotion_service
        from .order_tools import (
            _format_cart_display_lines,
            _format_price,
            _get_delivery_fee,
        )

        state_result = session_state_service.load(wa_id, business_id) or {}
        session = state_result.get("session") or {}
        order_context = session.get("order_context") or {}
        items = order_context.get("items") or []
        if not items:
            return ""

        preview = promotion_service.preview_cart(business_id, items)
        subtotal = preview["subtotal"]
        promo_discount = preview["promo_discount_total"]

        lines = _format_cart_display_lines(preview["display_groups"])
        parts = ["Tu pedido:", "", *lines, "", f"Subtotal: {_format_price(subtotal)}"]
        if promo_discount > 0:
            parts.append(f"🏷 Ahorro con promo: -{_format_price(promo_discount)}")
        if include_totals:
            delivery_fee = _get_delivery_fee({**business_context, "wa_id": wa_id})
            grand_total = subtotal + delivery_fee
            parts.append(f"🛵 Domicilio: {_format_price(delivery_fee)}")
            parts.append(f"**Total: {_format_price(grand_total)}**")
        return "\n".join(parts)
    except Exception as exc:
        logging.warning("[RENDERER] cart breakdown failed: %s", exc)
        return ""


# ── CTA path ────────────────────────────────────────────────────────


def _try_build_confirm_cta(
    business_context: Optional[Dict], wa_id: str
) -> Optional[Dict[str, Any]]:
    """Read canonical delivery_status, build the Twilio CTA payload.

    Returns ``None`` when the business has no SID configured, the
    customer is missing data, or anything raises. Caller falls back to
    plain text on a None return.
    """
    if not business_context or not wa_id:
        return None
    try:
        from .order_cta import cta_confirm_order_payload
        delivery_status = _read_delivery_status(business_context, wa_id)
        return cta_confirm_order_payload(business_context, delivery_status)
    except Exception as exc:
        logging.warning("[RENDERER] confirm CTA build failed: %s", exc)
        return None


def _build_confirm_text(
    business_context: Optional[Dict], wa_id: str
) -> str:
    """Render the v1-style structured confirmation recap as plain text.

    Used when the CTA path can't be taken (no SID, sandbox WABA, etc.)
    so the text fallback still shows the customer their full order
    summary the same way the CTA card would: name / address / phone /
    payment / total, then "¿Confirmamos el pedido?". Multi-line — this
    path doesn't go through Twilio's variable-validation, so newlines
    are fine.

    Returns empty string when:
      - There's no active cart (post-place_order leftover state)
      - Delivery state has no usable fields

    Both cases let callers fall through to LLM rendering. The
    cart-empty guard pairs with the agent's
    ``_guard_impossible_envelope`` — together they keep the "phantom
    confirm card" from rendering even if one layer is bypassed.
    """
    if not business_context or not wa_id:
        return ""
    try:
        # Cart-presence guard: confirm requires an active cart.
        # ``_read_delivery_status`` reads name/address/phone/payment
        # from session ∪ DB customer, so a placed-then-cleared session
        # still has populated DB customer fields. Without this check
        # the renderer would happily build a recap from leftover data.
        if not _has_cart_items(business_context, wa_id):
            return ""
        delivery_status = _read_delivery_status(business_context, wa_id)
        if not delivery_status:
            return ""
        ftype = (delivery_status.get("fulfillment_type") or "delivery").strip().lower()
        name = (delivery_status.get("name") or "").strip()
        notes = (delivery_status.get("notes") or "").strip()
        lines: list[str] = []
        if name:
            lines.append(f"*Nombre:* {name}")
        if ftype == "pickup":
            # Pickup recap: name + mode line. No address / phone / pago —
            # they don't apply (WhatsApp ID covers phone, payment is at
            # the register). Customer sees the same shape on the CTA card.
            lines.append("*Modo:* 🏃 Recoger en local")
        else:
            address = (delivery_status.get("address") or "").strip()
            phone = (delivery_status.get("phone") or "").strip()
            payment = (delivery_status.get("payment_method") or "").strip()
            if address:
                lines.append(f"*Dirección:* {address}")
            if phone:
                lines.append(f"*Teléfono:* {phone}")
            if payment:
                lines.append(f"*Pago:* {payment}")
        # Order-level notes appear last so the customer reads structured
        # fields first, then any free-form instructions they gave.
        if notes:
            lines.append(f"*Notas:* {notes}")
        if not lines:
            return ""
        summary = "\n".join(lines)
        return (
            "Tengo estos datos para tu pedido:\n\n"
            f"{summary}\n\n"
            "¿Confirmamos el pedido?"
        )
    except Exception as exc:
        logging.warning("[RENDERER] confirm text build failed: %s", exc)
        return ""


def _has_cart_items(business_context: Dict, wa_id: str) -> bool:
    """True iff the current session has at least one cart item.

    Pulled from canonical session state, not from the customer DB
    record (which persists across orders and would mask post-place
    cart resets).
    """
    business_id = (business_context or {}).get("business_id") or ""
    if not business_id:
        return False
    try:
        from ..database.session_state_service import session_state_service
        result = session_state_service.load(wa_id, business_id) or {}
        order_context = (result.get("session") or {}).get("order_context") or {}
        return bool(order_context.get("items"))
    except Exception:
        return False


def _read_delivery_status(
    business_context: Dict, wa_id: str
) -> Dict[str, Any]:
    """Mirror ``order_tools.get_customer_info`` — session + DB customer.

    Kept here (not imported from order_tools) to avoid pulling the
    LangChain @tool decorator's wrapper. The shape returned matches what
    ``cta_confirm_order_payload`` expects.
    """
    from ..database.session_state_service import session_state_service
    from ..database.customer_service import customer_service

    business_id = (business_context or {}).get("business_id") or ""
    if not business_id:
        return {}

    state_result = session_state_service.load(wa_id, business_id) or {}
    session = state_result.get("session") or {}
    order_context = session.get("order_context") or {}
    items = order_context.get("items") or []
    total = sum(
        (it.get("price") or 0) * (it.get("quantity") or 0) for it in items
    )
    session_delivery = order_context.get("delivery_info") or {}
    raw_ftype = (order_context.get("fulfillment_type") or "delivery").strip().lower()
    ftype = raw_ftype if raw_ftype in ("delivery", "pickup") else "delivery"
    notes = (order_context.get("notes") or "").strip()

    cust = customer_service.get_customer(wa_id) or {}
    name = (
        (session_delivery.get("name") or "").strip()
        or (cust.get("name") or "").strip()
    )
    address = (
        (session_delivery.get("address") or "").strip()
        or (cust.get("address") or "").strip()
    )
    phone = (
        (session_delivery.get("phone") or "").strip()
        or (cust.get("phone") or "").strip()
    )
    payment = (
        (session_delivery.get("payment_method") or "").strip()
        or (cust.get("payment_method") or "").strip()
    )
    if ftype == "pickup":
        # Pickup: only name is needed for the order to be ready.
        all_present = bool(name)
    else:
        all_present = bool(name and address and phone and payment)
    return {
        "name": name,
        "address": address,
        "phone": phone,
        "payment_method": payment,
        "total": total,
        "all_present": all_present,
        "fulfillment_type": ftype,
        "notes": notes,
    }


# ── Text path ───────────────────────────────────────────────────────


def _render_text(
    envelope: Dict[str, Any],
    business_context: Optional[Dict],
    last_user_message: str,
) -> str:
    biz = (business_context or {}).get("business") or {}
    biz_name = (biz.get("name") or "el restaurante").strip()
    try:
        from .business_info_service import format_business_info_for_prompt
        biz_info = format_business_info_for_prompt(business_context) or ""
    except Exception:
        biz_info = ""

    facts: List[str] = envelope.get("facts") or []
    facts_str = " | ".join(str(f) for f in facts) if facts else "(none)"

    system = _RENDER_GENERIC_SYSTEM_PROMPT.format(
        business_name=biz_name, business_info=biz_info
    )
    user = _RENDER_USER_TEMPLATE.format(
        last_user_message=last_user_message,
        kind=envelope.get("kind") or "chat",
        summary=envelope.get("summary") or "",
        facts=facts_str,
    )

    try:
        llm = _get_renderer_llm()
        msg = llm.invoke(
            [SystemMessage(content=system), HumanMessage(content=user)],
            config={"run_name": "order_response_renderer"},
        )
        body = (getattr(msg, "content", "") or "").strip()
        return body or _safe_fallback(envelope)
    except Exception as exc:
        logging.warning("[RENDERER] text render failed: %s", exc)
        return _safe_fallback(envelope)


def _safe_fallback(envelope: Dict[str, Any]) -> str:
    """Last-resort string when the renderer LLM fails or returns empty.

    Uses the envelope ``summary`` if present. Avoids inventing anything
    on its own.
    """
    summary = (envelope.get("summary") or "").strip()
    if summary:
        return summary
    kind = (envelope.get("kind") or "").strip()
    if kind == "error":
        return "Lo siento, tuve un problema. ¿Podemos intentar de nuevo?"
    return "Listo. ¿En qué más puedo ayudarte?"
