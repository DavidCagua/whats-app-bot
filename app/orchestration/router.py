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
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..services import business_greeting, catalog_cache
from .turn_context import TurnContext, render_for_prompt


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
DOMAIN_GREETING = "greeting"

_VALID_DOMAINS = {
    DOMAIN_ORDER,
    DOMAIN_CUSTOMER_SERVICE,
    DOMAIN_CHAT,
    DOMAIN_GREETING,
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
    # When the router detects a multi-word catalog product name as a
    # contiguous substring of the user message, it surfaces the
    # canonical catalog name here. Downstream agents read this to
    # avoid mis-resolving abbreviated/listed-option names. See
    # ``_recognize_full_product_name`` and the order planner's
    # ``Producto reconocido en el mensaje:`` rule.
    recognized_product: Optional[str] = None

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
    gate: Optional[dict] = None,
) -> Optional[str]:
    """Return the greeting template if message is a pure greeting, else None.

    ``gate`` is the order-availability decision from
    ``business_info_service.is_taking_orders_now`` — when present and
    closed, ``get_greeting`` appends the closed-status sentence so the
    customer is told upfront the shop is closed.
    """
    if business_greeting.is_pure_greeting(message_body):
        return business_greeting.get_greeting(business_context, customer_name, gate=gate)
    return None


# ── LLM classifier ──────────────────────────────────────────────────

_ROUTER_SYSTEM_PROMPT = """Eres el router de un bot de WhatsApp para un restaurante. Lees el mensaje del cliente y lo divides en SEGMENTOS. Cada segmento tiene un dominio (la INTENCIÓN del cliente) y el texto del mensaje que corresponde a ese dominio.

La mayoría de los mensajes son UN solo segmento (una sola intención). Solo divide en múltiples segmentos cuando el cliente expresa claramente DOS O MÁS intenciones independientes en el mismo mensaje.

CONTEXTO DEL TURNO (clave para desambiguar):
Recibes en el mensaje del usuario un bloque "CONTEXTO" con: estado del pedido en curso
(GREETING / ORDERING / COLLECTING_DELIVERY / READY_TO_PLACE), si hay carrito activo, si
existe un pedido CONFIRMADO previo cancelable, y la última respuesta del bot. Úsalos
para resolver mensajes cortos o ambiguos. En particular:

- Negativos cortos / "no más" / "nada más" / "que no" / "eso es todo" / "no, gracias"
  cuando el estado es ORDERING / COLLECTING_DELIVERY / READY_TO_PLACE y la última
  respuesta del bot terminó en una pregunta de cierre ("¿algo más?", "¿procedemos?",
  "¿confirmamos?") → SIEMPRE "order". El cliente está cerrando el carrito, no
  cancelando un pedido. El order agent decide si proceder o no.
- "cancela" / "anula" / "ya no quiero" / "déjalo así" cuando hay carrito activo
  (estado ORDERING / COLLECTING_DELIVERY / READY_TO_PLACE) → "order". El cliente
  abandona el carrito, lo maneja el order agent.
- "cancela mi pedido" / "anula el pedido" / "ya no lo quiero" cuando NO hay carrito
  activo y SÍ hay un pedido confirmado pendiente → "customer_service" (post-venta).
- Sin carrito activo y sin pedido confirmado pendiente, "cancela" no tiene objeto
  claro → "customer_service" (responderá que no hay pedido por cancelar).

Dominios disponibles (por INTENCIÓN del cliente):

- "order": el cliente quiere ORDENAR comida. Esto incluye TODO el funnel de pedido:
    * Browsing/exploración del menú dentro del bot ("qué tienen", "qué bebidas hay", "tienen coca cola", "muéstrame el menú", "qué hamburguesas tienen", "qué trae la barracuda")
    * AVAILABILITY / EXISTENCE de un producto — preguntas sobre si el negocio TIENE un
      producto, sea por nombre exacto, descripción imprecisa, o referencia descriptiva.
      Frases ilustrativas (NO exhaustivas): "tiene la barracuda?", "tienes la vimota?",
      "hay BIMOTA?", "tiene la del concurso?", "tienen la famosa?", "tienes la del lunes?",
      "está la pegoretti?", "hay algo con pollo?". Esta regla aplica AUNQUE el sustantivo
      NO sea un nombre exacto del catálogo — typos ("vimota" → BIMOTA), referencias
      descriptivas ("la del concurso", "la famosa", "la del menú del lunes") y nombres
      desconocidos pertenecen al order agent: el backend tiene búsqueda fuzzy + semántica
      que resuelve estos casos. Discriminación clave: el sustantivo refiere a un PRODUCTO/
      PLATO/BEBIDA, NO a un servicio del negocio. Servicios ("tienen estacionamiento?",
      "tienen WhatsApp?", "tienen domicilio?") siguen siendo customer_service.
    * Búsqueda por atributo ("algo con queso", "algo picante")
    * Detalles de un producto específico ("qué trae la montesa")
    * PRECIO/VALOR de un producto NOMBRADO — preguntar cuánto cuesta un producto del menú
      es navegar el menú, NO una pregunta de servicio al cliente. Frases típicas:
      "cuánto vale la barracuda", "qué precio tiene la picada", "una picada qué valor",
      "cuánto cuesta la honey burger", "el precio de la montesa", "qué valor tiene X".
      Aplica incluso cuando el producto y la pregunta vienen en el mismo mensaje sin
      verbo de orden explícito ("una picada que valor?" — preguntar el precio antes de pedir).
    * INTENCIÓN DE PEDIR sin nombrar producto — frases que abren la conversación
      de pedido pero no especifican qué quieren todavía: "para un domicilio",
      "un domicilio por favor", "quiero pedir", "para hacer un pedido", "para ordenar",
      "me pueden atender", "quiero un domicilio". La palabra "domicilio" aquí significa
      "quiero hacer un pedido a domicilio", NO una pregunta sobre el costo del domicilio.
      Discriminador: ¿hay una pregunta? ("cuánto", "qué precio", "vale", "cuesta") → CS.
      Si NO hay pregunta y solo es una frase de apertura → order.
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

- "greeting": el mensaje es ÚNICAMENTE un saludo, sin nombrar producto, sin pregunta,
    sin pedir información del negocio. Cubre saludos simples y compuestos:
    "hola", "buenas", "buenos días", "buen día", "buenas tardes", "buenas noches",
    "hey", "ey", "saludos", "qué más", "qué tal", "qué hubo", o combinaciones de
    estos ("hola buenas noches", "buenas qué más", "hola qué tal", "buenos días qué hubo"),
    con o sin signos / muletillas / emojis. Las frases listadas son ilustrativas, NO
    exhaustivas — cualquier mensaje cuya ÚNICA intención sea saludar cae aquí.
    Razón: el sistema responde con una bienvenida estándar (con link al menú) sin
    despachar a ningún agente. IMPORTANTE: si el mensaje añade CUALQUIER otra cosa
    además del saludo (un producto, una pregunta, una solicitud), NO uses "greeting"
    — clasifica por la intención sustantiva (ver regla "Saludos al inicio" abajo).

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
- "para un domicilio" / "un domicilio por favor" / "quiero pedir" → order (es una FRASE DE
  APERTURA de pedido, no una pregunta sobre el domicilio). Sin verbo interrogativo
  ("cuánto", "vale", "cuesta", "qué precio") es intención de ordenar — el order agent
  saluda e invita a decir su pedido. CON verbo interrogativo ("cuánto vale el domicilio",
  "cuánto cobran de domicilio") sí es customer_service.
- "qué promos tienen?" / "tienes alguna promo?" / "qué combos manejan?" → customer_service
  (pregunta por DISPONIBILIDAD de promos como dato, NO está agregando una al carrito,
  Y no nombra ningún producto específico del catálogo).
- "dame la promo del honey" / "agrega esa promo" / "quiero el combo lunes" → order
  (acción sobre el carrito; el order agent resuelve la promo y la agrega).
- "cuánto vale la barracuda?" / "una picada qué valor?" / "qué precio tiene la honey?" → order
  (pregunta sobre el precio/valor de un PRODUCTO NOMBRADO — eso es navegación del menú,
  NO información del negocio). Discriminador clave: ¿el cliente nombró un producto del
  catálogo? Si sí → order. Si pregunta sobre precios/info en general sin nombrar producto
  ("cuánto cuesta el domicilio", "qué precios manejan") → customer_service.
- "a qué hora me llega?" durante un pedido activo → customer_service (info de política/tiempo, no acción).
- "ya te pago" / "listo" durante un pedido → order (señal de checkout).
- DESPEDIDA POST-PEDIDO (regla contextual, aplica SOLO bajo todas estas condiciones): si `Estado del pedido` es `GREETING` Y `Carrito actual: vacío` Y el contexto incluye una línea `Último pedido (estado): pending|confirmed|out_for_delivery|completed|cancelled` (que indica que el cliente acaba de placear o tiene un pedido reciente) Y el mensaje del usuario es una despedida / agradecimiento / afirmación corta (`gracias`, `si gracias`, `ok gracias`, `listo gracias`, `muchas gracias`, `perfecto gracias`, `bueno gracias`, `vale gracias`, `con gusto`, `dale`, `genial`, `ok`, `listo`, `perfecto`, e incluso un simple `si`/`ok` cuando el carrito está vacío y hay un `Último pedido` reciente), CLASIFICA como `order` — NO como `customer_service`. Razón: el cliente está cerrando el flujo del pedido recién hecho; el order agent sabe responder con una despedida acorde al estado del pedido (ej. "¡Con gusto, que disfrutes!"). NO clasifiques como `customer_service` salvo que el mensaje pregunte explícitamente por información del negocio (horarios, dirección, etc.) o por el estado del pedido. Las palabras-ejemplo son ilustrativas, NO exhaustivas — incluyen typos cercanos y variantes regionales.
- CONTINUACIÓN DEL FLUJO DE PEDIDO (regla contextual, aplica SOLO bajo todas estas condiciones): si el `Estado del pedido` es `ORDERING` o `READY_TO_PLACE` Y el `Carrito actual` no está vacío Y el `Historial reciente` muestra que el último mensaje del bot terminó con una pregunta de continuación del pedido (ejemplos: `¿procedemos?`, `¿procedemos con el pedido?`, `¿algo más?`, `¿quieres agregar algo más?`, `¿te gustaría agregar alguna bebida o procedemos?`, `¿confirmas?`, `¿gustas proceder?`) — ENTONCES, una respuesta breve del usuario que sea una afirmación, cortesía, aceptación, typo cercano de cortesía, o palabra ambigua corta sin pregunta nueva (ej. "porfsvor" → por favor; "porfa", "dale", "claro", "con gusto", "siiii", "vamos") pertenece al dominio `order`. Razón: el cliente está respondiendo a TU pregunta del flujo. Las palabras-ejemplo son ilustrativas, NO exhaustivas. IMPORTANTE: si el antecedente NO se cumple (estado distinto, carrito vacío, o el bot NO acaba de hacer una pregunta de continuación), esta regla NO aplica — clasifica el mensaje según las demás reglas como si esta no existiera. En particular, preguntas de browsing del menú como "qué hamburguesas tienen?", "qué bebidas hay?", "muéstrame el menú" siguen siendo `order` por la regla de browsing arriba — no las redirijas a customer_service por contener "qué".

Reglas de segmentación:
- UNA sola intención → UN segmento con todo el texto.
- Varios productos del mismo pedido → UN segmento order, no separes producto por producto.
    "dame una barracuda y una cerveza" → UN segmento order.
- Dos intenciones DE DOMINIOS DIFERENTES → DOS segmentos.
    "dame una barracuda y a qué hora abren" → order + customer_service.
    "envíame la carta y dame una barracuda" → customer_service + order.
- Saludos al inicio de una pregunta → ABSORBER en el dominio principal. Un saludo
  prefijo (Hola, Buenas, Buenas tardes, Buenos días) NO cambia el dominio — son
  contexto, NO una intención. Clasifica por la INTENCIÓN DESPUÉS del saludo:
    "hola a qué hora abren" → UN segmento customer_service (la intención es horarios).
    "Buenas tiene la barracuda?" → UN segmento order (la intención es preguntar por
    un producto del menú; la regla de browsing aplica igual que "tienen coca cola").
    "Hola tienen montesa?" → UN segmento order (mismo patrón).
    "Buenos días me regalan una hamburguesa" → UN segmento order.
- Máximo 3 segmentos.
- El texto de cada segmento puede ser un extracto o una reformulación breve; debe conservar todos los datos relevantes.

Responde SOLO con JSON en esta forma exacta, sin markdown, sin explicación:
{"segments": [{"domain": "order" | "customer_service" | "chat" | "greeting", "text": "..."}]}
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
    ctx: Optional[TurnContext] = None,
    wa_id: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> Optional[List[Tuple[str, str]]]:
    """
    Call the classifier and return a list of (domain, text) segments,
    or None on failure.

    ``wa_id`` and ``turn_id`` go into the LangSmith metadata so router
    runs are filterable per user (debug-conversation skill) and so all
    LLM spans from the same inbound message share a correlation id
    (turn_id = Twilio MessageSid). Both are optional for legacy callers.
    """
    llm = _get_llm_classifier()
    if llm is None:
        return None

    business_id = str((business_context or {}).get("business_id") or "")

    if ctx is None:
        ctx = TurnContext()
    user_payload = (
        f"CONTEXTO:\n{render_for_prompt(ctx)}\n\n"
        f"Mensaje: {message_body}"
    )

    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        response = llm.invoke(
            [
                SystemMessage(content=_ROUTER_SYSTEM_PROMPT),
                HumanMessage(content=user_payload),
            ],
            config={
                "run_name": "router_classifier",
                "metadata": {
                    "wa_id": wa_id or "",
                    "business_id": business_id,
                    "turn_id": turn_id or "",
                    "message_length": len(message_body),
                    "order_state": ctx.order_state,
                    "has_active_cart": ctx.has_active_cart,
                    "has_recent_cancellable_order": ctx.has_recent_cancellable_order,
                },
            },
        )
        raw = (response.content if hasattr(response, "content") else str(response)).strip()
    except Exception as exc:
        logger.warning("[ROUTER] classifier LLM call failed: %s", exc)
        return None

    return _parse_segments(raw)


# ── Deterministic pre-classifier ────────────────────────────────────
# Catches the "price of named product" case before paying the LLM
# router. The LLM prompt covers this in theory (see _ROUTER_SYSTEM_PROMPT
# above) but production has shown it misroutes when the product name
# is unfamiliar to the model — see logs around 2026-05-03 / Biela /
# 3177000722, "Cuánto vale el pegoretti?" misrouted to
# customer_service → cs_chat_fallback.
#
# Logic:
#   1. message contains a price interrogative AND
#   2. message contains at least one token that the catalog lookup-set
#      flags as a product/tag/synonym AND
#   3. that token is not a generic "policy" word (domicilio, propina, …)
# → force DOMAIN_ORDER, skip the LLM.

_PRICE_INTERROGATIVES: frozenset = frozenset({
    "cuanto", "cuantos", "cuanta", "cuantas",
    "cuesta", "cuestan",
    "vale", "valen",
    "precio", "precios",
    "valor", "valores",
})


# Spanish indefinite/definite articles that, when a user types fast on
# WhatsApp, often run together with the noun: "una bimota" → "unabimota",
# "el pegoretti" → "elpegoretti". The router treats the resulting blob
# as an unknown noun and routes to customer_service. The splitter below
# expands these stuck-article tokens against the catalog lookup-set so
# downstream classification (or the deterministic price helper) can
# still see the product token.
_STUCK_ARTICLE_PREFIXES: tuple = (
    "una", "uno", "unos", "unas",
    "los", "las",
    "un", "el", "la",
)
# Order matters: longer prefixes first so "unas" wins over "un".
# Frozen tuple sorted descending by length to make the matcher
# deterministic.
_STUCK_ARTICLE_PREFIXES = tuple(
    sorted(set(_STUCK_ARTICLE_PREFIXES), key=lambda p: (-len(p), p))
)


def _strip_accents_lower(s: str) -> str:
    nfkd = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def _tokenize_for_router(message: str) -> List[str]:
    s = _strip_accents_lower(message)
    s = re.sub(r"[^\w\s]", " ", s)
    return [t for t in s.split() if t]


def _has_price_interrogative(tokens: List[str]) -> bool:
    return any(t in _PRICE_INTERROGATIVES for t in tokens)


def _split_stuck_article(token: str, lookup: frozenset) -> Optional[str]:
    """
    If ``token`` looks like a Spanish article concatenated with a
    catalog token (e.g. ``"unabimota"`` → ``"bimota"`` when ``"bimota"``
    is in ``lookup``), return the catalog token. Otherwise return None.

    Rules:
      - Token must start with one of _STUCK_ARTICLE_PREFIXES.
      - Stripped suffix must be in ``lookup``.
      - Stripped suffix must be at least 4 chars (avoids stripping
        ``"el"`` from ``"elote"`` to expose ``"ote"``).
    """
    if not token or len(token) < 6:
        return None
    if not lookup:
        return None
    for prefix in _STUCK_ARTICLE_PREFIXES:
        if not token.startswith(prefix):
            continue
        suffix = token[len(prefix):]
        if len(suffix) < 4:
            continue
        if suffix in lookup:
            return suffix
    return None


def _expand_stuck_articles(message: str, lookup: frozenset) -> str:
    """
    Return ``message`` with every stuck-article token replaced by
    ``"<article> <catalog_token>"``. Pure rewrite — does not
    re-classify. If no token is rewritten, returns the original
    string unchanged so callers can detect a no-op.
    """
    if not message or not lookup:
        return message
    rewritten = []
    changed = False
    # Tokenize while preserving non-word separators so we don't lose
    # punctuation like "?" / "!".
    parts = re.split(r"(\s+)", message)
    for part in parts:
        if not part or part.isspace():
            rewritten.append(part)
            continue
        # Strip surrounding punctuation, normalize for matching, but
        # preserve original casing/punctuation in the output.
        stripped = re.sub(r"[^\w]", "", part)
        if not stripped:
            rewritten.append(part)
            continue
        norm = _strip_accents_lower(stripped)
        match = _split_stuck_article(norm, lookup)
        if match is None:
            rewritten.append(part)
            continue
        # Found a stuck-article token. Insert a space before the
        # matched suffix in the original text, preserving the original
        # leading article casing.
        prefix_len = len(stripped) - len(match)
        # Locate the stripped substring inside the original part to
        # preserve leading punctuation (e.g. ``"(unabimota)"``).
        idx = part.lower().find(stripped.lower())
        if idx < 0:
            rewritten.append(part)
            continue
        head = part[: idx + prefix_len]
        tail = part[idx + prefix_len:]
        rewritten.append(f"{head} {tail}")
        changed = True
    return "".join(rewritten) if changed else message


def _deterministic_price_of_product(
    message_body: str,
    business_context: Optional[dict],
) -> bool:
    """
    Return True iff the message is unambiguously "what does <named
    product> cost?" — a price interrogative paired with at least one
    catalog-recognized token.
    """
    business_id = str((business_context or {}).get("business_id") or "")
    if not business_id:
        return False

    tokens = _tokenize_for_router(message_body)
    if not tokens or not _has_price_interrogative(tokens):
        return False

    try:
        lookup = catalog_cache.get_router_lookup_set(business_id)
    except Exception as exc:
        logger.warning("[ROUTER] router_lookup_set failed: %s", exc)
        return False
    if not lookup:
        return False

    for t in tokens:
        if t in lookup:
            return True
    return False


def _recognize_full_product_name(
    message_body: str,
    business_context: Optional[dict],
) -> Optional[str]:
    """
    Detect a multi-word catalog product name appearing as a contiguous
    substring of the (normalized) user message. Returns the canonical
    catalog name (e.g. ``"LA VUELTA"``) when exactly one product
    matches, else ``None``.

    The "exactly one" guard prevents false positives when the message
    contains a longer phrase that itself contains a shorter product
    name (e.g. ``"honey burger la vuelta"`` matches both, so we punt
    to the LLM rather than guess).
    """
    business_id = str((business_context or {}).get("business_id") or "")
    if not business_id or not (message_body or "").strip():
        return None
    try:
        full_names = catalog_cache.get_router_full_name_map(business_id)
    except Exception as exc:
        logger.warning("[ROUTER] router_full_name_map failed: %s", exc)
        return None
    if not full_names:
        return None
    norm_msg = _strip_accents_lower(message_body)
    norm_msg = re.sub(r"[^\w\s]", " ", norm_msg)
    norm_msg = re.sub(r"\s+", " ", norm_msg).strip()
    if not norm_msg:
        return None
    # Pad with spaces so we can check for token-aligned substring
    # ("la vuelta" must match " la vuelta " in " hamburguesa la vuelta ",
    #  not collide with "ela vueltaa" or any partial alphabetic blob).
    padded = f" {norm_msg} "
    matches: List[str] = []
    for normalized, canonical in full_names.items():
        needle = f" {normalized} "
        if needle in padded:
            matches.append(canonical)
    if len(matches) == 1:
        return matches[0]
    if len(matches) >= 2:
        # Multiple multi-word matches → ambiguous, punt to LLM.
        return None

    # No multi-word match. Fall back to single-token catalog lookup so
    # messages like "Buenas tiene la barracuda?" (single-word product
    # name + greeting prefix) still short-circuit to order. The LLM
    # classifier biases toward customer_service when a greeting precedes
    # the question — production 2026-05-06 (Biela / 14155238886): user
    # said "Buenas tiene la barracuda?" cold and the bot replied with
    # the CS chat fallback "No entendí bien tu pregunta".
    try:
        single_map = catalog_cache.get_router_single_token_map(business_id)
    except Exception as exc:
        logger.warning("[ROUTER] router_single_token_map failed: %s", exc)
        return None
    if not single_map:
        return None
    msg_tokens = set(norm_msg.split())
    single_matches = {
        single_map[t] for t in msg_tokens if t in single_map
    }
    if len(single_matches) == 1:
        return next(iter(single_matches))
    return None


# ── Public entry point ──────────────────────────────────────────────

def route(
    message_body: str,
    business_context: Optional[dict],
    customer_name: Optional[str],
    ctx: Optional[TurnContext] = None,
    wa_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    gate: Optional[dict] = None,
) -> RouterResult:
    """
    Classify the message and decide how to respond.

    Flow:
      1. Greeting fast-path — pure greeting returns a direct template reply.
      2. LLM classifier — returns list of (domain, text) segments.
      3. On classifier failure — caller falls back to primary agent.

    `ctx` is the per-turn snapshot (order state, cart, last assistant
    message, recent cancellable order). When omitted, the classifier
    runs without context — used by tests and the legacy callers.

    ``gate`` is the order-availability decision (``can_take_orders``
    + reason + next-open fields). Threaded into ``get_greeting`` so a
    pure greeting on a closed shop announces the closed state inline
    instead of waiting for the customer to send a product first.
    """
    # 1. Greeting fast-path
    greeting = _greeting_fast_path(message_body, business_context, customer_name, gate=gate)
    if greeting is not None:
        logger.info("[ROUTER] greeting fast-path hit")
        return RouterResult(direct_reply=greeting)

    if not (message_body or "").strip():
        return RouterResult()

    # Pre-compute the multi-word catalog product detection — it's
    # used both to short-circuit routing (when present, the user
    # named a specific product) AND to surface a hint to the order
    # planner so it doesn't redirect to a previously-listed option.
    # Same shape as the stuck-article splitter, for the multi-word
    # product-name case (Biela / 3147554464, 2026-05-06: LA VUELTA
    # was misrouted to HONEY BURGER + notes='a la vuelta' because
    # the planner picked from a partial listed-options block).
    recognized_product = _recognize_full_product_name(message_body, business_context)

    # 2. Deterministic pre-classifier — price-of-product short-circuit.
    # Skips the LLM when the catalog itself confirms the user named a
    # product. Independent of conversation state (price questions are
    # valid in any state).
    if _deterministic_price_of_product(message_body, business_context):
        logger.info("[ROUTER] deterministic price-of-product hit → order")
        return RouterResult(
            segments=[(DOMAIN_ORDER, message_body)],
            recognized_product=recognized_product,
        )

    # 3. Stuck-article splitter — "unabimota" / "elpegoretti" /
    # "lapicada" with a catalog match → force order with the
    # rewritten message. Production observation 2026-05-05 (Biela /
    # 3177000722): "unabimota" was misrouted to customer_service →
    # cs_chat_fallback because the LLM saw a single unknown token
    # with no article cue.
    business_id = str((business_context or {}).get("business_id") or "")
    if business_id:
        try:
            lookup = catalog_cache.get_router_lookup_set(business_id)
        except Exception as exc:
            logger.warning("[ROUTER] router_lookup_set failed: %s", exc)
            lookup = frozenset()
        if lookup:
            expanded = _expand_stuck_articles(message_body, lookup)
            if expanded != message_body:
                logger.info(
                    "[ROUTER] stuck-article splitter rewrote message → order: %r → %r",
                    message_body, expanded,
                )
                return RouterResult(
                    segments=[(DOMAIN_ORDER, expanded)],
                    recognized_product=recognized_product,
                )

    # 4. Multi-word product-name short-circuit. The catalog confirms
    # the user named a specific product (≥ 2 tokens, ≥ 5 chars
    # normalized). Force order routing — the order planner picks it
    # up via ``recognized_product``.
    if recognized_product is not None:
        logger.info(
            "[ROUTER] full-name product short-circuit → order: recognized=%r",
            recognized_product,
        )
        return RouterResult(
            segments=[(DOMAIN_ORDER, message_body)],
            recognized_product=recognized_product,
        )

    # 5. LLM classification
    segments = _classify_with_llm(
        message_body, business_context, ctx=ctx,
        wa_id=wa_id, turn_id=turn_id,
    )
    if not segments:
        logger.warning("[ROUTER] classification failed — caller falls back to primary agent")
        return RouterResult()

    # Greeting domain → render canonical welcome and converge with the
    # regex fast-path (downstream conversation_manager will upgrade to
    # the Twilio CTA template when settings.welcome_content_sid is set).
    # Only fires when greeting is the SOLE segment; if the LLM emits
    # greeting alongside other intents (shouldn't, per prompt rules) we
    # drop the greeting and let the substantive segments dispatch.
    if len(segments) == 1 and segments[0][0] == DOMAIN_GREETING:
        logger.info("[ROUTER] LLM classified as greeting → direct reply")
        return RouterResult(
            direct_reply=business_greeting.get_greeting(business_context, customer_name, gate=gate),
        )
    if any(d == DOMAIN_GREETING for d, _ in segments):
        segments = [(d, t) for d, t in segments if d != DOMAIN_GREETING]
        if not segments:
            return RouterResult()

    logger.info(
        "[ROUTER] classified n_segments=%d domains=%s state=%s cart=%s placed=%s",
        len(segments),
        [d for d, _ in segments],
        (ctx or TurnContext()).order_state,
        (ctx or TurnContext()).has_active_cart,
        (ctx or TurnContext()).has_recent_cancellable_order,
    )
    return RouterResult(
        segments=segments,
        recognized_product=recognized_product,
    )
