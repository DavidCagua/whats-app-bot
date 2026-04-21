"""
Turn router.

Sits between the webhook handler and the agent dispatcher. Decides
how a message should be answered:
- Greeting fast-path: template reply, no LLM, no agent.
- LLM domain classifier: picks one of {order, customer_service, catalog, chat}.
  Caller maps the domain to a concrete agent / handler.

Current scope (Phase 1b):
- Greeting fast-path (regex, no LLM).
- LLM-based domain classifier returning a single domain per turn.
- Single-segment output (no mixed-intent decomposition yet — that lands
  with the dispatcher + handoff contract in Phase 3).

Deliberate non-goals for now:
- No mixed-intent splitting. One domain per message.
- No mid-turn handoffs. That's Phase 3.
- No catalog fast-path. Catalog queries go through whichever agent
  currently owns catalog intents (today: order agent).

Note on rollout:
The classifier emits `customer_service` and `catalog` domains even
though dedicated handlers for those don't exist yet. The caller maps
unknown/unhandled domains to the business's primary agent (today: order)
so behavior is unchanged until the handlers ship. This gives us real
classifier telemetry (via LangSmith) before we swap agents in.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

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


@dataclass
class RouterResult:
    """
    Outcome of router.route().

    - `direct_reply` set → router produced a complete user-facing response
      itself (e.g. greeting template). Caller sends verbatim, skips agents.
    - `domain` set → classifier picked a domain. Caller maps to an agent
      and dispatches.
    - Neither set → no classification possible (LLM failure, empty input).
      Caller falls back to primary agent.
    """

    direct_reply: Optional[str] = None
    domain: Optional[str] = None


# ── Greeting fast-path (unchanged from Phase 1a) ────────────────────

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

_ROUTER_SYSTEM_PROMPT = """Eres el router de un bot de WhatsApp para un restaurante. Lees el mensaje del usuario y clasificas el dominio.

Dominios disponibles:
- "order": el usuario quiere ordenar, modificar su pedido o hacer checkout. Incluye mencionar productos, cantidades, "quiero X", "dame X", "quitar Y", "confirmar", "ya te pago", "ya listo".
- "customer_service": preguntas sobre el negocio (horarios, ubicación, domicilio, medios de pago, teléfono) O estado/historial de pedidos propios. Incluye "a qué hora abren", "dónde quedan", "cuánto cobran domicilio", "dónde está mi pedido", "qué pedí antes".
- "catalog": el usuario pregunta por productos o por el menú en general. "qué tienen", "tienen coca cola", "qué bebidas hay", "muéstrame el menú".
- "chat": saludos con pregunta ("cómo están"), pequeña conversación, agradecimientos, cualquier otra cosa que no encaja arriba.

Reglas de desambiguación importantes:
- "tienen coca cola?" → catalog (pregunta por producto)
- "tienen domicilio?" → customer_service (pregunta por política)
- "hacen delivery a [zona]?" → customer_service
- "a qué hora me llega?" durante pedido activo → customer_service (info de tiempo de entrega)
- "ya te pago" / "listo" durante pedido → order
- Si el mensaje NOMBRA un producto específico Y hace una pregunta sobre él, prioriza "catalog".
- Si el mensaje NOMBRA un producto específico Y pide agregarlo/ordenarlo, prioriza "order".

Responde SOLO con JSON en esta forma exacta, sin markdown, sin explicación:
{"domain": "order" | "customer_service" | "catalog" | "chat"}
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
            max_tokens=20,
            api_key=api_key,
        )
    except Exception as exc:
        logger.warning("[ROUTER] classifier init failed: %s", exc)
    return _llm_classifier


# Strip markdown code fences the model occasionally adds despite instructions.
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _classify_with_llm(
    message_body: str,
    business_context: Optional[dict],
) -> Optional[str]:
    """
    Call the classifier and return a domain string, or None on failure.
    None → caller falls back to primary agent.
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

    # Strip markdown fences the model sometimes emits.
    cleaned = _JSON_FENCE_RE.sub("", raw).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Last-ditch: extract a JSON object from anywhere in the text.
        match = re.search(r"\{[^{}]*\}", cleaned)
        if not match:
            logger.warning("[ROUTER] classifier returned unparseable: %r", raw)
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            logger.warning("[ROUTER] classifier returned unparseable: %r", raw)
            return None

    domain = str(parsed.get("domain") or "").strip().lower()
    if domain not in _VALID_DOMAINS:
        logger.warning("[ROUTER] classifier returned invalid domain: %r", domain)
        return None

    return domain


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
      2. LLM classifier — picks one of {order, customer_service, catalog, chat}.
      3. On classifier failure (None) — caller falls back to primary agent.
    """
    # 1. Greeting fast-path
    greeting = _greeting_fast_path(message_body, business_context, customer_name)
    if greeting is not None:
        logger.info("[ROUTER] greeting fast-path hit")
        return RouterResult(direct_reply=greeting)

    # 2. LLM classification
    if not (message_body or "").strip():
        return RouterResult()  # empty message — caller falls back
    domain = _classify_with_llm(message_body, business_context)
    if domain is None:
        logger.warning("[ROUTER] classification failed — caller falls back to primary agent")
        return RouterResult()

    logger.info("[ROUTER] classified domain=%s", domain)
    return RouterResult(domain=domain)
