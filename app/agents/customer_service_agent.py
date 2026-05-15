"""
Customer service agent — tool-calling architecture.

Single LLM loop with bound CS tools. Each tool returns one of:

  FINAL|<text>   → dispatch loop uses <text> verbatim as the reply
  HANDOFF|...    → dispatch loop returns AgentOutput with handoff payload
  <plain text>   → consumed by the LLM, which writes prose next iteration

No separate response renderer (unlike the order agent). For CS the data
is simple enough that tools render the final Spanish themselves; the
LLM acts as a classifier (which tool + args) and as the prose writer
only for open-chat / no-tool turns.

Deterministic fast-paths run before the tool loop:
  - Order-closed handoff (handoff_context.reason="order_closed")
  - Delivery-paused handoff (handoff_context.reason="delivery_paused")
  - Out-of-zone redirect (handoff_context.reason="out_of_zone")
  - Pre-loop safety nets: price-of-product, post-order despedida,
    stuck-article typos → hand off to order agent.

Per-tool guards live inside the tool body (see cs_tools.cancel_order) so
the destructive action and its safety check live together — no caller
can bypass the gate.
"""

import logging
import os
import re
import time
import unicodedata
import uuid
from typing import Any, Dict, List, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from .base_agent import BaseAgent, AgentOutput
from ..database.conversation_service import conversation_service
from ..services import business_info_service
from ..services.cs_tools import (
    cs_tools,
    set_tool_context,
    reset_tool_context,
    parse_final,
    parse_handoff,
)
from ..services.tracing import tracer
from ..orchestration.turn_context import render_for_prompt


MAX_ITERATIONS = 3


# ── Post-order close detection (used by the despedida safety net) ──────


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


_SYSTEM_PROMPT_TEMPLATE = """Eres el agente de servicio al cliente de {business_name}, un restaurante colombiano.

Tu trabajo es clasificar la pregunta del cliente y llamar UNA herramienta cuando aplique. Las herramientas devuelven la respuesta ya redactada — NO la reescribas, NO la parafrasees. Solo cuando ninguna herramienta aplique, redacta tú mismo una respuesta breve en español colombiano.

Herramientas disponibles:
- get_business_info(field): datos del negocio (horarios, dirección, teléfono, costo/tiempo de domicilio, link al menú). NO la uses para preguntas de pago — esas van a `get_payment_info`. Para "menu_url", cubre "carta"/"menú" con cualquier verbo.
- get_payment_info(): preguntas de pago — qué métodos aceptan, si reciben tarjeta/Nequi, dónde transferir, si pueden pagar al recibir o por adelantado. Devuelve un bloque PAYMENT_INFO con los métodos por contexto (domicilio/local × al recibir/por adelantado), los datos para pago adelantado, y el modo del pedido actual. **EXCEPCIÓN a la regla 1**: tras leer este bloque DEBES redactar tú la respuesta al cliente en español colombiano siguiendo las INSTRUCCIONES del bloque. NO copies el bloque al cliente. Filtra por lo que el cliente preguntó (si dijo "domicilio", solo responde sobre domicilio; si dijo "local"/"recoger", solo sobre el local; si no especificó pero hay pedido en curso, usa ese modo; si no hay contexto, da un panorama corto y pregunta cuál le interesa).
- get_order_status(asked_about_time, asked_for_breakdown): estado del pedido del cliente. Pon `asked_about_time=true` SOLO si preguntó por tiempo explícitamente; `asked_for_breakdown=true` SOLO si pidió el detalle por ítem.
- get_order_history(): pedidos pasados del cliente.
- cancel_order(): SOLO cuando el cliente pide EXPLÍCITAMENTE cancelar un pedido ya confirmado ("cancela", "anula", "ya no lo quiero"). NO la llames sin un verbo de cancelación.
  ⚠️ AMBIGÜEDAD COLOMBIANA: en Colombia "cancelar" también significa "pagar" ("cancelar la cuenta", "le cancelo al domiciliario", "¿cancelo de una vez?"). Antes de llamar cancel_order, lee la estructura del mensaje:
    - Forma de pregunta (signo `?`, modal `puedo / podría / debería`, alternativa `o le ... o ...`, `o de una vez o al ...`) → probablemente PREGUNTA, no orden de cancelar.
    - Co-ocurrencia de pago (`pago / pagar / al domiciliario / de una vez / efectivo / Nequi / tarjeta / contraentrega / transferencia`) → casi seguro significa "pagar", NO "cancelar el pedido".
  Si el mensaje cae en alguno de esos patrones, NO llames cancel_order. Responde para aclarar la intención del cliente (pregúntale si quiere cancelar el pedido o pagar) y espera su respuesta.
  Ejemplo (caso real): "Vale, puedo cancelar en este momento o le cancelo al domiciliario?" → forma de pregunta + co-ocurrencia "al domiciliario" → el cliente pregunta sobre PAGAR. Acción correcta: responder algo como "Puedes pagar al domiciliario contraentrega cuando llegue el pedido. ¿Te queda alguna otra duda?" — NO llames cancel_order.
- get_promos(): cuando preguntan SI HAY promos/ofertas/combos sin nombrar una específica.
- select_listed_promo(selector, query, promo_id): cuando el cliente elige UNA promo (ordinal "primera"/"segunda", o nombre parcial "la del honey", o id).

Reglas:
1. Llama COMO MUCHO UNA herramienta por turno. Después, NO escribas prosa adicional — la herramienta ya devolvió la respuesta final.
2. NUNCA inicies la respuesta con un saludo ("Hola", "Buenas", "Hey", "Buen día/tardes/noches") ni con el nombre del cliente como saludo. La conversación ya está en curso — empieza directo con el contenido.
3. NUNCA inventes URLs, links, números de Nequi, teléfonos, precios ni datos. Si no aparece en la herramienta o en las reglas del negocio (abajo), no lo digas.
4. Cuando NINGUNA herramienta aplique (chat general, preguntas sobre combos/acompañamientos/políticas del negocio, charla), responde directamente — breve (1-3 oraciones), tono cordial. Apóyate en las "Reglas y contexto del negocio" cuando existan.
5. Para preguntas sobre un producto específico del menú, precio de un producto, o intención de pedir algo: NO llames ninguna herramienta de CS — eso lo maneja el agente de pedido y será ruteado automáticamente.

{ai_prompt_rules}
"""


class CustomerServiceAgent(BaseAgent):
    """CS agent: bound tools + single tool-calling loop, no renderer."""

    agent_type = "customer_service"

    def __init__(self) -> None:
        self._llm: Optional[ChatOpenAI] = None
        logging.info("[CS_AGENT] Initialized (tool-calling, LLM lazy)")

    @property
    def llm(self) -> ChatOpenAI:
        if self._llm is None:
            self._llm = ChatOpenAI(
                model="gpt-5.4-mini-2026-03-17",
                temperature=0.3,
                api_key=os.getenv("OPENAI_API_KEY"),
            ).bind_tools(list(cs_tools))
        return self._llm

    def get_tools(self) -> List:
        return list(cs_tools)

    def get_system_prompt(
        self,
        business_context: Optional[Dict],
        current_date: str,
        current_year: int,
        wa_id: str,
        name: str,
    ) -> str:
        return self._build_system_prompt(business_context)

    def _build_system_prompt(self, business_context: Optional[Dict]) -> str:
        business_name = "el restaurante"
        ai_prompt_rules = ""
        if business_context and business_context.get("business"):
            biz = business_context["business"]
            business_name = biz.get("name") or business_name
            settings = biz.get("settings") or {}
            ai_prompt_rules = (settings.get("ai_prompt") or "").strip()

        rules_block = ""
        if ai_prompt_rules:
            rules_block = (
                "Reglas y contexto del negocio (úsalas SIEMPRE para "
                "preguntas sobre combos, acompañamientos incluidos por "
                "default — papas, bebidas — y cualquier política del "
                "negocio):\n" + ai_prompt_rules
            )

        return _SYSTEM_PROMPT_TEMPLATE.format(
            business_name=business_name,
            ai_prompt_rules=rules_block,
        )

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
        **kwargs: Any,
    ) -> AgentOutput:
        run_id = str(uuid.uuid4())
        start_time = time.time()
        business_id = (business_context or {}).get("business_id") or ""

        tracer.start_run(
            run_id=run_id, user_id=wa_id, message_id=message_id, business_id=str(business_id),
        )

        # ── 0a. Order-closed handoff fast-path (deterministic).
        # Compose the reply directly from business_info_service so the prose
        # matches the existing "¿están abiertos?" answer.
        handoff_context = kwargs.get("handoff_context") or {}
        reason = (handoff_context.get("reason") or "").strip()

        if reason == "order_closed":
            return self._handle_order_closed(
                wa_id, business_id, business_context, handoff_context,
                run_id, start_time,
            )

        if reason == "delivery_paused":
            return self._handle_delivery_paused(
                wa_id, business_id, business_context, handoff_context,
                run_id, start_time,
            )

        if reason == "out_of_zone":
            return self._handle_out_of_zone(
                wa_id, business_id, handoff_context, run_id, start_time,
            )

        # ── 0b. Pre-LLM safety nets. These patterns belong to the order
        # agent, not CS. Catches what the router occasionally misroutes.
        safety_handoff = self._pre_loop_safety_nets(
            message_body, business_context, turn_ctx,
        )
        if safety_handoff is not None:
            tracer.end_run(
                run_id, success=True,
                latency_ms=(time.time() - start_time) * 1000,
            )
            return safety_handoff

        # ── 1. Set per-turn tool context.
        ctx_for_tools: Dict[str, Any] = {
            "wa_id": wa_id,
            "business_id": str(business_id),
            "business_context": business_context,
            "session": session,
            "turn_ctx": turn_ctx,
            "message_body": message_body,
        }
        token = set_tool_context(ctx_for_tools)

        try:
            messages = self._build_initial_messages(
                business_context=business_context,
                conversation_history=conversation_history,
                message_body=message_body,
                turn_ctx=turn_ctx,
            )
            tool_map = {t.name: t for t in cs_tools}
            executed_tools: List[str] = []
            final_text: Optional[str] = None
            handoff_payload: Optional[Dict[str, Any]] = None
            last_model_text = ""

            for iteration in range(MAX_ITERATIONS):
                response = self.llm.invoke(
                    messages,
                    config={
                        "run_name": "cs_agent_tool_calling",
                        "metadata": {
                            "wa_id": wa_id,
                            "business_id": str(business_id),
                            "turn_id": message_id or "",
                            "iteration": iteration,
                            "run_id": run_id,
                        },
                    },
                )
                messages.append(response)
                last_model_text = (getattr(response, "content", "") or "").strip()

                tool_calls = getattr(response, "tool_calls", None) or []
                if not tool_calls:
                    # No tool call — LLM's prose IS the reply (chat fallback).
                    if last_model_text:
                        final_text = last_model_text
                    break

                # Dispatch tools. First FINAL/HANDOFF terminates the loop.
                terminate = False
                for tc in tool_calls:
                    tool_name, tool_args, tool_id = _unpack_tool_call(tc)
                    executed_tools.append(tool_name)

                    tool_fn = tool_map.get(tool_name)
                    if tool_fn is None:
                        result_str = (
                            f"Error: la herramienta '{tool_name}' no existe. "
                            "No la uses."
                        )
                    else:
                        try:
                            result = tool_fn.invoke({
                                **(tool_args or {}),
                                "injected_business_context": ctx_for_tools,
                            })
                            result_str = result if isinstance(result, str) else str(result)
                        except Exception as exc:
                            logging.exception(
                                "[CS_AGENT] tool=%s raised: %s",
                                tool_name, exc,
                            )
                            result_str = (
                                f"Error al ejecutar {tool_name}: {exc}. "
                                "Pide disculpas al cliente."
                            )

                    messages.append(ToolMessage(
                        content=result_str, tool_call_id=tool_id,
                    ))

                    handoff = parse_handoff(result_str)
                    if handoff is not None:
                        handoff_payload = _normalize_handoff(handoff, message_body)
                        terminate = True
                        break

                    final = parse_final(result_str)
                    if final is not None:
                        final_text = final
                        terminate = True
                        break

                    # Plain text → continue loop so LLM can write prose
                    # from the structured block.

                if terminate:
                    break
            else:
                logging.warning(
                    "[CS_AGENT] max iterations reached without final/handoff "
                    "(executed=%s)",
                    executed_tools,
                )

            # ── Handoff short-circuit.
            if handoff_payload is not None:
                logging.warning(
                    "[CS_AGENT] handoff to %s (reason=%s)",
                    handoff_payload.get("to"),
                    (handoff_payload.get("context") or {}).get("reason"),
                )
                tracer.end_run(
                    run_id, success=True,
                    latency_ms=(time.time() - start_time) * 1000,
                )
                return {
                    "agent_type": self.agent_type,
                    "message": "",
                    "state_update": {},
                    "handoff": handoff_payload,
                }

            # ── Build final reply.
            if not final_text:
                final_text = last_model_text or (
                    "Disculpa, tuve un problema. ¿Podrías intentar de nuevo?"
                )

            # Persist assistant turn.
            try:
                conversation_service.store_conversation_message(
                    wa_id, final_text, "assistant", business_id=business_id,
                )
            except Exception as exc:
                logging.error(
                    "[CS_AGENT] failed to store assistant message: %s", exc,
                )

            tracer.end_run(
                run_id, success=True,
                latency_ms=(time.time() - start_time) * 1000,
            )

            logging.warning(
                "[CS_TURN] wa_id=%s tools=%s latency_ms=%d",
                wa_id, executed_tools,
                int((time.time() - start_time) * 1000),
            )

            return {
                "agent_type": self.agent_type,
                "message": final_text,
                "state_update": {"active_agents": ["customer_service"]},
            }
        finally:
            reset_tool_context(token)

    # ── Helpers ────────────────────────────────────────────────────

    def _build_initial_messages(
        self,
        *,
        business_context: Optional[Dict],
        conversation_history: List[Dict],
        message_body: str,
        turn_ctx: Optional[object],
    ) -> List:
        ctx_block = ""
        if turn_ctx is not None:
            try:
                ctx_block = (
                    "===== ESTADO Y HISTORIAL DEL TURNO =====\n"
                    "(lo que YA pasó antes de este turno)\n\n"
                    + render_for_prompt(turn_ctx)
                    + "\n===== FIN DEL ESTADO =====\n\n"
                )
            except Exception:
                ctx_block = ""

        return [
            SystemMessage(content=self._build_system_prompt(business_context)),
            HumanMessage(content=(
                f"{ctx_block}"
                "[MENSAJE ACTUAL DEL CLIENTE — procesa SOLO este "
                "mensaje en este turno; los anteriores en CONTEXTO "
                "son historial]\n"
                f"Cliente: {message_body}"
            )),
        ]

    def _pre_loop_safety_nets(
        self,
        message_body: str,
        business_context: Optional[Dict],
        turn_ctx: Optional[object],
    ) -> Optional[AgentOutput]:
        """
        Deterministic patterns that belong to the order agent, not CS.
        Returns an AgentOutput with `handoff` set when a pattern matches;
        None otherwise.
        """
        # 1) price-of-product: catalog price questions land in order.
        try:
            from ..orchestration.router import _deterministic_price_of_product
            if _deterministic_price_of_product(message_body, business_context):
                logging.warning(
                    "[CS_AGENT] safety net: price-of-product → handoff to order"
                )
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
            logging.warning(
                "[CS_AGENT] price-of-product safety net failed: %s", exc,
            )

        # 2) post-PLACE_ORDER despedida — the order agent has a
        # status-aware response for "gracias" / "perfecto" right after
        # placement, so route there instead of answering as CS chat.
        try:
            latest_status = (
                getattr(turn_ctx, "latest_order_status", None)
                if turn_ctx is not None else None
            )
            if latest_status and _is_post_order_close(message_body):
                logging.warning(
                    "[CS_AGENT] safety net: despedida post-pedido "
                    "(latest_status=%s) → handoff to order",
                    latest_status,
                )
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
            logging.warning(
                "[CS_AGENT] despedida safety net failed: %s", exc,
            )

        # 3) stuck-article typos: "unabimota" / "elpegoretti" → order.
        try:
            from ..orchestration.router import _expand_stuck_articles
            from ..services import catalog_cache
            bid = str((business_context or {}).get("business_id") or "")
            if bid:
                lookup = catalog_cache.get_router_lookup_set(bid)
                if lookup:
                    expanded = _expand_stuck_articles(message_body, lookup)
                    if expanded != message_body:
                        logging.warning(
                            "[CS_AGENT] safety net: stuck-article → "
                            "handoff to order: %r → %r",
                            message_body, expanded,
                        )
                        return {
                            "agent_type": self.agent_type,
                            "message": "",
                            "state_update": {},
                            "handoff": {
                                "to": "order",
                                "segment": expanded,
                                "context": {"reason": "stuck_article_misroute"},
                            },
                        }
        except Exception as exc:
            logging.warning(
                "[CS_AGENT] stuck-article safety net failed: %s", exc,
            )

        return None

    def _handle_order_closed(
        self,
        wa_id: str,
        business_id: str,
        business_context: Optional[Dict],
        handoff_context: Dict,
        run_id: str,
        start_time: float,
    ) -> AgentOutput:
        blocked_intents = handoff_context.get("blocked_intents") or []
        has_active_cart = bool(handoff_context.get("has_active_cart"))
        logging.warning(
            "[ORDER_GATE] business=%s wa_id=%s CS handling order_closed "
            "blocked_intents=%s has_active_cart=%s",
            business_id, wa_id, blocked_intents, has_active_cart,
        )
        alt_contact_suffix = ""
        sentence = "Por ahora estamos cerrados."
        try:
            status = business_info_service.compute_open_status(str(business_id))
            sentence = business_info_service.format_open_status_sentence(status) or sentence
            if business_info_service.is_fully_closed_today(status):
                biz = (business_context or {}).get("business")
                alt_contact_suffix = business_info_service.format_alt_branch_suffix(biz, "closed")
        except Exception as exc:
            logging.warning(
                "[ORDER_GATE] business=%s wa_id=%s open-status compute failed: %s",
                business_id, wa_id, exc,
            )
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
        message = sentence + alt_contact_suffix + tail
        try:
            conversation_service.store_conversation_message(
                wa_id, message, "assistant", business_id=business_id,
            )
        except Exception as exc:
            logging.error(
                "[ORDER_GATE] business=%s wa_id=%s persist failed: %s",
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

    def _handle_delivery_paused(
        self,
        wa_id: str,
        business_id: str,
        business_context: Optional[Dict],
        handoff_context: Dict,
        run_id: str,
        start_time: float,
    ) -> AgentOutput:
        """
        Render the deterministic reply when the operator paused new
        orders from the orders page. Mirrors ``_handle_order_closed``
        but with different copy — "we're slammed right now" instead of
        "we're closed". Per the design review, pause blocks BOTH
        delivery and pickup (operator chose block-all-new-orders).
        """
        blocked_intents = handoff_context.get("blocked_intents") or []
        has_active_cart = bool(handoff_context.get("has_active_cart"))
        logging.warning(
            "[ORDER_GATE] business=%s wa_id=%s CS handling delivery_paused "
            "blocked_intents=%s has_active_cart=%s",
            business_id, wa_id, blocked_intents, has_active_cart,
        )
        sentence = (
            "Por ahora estamos al tope de pedidos y no estamos tomando "
            "más por el momento."
        )
        if has_active_cart:
            tail = (
                " Tu pedido se queda guardado, lo retomamos cuando "
                "reabramos los pedidos. Mientras tanto puedo resolverte "
                "cualquier duda."
            )
        else:
            tail = (
                " Mientras tanto puedo contarte del menú o resolverte "
                "cualquier duda."
            )
        message = sentence + tail
        try:
            conversation_service.store_conversation_message(
                wa_id, message, "assistant", business_id=business_id,
            )
        except Exception as exc:
            logging.error(
                "[ORDER_GATE] business=%s wa_id=%s persist failed: %s",
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

    def _handle_out_of_zone(
        self,
        wa_id: str,
        business_id: str,
        handoff_context: Dict,
        run_id: str,
        start_time: float,
    ) -> AgentOutput:
        city = (handoff_context.get("city") or "").strip()
        phone = (handoff_context.get("phone") or "").strip()
        logging.warning(
            "[OUT_OF_ZONE] business=%s wa_id=%s CS redirect city=%s phone=%s",
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


# ── Module-level helpers ───────────────────────────────────────────────


def _unpack_tool_call(tc: Any) -> tuple:
    """Return (name, args_dict, id) from a tool_call entry (dict or obj)."""
    if isinstance(tc, dict):
        return (
            tc.get("name") or "",
            tc.get("args") or {},
            tc.get("id") or "",
        )
    return (
        getattr(tc, "name", "") or "",
        getattr(tc, "args", None) or {},
        getattr(tc, "id", "") or "",
    )


def _normalize_handoff(
    parsed: Dict[str, str],
    message_body: str,
) -> Dict[str, Any]:
    """
    Convert a HANDOFF sentinel dict into the AgentOutput.handoff shape
    the dispatcher expects: {to, segment, context}.

    Reserved keys (`to`, `segment`) go to the top level; everything else
    is folded into `context`.
    """
    to = parsed.get("to") or "order"
    segment = parsed.get("segment") or message_body
    context = {
        k: v for k, v in parsed.items()
        if k not in ("to", "segment") and v not in (None, "")
    }
    return {"to": to, "segment": segment, "context": context}
