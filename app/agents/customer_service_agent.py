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
)
from ..database.conversation_service import conversation_service
from ..services import business_info_service
from ..services.tracing import tracer


PLANNER_SYSTEM_TEMPLATE = """Eres el clasificador de intención para el agente de servicio al cliente de un restaurante. Manejas preguntas pre-venta (horarios, ubicación, domicilio, medios de pago, promos) Y post-venta (estado de pedido, historial, cancelación).

Devuelves EXACTAMENTE una intención en JSON. Nunca markdown, nunca explicación.

Intenciones válidas: GET_BUSINESS_INFO, GET_ORDER_STATUS, GET_ORDER_HISTORY, CANCEL_ORDER, GET_PROMOS, SELECT_LISTED_PROMO, CUSTOMER_SERVICE_CHAT.

Reglas:
- GET_BUSINESS_INFO con params.field: pregunta sobre el negocio. Valores de field:
    "hours"           → horarios ("a qué hora abren", "cuándo cierran", "abren los domingos")
    "address"         → ubicación ("dónde quedan", "cuál es la dirección")
    "phone"           → teléfono de contacto
    "delivery_fee"    → costo del domicilio ("cuánto cobran domicilio", "cuánto vale el envío")
    "delivery_time"   → tiempo de entrega ("cuánto se demora la entrega", "cuánto tardan en entregar",
                        "en cuánto tiempo llega", "qué tan rápido entregan", "cuánto se demoran",
                        "cuánto tarda un domicilio"). El backend usa per-order ETA si el cliente ya
                        tiene un pedido en curso; sin pedido, devuelve la política general del negocio.
    "menu_url"        → link del menú
    "payment_methods" → medios de pago ("aceptan nequi", "qué pagos reciben", "efectivo?")
- GET_ORDER_STATUS: el usuario pregunta por el estado de su pedido actual o reciente
  ("dónde está mi pedido", "ya salió?", "cuánto falta", "qué pasa con mi pedido"). Sin params.
- GET_ORDER_HISTORY: el usuario pide ver pedidos anteriores
  ("qué he pedido antes", "muéstrame mis pedidos", "último pedido"). Sin params.
- CANCEL_ORDER: el usuario quiere CANCELAR/anular su pedido más reciente
  ("cancela mi pedido", "anula el pedido", "ya no quiero el pedido", "no lo manden",
   "cancélalo", "déjalo así, no quiero pedir"). Sin params.
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
    "delivery_time": "Nuestros pedidos llegan en {value}.",
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
                "Máximo 2 oraciones. Sin emojis salvo que el cliente los use. "
                "Sin frases relleno tipo 'si quieres saber', 'por si acaso', 'cualquier cosa'."
            )
            system = (
                base_system
                + "\nSITUACIÓN: El cliente pregunta por el estado de su pedido. "
                "Responde con el estado actual. Tiempos SOLO si los pide explícitamente.\n"
                + status_rules + "\n" + timing_rules + "\n" + tone_rules
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
        # Persist the listed promo set so the next turn can resolve
        # "dame esa" / "la primera" via SELECT_LISTED_PROMO.
        if result_kind == RESULT_KIND_PROMOS_LIST:
            cs_ctx_update["last_listed_promos"] = [
                {"id": p.get("id"), "name": p.get("name")}
                for p in (exec_result.get("promos") or [])
            ]

        state_update = {
            "customer_service_context": cs_ctx_update,
            "active_agents": ["customer_service"],
        }

        return {
            "agent_type": self.agent_type,
            "message": final_response_text,
            "state_update": state_update,
        }
