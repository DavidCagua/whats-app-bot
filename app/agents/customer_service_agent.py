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
import uuid
from typing import Any, Dict, List, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from .base_agent import BaseAgent, AgentOutput
from ..orchestration.customer_service_flow import (
    execute_customer_service_intent,
    INTENT_GET_BUSINESS_INFO,
    INTENT_GET_ORDER_STATUS,
    INTENT_GET_ORDER_HISTORY,
    INTENT_CUSTOMER_SERVICE_CHAT,
    RESULT_KIND_BUSINESS_INFO,
    RESULT_KIND_INFO_MISSING,
    RESULT_KIND_ORDER_STATUS,
    RESULT_KIND_NO_ORDER,
    RESULT_KIND_ORDER_HISTORY,
    RESULT_KIND_CHAT_FALLBACK,
    RESULT_KIND_INTERNAL_ERROR,
)
from ..database.conversation_service import conversation_service
from ..services import business_info_service
from ..services.tracing import tracer


PLANNER_SYSTEM_TEMPLATE = """Eres el clasificador de intención para el agente de servicio al cliente de un restaurante. Manejas preguntas pre-venta (horarios, ubicación, domicilio, medios de pago) Y post-venta (estado de pedido, historial).

Devuelves EXACTAMENTE una intención en JSON. Nunca markdown, nunca explicación.

Intenciones válidas: GET_BUSINESS_INFO, GET_ORDER_STATUS, GET_ORDER_HISTORY, CUSTOMER_SERVICE_CHAT.

Reglas:
- GET_BUSINESS_INFO con params.field: pregunta sobre el negocio. Valores de field:
    "hours"           → horarios ("a qué hora abren", "cuándo cierran", "abren los domingos")
    "address"         → ubicación ("dónde quedan", "cuál es la dirección")
    "phone"           → teléfono de contacto
    "delivery_fee"    → costo del domicilio ("cuánto cobran domicilio", "cuánto vale el envío")
    "menu_url"        → link del menú
    "payment_methods" → medios de pago ("aceptan nequi", "qué pagos reciben", "efectivo?")
- GET_ORDER_STATUS: el usuario pregunta por el estado de su pedido actual o reciente
  ("dónde está mi pedido", "ya salió?", "cuánto falta", "qué pasa con mi pedido"). Sin params.
- GET_ORDER_HISTORY: el usuario pide ver pedidos anteriores
  ("qué he pedido antes", "muéstrame mis pedidos", "último pedido"). Sin params.
- CUSTOMER_SERVICE_CHAT: cualquier otra cosa que no encaja. Sin params.

Si el mensaje pregunta por varias cosas a la vez, elige la más específica. Si un dato no aparece arriba (ej. "¿hacen eventos?"), usa CUSTOMER_SERVICE_CHAT.

Responde SOLO con JSON:
{"intent": "<INTENT>", "params": {}}
"""


# Template-based reply: short, no LLM. Used for the common
# "user asked for one field, we have it" case.
_BUSINESS_INFO_TEMPLATES = {
    "hours": "{value}",
    "address": "Estamos ubicados en {value}.",
    "phone": "Puedes contactarnos al {value}.",
    "delivery_fee": "El domicilio tiene un costo de {value}.",
    "menu_url": "Acá tienes nuestro menú: {value}",
    "payment_methods": "Aceptamos {value}.",
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
        Render a templated reply for the common simple case: GET_BUSINESS_INFO
        where the field resolved cleanly. Returns None when the response
        needs LLM generation.
        """
        if exec_result.get("result_kind") != RESULT_KIND_BUSINESS_INFO:
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
        if business_context and business_context.get("business"):
            business_name = business_context["business"].get("name") or business_name

        base_system = (
            f"Eres el agente de servicio al cliente de {business_name}. "
            "Respondes en español colombiano, natural y breve (1-3 oraciones). "
            "Nunca inventas información que no esté en los datos proporcionados."
        )

        if result_kind == RESULT_KIND_BUSINESS_INFO:
            field = exec_result.get("field")
            value = exec_result.get("value")
            system = base_system + "\nSITUACIÓN: Tienes el dato exacto del negocio — comunícalo."
            inp = f"Pregunta del cliente: {message_body}\nCampo: {field}\nValor: {value}"
            return system, inp

        if result_kind == RESULT_KIND_INFO_MISSING:
            field = exec_result.get("field") or "(no identificado)"
            fields_available = exec_result.get("available_fields") or []
            system = (
                base_system
                + "\nSITUACIÓN: No tienes ese dato configurado. Discúlpate brevemente y sugiere "
                "que puedes responder sobre otros temas (horarios, dirección, domicilio, pagos, "
                "estado de pedidos). NO inventes el dato."
            )
            inp = (
                f"Pregunta del cliente: {message_body}\n"
                f"Campo no disponible: {field}\n"
                f"Campos disponibles: {', '.join(fields_available)}"
            )
            return system, inp

        if result_kind == RESULT_KIND_ORDER_STATUS:
            order = exec_result.get("order") or {}
            status = order.get("status") or "desconocido"
            total = order.get("total_amount")
            items = order.get("items") or []
            items_lines = "\n".join(
                f"- {it.get('quantity')}x (${int(float(it.get('unit_price') or 0)):,})".replace(",", ".")
                for it in items
            ) or "(sin items)"
            # Status-aware framing rules so we don't over-promise about
            # fulfillment state we don't track in real time.
            status_rules = (
                "REGLAS DE ESTADO:\n"
                "- pending: 'Tu pedido está en preparación. Pronto nos comunicamos para coordinar entrega.'\n"
                "- completed: 'Tu pedido ya fue entregado. ¿Algo más?'\n"
                "- cancelled: 'Tu pedido fue cancelado. Si fue un error, cuéntame.'\n"
                "- cualquier otro: reporta el estado literal y ofrece contactar al equipo."
            )
            system = (
                base_system
                + "\nSITUACIÓN: El cliente pregunta por el estado de su pedido más reciente. "
                + "Resume estado y items. NO inventes ETA exacto.\n" + status_rules
            )
            total_str = f"${int(float(total or 0)):,}".replace(",", ".")
            inp = (
                f"Pregunta del cliente: {message_body}\n"
                f"Estado: {status}\n"
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

        if result_kind == RESULT_KIND_CHAT_FALLBACK:
            fields = exec_result.get("available_fields") or []
            system = (
                base_system
                + "\nSITUACIÓN: No entendiste bien qué pregunta el cliente. "
                "Dile brevemente con qué puedes ayudarle: horarios, dirección, domicilio, "
                "medios de pago, estado de pedido, historial."
            )
            inp = (
                f"Pregunta del cliente: {message_body}\n"
                f"Temas disponibles: {', '.join(fields)}"
            )
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
        **kwargs,
    ) -> AgentOutput:
        """Planner → executor → response generator (template or LLM)."""
        start_time = time.time()
        run_id = str(uuid.uuid4())
        business_id = (business_context or {}).get("business_id") or ""

        tracer.start_run(run_id=run_id, user_id=wa_id, message_id=message_id, business_id=str(business_id))

        # 1) Planner
        history_text = ""
        for msg in (conversation_history or [])[-4:]:
            role = msg.get("role", "")
            content = (msg.get("content") or msg.get("message", ""))[:180]
            history_text += f"{role}: {content}\n"

        planner_messages = [
            SystemMessage(content=PLANNER_SYSTEM_TEMPLATE),
            HumanMessage(
                content=(
                    f"Historial reciente:\n{history_text}\n"
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
        logging.warning("[CS_AGENT] Planner intent=%s params=%s", intent, params)

        # 2) Executor
        exec_result = execute_customer_service_intent(
            wa_id=wa_id,
            business_id=str(business_id),
            business_context=business_context,
            intent=intent,
            params=params,
        )
        result_kind = exec_result.get("result_kind") or RESULT_KIND_CHAT_FALLBACK

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

        state_update = {
            "customer_service_context": {
                "last_intent": intent,
                "last_result_kind": result_kind,
            },
            "active_agents": ["customer_service"],
        }

        return {
            "agent_type": self.agent_type,
            "message": final_response_text,
            "state_update": state_update,
        }
