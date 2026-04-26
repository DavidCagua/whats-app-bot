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
#
# Design principle: domains map to USER CONCERNS, not to technical operations.
# Browsing the catalog WHILE ordering is a sub-step of "order" — same concern.
# A separate "catalog" domain was tried and removed (see docs/agents-vs-services.md)
# because it overloaded two different concerns: in-bot browsing (an order sub-step)
# and asset requests like "send me the menu URL" (a business-info request belonging
# to customer_service).
DOMAIN_ORDER = "order"
DOMAIN_CUSTOMER_SERVICE = "customer_service"
DOMAIN_CHAT = "chat"

_VALID_DOMAINS = {
    DOMAIN_ORDER,
    DOMAIN_CUSTOMER_SERVICE,
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

_ROUTER_SYSTEM_PROMPT = """Eres el router de un bot de WhatsApp para un restaurante. Lees el mensaje del cliente y lo divides en SEGMENTOS. Cada segmento tiene un dominio (la INTENCIÓN del cliente) y el texto del mensaje que corresponde a ese dominio.

La mayoría de los mensajes son UN solo segmento (una sola intención). Solo divide en múltiples segmentos cuando el cliente expresa claramente DOS O MÁS intenciones independientes en el mismo mensaje.

Dominios disponibles (por INTENCIÓN del cliente):

- "order": el cliente quiere ORDENAR comida. Esto incluye TODO el funnel de pedido:
    * Browsing/exploración del menú dentro del bot ("qué tienen", "qué bebidas hay", "tienen coca cola", "muéstrame el menú", "qué hamburguesas tienen", "qué trae la barracuda")
    * Búsqueda por atributo ("algo con queso", "algo picante")
    * Detalles de un producto específico ("qué trae la montesa")
    * Agregar/modificar/quitar del carrito ("quiero X", "dame X", "una coca", "quita la cerveza")
    * Checkout y confirmación ("listo", "ya te pago", "confirma", "procedamos")

- "customer_service": el cliente pide INFORMACIÓN del negocio o pregunta por sus pedidos pasados/actuales:
    * Información del negocio como ACTIVO/DATO: horarios, ubicación/dirección, teléfono, medios de pago, política de domicilio (cuánto cobran, hasta dónde llegan), LINK/URL del menú cuando lo pide enviado/compartido
    * Estado de un pedido ya hecho ("dónde está mi pedido", "ya salió", "cuánto falta")
    * Historial ("qué he pedido", "muéstrame mis pedidos anteriores")
    * PROMOCIONES como información — preguntas sobre qué promos / ofertas / combos hay:
      "qué promos tienes", "qué promos tienen hoy", "tienes alguna promo",
      "hay ofertas", "qué combos manejan", "promociones del lunes".
      Razón: el cliente pregunta SI hay promos disponibles, no está pidiendo
      una específica para agregar al carrito.

- "chat": pequeña conversación, agradecimientos, despedidas, sin intención clara en otro dominio.

Reglas de desambiguación (claves):
- VERBO de SOLICITAR/COMPARTIR + objeto INFORMACIÓN o LINK → customer_service.
    "envíame la carta", "me mandas el menú", "pásame el link", "compárteme la dirección",
    "me das el teléfono", "cuál es la dirección", "cuánto cobran de domicilio".
    Razón: el cliente pide un dato/link del NEGOCIO como activo, no quiere navegar el menú dentro del bot.
- VERBO de TENER/MOSTRAR + producto/categoría → order.
    "qué tienen de bebidas", "tienen coca cola", "muéstrame el menú", "qué hamburguesas tienen",
    "qué hay para tomar".
    Razón: el cliente está browsing dentro del bot — eso es parte del funnel de ordenar.
- "tienen domicilio?" → customer_service (pregunta por POLÍTICA de domicilio, no por un producto).
- "tienen coca cola?" → order (browsing de productos).
- "qué promos tienen?" / "tienes alguna promo?" / "qué combos manejan?" → customer_service
  (pregunta por DISPONIBILIDAD de promos como dato, NO está agregando una al carrito).
- "dame la promo del honey" / "agrega esa promo" / "quiero el combo lunes" → order
  (acción sobre el carrito; el order agent resuelve la promo y la agrega).
- "a qué hora me llega?" durante un pedido activo → customer_service (info de política/tiempo, no acción).
- "ya te pago" / "listo" durante un pedido → order (señal de checkout).

Reglas de segmentación:
- UNA sola intención → UN segmento con todo el texto.
- Varios productos del mismo pedido → UN segmento order, no separes producto por producto.
    "dame una barracuda y una cerveza" → UN segmento order.
- Dos intenciones DE DOMINIOS DIFERENTES → DOS segmentos.
    "dame una barracuda y a qué hora abren" → order + customer_service.
    "envíame la carta y dame una barracuda" → customer_service + order.
- Saludos al inicio de una pregunta → ABSORBER en el dominio principal.
    "hola a qué hora abren" → UN segmento customer_service (no un "chat" aparte).
- Máximo 3 segmentos.
- El texto de cada segmento puede ser un extracto o una reformulación breve; debe conservar todos los datos relevantes.

Responde SOLO con JSON en esta forma exacta, sin markdown, sin explicación:
{"segments": [{"domain": "order" | "customer_service" | "chat", "text": "..."}]}
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
