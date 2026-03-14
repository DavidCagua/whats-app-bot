"""
Order agent: product catalog, cart management, order placement.
"""

import os
import logging
import uuid
import time
from typing import Dict, List, Optional
from datetime import date
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage

from .base_agent import BaseAgent, AgentOutput
from ..services.order_tools import order_tools
from ..database.conversation_service import conversation_service
from ..services.tracing import tracer


ORDER_SYSTEM_PROMPT_TEMPLATE = """Eres un asistente de pedidos para un restaurante/negocio. Hablas español colombiano de forma amigable.
Te presentas como asistente de {business_name}.

ORDEN DEL FLUJO (respeta este orden estrictamente):
1. USAR get_customer_info (NUNCA lo llames antes de que el pedido esté completo).
2. SALUDAR: al iniciar la conversación, saluda por el nombre si lo tienes, comparte el menú y pregunta qué desea ordenar.
   - Si menu_url está configurado (te lo indico abajo), inclúyelo: "Puedes ver nuestro menú aquí: [url]"
   - Si no hay menu_url, di que puedes mostrarle el menú por categorías y pregunta qué le gustaría (hamburguesas, bebidas, etc).
   - Siempre pregunta: "¿Qué te gustaría ordenar?"
3. TOMAR EL PEDIDO: list_products, search_products, get_product, add_to_cart, update_cart_item, remove_from_cart.
   - Búsqueda flexible: get_product y add_to_cart buscan por nombre O por ingredientes. "hamburguesa barracuda" encuentra Barracuda; "hamburguesa con queso azul" encuentra Montesa; "coca zero" encuentra Coca-Cola Zero.
   - Si el cliente pide varios ítems en un mensaje ("una barracuda y una limonada de cereza"), agrega TODOS. No digas que no encontraste uno si existe.
AMBIGÜEDAD: Si el cliente pide algo que puede ser varios productos (ej. "dos limonadas" cuando hay Limonada natural, de cereza, de fresa):
   - Usa search_products(query) primero. Si retorna más de uno, lista las opciones y pregunta "¿Cuál prefieres?" (ej. Limonada natural, de cereza, de fresa). NO agregues al carrito hasta que el cliente especifique.
CORRECCIONES: Si el cliente corrige ("no de cereza", "cambia eso", "no ese", "la otra"):
   - Usa remove_from_cart(product_id) para ELIMINAR el producto incorrecto, LUEGO add_to_cart con el correcto. NUNCA solo agregar sin quitar el que estaba mal.
3. RECOLECTAR/CONFIRMAR DATOS: solo cuando el cliente indique que quiere proceder/confirmar (ej. "listo", "procedamos", "ya está"):
   - Usa los valores EXACTOS que retorna get_customer_info. NUNCA inventes placeholders como [dirección].
   - Si address=NO_REGISTRADA: pide la dirección al cliente.
   - Si address tiene un valor real: confirma "Tengo registrada la dirección [valor exacto]. ¿Deseas recibir ahí o en otra?"
   - Pide: dirección (si falta), teléfono de contacto (obligatorio; si es el mismo WhatsApp puede decir "este" o "mismo"), medio de pago.
   - Usa submit_delivery_info cuando tengas dirección, teléfono y medio de pago.
4. CONFIRMAR PEDIDO: place_order solo después de submit_delivery_info exitoso.

Herramientas: list_products, search_products, get_product, add_to_cart, view_cart, update_cart_item, remove_from_cart, get_customer_info, submit_delivery_info, place_order.
Precios en COP.
Cliente: {name} (WhatsApp: {wa_id})
Fecha: {current_date}
Menu URL (incluir en saludo si existe): {menu_url}
"""


class OrderAgent(BaseAgent):
    """Agent for product browsing, cart management, and order placement."""

    agent_type = "order"

    def __init__(self):
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.5,
            api_key=os.getenv("OPENAI_API_KEY"),
        )
        self.llm_with_tools = self.llm.bind_tools(order_tools)
        logging.info("[ORDER_AGENT] Initialized with order tools")

    def get_system_prompt(
        self,
        business_context: Optional[Dict],
        current_date: str,
        current_year: int,
        wa_id: str,
        name: str,
    ) -> str:
        business_name = "el restaurante"
        menu_url = ""
        if business_context and business_context.get("business"):
            biz = business_context["business"]
            business_name = biz.get("name") or business_name
            settings = biz.get("settings") or {}
            menu_url = settings.get("menu_url") or ""
        return ORDER_SYSTEM_PROMPT_TEMPLATE.format(
            name=name,
            wa_id=wa_id,
            current_date=current_date,
            business_name=business_name,
            menu_url=menu_url,
        )

    def get_tools(self):
        return order_tools

    def _execute_tool_calls(
        self,
        tool_calls: List,
        business_context: Optional[Dict],
        wa_id: str,
        run_id: Optional[str],
    ) -> List[ToolMessage]:
        """Execute tool calls and return ToolMessage objects. Injects wa_id for cart/session."""
        # Order tools need wa_id in context for session (cart) operations
        enriched_context = {**(business_context or {}), "wa_id": wa_id}

        tool_messages = []
        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            tool_call_id = tool_call.get("id", "unknown")
            logging.warning(f"[ORDER_TOOL] Executing {tool_name} with args: {tool_args}")

            if run_id:
                tracer.log_event(
                    run_id, "tool_call", {"tool_name": tool_name, "tool_call_id": tool_call_id, "args": tool_args}
                )

            tool_found = False
            tool_start = time.time()
            for tool in order_tools:
                if tool.name == tool_name:
                    tool_found = True
                    try:
                        tool_args_with_context = {**tool_args, "injected_business_context": enriched_context}
                        result = tool.invoke(tool_args_with_context)
                        tool_latency = (time.time() - tool_start) * 1000
                        if run_id:
                            tracer.log_event(
                                run_id, "tool_result", {"tool_name": tool_name, "success": True, "latency_ms": tool_latency}
                            )
                        tool_messages.append(ToolMessage(content=str(result), tool_call_id=tool_call_id, name=tool_name))
                    except Exception as e:
                        error_msg = str(e)
                        logging.error(f"[ORDER_TOOL] Error executing {tool_name}: {error_msg}")
                        if run_id:
                            tracer.log_event(
                                run_id, "tool_result", {"tool_name": tool_name, "success": False, "error": error_msg}
                            )
                        tool_messages.append(
                            ToolMessage(
                                content=f"Error: {error_msg}",
                                tool_call_id=tool_call_id,
                                name=tool_name,
                                additional_kwargs={"error": True},
                            )
                        )
                    break

            if not tool_found:
                tool_messages.append(
                    ToolMessage(
                        content=f"Tool {tool_name} not found",
                        tool_call_id=tool_call_id,
                        name=tool_name,
                        additional_kwargs={"error": True},
                    )
                )
        return tool_messages

    def execute(
        self,
        message_body: str,
        wa_id: str,
        name: str,
        business_context: Optional[Dict],
        conversation_history: List[Dict],
        message_id: Optional[str] = None,
    ) -> AgentOutput:
        """Run order agent: LLM + tool loop. Return AgentOutput."""
        run_id = str(uuid.uuid4())
        start_time = time.time()
        business_id = business_context.get("business_id") if business_context else None

        try:
            tracer.start_run(run_id=run_id, user_id=wa_id, message_id=message_id, business_id=str(business_id) if business_id else None)

            current_date_obj = date.today()
            current_date = f"{current_date_obj.day}/{current_date_obj.month}/{current_date_obj.year}"

            system_prompt = self.get_system_prompt(
                business_context=business_context,
                current_date=current_date,
                current_year=current_date_obj.year,
                wa_id=wa_id,
                name=name,
            )

            messages = [SystemMessage(content=system_prompt)]
            for msg in conversation_history:
                content = msg.get("content") or msg.get("message", "")
                if msg.get("role") == "user":
                    messages.append(HumanMessage(content=content))
                elif msg.get("role") == "assistant":
                    messages.append(AIMessage(content=content))

            messages.append(HumanMessage(content=message_body))

            max_iterations = 5
            iteration = 0
            response = None

            while iteration < max_iterations:
                iteration += 1
                logging.info(f"[ORDER_AGENT] Iteration {iteration}/{max_iterations}")
                response = self.llm_with_tools.invoke(messages)
                messages.append(response)

                if hasattr(response, "tool_calls") and response.tool_calls:
                    tool_messages = self._execute_tool_calls(
                        response.tool_calls, business_context, wa_id, run_id
                    )
                    messages.extend(tool_messages)
                    continue
                else:
                    break

            final_response_text = (
                response.content
                if response and hasattr(response, "content")
                else "Lo siento, necesito más tiempo para procesar tu solicitud."
            )

            conversation_service.store_conversation_message(
                wa_id, message_body, "user", business_id=business_id
            )
            conversation_service.store_conversation_message(
                wa_id, final_response_text, "assistant", business_id=business_id
            )

            tracer.end_run(run_id, success=True, latency_ms=(time.time() - start_time) * 1000)

            # If place_order succeeded this turn, it already persisted session reset.
            # Return empty state_update to avoid overwriting.
            order_completed = False
            for m in messages:
                if getattr(m, "name", None) == "place_order" and hasattr(m, "content"):
                    content = str(m.content) if m.content else ""
                    if "Pedido confirmado" in content or content.strip().startswith("✅"):
                        order_completed = True
                        break
            state_update = {} if order_completed else {"active_agents": ["order"]}

            return {
                "agent_type": self.agent_type,
                "message": final_response_text,
                "state_update": state_update,
            }

        except Exception as e:
            error_msg = str(e)
            logging.error(f"[ORDER_AGENT] Error: {error_msg}")
            tracer.end_run(run_id, success=False, error=error_msg, latency_ms=(time.time() - start_time) * 1000)
            return {
                "agent_type": self.agent_type,
                "message": "Lo siento, tuve un problema procesando tu mensaje. ¿Podrías intentar de nuevo?",
                "state_update": {},
            }
