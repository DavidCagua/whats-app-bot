"""
Order agent: planner (intent) -> executor (one tool) -> response generator.
Backend is single source of truth; response is generated from actual tool result and cart state.
"""

import json
import os
import logging
import re
import uuid
import time
from typing import Any, Dict, List, Optional
from datetime import date
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from .base_agent import BaseAgent, AgentOutput
from ..services.order_tools import order_tools
from ..orchestration.order_flow import (
    execute_order_intent,
    INTENT_CHAT,
    INTENT_GREET,
    INTENT_ADD_TO_CART,
    INTENT_REMOVE_FROM_CART,
    INTENT_UPDATE_CART_ITEM,
)
from ..database.conversation_service import conversation_service
from ..services.tracing import tracer


PLANNER_SYSTEM_TEMPLATE = """Eres un clasificador de intención para un bot de pedidos. Dado el estado actual del pedido y el mensaje del usuario, devuelves EXACTAMENTE una intención y sus parámetros en JSON.

Estado actual: {order_state}
Resumen del carrito: {cart_summary}

Intenciones válidas: GREET, GET_MENU_CATEGORIES, LIST_PRODUCTS, SEARCH_PRODUCTS, GET_PRODUCT, ADD_TO_CART, VIEW_CART, UPDATE_CART_ITEM, REMOVE_FROM_CART, PROCEED_TO_CHECKOUT, GET_CUSTOMER_INFO, SUBMIT_DELIVERY_INFO, PLACE_ORDER, CHAT.

Reglas de menú y búsqueda (importante):
- GET_MENU_CATEGORIES: cuando el usuario pregunta qué hay, qué tienes en general, o qué categorías hay (ej. "qué tienes", "qué hay en el menú"). Sin params.
- LIST_PRODUCTS con category: cuando pregunta qué tienes EN UNA CATEGORÍA (ej. "qué tienes de bebidas", "qué hamburguesas tienes", "qué bebidas hay"). Siempre pasa params: {{"category": "bebidas"}} o "hamburguesas", "BEBIDAS", etc. category vacío = menú completo.
- SEARCH_PRODUCTS con query: cuando el usuario NOMBRA un producto o ingrediente (ej. "quiero barracuda", "tienes coca cola", "algo con queso azul"). No uses para preguntas de categoría; para "qué tienes de X" usa LIST_PRODUCTS con category.
- GET_PRODUCT con product_name: cuando pregunta qué trae o qué tiene un producto (ej. "qué trae la barracuda", "qué tiene la montesa").

Otras reglas:
- Si el usuario saluda o es el inicio: GREET.
- Si pide agregar uno o más productos: ADD_TO_CART. Para un solo producto: params con "product_name" (o "product_id") y "quantity". Para varios productos: params con "items": [ {{"product_name": "NOMBRE", "quantity": 1}}, ... ]. Ejemplo varios: "dame una montesa y una booster" → {{"intent": "ADD_TO_CART", "params": {{"items": [{{"product_name": "MONTESA", "quantity": 1}}, {{"product_name": "BOOSTER", "quantity": 1}}]}}}}.
- Si pide quitar algo: REMOVE_FROM_CART con product_id.
- Si dice "listo", "procedamos", "confirmar": PROCEED_TO_CHECKOUT.
- Si ya están en recolección de datos (COLLECTING_DELIVERY): usa GET_CUSTOMER_INFO cuando necesites saber qué tenemos o qué falta (ej. usuario dice "listo", "ok", o para mostrar confirmación). Usa SUBMIT_DELIVERY_INFO cuando el usuario proporcione uno o más de: address, phone, name, payment_method; params pueden ser parciales, ej. {{"address": "Calle 1"}}, {{"payment_method": "Efectivo"}}, {{"name": "Juan", "phone": "+57..."}}.
- Si ya tienen todos los datos y confirman pedido: PLACE_ORDER.
- Si solo conversa: CHAT.

Responde ÚNICAMENTE con un JSON válido, sin markdown ni texto extra: {{"intent": "NOMBRE", "params": {{}}}}
"""

RESPONSE_GENERATOR_SYSTEM = """Generas la respuesta del asistente en español colombiano, amigable y breve.

Reglas críticas:
- NUNCA afirmes que agregaste, quitaste o modificaste algo en el carrito si la intención ejecutada no fue ADD_TO_CART, REMOVE_FROM_CART o UPDATE_CART_ITEM con éxito. Solo describe cambios que el backend confirmó.
- Si se ejecutó add/remove/update con éxito, incluye el resumen del carrito actual que te doy (es la verdad del backend).
- Usa solo la información del resultado de la herramienta y del resumen del carrito; no inventes datos.
- Si hubo error, explica brevemente y sugiere qué hacer.
- Después de un ADD_TO_CART exitoso: (1) confirma lo que se agregó, (2) muestra el resumen del carrito actual, (3) sugiere el siguiente paso: pregunta si desea agregar algo más (ej. bebida) o si procede con el pedido (ej. "¿Te gustaría agregar alguna bebida o procedemos con el pedido?").
- Búsqueda por ingrediente: cuando el resultado de la herramienta incluya descripciones de productos (varias líneas por producto) y el usuario preguntó por un ingrediente o tipo de plato (ej. "algo con queso azul", "hamburguesa con pollo"), menciona primero y de forma explícita el producto cuya descripción coincida con lo que pidió (ej. "La que lleva queso azul es la MONTESA: ...") y luego puedes listar brevemente otras opciones si aplica.
- Datos de entrega: NUNCA digas "Tengo esta dirección, teléfono y tipo de pago" a menos que el resultado de la herramienta contenga exactamente "DELIVERY_STATUS" y "all_present=true". Si el resultado es "OK_COLLECTING_DELIVERY" (sin DELIVERY_STATUS), responde pidiendo los datos: "Para continuar con tu pedido necesito: nombre, dirección, teléfono y medio de pago. ¿Me los indicas?". Si el resultado tiene DELIVERY_STATUS y all_present=true, confirma incluyendo los valores reales (dirección, teléfono, medio de pago) en el mensaje: "Tengo esta dirección: [valor], teléfono [valor] y pago [valor]. ¿Gustas proceder o quieres enviarla a otra dirección?". Si DELIVERY_STATUS tiene missing= o all_present=false, pide solo lo que falta (Me falta: ...) o todo si faltan todos.
"""


def _parse_planner_response(text: str) -> Dict[str, Any]:
    """Extract intent and params from planner LLM response (JSON only or embedded)."""
    text = (text or "").strip()
    # Try raw JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try to find JSON object in text
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {"intent": INTENT_CHAT, "params": {}}


class OrderAgent(BaseAgent):
    """Order agent: planner (intent) -> executor (one tool) -> response from real state."""

    agent_type = "order"

    def __init__(self):
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.3,
            api_key=os.getenv("OPENAI_API_KEY"),
        )
        logging.info("[ORDER_AGENT] Initialized with planner + executor + response generator")

    def get_tools(self):
        return order_tools

    def get_system_prompt(
        self,
        business_context: Optional[Dict],
        current_date: str,
        current_year: int,
        wa_id: str,
        name: str,
    ) -> str:
        """Used by response generator for business name/menu_url context."""
        business_name = "el restaurante"
        menu_url = ""
        if business_context and business_context.get("business"):
            biz = business_context["business"]
            business_name = biz.get("name") or business_name
            settings = biz.get("settings") or {}
            menu_url = settings.get("menu_url") or ""
        return f"Negocio: {business_name}. Menu URL: {menu_url or 'no configurado'}. Fecha: {current_date}."

    def execute(
        self,
        message_body: str,
        wa_id: str,
        name: str,
        business_context: Optional[Dict],
        conversation_history: List[Dict],
        message_id: Optional[str] = None,
        session: Optional[Dict] = None,
    ) -> AgentOutput:
        """Planner (intent) -> executor (one tool) -> response generator from actual tool result and cart."""
        run_id = str(uuid.uuid4())
        start_time = time.time()
        business_id = business_context.get("business_id") if business_context else None

        if not business_id:
            return {
                "agent_type": self.agent_type,
                "message": "Lo siento, no pude identificar el negocio. Intenta de nuevo.",
                "state_update": {},
            }

        # Load session if not provided (e.g. when executor passes it)
        if session is None:
            from ..database.session_state_service import session_state_service
            load_result = session_state_service.load(wa_id, str(business_id))
            session = load_result.get("session", {})

        order_context = session.get("order_context") or {}
        order_state = order_context.get("state") or "GREETING"
        items = order_context.get("items") or []
        total = order_context.get("total") or 0
        if items:
            lines = [f"{it.get('quantity', 0)}x {it.get('name', '')}" for it in items]
            cart_summary_str = "; ".join(lines) + f". Total: ${int(total):,}".replace(",", ".")
        else:
            cart_summary_str = "Carrito vacío."

        try:
            tracer.start_run(run_id=run_id, user_id=wa_id, message_id=message_id, business_id=str(business_id))

            # 1) Planner: one intent + params
            planner_system = PLANNER_SYSTEM_TEMPLATE.format(
                order_state=order_state,
                cart_summary=cart_summary_str,
            )
            history_text = ""
            for msg in conversation_history[-6:]:
                role = msg.get("role", "")
                content = (msg.get("content") or msg.get("message", ""))[:200]
                history_text += f"{role}: {content}\n"
            planner_messages = [
                SystemMessage(content=planner_system),
                HumanMessage(content=f"Historial reciente:\n{history_text}\nUsuario: {message_body}\n\nResponde solo con JSON: intent y params."),
            ]
            planner_response = self.llm.invoke(planner_messages)
            planner_text = planner_response.content if hasattr(planner_response, "content") else str(planner_response)
            parsed = _parse_planner_response(planner_text)
            intent = (parsed.get("intent") or INTENT_CHAT).upper().replace(" ", "_")
            params = parsed.get("params") or {}
            logging.warning("[ORDER_AGENT] Planner intent=%s params=%s", intent, params)

            # 2) Executor: validate state, run one tool, update state
            exec_result = execute_order_intent(
                wa_id=wa_id,
                business_id=str(business_id),
                business_context=business_context,
                session=session,
                intent=intent,
                params=params,
            )
            tool_result = exec_result.get("tool_result") or ""
            success = exec_result.get("success", False)
            cart_summary_after = exec_result.get("cart_summary") or cart_summary_str
            state_after = exec_result.get("state_after", order_state)
            error_msg = exec_result.get("error")

            # 3) Response: deterministic greeting for GREET, else LLM response generator
            if intent == INTENT_GREET:
                business_name = "el restaurante"
                menu_url = ""
                if business_context and business_context.get("business"):
                    biz = business_context["business"]
                    business_name = biz.get("name") or business_name
                    settings = biz.get("settings") or {}
                    menu_url = (settings.get("menu_url") or "").strip()
                customer_name = (name or "").strip()
                if customer_name and customer_name.lower() not in ("usuario", "cliente", "user"):
                    final_response_text = f"Hola {customer_name}, bienvenido a {business_name}. "
                else:
                    final_response_text = f"Hola, bienvenido a {business_name}. "
                if menu_url:
                    final_response_text += f"Este es nuestro menú: {menu_url}. "
                else:
                    final_response_text += "Puedes preguntarme por el menú por categorías. "
                final_response_text += "¿Qué te gustaría ordenar?"
            else:
                response_system = RESPONSE_GENERATOR_SYSTEM
                if intent in (INTENT_ADD_TO_CART, INTENT_REMOVE_FROM_CART, INTENT_UPDATE_CART_ITEM) and success:
                    response_system += f"\nEl backend confirmó la acción. Incluye este resumen del carrito actual: {cart_summary_after}"
                resp_input = f"Usuario: {message_body}\nIntención ejecutada: {intent}. Éxito: {success}.\nResultado del backend: {tool_result}\nResumen carrito: {cart_summary_after}"
                if error_msg:
                    resp_input += f"\nError: {error_msg}"
                response_messages = [
                    SystemMessage(content=response_system),
                    HumanMessage(content=resp_input + "\n\nGenera la respuesta breve en español para el usuario:"),
                ]
                response_llm = self.llm.invoke(response_messages)
                final_response_text = response_llm.content if hasattr(response_llm, "content") else str(response_llm)
                final_response_text = (final_response_text or "").strip() or "Listo. ¿En qué más puedo ayudarte?"

            conversation_service.store_conversation_message(wa_id, message_body, "user", business_id=business_id)
            conversation_service.store_conversation_message(wa_id, final_response_text, "assistant", business_id=business_id)

            tracer.end_run(run_id, success=True, latency_ms=(time.time() - start_time) * 1000)

            # state_update: if place_order cleared context, don't overwrite; else keep order agent active
            state_update = {"active_agents": ["order"]}
            if intent == "PLACE_ORDER" and success:
                state_update = {}

            return {
                "agent_type": self.agent_type,
                "message": final_response_text,
                "state_update": state_update,
            }

        except Exception as e:
            logging.exception("[ORDER_AGENT] Error: %s", e)
            tracer.end_run(run_id, success=False, error=str(e), latency_ms=(time.time() - start_time) * 1000)
            return {
                "agent_type": self.agent_type,
                "message": "Lo siento, tuve un problema procesando tu mensaje. ¿Podrías intentar de nuevo?",
                "state_update": {},
            }
