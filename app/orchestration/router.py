"""
Turn router.

Sits between the webhook handler and the agent dispatcher. Decides
how a message should be answered:
- Greeting fast-path: template reply, no LLM, no agent.
- LLM domain classifier: decomposes the message into one or more
  (domain, text) segments. For most messages this is a single segment;
  mixed-intent messages produce multiple.

Scope (Phase 3b):
- Greeting fast-path (regex, no LLM).
- LLM classifier returns a list of segments. Each segment has a
  domain ∈ {order, customer_service, catalog, chat} and the text
  relevant to that domain.
- Safety caps:
    * At most MAX_SEGMENTS_PER_TURN segments (excess are dropped).
    * Unparseable / invalid responses fall back to a single-segment
      result with the caller's preferred primary domain. (Actually we
      return None segments → caller falls back to primary agent.)

Deliberate non-goals:
- No handoff-chain logic — that's dispatcher territory.
- No segment reordering — segments are dispatched in the order the
  router returned them, which gives the model control over priority
  (e.g. state-mutating intents first).
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..services import business_greeting


logger = logging.getLogger(__name__)


# Domain labels the classifier may emit.
DOMAIN_ORDER = "order"
DOMAIN_CUSTOMER_SERVICE = "customer_service"
DOMAIN_CATALOG = "catalog"
DOMAIN_CHAT = "chat"

_VALID_DOMAINS = {
    DOMAIN_ORDER,
    DOMAIN_CUSTOMER_SERVICE,
    DOMAIN_CATALOG,
    DOMAIN_CHAT,
}


# Cap segments per turn. Matches dispatcher MAX_HOPS so a router that
# over-decomposes can't produce more agent invocations than a handoff
# chain would.
MAX_SEGMENTS_PER_TURN = 3


@dataclass
class RouterResult:
    """
    Outcome of router.route().

    - `direct_reply` set → router produced a complete user-facing response
      itself (e.g. greeting template). Caller sends verbatim.
    - `segments` set → list of (domain, segment_text) pairs. Length 1
      for single-intent, >1 for mixed-intent. Caller maps domain →
      agent_type and passes to the dispatcher.
    - Neither set → classification failed. Caller falls back to the
      business's primary agent on the whole message.
    """

    direct_reply: Optional[str] = None
    segments: Optional[List[Tuple[str, str]]] = None

    @property
    def domain(self) -> Optional[str]:
        """
        Backward-compat helper: returns the lone segment's domain when
        exactly one segment was produced. None otherwise (including for
        multi-segment results).
        """
        if self.segments and len(self.segments) == 1:
            return self.segments[0][0]
        return None


# ── Greeting fast-path ──────────────────────────────────────────────

def _greeting_fast_path(
    message_body: str,
    business_context: Optional[dict],
    customer_name: Optional[str],
) -> Optional[str]:
    """Return the greeting template if message is a pure greeting, else None."""
    if business_greeting.is_pure_greeting(message_body):
        return business_greeting.get_greeting(business_context, customer_name)
    return None


# ── LLM classifier ──────────────────────────────────────────────────

_ROUTER_SYSTEM_PROMPT = """Eres el router de un bot de WhatsApp para un restaurante. Lees el mensaje del cliente y lo divides en SEGMENTOS. Cada segmento tiene un dominio y el texto del mensaje que corresponde a ese dominio.

La mayoría de los mensajes son UN solo segmento (una sola intención). Solo divide en múltiples segmentos cuando el cliente expresa claramente dos o más intenciones independientes en el mismo mensaje.

Dominios disponibles:
- "order": el cliente quiere ordenar, modificar su carrito o hacer checkout. Incluye nombrar productos con intención de compra, cantidades, "quiero X", "dame X", "quitar Y", "confirmar", "ya te pago", "ya listo".
- "customer_service": preguntas sobre el negocio (horarios, ubicación, domicilio, medios de pago, teléfono) O estado/historial de pedidos propios.
- "catalog": el cliente pregunta por productos o por el menú en general. "qué tienen", "tienen coca cola", "qué bebidas hay", "muéstrame el menú".
- "chat": pequeña conversación o saludos con pregunta, sin intención clara en otro dominio.

Reglas de segmentación:
- Mensaje compuesto por UNA sola intención → UN segmento, con todo el texto del cliente.
- Varios productos del mismo pedido → UN segmento (order), no lo separes producto por producto.
  Ejemplo: "dame una barracuda y una cerveza" → UN segmento order.
- Dos intenciones DE DOMINIOS DIFERENTES → DOS segmentos.
  Ejemplo: "dame una barracuda y a qué hora abren mañana" → order + customer_service.
- Saludos cortos ("hola") al inicio de una pregunta → ABSORBER en el dominio principal, no como segmento "chat" aparte.
  Ejemplo: "hola a qué hora abren" → UN segmento customer_service.
- Nunca emitas más de 3 segmentos.
- El texto de cada segmento puede ser un extracto del mensaje original o una reformulación breve; debe conservar todos los datos relevantes (productos, cantidades, campo pedido).

Reglas de desambiguación (dentro de cada segmento):
- "tienen coca cola?" → catalog
- "tienen domicilio?" → customer_service (pregunta por política, no por producto)
- "a qué hora me llega?" durante un pedido activo → customer_service
- "ya te pago" / "listo" durante pedido → order

Responde SOLO con JSON en esta forma exacta, sin markdown, sin explicación:
{"segments": [{"domain": "order" | "customer_service" | "catalog" | "chat", "text": "..."}]}
"""


_llm_classifier = None


def _get_llm_classifier():
    """Lazy-init a cheap LLM for classification."""
    global _llm_classifier
    if _llm_classifier is not None:
        return _llm_classifier
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from langchain_openai import ChatOpenAI
        _llm_classifier = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            # Small message, but segments list can have several items;
            # bump max_tokens a bit over single-domain output.
            max_tokens=200,
            api_key=api_key,
        )
    except Exception as exc:
        logger.warning("[ROUTER] classifier init failed: %s", exc)
    return _llm_classifier


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_segments(raw: str) -> Optional[List[Tuple[str, str]]]:
    """
    Parse the classifier response into a list of (domain, text) tuples.
    Returns None on unparseable / invalid responses — caller falls back
    to primary agent.
    """
    if not raw:
        return None
    cleaned = _JSON_FENCE_RE.sub("", raw).strip()

    parsed = None
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    if not isinstance(parsed, dict):
        logger.warning("[ROUTER] classifier returned unparseable: %r", raw)
        return None

    raw_segments = parsed.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        logger.warning("[ROUTER] classifier returned empty/invalid segments: %r", raw)
        return None

    out: List[Tuple[str, str]] = []
    for item in raw_segments[:MAX_SEGMENTS_PER_TURN]:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain") or "").strip().lower()
        text = str(item.get("text") or "").strip()
        if domain not in _VALID_DOMAINS:
            logger.warning("[ROUTER] invalid domain in segment: %r", item)
            continue
        if not text:
            continue
        out.append((domain, text))

    if not out:
        return None
    return out


def _classify_with_llm(
    message_body: str,
    business_context: Optional[dict],
) -> Optional[List[Tuple[str, str]]]:
    """
    Call the classifier and return a list of (domain, text) segments,
    or None on failure.
    """
    llm = _get_llm_classifier()
    if llm is None:
        return None

    business_id = str((business_context or {}).get("business_id") or "")

    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        response = llm.invoke(
            [
                SystemMessage(content=_ROUTER_SYSTEM_PROMPT),
                HumanMessage(content=f"Mensaje: {message_body}"),
            ],
            config={
                "run_name": "router_classifier",
                "metadata": {
                    "business_id": business_id,
                    "message_length": len(message_body),
                },
            },
        )
        raw = (response.content if hasattr(response, "content") else str(response)).strip()
    except Exception as exc:
        logger.warning("[ROUTER] classifier LLM call failed: %s", exc)
        return None

    return _parse_segments(raw)


# ── Public entry point ──────────────────────────────────────────────

def route(
    message_body: str,
    business_context: Optional[dict],
    customer_name: Optional[str],
) -> RouterResult:
    """
    Classify the message and decide how to respond.

    Flow:
      1. Greeting fast-path — pure greeting returns a direct template reply.
      2. LLM classifier — returns list of (domain, text) segments.
      3. On classifier failure — caller falls back to primary agent.
    """
    # 1. Greeting fast-path
    greeting = _greeting_fast_path(message_body, business_context, customer_name)
    if greeting is not None:
        logger.info("[ROUTER] greeting fast-path hit")
        return RouterResult(direct_reply=greeting)

    # 2. LLM classification
    if not (message_body or "").strip():
        return RouterResult()
    segments = _classify_with_llm(message_body, business_context)
    if not segments:
        logger.warning("[ROUTER] classification failed — caller falls back to primary agent")
        return RouterResult()

    logger.info(
        "[ROUTER] classified n_segments=%d domains=%s",
        len(segments), [d for d, _ in segments],
    )
    return RouterResult(segments=segments)
