"""
Customer service tools for the CS agent (tool-calling architecture).

Thin ``@tool`` wrappers around the existing ``_handle_*`` functions in
``customer_service_flow``. Each wrapper:

  - Pulls per-turn context (wa_id, business_id, business_context, session)
    from the context var set by the agent before the tool loop.
  - Delegates business logic to the existing handler.
  - Renders the handler's result dict into one of three string shapes
    the agent's dispatch loop understands:

      FINAL|<text>              deterministic final reply; agent returns
                                this verbatim, no second LLM iteration.
      HANDOFF|to=...|k=v|...    signal to hand off to another agent.
      <plain text>              consumed by the LLM, which writes prose
                                in the next iteration (used only when we
                                want LLM phrasing — e.g. when a tool
                                emits structured data with formatting hints).

Tools own their session-state side effects directly (last_listed_promos,
per-order ask counter) so the agent doesn't need per-tool knowledge.
"""

import contextvars
import logging
from typing import Annotated, Any, Dict, List, Optional

from langchain.tools import tool
from langchain_core.tools import InjectedToolArg

from ..database.session_state_service import session_state_service
from ..orchestration.customer_service_flow import (
    _handle_business_info,
    _handle_order_status,
    _handle_order_history,
    _handle_cancel_order,
    _handle_get_promos,
    _handle_select_listed_promo,
    RESULT_KIND_BUSINESS_INFO,
    RESULT_KIND_INFO_MISSING,
    RESULT_KIND_ORDER_STATUS,
    RESULT_KIND_NO_ORDER,
    RESULT_KIND_ORDER_HISTORY,
    RESULT_KIND_ORDER_CANCELLED,
    RESULT_KIND_CANCEL_NOT_ALLOWED,
    RESULT_KIND_PROMOS_LIST,
    RESULT_KIND_NO_PROMOS,
    RESULT_KIND_PROMO_NOT_RESOLVED,
    RESULT_KIND_PROMO_AMBIGUOUS,
    RESULT_KIND_HANDOFF,
    RESULT_KIND_DELIVERY_HANDOFF,
    RESULT_KIND_INTERNAL_ERROR,
)
from . import business_info_service
from . import payment_config
from .cancel_keywords import has_explicit_cancel_keyword


logger = logging.getLogger(__name__)


# Per-turn context var. The CS tool-calling agent sets this before
# invoking any tool so the tool body can read wa_id, business_id,
# business_context, and the session snapshot without those values
# appearing in the model's tool schema. Same pattern as order_tools.py.
_cs_tool_context: contextvars.ContextVar[Optional[Dict]] = contextvars.ContextVar(
    "_cs_tool_context", default=None,
)


def set_tool_context(ctx: Dict) -> contextvars.Token:
    return _cs_tool_context.set(ctx)


def reset_tool_context(token: contextvars.Token) -> None:
    _cs_tool_context.reset(token)


def _ctx(injected_business_context: Optional[Dict]) -> Dict:
    return injected_business_context or _cs_tool_context.get() or {}


# Lifted from customer_service_agent._BUSINESS_INFO_TEMPLATES.
# Single source of truth for the "we have the value, communicate it" path.
_BUSINESS_INFO_TEMPLATES = {
    "hours": "{value}",
    "address": "Estamos ubicados en {value}.",
    "phone": "Puedes contactarnos al {value}.",
    "delivery_fee": "El domicilio tiene un costo base de {value}, puede variar según la distancia.",
    "delivery_time": "Nuestros pedidos llegan en {value}.",
    "menu_url": "Acá tienes nuestro menú: {value}",
}


# ── Sentinel helpers ───────────────────────────────────────────────────


def _final(text: str) -> str:
    return f"FINAL|{text}"


def _handoff(to: str, segment: str = "", **context: Any) -> str:
    """
    Serialize a handoff payload into a sentinel the dispatch loop parses
    back via ``parse_handoff``. Shape: HANDOFF|to=<agent>|segment=<>|k=v|...
    Empty/None values are dropped.
    """
    parts = [f"to={to}"]
    if segment:
        parts.append(f"segment={segment}")
    for k, v in context.items():
        if v is None or v == "":
            continue
        parts.append(f"{k}={v}")
    return "HANDOFF|" + "|".join(parts)


def parse_handoff(s: str) -> Optional[Dict[str, str]]:
    """Parse a HANDOFF sentinel back into a dict; None when not a sentinel."""
    if not s or not s.startswith("HANDOFF|"):
        return None
    out: Dict[str, str] = {}
    for chunk in s[len("HANDOFF|"):].split("|"):
        if not chunk or "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def parse_final(s: str) -> Optional[str]:
    """Return the FINAL payload text, or None when not a FINAL sentinel."""
    if not s or not s.startswith("FINAL|"):
        return None
    return s[len("FINAL|"):]


# ── Rendering helpers ──────────────────────────────────────────────────


def _format_cop(value: Any) -> str:
    try:
        return f"${int(float(value)):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "$0"


def _short_order_id(order_id: Any) -> str:
    return (str(order_id or "")[:8]).upper()


def _business_phone(injected_business_context: Optional[Dict]) -> str:
    ctx = _ctx(injected_business_context)
    business_context = ctx.get("business_context")
    if not business_context:
        return ""
    try:
        return business_info_service.get_business_info(business_context, "phone") or ""
    except Exception:
        return ""


def _status_sentence(
    status: str,
    fulfillment_type: Optional[str] = None,
    cancellation_reason: Optional[str] = None,
) -> str:
    """
    Map an order status to its canonical customer-facing sentence.

    Branches on ``fulfillment_type`` for the cases where pickup and
    delivery semantically diverge: a pickup customer is told "your
    order is ready, come pick it up" / "you already picked it up";
    a delivery customer is told "your order is on the way" / "your
    order was delivered". ``pending`` / ``confirmed`` / ``cancelled``
    read the same for both fulfillment types.
    """
    s = (status or "").lower()
    is_pickup = (fulfillment_type or "").lower() == "pickup"
    if s == "pending":
        return "Tu pedido quedó registrado y está pendiente de confirmación. En un momento te avisamos."
    if s == "confirmed":
        return "Tu pedido ya fue confirmado y lo estamos preparando con cuidado."
    if s == "out_for_delivery":
        return "Tu pedido va en camino, ya casi llega."
    if s == "ready_for_pickup":
        return "Tu pedido ya está listo, te esperamos en el local para recogerlo."
    if s == "completed":
        if is_pickup:
            return "Ya recogiste tu pedido, gracias por venir. ¿Hay algo más en lo que te podamos ayudar?"
        return "Tu pedido ya fue entregado. ¿Hay algo más en lo que te podamos ayudar?"
    if s == "cancelled":
        if cancellation_reason:
            return f"Tu pedido fue cancelado ({cancellation_reason})."
        return "Tu pedido fue cancelado."
    return f"El estado actual de tu pedido es: {status}."


def _format_item_line(it: Dict[str, Any]) -> str:
    qty = int(it.get("quantity") or 0)
    name = it.get("name") or "(sin nombre)"
    price = int(float(it.get("unit_price") or 0))
    line_total = int(float(it.get("line_total") or (price * qty)))
    notes = (it.get("notes") or "").strip()
    notes_part = f" ({notes})" if notes else ""
    if qty > 1:
        return (
            f"- {qty}x {name}{notes_part} — {_format_cop(price)} c/u "
            f"(total {_format_cop(line_total)})"
        )
    return f"- {qty}x {name}{notes_part} — {_format_cop(price)}"


def _persist_cs_ctx(wa_id: str, business_id: str, patch: Dict[str, Any]) -> None:
    """
    Side-effect: merge ``patch`` into agent_contexts.customer_service for
    this conversation. Two-level merge in session_state_service.save
    preserves keys the tool didn't touch.
    """
    if not (wa_id and business_id and patch):
        return
    try:
        session_state_service.save(
            wa_id, business_id,
            {"agent_contexts": {"customer_service": patch}},
        )
    except Exception as exc:
        logger.error(
            "[CS_TOOLS] persist state_patch failed wa_id=%s business_id=%s: %s",
            wa_id, business_id, exc, exc_info=True,
        )


# ── Tools ──────────────────────────────────────────────────────────────


@tool
def get_business_info(
    field: str,
    *,
    injected_business_context: Annotated[dict, InjectedToolArg],
) -> str:
    """
    Devuelve un dato configurado del negocio, ya formateado para enviar al cliente.

    Llama esta herramienta cuando el cliente pregunta por información del local.
    Elige `field` según el tipo de pregunta:

    - "hours": HORARIOS / DISPONIBILIDAD / si están OPERANDO ahora.
      Cubre "a qué hora abren", "qué horario tienen", "abren los domingos",
      "hay atención", "hay servicio", "están atendiendo", "siguen abiertos",
      "ya abrieron", "ya cerraron", "están operando".
    - "address": ubicación / dirección ("dónde quedan", "cuál es la dirección").
    - "phone": teléfono DE CONTACTO general del negocio para llamar/escribir
      ("cuál es su número", "tienen WhatsApp"). NO uses esto para preguntas
      de PAGO — esas van a "payment_details" aunque mencionen "número".
    - "delivery_fee": costo del domicilio ("cuánto cobran domicilio",
      "cuánto vale el envío").
    - "delivery_time": tiempo de entrega ("cuánto se demora la entrega",
      "en cuánto llega", "qué tan rápido entregan"). Si el cliente ya tiene
      pedido en curso, la herramienta devuelve el ETA real de ese pedido.
    - "menu_url": link al MENÚ o CARTA. Cubre cualquier verbo de envío
      ("envíame la carta", "pásame el menú", "compárteme", "regálame",
      "quiero ver la carta") — en Colombia "regalar"/"me regalas" es
      coloquial por "dar".

    NOTA: para CUALQUIER pregunta de pago (medios aceptados, dónde
    transferir, si reciben tarjeta/Nequi, si pueden pagar al recibir)
    usa `get_payment_info`, NO esta herramienta — `get_payment_info`
    sabe el modo del cliente (domicilio vs local) y responde con los
    métodos correctos para ese contexto. NUNCA uses `phone` para
    preguntas de pago.

    Args:
        field: Uno de hours | address | phone | delivery_fee | delivery_time
               | menu_url.
    """
    ctx = _ctx(injected_business_context)
    wa_id = ctx.get("wa_id") or ""
    business_id = ctx.get("business_id") or ""
    business_context = ctx.get("business_context")
    session = ctx.get("session")

    result = _handle_business_info(
        wa_id, business_id, business_context, {"field": field}, session,
    )
    kind = result.get("result_kind")

    if kind == RESULT_KIND_BUSINESS_INFO:
        value = result.get("value")
        tmpl = _BUSINESS_INFO_TEMPLATES.get(result.get("field"))
        if tmpl and value is not None:
            return _final(tmpl.format(value=value))
        return _final(str(value) if value is not None else "")

    if kind == RESULT_KIND_ORDER_STATUS:
        # delivery_time fell through to per-order ETA. Reuse the order-status
        # path so timing answers stay accurate.
        return _render_order_status(
            result.get("order") or {},
            asked_about_time=True,
            asked_for_breakdown=False,
            injected_business_context=injected_business_context,
        )

    if kind == RESULT_KIND_INFO_MISSING:
        missing_field = result.get("field") or "(no identificado)"
        # Plain text → LLM writes the apology. Keeps tone natural and lets
        # the LLM offer adjacent fields it can answer.
        return (
            f"INFO_MISSING\nfield: {missing_field}\n\n"
            "INSTRUCCIONES: No tenemos ese dato configurado. Discúlpate brevemente "
            "y ofrece ayudar con horarios, dirección, domicilio, medios de pago o "
            "estado de pedidos. NO inventes URLs, links, números ni placeholders entre paréntesis."
        )

    return _final("Disculpa, tuve un problema. ¿Podrías intentar de nuevo?")


# ── Payment info ──────────────────────────────────────────────────────


def _resolve_fulfillment_type(session: Optional[Dict]) -> Optional[str]:
    """Read the customer's current fulfillment_type from the session, if any.

    Returns the lowercased value when set ("delivery" / "pickup" / "dine_in"
    / "on_site"), or None when the customer hasn't picked yet.
    """
    if not isinstance(session, dict):
        return None
    order_context = session.get("order_context") or {}
    raw = (order_context.get("fulfillment_type") or "").strip().lower()
    return raw or None


# Context-string → human label used in the data block the LLM reads.
# Two pieces per context: which fulfillment ("domicilio" / "local") and
# which timing ("al recibir" / "al pagar" / "por adelantado").
_CONTEXT_FULFILLMENT_LABEL = {
    payment_config.CONTEXT_DELIVERY_PAY_NOW: "domicilio",
    payment_config.CONTEXT_DELIVERY_ON_FULFILLMENT: "domicilio",
    payment_config.CONTEXT_ON_SITE_PAY_NOW: "local",
    payment_config.CONTEXT_ON_SITE_ON_FULFILLMENT: "local",
}
_CONTEXT_TIMING_LABEL = {
    payment_config.CONTEXT_DELIVERY_PAY_NOW: "por adelantado",
    payment_config.CONTEXT_DELIVERY_ON_FULFILLMENT: "al recibir",
    payment_config.CONTEXT_ON_SITE_PAY_NOW: "por adelantado",
    payment_config.CONTEXT_ON_SITE_ON_FULFILLMENT: "al pagar",
}


def _describe_method_contexts(contexts: List[str]) -> str:
    """Render a method's contexts as 'domicilio (al recibir, por adelantado), local (al pagar)'."""
    # Preserve a stable ordering: domicilio first, then local; within each,
    # at-fulfillment timing before pay-now.
    ordered_contexts = [
        payment_config.CONTEXT_DELIVERY_ON_FULFILLMENT,
        payment_config.CONTEXT_DELIVERY_PAY_NOW,
        payment_config.CONTEXT_ON_SITE_ON_FULFILLMENT,
        payment_config.CONTEXT_ON_SITE_PAY_NOW,
    ]
    by_fulfillment: Dict[str, List[str]] = {"domicilio": [], "local": []}
    for c in ordered_contexts:
        if c not in contexts:
            continue
        by_fulfillment[_CONTEXT_FULFILLMENT_LABEL[c]].append(_CONTEXT_TIMING_LABEL[c])
    parts = []
    for label in ("domicilio", "local"):
        timings = by_fulfillment[label]
        if not timings:
            continue
        parts.append(f"{label} ({', '.join(timings)})")
    return ", ".join(parts) if parts else "ningún contexto configurado"


def _session_fulfillment_label(fulfillment_type: Optional[str]) -> str:
    """Normalize the session's fulfillment_type to a label for the data block."""
    if fulfillment_type == "delivery":
        return "domicilio"
    if fulfillment_type in ("pickup", "dine_in", "on_site"):
        return "local"
    return "desconocido"


def _render_payment_info_data(
    settings: Dict, fulfillment_type: Optional[str],
) -> str:
    """Structured payment snapshot the LLM composes a reply from.

    Returned as plain text (no FINAL sentinel) so the agent's loop iterates
    once more and the LLM writes the actual user-facing message. The LLM
    sees the customer's message in conversation history and decides what
    to emphasize based on what they asked.
    """
    methods = payment_config.get_payment_methods(settings)
    destinations = (settings or {}).get("payment_destinations") or {}

    if not methods:
        return (
            "PAYMENT_INFO\n"
            "(El negocio aún no tiene métodos de pago configurados.)\n\n"
            "INSTRUCCIONES: Discúlpate brevemente y di que confirmas en un momento. "
            "NO inventes métodos."
        )

    method_lines = [
        f"- {m['name']}: {_describe_method_contexts(m['contexts'])}"
        for m in methods
    ]

    # Destinations only matter when at least one method is pay-now.
    pay_now_methods = [
        m["name"]
        for m in methods
        if payment_config.CONTEXT_DELIVERY_PAY_NOW in m["contexts"]
        or payment_config.CONTEXT_ON_SITE_PAY_NOW in m["contexts"]
    ]
    destination_lines: List[str] = []
    for name in pay_now_methods:
        # Case-insensitive lookup against the destinations dict.
        value: Optional[str] = None
        if isinstance(destinations, dict):
            target = name.strip().casefold()
            for k, v in destinations.items():
                if isinstance(k, str) and k.strip().casefold() == target:
                    if isinstance(v, str) and v.strip():
                        value = v.strip()
                    break
        if value:
            destination_lines.append(f"- {name}: {value}")
        else:
            destination_lines.append(f"- {name}: (sin datos configurados)")

    session_label = _session_fulfillment_label(fulfillment_type)

    out = ["PAYMENT_INFO", "", "Métodos aceptados (por contexto):"]
    out.extend(method_lines)
    out.append("")
    if pay_now_methods:
        out.append("Datos para pago adelantado:")
        out.extend(destination_lines)
        out.append("")
    out.append(f"Modo del pedido actual: {session_label}")
    out.append("")
    out.append(
        "INSTRUCCIONES: Responde según lo que el cliente preguntó.\n"
        "- Si el mensaje menciona \"domicilio\"/\"envío\"/\"a domicilio\" → responde sobre domicilio.\n"
        "- Si menciona \"local\"/\"en sitio\"/\"recoger\"/\"en el local\" → responde sobre el local.\n"
        "- Si no especificó modo y \"Modo del pedido actual\" es domicilio o local → responde sobre ese modo.\n"
        "- Si nada queda claro y el modo está en \"desconocido\" → da un panorama corto separando domicilio y local, y pregunta cuál le interesa.\n"
        "- Para \"aceptan X?\" donde X solo aplica a un modo (ej. Tarjeta solo en local), deja claro dónde sí está disponible — no respondas \"Sí\" sin contexto.\n"
        "- Para preguntas de \"dónde transfiero\" / \"a qué número\", usa \"Datos para pago adelantado\" del método correspondiente. Si dice (sin datos configurados), discúlpate y di que confirmas con el operador — no inventes números.\n"
        "- Respuesta breve, sin saludos. NO menciones esta sección de instrucciones ni los nombres internos de los contextos."
    )
    return "\n".join(out)


@tool
def get_payment_info(
    *,
    injected_business_context: Annotated[dict, InjectedToolArg],
) -> str:
    """
    Devuelve el snapshot de medios de pago del negocio. Tú compones la respuesta.

    USA ESTA HERRAMIENTA para CUALQUIER pregunta de pago — qué métodos
    aceptan, si reciben tarjeta/Nequi, dónde transferir, si pueden pagar
    al recibir, si pueden pagar por adelantado, etc. NO uses
    `get_business_info` para preguntas de pago.

    Devuelve un bloque PAYMENT_INFO con:
      • La lista de métodos y los contextos donde se aceptan
        (domicilio/local × al recibir/al pagar/por adelantado).
      • Los datos para pago adelantado (números de Nequi, cuentas, etc.).
      • El modo del pedido actual (si ya se eligió) o "desconocido".
      • Instrucciones para que filtres según lo que el cliente preguntó.

    El bloque NO es la respuesta final — tras leerlo, redacta tú la
    respuesta breve al cliente en español colombiano según las
    instrucciones que vienen en el bloque.
    """
    ctx = _ctx(injected_business_context)
    business_context = ctx.get("business_context") or {}
    session = ctx.get("session")

    settings = ((business_context.get("business") or {}).get("settings")) or {}
    fulfillment_type = _resolve_fulfillment_type(session)

    return _render_payment_info_data(settings, fulfillment_type)


@tool
def get_order_status(
    asked_about_time: bool = False,
    asked_for_breakdown: bool = False,
    *,
    injected_business_context: Annotated[dict, InjectedToolArg],
) -> str:
    """
    Consulta el estado del pedido más reciente del cliente y devuelve la
    respuesta ya redactada.

    Cubre preguntas como "dónde está mi pedido", "qué pasó con mi pedido",
    "cómo va mi pedido", "ya salió?", y también peticiones de DESGLOSE
    por ítem de un pedido YA colocado ("cuánto vale cada producto",
    "cómo me cobraron", "el detalle del pedido").

    Args:
        asked_about_time: True SOLO si el cliente preguntó explícitamente
            por TIEMPO ("cuánto se demora", "cuánto falta", "en cuánto llega",
            "cuándo llega", "a qué hora", "tarda mucho"). Preguntas como
            "cómo va", "qué pasó", "dónde está" NO son preguntas por tiempo.
            Si True, incluye el ETA aproximado cuando exista.
        asked_for_breakdown: True SOLO si el cliente pidió EXPLÍCITAMENTE
            el desglose por ítem ("cuánto vale cada producto", "cómo me
            cobraron", "el detalle del pedido", "cuánto valió cada cosa",
            "el total de cada uno"). Si True, lista los items con precios.
    """
    ctx = _ctx(injected_business_context)
    wa_id = ctx.get("wa_id") or ""
    business_id = ctx.get("business_id") or ""
    session = ctx.get("session")

    business_context = ctx.get("business_context")
    result = _handle_order_status(
        wa_id, business_id, session, business_context=business_context,
    )
    kind = result.get("result_kind")

    # Persist any state patch the handler emitted (per-order ask counter).
    state_patch = result.get("state_patch") or {}
    if isinstance(state_patch, dict) and state_patch:
        _persist_cs_ctx(wa_id, business_id, state_patch)

    if kind == RESULT_KIND_HANDOFF:
        h = result.get("handoff") or {}
        h_ctx = h.get("context") or {}
        return _handoff(
            to=h.get("to") or "order",
            segment=h.get("segment") or "",
            reason=h_ctx.get("reason"),
        )

    if kind == RESULT_KIND_NO_ORDER:
        return _final(
            "No tengo registro de un pedido tuyo en nuestro sistema. "
            "¿Te ayudo a hacer uno?"
        )

    if kind == RESULT_KIND_DELIVERY_HANDOFF:
        # The handler already disabled the bot for this conversation.
        # Fixed apology so the message cannot drift or hallucinate ETAs.
        return _final(
            "Disculpa la demora con tu pedido. Voy a contactar al "
            "domiciliario para verificar y te confirmamos cuanto antes "
            "por aquí."
        )

    if kind == RESULT_KIND_ORDER_STATUS:
        return _render_order_status(
            result.get("order") or {},
            asked_about_time=asked_about_time,
            asked_for_breakdown=asked_for_breakdown,
            injected_business_context=injected_business_context,
        )

    return _final("Disculpa, no pude consultar tu pedido en este momento.")


def _render_order_status(
    order: Dict[str, Any],
    *,
    asked_about_time: bool,
    asked_for_breakdown: bool,
    injected_business_context: Optional[Dict],
) -> str:
    """Compose the FINAL reply for an order-status answer."""
    status = (order.get("status") or "").lower()
    fulfillment_type = (order.get("fulfillment_type") or "delivery").lower()
    is_pickup = fulfillment_type == "pickup"
    cancellation_reason = (order.get("cancellation_reason") or "").strip() or None
    eta_minutes = order.get("eta_minutes")

    sentence = _status_sentence(status, fulfillment_type, cancellation_reason)

    parts: List[str] = [sentence]

    if asked_about_time:
        # Pickup: ETA means "ready in X min" while preparing; no ETA
        # once the order is already in ready_for_pickup state.
        # Delivery: ETA means "arrives in X min" while in flight.
        in_flight_with_eta = (
            status in {"pending", "confirmed", "out_for_delivery"}
            if not is_pickup
            else status in {"pending", "confirmed"}
        )
        if eta_minutes is not None and in_flight_with_eta:
            if is_pickup:
                parts.append(
                    f"Estará listo en aproximadamente {int(eta_minutes)} minutos."
                )
            else:
                parts.append(f"Tiempo aproximado: {int(eta_minutes)} minutos.")
        elif is_pickup and status == "ready_for_pickup":
            # Already ready — no ETA line; the status sentence already
            # tells them to come pick it up.
            pass
        else:
            parts.append(
                "No tengo un tiempo exacto en este momento, pero estamos pendientes."
            )

    if asked_for_breakdown:
        items = order.get("items") or []
        total = order.get("total_amount")
        if items:
            lines = [_format_item_line(it) for it in items]
            parts.append("Detalle del pedido:\n" + "\n".join(lines))
            parts.append(f"Total: {_format_cop(total)}")

    return _final(" ".join(parts[:1]) + ("\n" + "\n\n".join(parts[1:]) if len(parts) > 1 else ""))


@tool
def get_order_history(
    *,
    injected_business_context: Annotated[dict, InjectedToolArg],
) -> str:
    """
    Devuelve un resumen de los pedidos anteriores del cliente, ya redactado.

    Llama esta herramienta cuando el cliente pide ver pedidos pasados
    ("qué he pedido antes", "muéstrame mis pedidos", "último pedido",
    "qué pedí la otra vez").
    """
    ctx = _ctx(injected_business_context)
    wa_id = ctx.get("wa_id") or ""
    business_id = ctx.get("business_id") or ""

    business_context = ctx.get("business_context")
    result = _handle_order_history(
        wa_id, business_id, {}, business_context=business_context,
    )
    kind = result.get("result_kind")

    if kind == RESULT_KIND_NO_ORDER:
        return _final(
            "No tengo pedidos anteriores tuyos en nuestro sistema. "
            "¿Te ayudo a hacer el primero?"
        )

    if kind == RESULT_KIND_ORDER_HISTORY:
        orders = result.get("orders") or []
        if not orders:
            return _final("No tengo pedidos anteriores tuyos registrados.")
        lines = []
        for o in orders:
            status = o.get("status") or "?"
            total = o.get("total_amount")
            created = (o.get("created_at") or "").split("T")[0] or "—"
            lines.append(f"- {created} | {status} | total {_format_cop(total)}")
        return _final("Estos son tus pedidos recientes:\n" + "\n".join(lines))

    return _final("Disculpa, no pude consultar tu historial de pedidos.")


@tool
def cancel_order(
    *,
    injected_business_context: Annotated[dict, InjectedToolArg],
) -> str:
    """
    Cancela el pedido más reciente del cliente, si su estado lo permite.

    Llama esta herramienta SOLO cuando el cliente pide EXPLÍCITAMENTE
    cancelar un pedido YA CONFIRMADO ("cancela mi pedido", "anula el
    pedido", "ya no quiero el pedido que hice", "cancélalo").

    NO la llames si:
      - El cliente tiene un carrito en curso (sin colocar) — abandonar
        un carrito lo maneja el agente de pedido.
      - El cliente no usó una palabra explícita de cancelación.
    """
    ctx = _ctx(injected_business_context)
    wa_id = ctx.get("wa_id") or ""
    business_id = ctx.get("business_id") or ""
    message_body = ctx.get("message_body") or ""
    turn_ctx = ctx.get("turn_ctx")

    # Two-AND safety gate, co-located with the destructive action so no
    # caller can bypass it. The LLM has emitted cancel_order on bare
    # affirmations ("Si\nGracias") and on payment-questions where the
    # customer used "cancelar" in the Colombian sense of "pagar"
    # (Biela 2026-05-12 / Angela Enriquez, order #9CF8AB56). The LLM is
    # also taught the cancelar=pagar disambiguation upstream, but this
    # gate is the floor — destructive actions require an explicit
    # cancel verb AND a cancellable placed order.
    if not has_explicit_cancel_keyword(message_body):
        logging.warning(
            "[CS_TOOL] cancel_order refused: no explicit cancel keyword "
            "(wa_id=%s, msg=%r)", wa_id, (message_body or "")[:120],
        )
        return (
            "REFUSED|reason=no_cancel_keyword. El cliente no usó una "
            "palabra explícita de cancelación. NO uses cancel_order. "
            "En Colombia 'cancelar' a veces significa 'pagar' — si el "
            "mensaje es una pregunta o menciona pago/domiciliario, "
            "pregúntale al cliente qué quiere hacer (cancelar el pedido, "
            "pagar, u otra cosa) y espera su respuesta."
        )

    if (
        turn_ctx is not None
        and not getattr(turn_ctx, "has_recent_cancellable_order", False)
    ):
        logging.warning(
            "[CS_TOOL] cancel_order refused: no cancellable placed order "
            "(wa_id=%s, order_state=%s, has_active_cart=%s)",
            wa_id,
            getattr(turn_ctx, "order_state", "?"),
            getattr(turn_ctx, "has_active_cart", False),
        )
        return (
            "REFUSED|reason=no_cancellable_order. No hay un pedido "
            "confirmado pendiente que se pueda cancelar. NO uses "
            "cancel_order. Responde al cliente que no encuentras un "
            "pedido cancelable y ofrece ayudarle."
        )

    business_context = ctx.get("business_context")
    result = _handle_cancel_order(
        wa_id, business_id, business_context=business_context,
    )
    kind = result.get("result_kind")

    if kind == RESULT_KIND_NO_ORDER:
        return _final(
            "No tengo registro de un pedido tuyo que pueda cancelar."
        )

    if kind == RESULT_KIND_ORDER_CANCELLED:
        order = result.get("order") or {}
        order_id = _short_order_id(order.get("id"))
        suffix = f" (#{order_id})" if order_id else ""
        return _final(
            f"Listo, cancelé tu pedido{suffix}. Cuando quieras "
            "volver a pedir, aquí estamos."
        )

    if kind == RESULT_KIND_CANCEL_NOT_ALLOWED:
        order = result.get("order") or {}
        status = (order.get("status") or "").lower()
        is_pickup = (order.get("fulfillment_type") or "").lower() == "pickup"
        phone = _business_phone(injected_business_context)
        if status == "out_for_delivery":
            base = "Tu pedido ya va en camino, ya no lo podemos cancelar desde acá."
        elif status == "ready_for_pickup":
            base = "Tu pedido ya está listo en el local, ya no lo podemos cancelar desde acá."
        elif status == "completed":
            base = (
                "Ya recogiste tu pedido, no se puede cancelar."
                if is_pickup else
                "Tu pedido ya fue entregado, no se puede cancelar."
            )
        elif status == "cancelled":
            base = "Tu pedido ya estaba cancelado."
        else:
            base = "Por el estado actual del pedido no puedo cancelarlo desde acá."
        tail = (
            f" Si necesitas ayuda urgente, llámanos al {phone}."
            if phone else
            " Para cualquier ajuste, comunícate directamente con el restaurante."
        )
        return _final(base + tail)

    return _final("Disculpa, tuve un problema al procesar la cancelación.")


@tool
def get_promos(
    include_upcoming_other_days: bool = False,
    *,
    injected_business_context: Annotated[dict, InjectedToolArg],
) -> str:
    """
    Lista las promos activas hoy (y las próximas de la semana si aplican),
    con respuesta ya redactada.

    Llama esta herramienta cuando el cliente pregunta SI HAY promos /
    ofertas / combos disponibles, sin identificar una en particular
    ("qué promos tienen", "tienen ofertas hoy", "hay alguna promo",
    "qué combos manejan", "promos del lunes").

    NO la llames si el cliente nombra una promo específica que quiere —
    en ese caso usa `select_listed_promo`.

    `include_upcoming_other_days`: pásalo en True SOLO cuando el cliente
    pregunta explícitamente por otros días o el resto de la semana
    ("¿hay otras esta semana?", "¿y los otros días?", "¿qué promos hay
    los lunes?"). Por defecto False: si hoy hay promos activas, NO
    listes las de otros días — el cliente solo necesita saber lo de
    hoy. (Cuando no hay promos hoy pero sí próximas, el listado de
    próximas se muestra siempre — es la única opción procesable.)

    Efecto secundario: guarda la lista de promos activas en la sesión
    para que un próximo turno pueda resolver referencias como
    "dame la primera" / "la del honey".
    """
    ctx = _ctx(injected_business_context)
    wa_id = ctx.get("wa_id") or ""
    business_id = ctx.get("business_id") or ""
    business_context = ctx.get("business_context")

    result = _handle_get_promos(wa_id, business_id, business_context)
    kind = result.get("result_kind")

    if kind == RESULT_KIND_NO_PROMOS:
        return _final(
            "Por hoy no tenemos promos activas, pero te puedo ayudar "
            "con el menú o un pedido normal."
        )

    if kind != RESULT_KIND_PROMOS_LIST:
        return _final("Disculpa, no pude consultar las promos en este momento.")

    promos = result.get("promos") or []
    upcoming = result.get("upcoming_promos") or []

    # Side effect: remember the active set so the next turn can resolve
    # "dame la primera" / "la del honey" via select_listed_promo.
    _persist_cs_ctx(wa_id, business_id, {
        "last_listed_promos": [
            {"id": p.get("id"), "name": p.get("name")}
            for p in promos
        ],
    })

    def _render(p: Dict[str, Any], idx: int) -> str:
        bits = [f"{idx}. {p.get('name')}"]
        if p.get("price_kind"):
            bits.append(f"— {p['price_kind']}")
        if p.get("schedule_label"):
            bits.append(f"({p['schedule_label']})")
        line = " ".join(bits)
        if p.get("description"):
            line += f"\n   {p['description']}"
        return line

    if promos:
        active_lines = "\n".join(_render(p, i) for i, p in enumerate(promos, start=1))
        message = f"Estas son nuestras promos activas hoy:\n{active_lines}"
        # Only mention other-day upcoming promos when the agent explicitly
        # opted in (i.e. the customer asked about "the rest of the week").
        # Default behavior: keep the answer focused on today.
        if include_upcoming_other_days and upcoming:
            # Include the day each upcoming promo applies so the customer
            # knows when to come back. Bare "También hay Dos Misuri con
            # papas" is misleading — it reads like the promo is available
            # somewhere this week without saying which day.
            up_bits = []
            for p in upcoming:
                name = (p.get("name") or "").strip()
                if not name:
                    continue
                label = (p.get("schedule_label") or "").strip()
                up_bits.append(f"{name} ({label})" if label else name)
            if up_bits:
                message += f"\n\nTambién hay otras esta semana: {', '.join(up_bits)}."
        # Closer: with a single active promo, "dime cuál" is nonsensical —
        # there's only one. Switch to a direct offer.
        if len(promos) == 1:
            message += "\n\n¿Quieres pedirla?"
        else:
            message += "\n\nSi quieres alguna, dime cuál."
        return _final(message)

    # No active promos but upcoming exist.
    up_lines = "\n".join(
        _render(p, i) for i, p in enumerate(upcoming, start=1)
    )
    return _final(
        "Hoy no tenemos promos activas, pero estas vienen esta semana:\n"
        + up_lines
    )


@tool
def select_listed_promo(
    selector: str = "",
    query: str = "",
    promo_id: str = "",
    *,
    injected_business_context: Annotated[dict, InjectedToolArg],
) -> str:
    """
    Resuelve qué promo eligió el cliente cuando, en un turno previo, le
    listamos promos disponibles. Cubre tres formas de elegir:

    Args:
        selector: Ordinal cuando usa posición ("primera"/"1", "segunda"/"2",
                  "la tercera"). Pasa el texto crudo del cliente.
        query: Frase parcial del nombre cuando lo nombra ("la del honey",
               "el combo familiar", "esa de hamburguesa").
        promo_id: UUID exacto si por algún motivo el cliente lo cita.

    Frases típicas: "dame esa", "quiero la primera", "la del honey burger",
    "esa segunda", "sí, esa".

    La herramienta hace handoff al agente de pedido si la resolución es
    inequívoca; en otros casos pide al cliente que aclare.
    """
    ctx = _ctx(injected_business_context)
    wa_id = ctx.get("wa_id") or ""
    business_id = ctx.get("business_id") or ""
    session = ctx.get("session")

    params = {
        "selector": selector or "",
        "query": query or "",
        "promo_id": promo_id or "",
    }
    result = _handle_select_listed_promo(wa_id, business_id, params, session)
    kind = result.get("result_kind")

    if kind == RESULT_KIND_HANDOFF:
        h = result.get("handoff") or {}
        h_ctx = h.get("context") or {}
        return _handoff(
            to=h.get("to") or "order",
            segment=h.get("segment") or "",
            promo_id=h_ctx.get("promo_id"),
            reason=h_ctx.get("reason"),
        )

    if kind == RESULT_KIND_PROMO_AMBIGUOUS:
        candidates = result.get("candidates") or []
        if not candidates:
            return _final(
                "Tengo varias que podrían coincidir. ¿Me dices cuál por nombre?"
            )
        lines = "\n".join(
            f"{i}. {c.get('name')}"
            for i, c in enumerate(candidates, start=1)
        )
        return _final(
            "Tengo varias que coinciden:\n" + lines + "\n\n¿Cuál prefieres?"
        )

    if kind == RESULT_KIND_PROMO_NOT_RESOLVED:
        listed_count = int(result.get("listed_count") or 0)
        q = (result.get("query") or "").strip()
        if q:
            return _final(
                f"No encuentro una promo activa que coincida con \"{q}\". "
                "¿Quieres que te liste las que están disponibles hoy?"
            )
        if listed_count > 0:
            return _final(
                "¿Me confirmas cuál promo quieres? Puedes decirme el número "
                "(ej. \"la primera\") o el nombre."
            )
        return _final(
            "¿Quieres que te liste las promos disponibles hoy?"
        )

    return _final("Disculpa, no pude procesar la selección de promo.")


# Tuple of all CS tools — what the agent binds onto the LLM.
cs_tools = (
    get_business_info,
    get_payment_info,
    get_order_status,
    get_order_history,
    cancel_order,
    get_promos,
    select_listed_promo,
)
