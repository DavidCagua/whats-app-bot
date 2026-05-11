"""
Customer service agent.

Handles non-transactional customer questions — both pre-sale (hours,
location, delivery policy, payment methods) AND post-sale (order status,
history). Strictly read-only for this phase: no cart mutations, no order
modifications, no complaints logging.

Three-stage pipeline mirrors the order agent so architectural invariants
stay consistent:

    1. Planner (LLM, gpt-4o-mini)
       Classifies the message into one of 4 intents + params.

    2. Executor (customer_service_flow.execute_customer_service_intent)
       Deterministic. Reads from business_info_service and
       order_lookup_service. Returns a structured result with a
       result_kind tag.

    3. Response generator
       Two paths:
         - Template path (no LLM): simple business-info lookups with a
           known field and found value. ~0ms.
         - LLM path (gpt-4o-mini): order status, history, chat fallback,
           compound questions, missing-info cases. ~400-600ms.

Session state slot: `customer_service_context`. Owns only its own slot;
never writes to order_context.
"""

import json
import logging
import os
import re
import time
import unicodedata
import uuid
from typing import Any, Dict, List, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from .base_agent import BaseAgent, AgentOutput
from ..orchestration.customer_service_flow import (
    execute_customer_service_intent,
    VALID_INTENTS,
    INTENT_GET_BUSINESS_INFO,
    INTENT_GET_ORDER_STATUS,
    INTENT_GET_ORDER_HISTORY,
    INTENT_CANCEL_ORDER,
    INTENT_GET_PROMOS,
    INTENT_SELECT_LISTED_PROMO,
    INTENT_CUSTOMER_SERVICE_CHAT,
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
    RESULT_KIND_CHAT_FALLBACK,
    RESULT_KIND_INTERNAL_ERROR,
    RESULT_KIND_HANDOFF,
    RESULT_KIND_DELIVERY_HANDOFF,
)
from ..database.conversation_service import conversation_service
from ..services import business_info_service
from ..services.cancel_keywords import has_explicit_cancel_keyword
from ..services.tracing import tracer


_POST_ORDER_CLOSE_LONE_TOKENS = (
    # Lone tokens that mean "polite close" with very low ambiguity.
    # Conservative: words like "vale" (also "cuánto vale") and "bueno"
    # (often a question filler) are NOT included as lone tokens — they
    # need to appear with another close word ("vale gracias",
    # "bueno gracias"). The multi-word phrase list below covers those.
    "gracias", "graciassss", "graciasss",
    "ok", "okay", "listo", "perfecto", "dale", "genial",
    "chao", "bye",
)
_POST_ORDER_CLOSE_PHRASES = (
    "muchas gracias", "muchisimas gracias", "muchísimas gracias",
    "mil gracias", "gracias bro", "gracias amigo",
    "si gracias", "ok gracias", "listo gracias", "vale gracias",
    "perfecto gracias", "bueno gracias", "dale gracias", "ya gracias",
    "todo bien", "ya esta", "esta bien", "asi esta bien",
    "con gusto",
    "hasta luego", "nos vemos",
    "que disfrute", "que disfruten", "que estes bien",
)
# Interrogatives that block the post-order close detection — even if
# the message contains a polite token, we MUST NOT treat it as a close
# when the user is asking something. "ok pero cuánto?" is not a close.
_BLOCKING_INTERROGATIVES = frozenset({
    "cuanto", "cuantos", "cuanta", "cuantas",
    "que", "qué", "como", "cómo", "donde", "dónde",
    "cuando", "cuándo", "cual", "cuál", "cuales",
    "quien", "quién", "quienes",
    "porque", "por", "porqué",
    "vale", "cuesta", "cuestan", "valen", "precio",
})


def _is_post_order_close(message: Optional[str]) -> bool:
    """
    Return True iff ``message`` reads as a polite close / thanks /
    affirmation that fits the post-PLACE_ORDER scenario.

    Caller MUST gate the call on ``turn_ctx.latest_order_status`` —
    otherwise this fires on plain greetings.
    """
    if not message:
        return False
    nfkd = unicodedata.normalize("NFD", message.lower())
    cleaned = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    cleaned = re.sub(r"[^\w\s!]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return False
    tokens = cleaned.split()
    # Cap at 5 tokens — post-order closes are short by definition.
    if len(tokens) > 5:
        return False
    token_set = set(tokens)
    # Multi-word phrases (substring match on the normalized message).
    # Checked BEFORE the interrogative blocker so "vale gracias" /
    # "bueno gracias" still count as closes — the gratitude word
    # disambiguates them from a price question.
    for phrase in _POST_ORDER_CLOSE_PHRASES:
        nfkd2 = unicodedata.normalize("NFD", phrase.lower())
        norm = "".join(c for c in nfkd2 if unicodedata.category(c) != "Mn")
        if norm in cleaned:
            return True
    # Hard block: any interrogative-like word means the user is asking,
    # not closing. "ok pero cuánto?" / "vale" alone (price) → False.
    if token_set & _BLOCKING_INTERROGATIVES:
        return False
    # Lone tokens (word-level match, very conservative list). Only
    # applied when the message is essentially that token alone — a
    # 3+ token message like "ok dame otra" must NOT match because the
    # extra tokens carry a different intent (a new order, a question,
    # etc.).
    if len(tokens) <= 2:
        for tok in _POST_ORDER_CLOSE_LONE_TOKENS:
            if tok in token_set:
                return True
    return False


PLANNER_SYSTEM_TEMPLATE = """Eres el clasificador de intención para el agente de servicio al cliente de un restaurante. Manejas preguntas pre-venta (horarios, ubicación, domicilio, medios de pago, promos) Y post-venta (estado de pedido, historial, cancelación).

Devuelves EXACTAMENTE una intención en JSON. Nunca markdown, nunca explicación.

Intenciones válidas: GET_BUSINESS_INFO, GET_ORDER_STATUS, GET_ORDER_HISTORY, CANCEL_ORDER, GET_PROMOS, SELECT_LISTED_PROMO, CUSTOMER_SERVICE_CHAT.

Reglas:
- GET_BUSINESS_INFO con params.field: pregunta sobre el negocio. Valores de field:
    "hours"           → cualquier pregunta sobre HORARIOS, DISPONIBILIDAD, o si el local está OPERANDO ahora. Cubre tanto preguntas explícitas de hora ("a qué hora abren", "cuándo cierran", "abren los domingos", "qué horario tienen") como preguntas de disponibilidad/atención/servicio ("hay atención", "hay atención hoy", "hay servicio", "tienen servicio", "están atendiendo", "siguen atendiendo", "ya atienden", "todavía atienden", "están abiertos", "siguen abiertos", "ya están abiertos", "ya abrieron", "están operando", "ya cerraron"). Son la misma intención: saber si el negocio está operando. Las frases listadas son ilustrativas, NO exhaustivas — interpreta contextualmente cualquier pregunta del cliente sobre si la tienda está atendiendo, ofreciendo servicio, abierta, o disponible AHORA, incluso si usa palabras distintas a las listadas.
    "address"         → ubicación ("dónde quedan", "cuál es la dirección")
    "phone"           → teléfono DE CONTACTO general del negocio para llamarlos o escribirles
                        ("cuál es su número", "a qué teléfono los llamo", "número de contacto",
                        "tienen WhatsApp"). NO uses esto para preguntas de PAGO — esas van a
                        "payment_details", aunque mencionen un "número".
    "delivery_fee"    → costo del domicilio ("cuánto cobran domicilio", "cuánto vale el envío")
    "delivery_time"   → tiempo de entrega ("cuánto se demora la entrega", "cuánto tardan en entregar",
                        "en cuánto tiempo llega", "qué tan rápido entregan", "cuánto se demoran",
                        "cuánto tarda un domicilio"). El backend usa per-order ETA si el cliente ya
                        tiene un pedido en curso; sin pedido, devuelve la política general del negocio.
    "menu_url"        → cualquier pedido del MENÚ o la CARTA, incluyendo el link.
                        Cubre "menú" y "carta" (sinónimos en Colombia) con cualquier verbo
                        de visualización/envío: "envíame la carta", "me mandas el menú",
                        "pásame el menú", "quiero ver la carta", "quiero conocer la carta",
                        "tienes carta", "me puedes enviar la carta", "compárteme el menú",
                        "me regalas la carta", "me podrías regalar la carta",
                        "regálame el menú", "regalame la carta",
                        "qué tienen para comer", "qué venden". En Colombia "regalar"/"me regalas"
                        es un verbo coloquial equivalente a "dar"/"compartir" — trátalo igual que
                        "envíame", "pásame", "compárteme". Las frases listadas son ilustrativas,
                        NO exhaustivas — cualquier solicitud del cliente para ver/recibir el
                        menú o la carta cae aquí.
    "payment_methods" → MEDIOS DE PAGO que el negocio acepta — la LISTA de opciones
                        ("aceptan nequi?", "qué pagos reciben", "puedo pagar con tarjeta?",
                        "aceptan efectivo?"). Responde si tal o cual medio se acepta.
    "payment_details" → CÓMO o DÓNDE PAGAR — instrucciones de pago: número de Nequi,
                        cuenta bancaria, datos de transferencia, o si el pago es CONTRA
                        ENTREGA al domiciliario. Frases típicas (NO exhaustivas):
                        "donde pago", "donde transfiero", "a qué número se realiza el pago",
                        "a qué número pago", "cuál es el Nequi", "número de Nequi para
                        transferir", "ese es el Nequi?", "cuenta para depositar",
                        "datos para transferir", "pásame el Nequi", "cómo te pago",
                        "dónde consigno". CRÍTICO: aunque la pregunta mencione "número",
                        si el contexto es PAGO usa "payment_details", NUNCA "phone".
- GET_ORDER_STATUS: el usuario pregunta por el estado o el DESGLOSE de su pedido actual o reciente. Cubre:
  (a) Estado / tiempo de entrega: "dónde está mi pedido", "ya salió?", "cuánto falta", "qué pasa con mi pedido".
  (b) DESGLOSE DE PRECIOS POR ÍTEM de un pedido YA COLOCADO: "cuánto vale cada producto", "cuánto me cobraron por cada uno", "el desglose del pedido", "el total de cada uno", "cómo me lo cobraron", "el detalle del pedido", "cuánto valió cada cosa". Frases ilustrativas, NO exhaustivas — cualquier pregunta del cliente sobre los precios de los items específicos del pedido que ya hizo cae aquí. El response generator ya tiene `unit_price` + `line_total` por item; usa GET_ORDER_STATUS y la respuesta los muestra. NO confundas con `payment_methods` (qué medios acepta el negocio) ni con `delivery_fee` (cuánto cuesta el domicilio).
  Sin params.
- GET_ORDER_HISTORY: el usuario pide ver pedidos anteriores
  ("qué he pedido antes", "muéstrame mis pedidos", "último pedido"). Sin params.
- CANCEL_ORDER: el usuario quiere CANCELAR/anular un pedido YA CONFIRMADO en el sistema
  ("cancela mi pedido", "anula el pedido", "ya no quiero el pedido que hice",
   "cancélalo"). Sin params.
  REGLA DURA: SOLO emite CANCEL_ORDER cuando el CONTEXTO indica que existe un pedido
  CONFIRMADO pendiente cancelable ("Pedido confirmado pendiente: sí"). Si el contexto
  dice "Pedido confirmado pendiente: no" o si hay un carrito activo (carrito en
  ORDERING / COLLECTING_DELIVERY / READY_TO_PLACE sin colocar todavía), NO uses
  CANCEL_ORDER — el order agent maneja abandonar carritos en curso. En ese caso
  responde con CUSTOMER_SERVICE_CHAT.
- GET_PROMOS: el usuario pregunta SI HAY promos / ofertas / combos disponibles, sin
  identificar una en particular ("qué promos tienen", "tienen ofertas hoy",
  "hay alguna promo", "qué combos manejan", "promos del lunes"). Sin params.
  NO uses GET_PROMOS si el usuario nombra una promo específica que quiere — ese caso
  va a SELECT_LISTED_PROMO o lo maneja el agente de pedido.
- SELECT_LISTED_PROMO: cuando el bot ACABA DE LISTAR promos (turno anterior) y el
  usuario eligió UNA. Pasa params según cómo eligió:
    * params.selector="primera" / "segunda" / "1" / "2" cuando usa ordinal
      ("la primera", "esa segunda", "dame la 1").
    * params.query="<frase>" cuando nombra parte del título ("la del honey",
      "el combo familiar", "esa de hamburguesa").
    * params.promo_id="<uuid>" si el usuario por algún motivo cita el id.
  Frases típicas: "dame esa", "quiero la primera", "la del honey burger",
  "esa segunda", "sí, esa". El handoff al agente de pedido lo hace el backend.
- CUSTOMER_SERVICE_CHAT: cualquier otra cosa que no encaja. Sin params.

Si el mensaje pregunta por varias cosas a la vez, elige la más específica. Si un dato no aparece arriba (ej. "¿hacen eventos?"), usa CUSTOMER_SERVICE_CHAT.

CRÍTICO sobre la forma del JSON: el "intent" SIEMPRE es uno de los siete listados arriba (GET_BUSINESS_INFO, GET_ORDER_STATUS, GET_ORDER_HISTORY, CANCEL_ORDER, GET_PROMOS, SELECT_LISTED_PROMO, CUSTOMER_SERVICE_CHAT). Los nombres de field ("hours", "phone", "payment_details", etc.) NUNCA van como intent — siempre como params.field dentro de GET_BUSINESS_INFO.

Ejemplos correctos:
  Cliente: "donde transfiero?" → {"intent": "GET_BUSINESS_INFO", "params": {"field": "payment_details"}}
  Cliente: "a qué número pago?" → {"intent": "GET_BUSINESS_INFO", "params": {"field": "payment_details"}}
  Cliente: "aceptan nequi?"     → {"intent": "GET_BUSINESS_INFO", "params": {"field": "payment_methods"}}
  Cliente: "cuál es el teléfono del local?" → {"intent": "GET_BUSINESS_INFO", "params": {"field": "phone"}}

Responde SOLO con JSON:
{"intent": "<INTENT>", "params": {}}
"""


# Template-based reply: short, no LLM. Used for the common
# "user asked for one field, we have it" case.
_BUSINESS_INFO_TEMPLATES = {
    "hours": "{value}",
    "address": "Estamos ubicados en {value}.",
    "phone": "Puedes contactarnos al {value}.",
    "delivery_fee": "El domicilio tiene un costo base de {value}, puede variar según la distancia.",
    "delivery_time": "Nuestros pedidos llegan en {value}.",
    "menu_url": "Acá tienes nuestro menú: {value}",
    "payment_methods": "Aceptamos {value}.",
    "payment_details": "{value}",
}


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_planner_response(text: str) -> Dict[str, Any]:
    """Parse planner JSON with markdown-fence tolerance, same contract as order agent."""
    if not text:
        return {"intent": INTENT_CUSTOMER_SERVICE_CHAT, "params": {}}
    cleaned = _JSON_FENCE_RE.sub("", text).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", cleaned, re.DOTALL)
        if not match:
            return {"intent": INTENT_CUSTOMER_SERVICE_CHAT, "params": {}}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {"intent": INTENT_CUSTOMER_SERVICE_CHAT, "params": {}}
    if not isinstance(parsed, dict):
        return {"intent": INTENT_CUSTOMER_SERVICE_CHAT, "params": {}}
    return {
        "intent": (parsed.get("intent") or INTENT_CUSTOMER_SERVICE_CHAT),
        "params": parsed.get("params") or {},
    }


class CustomerServiceAgent(BaseAgent):
    """Customer service agent: planner → executor → response (template or LLM)."""

    agent_type = "customer_service"

    def __init__(self):
        self._llm = None
        logging.info("[CS_AGENT] Initialized (LLM lazy, template responses for simple cases)")

    @property
    def llm(self) -> ChatOpenAI:
        if self._llm is None:
            self._llm = ChatOpenAI(
                model="gpt-4o-mini",
                temperature=0.3,
                api_key=os.getenv("OPENAI_API_KEY"),
            )
        return self._llm

    def get_tools(self) -> List:
        # No @tool-wrapped tools — the executor calls services directly.
        return []

    def get_system_prompt(
        self,
        business_context: Optional[Dict],
        current_date: str,
        current_year: int,
        wa_id: str,
        name: str,
    ) -> str:
        """Used by the LLM response path for business context."""
        business_name = "el restaurante"
        if business_context and business_context.get("business"):
            business_name = business_context["business"].get("name") or business_name
        return f"Negocio: {business_name}. Fecha: {current_date}."

    # ── Response generation ────────────────────────────────────────

    def _try_template_reply(self, exec_result: Dict[str, Any]) -> Optional[str]:
        """
        Render a templated reply for cases where we want a deterministic,
        zero-latency response with no LLM call. Returns None when the
        response needs LLM generation.
        """
        result_kind = exec_result.get("result_kind")

        # Delivery handoff: the executor already disabled the bot for
        # this conversation. Render a fixed apology so the message
        # cannot drift, hallucinate ETAs, or undermine the handoff.
        if result_kind == RESULT_KIND_DELIVERY_HANDOFF:
            return (
                "Disculpa la demora con tu pedido. Voy a contactar al "
                "domiciliario para verificar y te confirmamos cuanto antes "
                "por aquí."
            )

        if result_kind != RESULT_KIND_BUSINESS_INFO:
            return None
        field = exec_result.get("field")
        value = exec_result.get("value")
        template = _BUSINESS_INFO_TEMPLATES.get(field)
        if not template or not value:
            return None
        return template.format(value=value)

    def _build_response_prompt(
        self,
        *,
        result_kind: str,
        exec_result: Dict[str, Any],
        message_body: str,
        business_context: Optional[Dict],
    ) -> tuple:
        """
        Build (system, human_input) for the LLM response path. Only called
        when templating isn't applicable.
        """
        business_name = "el restaurante"
        ai_prompt_rules = ""
        if business_context and business_context.get("business"):
            business_name = business_context["business"].get("name") or business_name
            settings = (business_context["business"].get("settings") or {})
            ai_prompt_rules = (settings.get("ai_prompt") or "").strip()

        base_system = (
            f"Eres el agente de servicio al cliente de {business_name}. "
            "Respondes en español colombiano, natural y breve (1-3 oraciones). "
            "Nunca inventas información que no esté en los datos proporcionados. "
            "NUNCA inicies la respuesta con un saludo (Hola, Buenas, Buen día/tardes/noches, "
            "Hey) ni con el nombre del cliente como saludo (ej. 'Hola Yisela', 'Yisela,'). "
            "La conversación ya está en curso — empieza directo con el contenido. El saludo "
            "de bienvenida lo maneja el router en el primer turno."
        )
        # Surface business-declared rules (combos, default accompaniments,
        # policies) so CS responses can answer questions like "vienen en
        # combo?" / "las hamburguesas traen papas?" without falling back
        # to "no entendí". Mirrors what order_agent.py does.
        if ai_prompt_rules:
            base_system += (
                "\n\nIMPORTANTE: Reglas y contexto del negocio "
                "(úsalas SIEMPRE para preguntas sobre combos, acompañamientos "
                "incluidos por default como papas, bebidas, y cualquier política "
                "del negocio):\n"
                + ai_prompt_rules
            )

        if result_kind == RESULT_KIND_BUSINESS_INFO:
            field = exec_result.get("field")
            value = exec_result.get("value")
            system = base_system + "\nSITUACIÓN: Tienes el dato exacto del negocio — comunícalo."
            inp = f"Pregunta del cliente: {message_body}\nCampo: {field}\nValor: {value}"
            return system, inp

        if result_kind == RESULT_KIND_INFO_MISSING:
            field = exec_result.get("field") or "(no identificado)"
            system = (
                base_system
                + "\nSITUACIÓN: No tienes ese dato configurado. Discúlpate brevemente y sugiere "
                "que puedes responder sobre otros temas (horarios, dirección, domicilio, pagos, "
                "estado de pedidos). NO inventes el dato. NUNCA inventes URLs, links, ni uses "
                "placeholders entre paréntesis tipo `(menu_url)` o `<link>` — si no tienes la URL real, "
                "no la menciones."
            )
            inp = (
                f"Pregunta del cliente: {message_body}\n"
                f"Campo no disponible: {field}"
            )
            return system, inp

        if result_kind == RESULT_KIND_ORDER_STATUS:
            order = exec_result.get("order") or {}
            status = order.get("status") or "desconocido"
            total = order.get("total_amount")
            items = order.get("items") or []
            # Render `1x BARRACUDA — $28.000` (with notes when present)
            # so customers asking "cuánto vale cada producto?" / "cómo
            # me cobraron?" after order placement get the actual
            # per-item breakdown. Production 2026-05-06 (Biela /
            # +573159280840): the prior format dropped the product
            # name and the bot replied "no tengo esa información".
            def _fmt_item(it):
                qty = int(it.get("quantity") or 0)
                name = it.get("name") or "(sin nombre)"
                price = int(float(it.get("unit_price") or 0))
                line_total = int(float(it.get("line_total") or (price * qty)))
                notes = (it.get("notes") or "").strip()
                notes_part = f" ({notes})" if notes else ""
                price_str = f"${price:,}".replace(",", ".")
                line_total_str = f"${line_total:,}".replace(",", ".")
                # When qty > 1, surface the line total alongside the unit price.
                if qty > 1:
                    return f"- {qty}x {name}{notes_part} — {price_str} c/u (total {line_total_str})"
                return f"- {qty}x {name}{notes_part} — {price_str}"
            items_lines = "\n".join(_fmt_item(it) for it in items) or "(sin items)"
            cancellation_reason = (order.get("cancellation_reason") or "").strip() or None
            eta_minutes = order.get("eta_minutes")

            # State-only framing by default. Timing is conditional: only
            # surface the ETA when the customer explicitly asks about time
            # ("cuánto se demora", etc.). Tone should read like a polite
            # restaurant rep — warm, brief, no filler, no time hints.
            status_rules = (
                "REGLAS DE ESTADO (UNA sola oración, tono cordial y profesional):\n"
                "- pending: 'Tu pedido quedó registrado y está pendiente de confirmación. En un momento te avisamos.'\n"
                "- confirmed: 'Tu pedido ya fue confirmado y lo estamos preparando con cuidado.'\n"
                "- out_for_delivery: 'Tu pedido va en camino, ya casi llega.'\n"
                "- completed: 'Tu pedido ya fue entregado. ¿Hay algo más en lo que te podamos ayudar?'\n"
                "- cancelled: 'Tu pedido fue cancelado.' (si hay motivo de cancelación, intégralo con naturalidad)\n"
                "- cualquier otro: reporta el estado literal sin inventar.\n"
                "NUNCA digas 'en camino' a menos que el estado sea exactamente out_for_delivery."
            )
            timing_rules = (
                "REGLAS DE TIEMPO (CRÍTICO):\n"
                "- El cliente NO está preguntando por tiempo a menos que use frases como "
                "'cuánto se demora', 'cuánto falta', 'en cuánto llega', 'cuándo llega', "
                "'a qué hora', 'tarda mucho'. Preguntas como 'cómo va', 'qué pasó', "
                "'dónde está' NO son preguntas por tiempo.\n"
                "- Si el cliente NO pidió tiempo: NO menciones minutos, NO digas 'aproximado', "
                "NO digas 'si quieres saber...', NO ofrezcas el ETA. Solo el estado.\n"
                "- Si SÍ pidió tiempo y hay ETA: di 'un aproximado de X minutos' usando el "
                "número exacto del bloque de datos.\n"
                "- Si SÍ pidió tiempo y NO hay ETA: discúlpate breve y di que no tienes el tiempo exacto."
            )
            tone_rules = (
                "TONO: profesional pero cálido (como mesero atento). "
                "Máximo 2 oraciones (hasta 4 si el cliente pidió el desglose por ítem). "
                "Sin emojis salvo que el cliente los use. "
                "Sin frases relleno tipo 'si quieres saber', 'por si acaso', 'cualquier cosa'."
            )
            breakdown_rules = (
                "REGLAS DE DESGLOSE (CRÍTICO):\n"
                "- Si el cliente pidió EXPLÍCITAMENTE el desglose por ítem ('cuánto vale cada producto', "
                "'cómo me cobraron', 'el detalle del pedido', 'cuánto valió cada cosa', 'el total "
                "de cada uno'): muestra LITERALMENTE las líneas de Items (con nombre + precio) tal "
                "como vienen en el bloque de datos, después del estado. Termina con el Total. NO "
                "inventes precios; usa los del bloque.\n"
                "- Si el cliente NO pidió desglose, NO listes los items — solo el estado."
            )
            system = (
                base_system
                + "\nSITUACIÓN: El cliente pregunta por el estado de su pedido. "
                "Responde con el estado actual. Tiempos SOLO si los pide explícitamente. "
                "Desglose por ítem SOLO si lo pide explícitamente.\n"
                + status_rules + "\n" + timing_rules + "\n" + breakdown_rules + "\n" + tone_rules
            )
            eta_str = f"{eta_minutes} min" if eta_minutes is not None else "—"
            total_str = f"${int(float(total or 0)):,}".replace(",", ".")
            inp = (
                f"Pregunta del cliente: {message_body}\n"
                f"Estado: {status}\n"
                f"ETA aproximado: {eta_str}\n"
                f"Motivo de cancelación: {cancellation_reason or '—'}\n"
                f"Total: {total_str}\n"
                f"Items:\n{items_lines}"
            )
            return system, inp

        if result_kind == RESULT_KIND_NO_ORDER:
            system = (
                base_system
                + "\nSITUACIÓN: El cliente pregunta por un pedido pero no tiene ninguno registrado "
                "en nuestro sistema. Informa de forma amable y ofrece ayudarle a hacer un pedido."
            )
            inp = f"Pregunta del cliente: {message_body}"
            return system, inp

        if result_kind == RESULT_KIND_ORDER_HISTORY:
            orders = exec_result.get("orders") or []
            summaries = []
            for o in orders:
                status = o.get("status") or "?"
                total = o.get("total_amount")
                created = (o.get("created_at") or "").split("T")[0]
                summaries.append(
                    f"- {created} | {status} | total ${int(float(total or 0)):,}".replace(",", ".")
                )
            system = (
                base_system
                + "\nSITUACIÓN: Resumes los pedidos anteriores del cliente en formato corto."
            )
            inp = (
                f"Pregunta del cliente: {message_body}\n"
                f"Pedidos:\n" + "\n".join(summaries)
            )
            return system, inp

        if result_kind == RESULT_KIND_ORDER_CANCELLED:
            order = exec_result.get("order") or {}
            order_id = (order.get("id") or "")[:8].upper()
            system = (
                base_system
                + "\nSITUACIÓN: Acabas de cancelar el pedido del cliente exitosamente. "
                "REGLAS:\n"
                "- Confirma la cancelación de forma clara y profesional.\n"
                "- Menciona el número de pedido si está disponible.\n"
                "- Cierra ofreciendo ayuda futura ('cuando quieras volver a pedir, aquí estamos').\n"
                "- 1-2 oraciones, tono cordial."
            )
            inp = (
                f"Mensaje original del cliente: {message_body}\n"
                f"Número de pedido cancelado: #{order_id}"
            )
            return system, inp

        if result_kind == RESULT_KIND_CANCEL_NOT_ALLOWED:
            order = exec_result.get("order") or {}
            status = order.get("status") or "desconocido"
            order_id = (order.get("id") or "")[:8].upper()
            phone = (
                business_info_service.get_business_info(business_context, "phone")
                if business_context else None
            )
            phone_clause = (
                f"Si necesitas ayuda urgente, llámanos al {phone}."
                if phone else
                "Para cualquier ajuste, comunícate directamente con el restaurante."
            )
            system = (
                base_system
                + "\nSITUACIÓN: El cliente quiere cancelar pero el estado del pedido NO lo permite. "
                "REGLAS POR ESTADO:\n"
                "- out_for_delivery: 'Tu pedido ya va en camino, ya no lo podemos cancelar desde acá.'\n"
                "- completed: 'Tu pedido ya fue entregado, no se puede cancelar.'\n"
                "- cancelled: 'Tu pedido ya estaba cancelado.'\n"
                "- cualquier otro: explica brevemente que no es posible cancelar en este estado.\n"
                "Sé profesional y empático. NO inventes razones. "
                f"Cierra con: '{phone_clause}'\n"
                "Máximo 2 oraciones."
            )
            inp = (
                f"Mensaje original del cliente: {message_body}\n"
                f"Pedido: #{order_id}\n"
                f"Estado actual: {status}"
            )
            return system, inp

        if result_kind == RESULT_KIND_PROMOS_LIST:
            promos = exec_result.get("promos") or []
            upcoming = exec_result.get("upcoming_promos") or []

            def render(p: Dict[str, Any], idx: int) -> str:
                bits = [f"{idx}. {p.get('name')}"]
                if p.get("price_kind"):
                    bits.append(f"— {p['price_kind']}")
                if p.get("schedule_label"):
                    bits.append(f"({p['schedule_label']})")
                if p.get("description"):
                    bits.append(f"\n   {p['description']}")
                return " ".join(bits)

            active_lines = "\n".join(render(p, i) for i, p in enumerate(promos, start=1))
            upcoming_lines = "\n".join(
                render(p, i) for i, p in enumerate(upcoming, start=1)
            )

            system = (
                base_system
                + "\nSITUACIÓN: El cliente preguntó por las promos disponibles. "
                "REGLAS:\n"
                "- Si hay 'Promos activas hoy', preséntalas primero (numeradas) y "
                "  cierra invitando a elegir ('si quieres alguna, dime cuál').\n"
                "- Si NO hay activas hoy pero SÍ hay 'Próximas promos', dile "
                "  amablemente que hoy no hay promos disponibles, y menciona qué "
                "  días aplican las próximas (ej. 'pero el viernes tenemos X').\n"
                "- Si hay AMBAS (activas + próximas), prioriza las activas. Puedes "
                "  añadir UNA línea breve mencionando que también hay otras durante "
                "  la semana, sin listarlas todas.\n"
                "- NO inventes promos; usa solo lo del bloque de datos.\n"
                "- Tono cordial y breve. Una promo por línea."
            )
            inp = (
                f"Pregunta del cliente: {message_body}\n"
                f"Promos activas hoy:\n{active_lines or '(ninguna)'}\n\n"
                f"Próximas promos esta semana:\n{upcoming_lines or '(ninguna)'}"
            )
            return system, inp

        if result_kind == RESULT_KIND_NO_PROMOS:
            system = (
                base_system
                + "\nSITUACIÓN: El cliente preguntó por promos pero hoy no hay ninguna activa. "
                "REGLAS:\n"
                "- Dile claro y amable que por hoy no hay promos activas.\n"
                "- Ofrece ayudarle con el menú o un pedido normal.\n"
                "- 1-2 oraciones."
            )
            inp = f"Pregunta del cliente: {message_body}"
            return system, inp

        if result_kind == RESULT_KIND_PROMO_NOT_RESOLVED:
            listed_count = int(exec_result.get("listed_count") or 0)
            query = exec_result.get("query")
            system = (
                base_system
                + "\nSITUACIÓN: El cliente pidió una promo pero no encontré ninguna que coincida. "
                "REGLAS:\n"
                "- Si el cliente nombró algo (hay 'Texto buscado'), dile que no hay una promo "
                "  activa con ese nombre y ofrece listar las que sí están disponibles hoy.\n"
                "- Si NO nombró nada (solo 'dame una de esas') y listed_count=0, ofrece "
                "  primero listar las promos disponibles.\n"
                "- Si listed_count > 0 y NO nombró nada, pide que la identifique por "
                "  número (ej. 'la primera', 'la 2') o por nombre.\n"
                "- 1-2 oraciones, tono cordial."
            )
            inp = (
                f"Pregunta del cliente: {message_body}\n"
                f"Texto buscado: {query or '(ninguno)'}\n"
                f"Promos previamente listadas: {listed_count}"
            )
            return system, inp

        if result_kind == RESULT_KIND_PROMO_AMBIGUOUS:
            query = exec_result.get("query") or ""
            candidates = exec_result.get("candidates") or []
            options_lines = "\n".join(
                f"{idx}. {c.get('name')}"
                for idx, c in enumerate(candidates, start=1)
            )
            system = (
                base_system
                + "\nSITUACIÓN: El cliente pidió una promo y varias coinciden. "
                "REGLAS:\n"
                "- Lista las opciones numeradas (1., 2., ...) en una línea cada una.\n"
                "- Pide al cliente que indique cuál — por número o por nombre.\n"
                "- NO inventes opciones; usa solo las del bloque de datos.\n"
                "- 2-3 líneas, tono cordial."
            )
            inp = (
                f"Pregunta del cliente: {message_body}\n"
                f"Texto buscado: {query}\n"
                f"Opciones que coinciden:\n{options_lines}"
            )
            return system, inp

        if result_kind == RESULT_KIND_CHAT_FALLBACK:
            system = (
                base_system
                + "\nSITUACIÓN: La pregunta del cliente no encajó en una intención específica. "
                "PRIMERO revisa la sección 'Reglas y contexto del negocio' (si existe arriba) — "
                "muchas preguntas sobre combos, acompañamientos incluidos (papas, bebida), políticas "
                "o lo que trae cada plato se responden DIRECTAMENTE desde esas reglas. Si las reglas "
                "responden la pregunta, contesta usando esa información (NO digas 'no entendí'). "
                "Solo si las reglas no aplican Y no tienes datos para responder, dile brevemente "
                "con qué puedes ayudar: menú/carta, horarios, dirección, domicilio, medios de pago, "
                "estado de pedido, historial. NUNCA inventes URLs, links, ni uses placeholders entre "
                "paréntesis tipo `(menu_url)` o `<link>` — si no tienes la URL real, no la menciones."
            )
            inp = f"Pregunta del cliente: {message_body}"
            return system, inp

        if result_kind == RESULT_KIND_INTERNAL_ERROR:
            system = (
                base_system
                + "\nSITUACIÓN: Ocurrió un error técnico. Discúlpate brevemente y "
                "sugiere que lo intenten de nuevo o contacten directamente al negocio."
            )
            inp = f"Pregunta del cliente: {message_body}"
            return system, inp

        # Safe default — should not be reached given VALID intents.
        system = base_system
        inp = f"Pregunta del cliente: {message_body}"
        return system, inp

    # ── execute ────────────────────────────────────────────────────

    def execute(
        self,
        message_body: str,
        wa_id: str,
        name: str,
        business_context: Optional[Dict],
        conversation_history: List[Dict],
        message_id: Optional[str] = None,
        session: Optional[Dict] = None,
        stale_turn: bool = False,
        turn_ctx: Optional[object] = None,
        **kwargs,
    ) -> AgentOutput:
        """Planner → executor → response generator (template or LLM)."""
        start_time = time.time()
        run_id = str(uuid.uuid4())
        business_id = (business_context or {}).get("business_id") or ""

        tracer.start_run(run_id=run_id, user_id=wa_id, message_id=message_id, business_id=str(business_id))

        # 0) Order-closed handoff fast-path. When the order agent's
        # availability gate blocked a mutating intent, it hands off to
        # us with handoff_context.reason="order_closed". Compose a
        # deterministic reply using business_info_service so the prose
        # matches the existing "¿están abiertos?" answer (single source
        # of truth: format_open_status_sentence). Skip the planner LLM —
        # this is a fixed situation, not a free-text classification.
        handoff_context = kwargs.get("handoff_context") or {}
        if (handoff_context.get("reason") or "").strip() == "order_closed":
            blocked_intents = handoff_context.get("blocked_intents") or []
            has_active_cart = bool(handoff_context.get("has_active_cart"))
            logging.warning(
                "[ORDER_GATE] business=%s wa_id=%s CS handling order_closed "
                "handoff blocked_intents=%s has_active_cart=%s",
                business_id, wa_id, blocked_intents, has_active_cart,
            )
            fully_closed_today = False
            alt_contact_suffix = ""
            try:
                from ..services import business_info_service as _bi_svc
                status = _bi_svc.compute_open_status(str(business_id))
                sentence = _bi_svc.format_open_status_sentence(status)
                fully_closed_today = _bi_svc.is_fully_closed_today(status)
                if fully_closed_today:
                    biz = (business_context or {}).get("business")
                    alt_contact_suffix = _bi_svc.format_closed_alt_contact_suffix(biz)
            except Exception as exc:
                logging.warning(
                    "[ORDER_GATE] business=%s wa_id=%s open-status compute failed: %s",
                    business_id, wa_id, exc,
                )
                sentence = "Por ahora estamos cerrados."
            base = sentence or "Por ahora estamos cerrados."
            if has_active_cart:
                tail = (
                    " Tu pedido se queda guardado, lo retomamos cuando "
                    "abramos. Mientras tanto puedo resolverte cualquier duda."
                )
            else:
                tail = (
                    " Mientras tanto puedo contarte del menú o resolverte "
                    "cualquier duda."
                )
            # Alt-contact suffix sits between the closed sentence and the
            # tail so the redirect lands BEFORE the "talk about the menu"
            # offer — the redirect is the more actionable answer.
            message = base + alt_contact_suffix + tail
            try:
                conversation_service.store_conversation_message(
                    wa_id, message, "assistant", business_id=business_id,
                )
            except Exception as exc:
                logging.error(
                    "[ORDER_GATE] business=%s wa_id=%s persist failed: %s",
                    business_id, wa_id, exc,
                )
            logging.warning(
                "[ORDER_GATE] business=%s wa_id=%s CS replied "
                "(deterministic, no LLM)",
                business_id, wa_id,
            )
            tracer.end_run(
                run_id, success=True,
                latency_ms=(time.time() - start_time) * 1000,
            )
            return {
                "agent_type": self.agent_type,
                "message": message,
                "state_update": {},
            }

        # 0b) Out-of-zone delivery redirect. Order agent hands off here
        # with reason="out_of_zone" + city/phone when the customer asks
        # to order/deliver to a city listed in
        # ``business.settings.out_of_zone_delivery_contacts``. We render
        # a polished, deterministic redirect — no LLM, no hallucinated
        # phone numbers.
        if (handoff_context.get("reason") or "").strip() == "out_of_zone":
            city = (handoff_context.get("city") or "").strip()
            phone = (handoff_context.get("phone") or "").strip()
            logging.warning(
                "[OUT_OF_ZONE] business=%s wa_id=%s CS handling redirect "
                "city=%s phone=%s",
                business_id, wa_id, city, phone,
            )
            if city and phone:
                message = (
                    f"📍 Por ahora no tenemos cobertura de domicilio en *{city}*.\n\n"
                    f"Para tu pedido en esa zona, escríbele directamente a "
                    f"este WhatsApp 👉 *{phone}*\n\n"
                    "¡Allá te atienden con todo! 🙌"
                )
            else:
                message = (
                    "📍 Por ahora no tenemos cobertura de domicilio en esa zona. "
                    "¿Te puedo ayudar con algo más?"
                )
            try:
                conversation_service.store_conversation_message(
                    wa_id, message, "assistant", business_id=business_id,
                )
            except Exception as exc:
                logging.error(
                    "[OUT_OF_ZONE] business=%s wa_id=%s persist failed: %s",
                    business_id, wa_id, exc,
                )
            tracer.end_run(
                run_id, success=True,
                latency_ms=(time.time() - start_time) * 1000,
            )
            return {
                "agent_type": self.agent_type,
                "message": message,
                "state_update": {},
            }

        # 1) Planner
        # Unified context block — same shape every layer (router /
        # order / CS) sees. render_for_prompt now includes recent
        # history with role labels, so we don't render it separately.
        # Avoids the prior double-rendering and ensures operator turns
        # are labeled identically across layers.
        ctx_block = ""
        if turn_ctx is not None:
            try:
                from ..orchestration.turn_context import render_for_prompt as _render_ctx
                ctx_block = (
                    "===== ESTADO Y HISTORIAL DEL TURNO =====\n"
                    "(lo que YA pasó antes de este turno)\n\n"
                    + _render_ctx(turn_ctx)
                    + "\n===== FIN DEL ESTADO =====\n\n"
                )
            except Exception:
                ctx_block = ""

        planner_messages = [
            SystemMessage(content=PLANNER_SYSTEM_TEMPLATE),
            HumanMessage(
                content=(
                    f"{ctx_block}"
                    "[MENSAJE ACTUAL DEL CLIENTE — procesa SOLO este "
                    "mensaje en este turno; los anteriores en CONTEXTO "
                    "son historial]\n"
                    f"Usuario: {message_body}\n\n"
                    "Responde solo con JSON: intent y params."
                )
            ),
        ]
        try:
            planner_response = self.llm.invoke(
                planner_messages,
                config={
                    "run_name": "customer_service_planner",
                    "metadata": {
                        "wa_id": wa_id,
                        "business_id": str(business_id),
                        "turn_id": message_id or "",
                        "stale_turn": stale_turn,
                        "run_id": run_id,
                    },
                },
            )
            planner_text = planner_response.content if hasattr(planner_response, "content") else str(planner_response)
        except Exception as exc:
            logging.error("[CS_AGENT] planner LLM failed: %s", exc, exc_info=True)
            planner_text = ""

        parsed = _parse_planner_response(planner_text)
        intent = (parsed.get("intent") or INTENT_CUSTOMER_SERVICE_CHAT).upper().replace(" ", "_")
        params = parsed.get("params") or {}

        # Field-name-as-intent remap. The planner is supposed to emit
        # GET_BUSINESS_INFO with params.field=<key>, but in production we
        # observed it sometimes emits the field name itself as the
        # intent (e.g. {"intent": "PAYMENT_DETAILS", "params": {}} for
        # "gracias Donde transfiero?", 2026-05-06 Biela / 3177000722).
        # That bounced to chat fallback. Remap so the executor still
        # serves the lookup.
        if intent not in VALID_INTENTS:
            field_key = intent.lower()
            if field_key in business_info_service.ALL_FIELDS:
                logging.warning(
                    "[CS_AGENT] field-name-as-intent remap: %r → "
                    "GET_BUSINESS_INFO(field=%s)",
                    intent, field_key,
                )
                intent = INTENT_GET_BUSINESS_INFO
                params = {"field": field_key}

        # Deterministic guard: refuse CANCEL_ORDER unless the customer
        # actually has a placed cancellable order. The router should keep
        # this from happening (active-cart "cancel" goes to order), but
        # we belt-and-suspenders here so a misroute can't silently cancel
        # an in-progress cart from CS. See app/orchestration/turn_context.py
        # for how has_recent_cancellable_order is computed.
        if (
            intent == INTENT_CANCEL_ORDER
            and turn_ctx is not None
            and not getattr(turn_ctx, "has_recent_cancellable_order", False)
        ):
            logging.warning(
                "[CS_AGENT] CANCEL_ORDER refused: no cancellable placed order "
                "(state=%s active_cart=%s) — downgrading to CHAT",
                getattr(turn_ctx, "order_state", "?"),
                getattr(turn_ctx, "has_active_cart", False),
            )
            intent = INTENT_CUSTOMER_SERVICE_CHAT
            params = {}

        # Hard guard: refuse CANCEL_ORDER unless the user message contains
        # an explicit cancel keyword. Prior production incident
        # (2026-05-04, Biela / 3108069647): user said "Si\nGracias" right
        # after PLACE_ORDER and the CS planner emitted CANCEL_ORDER for it,
        # which deleted order #6A8D5250. The customer never said "cancela"
        # / "anula" / etc. — the LLM hallucinated a cancellation question
        # that the bot had not asked. Without an explicit verb of
        # cancellation, we MUST not act on a destructive intent.
        if intent == INTENT_CANCEL_ORDER and not has_explicit_cancel_keyword(message_body):
            logging.warning(
                "[CS_AGENT] CANCEL_ORDER refused: no explicit cancel keyword in "
                "message=%r — downgrading to CHAT",
                (message_body or "")[:120],
            )
            intent = INTENT_CUSTOMER_SERVICE_CHAT
            params = {}

        logging.warning("[CS_AGENT] Planner intent=%s params=%s", intent, params)

        # Safety net: if the planner punted to CUSTOMER_SERVICE_CHAT but
        # the message is actually a price question about a named catalog
        # product, hand off to the order agent instead of replying with
        # "no tengo información". The router's deterministic pre-classifier
        # should already catch this upstream, but we belt-and-suspenders
        # at this layer too — production has shown LLM misroutes here when
        # the product name is unfamiliar (e.g. "Cuánto vale el pegoretti?"
        # for Biela on 2026-05-03). See app/orchestration/router.py for
        # the same logic at the routing layer.
        if intent == INTENT_CUSTOMER_SERVICE_CHAT:
            try:
                from ..orchestration.router import _deterministic_price_of_product
                if _deterministic_price_of_product(message_body, business_context):
                    logging.warning(
                        "[CS_AGENT] CHAT fallback overridden: price-of-product "
                        "detected → handoff to order"
                    )
                    tracer.end_run(run_id, success=True, latency_ms=(time.time() - start_time) * 1000)
                    return {
                        "agent_type": self.agent_type,
                        "message": "",
                        "state_update": {},
                        "handoff": {
                            "to": "order",
                            "segment": message_body,
                            "context": {"reason": "price_of_product_misroute"},
                        },
                    }
            except Exception as exc:
                logging.warning("[CS_AGENT] price-of-product safety net failed: %s", exc)

        # Safety net: post-PLACE_ORDER despedida ("gracias", "si gracias",
        # "perfecto", etc.). The router should already route these to the
        # order agent (DESPEDIDA POST-PEDIDO rule) so the order agent's
        # status-aware response template runs. This is the belt-and-
        # suspenders layer for when the router still lands the turn on
        # CS — production observation 2026-05-05 (Biela / 3177000722)
        # had "Gracias" right after PLACE_ORDER misrouted to CS chat.
        if intent == INTENT_CUSTOMER_SERVICE_CHAT:
            try:
                latest_status = getattr(turn_ctx, "latest_order_status", None) if turn_ctx is not None else None
                if latest_status and _is_post_order_close(message_body):
                    logging.warning(
                        "[CS_AGENT] CHAT fallback overridden: despedida post-pedido "
                        "(latest_status=%s) → handoff to order",
                        latest_status,
                    )
                    tracer.end_run(run_id, success=True, latency_ms=(time.time() - start_time) * 1000)
                    return {
                        "agent_type": self.agent_type,
                        "message": "",
                        "state_update": {},
                        "handoff": {
                            "to": "order",
                            "segment": message_body,
                            "context": {"reason": "despedida_post_pedido_misroute"},
                        },
                    }
            except Exception as exc:
                logging.warning("[CS_AGENT] despedida-post-pedido safety net failed: %s", exc)

        # Safety net 2: stuck-article typos like "unabimota" / "elpegoretti".
        # If the message contains a stuck-article token whose suffix is in
        # the catalog lookup-set, hand off to the order agent with the
        # rewritten message. Mirrors the router-level splitter — see
        # app/orchestration/router.py::_expand_stuck_articles. Production
        # observation 2026-05-05 (Biela / 3177000722): "unabimota" was
        # misrouted to customer_service.
        if intent == INTENT_CUSTOMER_SERVICE_CHAT:
            try:
                from ..orchestration.router import _expand_stuck_articles
                from ..services import catalog_cache as _catalog_cache
                _bid = str((business_context or {}).get("business_id") or "")
                if _bid:
                    _lookup = _catalog_cache.get_router_lookup_set(_bid)
                    if _lookup:
                        _expanded = _expand_stuck_articles(message_body, _lookup)
                        if _expanded != message_body:
                            logging.warning(
                                "[CS_AGENT] CHAT fallback overridden: stuck-article "
                                "typo → handoff to order: %r → %r",
                                message_body, _expanded,
                            )
                            tracer.end_run(run_id, success=True, latency_ms=(time.time() - start_time) * 1000)
                            return {
                                "agent_type": self.agent_type,
                                "message": "",
                                "state_update": {},
                                "handoff": {
                                    "to": "order",
                                    "segment": _expanded,
                                    "context": {"reason": "stuck_article_misroute"},
                                },
                            }
            except Exception as exc:
                logging.warning("[CS_AGENT] stuck-article safety net failed: %s", exc)

        # 2) Executor
        exec_result = execute_customer_service_intent(
            wa_id=wa_id,
            business_id=str(business_id),
            business_context=business_context,
            intent=intent,
            params=params,
            session=session,
        )
        result_kind = exec_result.get("result_kind") or RESULT_KIND_CHAT_FALLBACK

        # Handoff short-circuit: the flow detected this turn belongs to a
        # different agent (e.g. "qué tengo en mi pedido?" with active cart
        # → order/VIEW_CART). Return an empty-message AgentOutput with
        # `handoff` set so the dispatcher runs the target agent. No LLM
        # response call, no conversation history write — the target
        # agent's reply is what the user sees.
        if result_kind == RESULT_KIND_HANDOFF:
            hand = exec_result.get("handoff") or {}
            logging.warning(
                "[CS_AGENT] handoff to %s (reason=%s)",
                hand.get("to"), (hand.get("context") or {}).get("reason"),
            )
            tracer.end_run(run_id, success=True, latency_ms=(time.time() - start_time) * 1000)
            return {
                "agent_type": self.agent_type,
                "message": "",
                "state_update": {},
                "handoff": hand,
            }

        logging.warning(
            "[CS_TURN] wa_id=%s intent=%s result_kind=%s latency_ms=%d",
            wa_id, intent, result_kind,
            int((time.time() - start_time) * 1000),
        )

        # 3) Response: try template first, fall back to LLM
        final_response_text = self._try_template_reply(exec_result)
        if final_response_text is None:
            response_system, resp_input = self._build_response_prompt(
                result_kind=result_kind,
                exec_result=exec_result,
                message_body=message_body,
                business_context=business_context,
            )
            try:
                response_llm = self.llm.invoke(
                    [
                        SystemMessage(content=response_system),
                        HumanMessage(content=resp_input + "\n\nResponde en español colombiano, breve y natural:"),
                    ],
                    config={
                        "run_name": "customer_service_response",
                        "metadata": {
                            "wa_id": wa_id,
                            "business_id": str(business_id),
                            "turn_id": message_id or "",
                            "intent": intent,
                            "result_kind": result_kind,
                            "run_id": run_id,
                        },
                    },
                )
                final_response_text = (response_llm.content if hasattr(response_llm, "content") else str(response_llm)).strip()
            except Exception as exc:
                logging.error("[CS_AGENT] response LLM failed: %s", exc, exc_info=True)
                final_response_text = ""
            final_response_text = final_response_text or "Disculpa, tuve un problema. ¿Podrías intentar de nuevo?"

        # Persist assistant message to conversation history.
        try:
            conversation_service.store_conversation_message(
                wa_id, final_response_text, "assistant", business_id=business_id,
            )
        except Exception as exc:
            logging.error("[CS_AGENT] failed to store assistant message: %s", exc)

        tracer.end_run(run_id, success=True, latency_ms=(time.time() - start_time) * 1000)

        cs_ctx_update: Dict[str, Any] = {
            "last_intent": intent,
            "last_result_kind": result_kind,
        }
        # Executor-emitted state patch (e.g. per-order ask counter from
        # _handle_order_status). Keys merge into customer_service_context
        # so they persist across turns alongside last_intent / last_result_kind.
        exec_state_patch = exec_result.get("state_patch") or {}
        if isinstance(exec_state_patch, dict):
            cs_ctx_update.update(exec_state_patch)
        # Persist the listed promo set so the next turn can resolve
        # "dame esa" / "la primera" via SELECT_LISTED_PROMO.
        if result_kind == RESULT_KIND_PROMOS_LIST:
            cs_ctx_update["last_listed_promos"] = [
                {"id": p.get("id"), "name": p.get("name")}
                for p in (exec_result.get("promos") or [])
            ]

        state_update = {
            # CS state lives under agent_contexts["customer_service"] —
            # the only sub-dict the ConversationSession model persists for
            # per-agent state. Writing directly to a top-level
            # `customer_service_context` key was silently dropped by
            # session_state_service.save().
            "agent_contexts": {"customer_service": cs_ctx_update},
            "active_agents": ["customer_service"],
        }

        return {
            "agent_type": self.agent_type,
            "message": final_response_text,
            "state_update": state_update,
        }
