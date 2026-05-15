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

_ROUTER_SYSTEM_PROMPT = """Eres el router de un bot de WhatsApp para un restaurante. Tu trabajo es leer el mensaje del cliente — junto con la última respuesta del bot y el estado del pedido — y decidir a qué dominio pertenece. La mayoría de los mensajes son UN solo segmento; divide en varios solo cuando el cliente exprese DOS o más intenciones independientes en el mismo turno.

CONTEXTO QUE RECIBES (úsalo como señal primaria):
- Estado del pedido: GREETING / ORDERING / COLLECTING_DELIVERY / READY_TO_PLACE
- Carrito actual (vacío o con items)
- Último pedido (estado): pending / confirmed / out_for_delivery / ready_for_pickup / completed / cancelled — solo si hay uno reciente.
- Última respuesta del bot (literal).
- Mensaje del cliente.

DOMINIOS:
- "order": el cliente está en el funnel de pedido — explorar el menú, preguntar por productos del catálogo (existencia, ingredientes, precio), agregar/quitar/modificar items, dar datos de entrega, confirmar el pedido.
- "customer_service": el cliente pide INFORMACIÓN del negocio (horarios, ubicación, política de domicilio, medios de pago, link del menú) o pregunta por sus pedidos pasados/actuales (estado, cancelar uno ya confirmado, historial). También: discovery de promos como información ("¿qué promos tienen?").
- "greeting": saludo Y/O apertura de pedido SIN producto. Incluye saludos simples y compuestos ("hola", "buenas noches", "buenos días qué hubo") y frases de apertura sin nombrar producto ("para un domicilio", "quiero pedir", "buenas para un domicilio"). El sistema responde con la bienvenida estándar que ya invita a pedir y muestra el menú. Si el mensaje añade un producto nombrado, una pregunta interrogativa o un pedido de info del negocio, NO es greeting — clasifica por esa intención.
- "chat": pequeña conversación general sin tema claro de los otros dominios. NO uses chat cuando el mensaje sea un follow-up a una respuesta de customer_service o order — esos siguen en su dominio.

CÓMO DECIDIR (regla central — sigue este orden):
1. Lee la **última respuesta del bot** Y el **mensaje del cliente** juntos. Pregúntate: ¿es FOLLOW-UP (responde a lo que el bot acaba de decir) o CAMBIO DE TEMA (nueva intención independiente)?
2. Si el mensaje contiene un trigger CLARO de un dominio (verbo de pedir + producto, pregunta interrogativa sobre datos del negocio, etc.) → ese dominio gana, sin importar el follow-up.
3. Si es follow-up corto / ambiguo (ack, agradecimiento, pregunta breve relacionada al tema previo) → MANTÉN el dominio del turno anterior:
    * Si el bot habló de un dato CS (horarios, dirección, política de pago, política de domicilio, link del menú, estado de un pedido pasado) → cualquier follow-up corto del cliente ("ok", "ah ya", "vale", "gracias", "entiendo", "perfecto", "y cuánto X", "y la dirección") sigue siendo customer_service.
    * Si el bot estaba en el flujo de pedido (mostrando carrito, pidiendo datos, preguntando ¿procedemos?) → follow-ups cortos siguen en order.
4. Si no hay contexto previo claro y el mensaje es genuinamente ambiguo → "chat".

DOMAIN KNOWLEDGE (cosas específicas del negocio que el modelo no puede inferir):
- "tienen X?" donde X es un PRODUCTO/PLATO/BEBIDA → order (browsing). Aplica con typos y descripciones ("la del concurso", "la famosa") — el backend hace fuzzy match.
- "tienen X?" donde X es un SERVICIO/DATO (estacionamiento, wifi, domicilio como política, horarios) → customer_service.
- PRECIO de un producto NOMBRADO ("cuánto vale la barracuda", "una picada qué valor") → order. Precio/política sin producto nombrado ("cuánto cuesta el domicilio", "valor del envío") → customer_service.
- VERBOS DE PEDIR — "dame", "regálame", "me regalan", "tráeme", "ponme", "agrégame", "una/un X" + producto, "quiero X" → order. Aplica también con saludo prefijo: "Hola, me regalan una hamburguesa" → order.
- "para un domicilio" / "quiero pedir" sin producto y sin verbo interrogativo → greeting (apertura de pedido; la bienvenida ya invita a pedir y muestra el menú). Esto aplica con o sin saludo prefijo. CON producto en el mismo mensaje → order. CON interrogativo sobre costo de envío → customer_service.
- CANCELAR (depende del estado, NO del verbo):
    * Carrito activo (state ORDERING/COLLECTING_DELIVERY/READY_TO_PLACE con items) → order (abandona el carrito; "cancela", "anula", "quítalo todo", "no quiero ya" todos van aquí).
    * Sin carrito + pedido confirmado pendiente → customer_service (cancelación post-venta).
    * Sin carrito + sin pedido pendiente → customer_service (responde "no hay pedido por cancelar").
- NEGATIVOS DURANTE CHECKOUT — estado ORDERING/COLLECTING_DELIVERY/READY_TO_PLACE con carrito Y bot acaba de preguntar "¿algo más?" / "¿procedemos?" / "¿confirmamos?" → order ("no más", "eso es todo", "nada más" cierran el carrito, NO cancelan).
- DESPEDIDAS POST-PEDIDO — carrito vacío Y Último pedido reciente (pending/confirmed/etc.): cualquier mensaje del cliente que sea acuse de recibo o despedida ("gracias", "vale gracias", "perfecto", "ok", "dale", "listo", "muchas gracias", "dale espero entonces", "bueno gracias") → order. El order agent maneja la despedida acorde al pedido. Solo NO clasifiques como order si el mensaje pregunta explícitamente por información del negocio o por el estado del pedido.

SEGMENTACIÓN:
- UNA intención → UN segmento.
- Varios productos en un solo pedido → UN solo segmento order.
- Dos intenciones de DOMINIOS DIFERENTES → segmentos separados (máx 3).
- Saludo prefijo (Hola, Buenas, Buenos días) NUNCA cambia el dominio — es contexto. Clasifica por la intención DESPUÉS del saludo.

SALIDA — SOLO JSON, sin markdown, sin explicación:
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
            model="gpt-5.4-mini-2026-03-17",
            temperature=0,
            # Small message, but segments list can have several items;
            # bump max_tokens a bit over single-domain output.
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
    attachments: Optional[list] = None,
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
        "===== ESTADO Y HISTORIAL DEL TURNO =====\n"
        "(lo que YA pasó antes de este turno)\n\n"
        f"{render_for_prompt(ctx)}\n"
        "===== FIN DEL ESTADO =====\n\n"
        "[MENSAJE ACTUAL DEL CLIENTE — procesa SOLO este mensaje; "
        "los anteriores en ESTADO son historial]\n"
        f"Mensaje: {message_body}"
    )

    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        from .multimodal import build_user_content
        response = llm.invoke(
            [
                SystemMessage(content=_ROUTER_SYSTEM_PROMPT),
                HumanMessage(content=build_user_content(user_payload, attachments)),
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


# Imperative cues that turn "X promo de Y" into "give me X promo of Y".
# Both Spanish article-noun shorthand ("una promo de oregon") and explicit
# verbs ("dame", "quiero", "agrega") count.
_PROMO_IMPERATIVE_TRIGGERS = frozenset({
    "una", "un", "uno",
    "dame", "deme", "quiero", "queremos",
    "ponme", "ponnos",
    "agregame", "agrega", "agregue", "agreguen",
    "añade", "añademe", "anade", "anademe",
    "incluye", "incluyeme",
    "regalame", "regala",
})
_PROMO_KEYWORDS = frozenset({
    "promo", "promos", "promocion", "promociones",
    "combo", "combos",
    "oferta", "ofertas",
})
# Tokens that signal an inquiry ("do you have a promo of X?") rather
# than an imperative add. Their presence anywhere in the message
# disqualifies the deterministic match — let the LLM handle it.
_PROMO_INTERROGATIVE_BLOCKERS = frozenset({
    "que", "cual", "cuales",
    "como", "cuanto", "cuanta", "cuantos", "cuantas",
    "cuando", "donde",
    "hay", "tienen", "tienes", "manejan", "ofrecen",
    "existe", "existen", "sirven", "aplican", "aplica",
})
_PROMO_STOPWORDS = frozenset({
    "de", "del", "la", "el", "los", "las",
    "un", "una", "uno",
    "y", "o",
    "porfa", "porfis", "porfavor", "favor", "por",
    "please",
})


def _deterministic_promo_add(
    message_body: str,
    business_context: Optional[dict],
) -> bool:
    """
    Return True iff the message is unambiguously an imperative
    "add this promo" with a specific identifier — e.g. "una promo de
    oregon", "un combo familiar", "quiero la oferta del lunes".

    Targets the gap the LLM classifier kept missing: Colombian Spanish
    drops the verb in article-noun constructions ("una X de Y" = "dame
    una X de Y"), and the router used to send these to customer_service
    because they look superficially like info questions. Production
    observation 2026-05-11 (Biela / 3177000722): "una promo de oregon"
    was routed to CS → get_promos → list-and-ask, instead of straight
    to order → add_promo_to_cart.

    Heuristic (intentionally conservative — false negatives are fine,
    they fall through to the LLM):
      - No "?"/"¿" anywhere (questions are inquiries, not commands).
      - No interrogative blocker token ("qué", "hay", "tienen", ...).
      - A promo keyword (promo|combo|oferta + plurals/variants) appears
        in the message.
      - An imperative trigger (article or verb) appears BEFORE the
        promo keyword.
      - At least one identifier-like token (non-stopword) appears
        AFTER the promo keyword.
    """
    text = (message_body or "").strip()
    if not text:
        return False
    if "?" in text or "¿" in text:
        return False
    tokens = _tokenize_for_router(text)
    if not tokens:
        return False
    if any(t in _PROMO_INTERROGATIVE_BLOCKERS for t in tokens):
        return False
    try:
        idx = next(i for i, t in enumerate(tokens) if t in _PROMO_KEYWORDS)
    except StopIteration:
        return False
    head = tokens[:idx]
    if not any(t in _PROMO_IMPERATIVE_TRIGGERS for t in head):
        return False
    after = tokens[idx + 1:]
    meaningful = [t for t in after if t not in _PROMO_STOPWORDS]
    if not meaningful:
        return False
    return True


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
    attachments: Optional[list] = None,
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

    # 4. Imperative promo-add short-circuit. "una promo de oregon" /
    # "un combo familiar" / "quiero la oferta del lunes" — Colombian
    # article-noun-imperative or explicit-verb add. The LLM kept
    # mis-classifying these as customer_service (info question) because
    # the verb is implicit. Force order so add_promo_to_cart resolves
    # and adds in one step.
    if _deterministic_promo_add(message_body, business_context):
        logger.info("[ROUTER] deterministic promo-add hit → order")
        return RouterResult(
            segments=[(DOMAIN_ORDER, message_body)],
            recognized_product=recognized_product,
        )

    # 5. Multi-word product-name short-circuit. The catalog confirms
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
        attachments=attachments,
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
