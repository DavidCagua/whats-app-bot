"""
Sales agent: product catalog browsing, product inquiries, and purchase facilitation.
Uses a lightweight intent filter to stay on-topic, then a flexible LLM + tool loop.
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
from ..services.sales_tools import sales_tools
from ..services.business_config_service import business_config_service
from ..database.conversation_service import conversation_service
from ..services.tracing import tracer


_ALLOWED_INTENTS = {"GREET", "ASK_PRODUCT", "ASK_PRICE", "BUY_INTENT", "GENERAL_INQUIRY"}
_OUT_OF_SCOPE_INTENT = "OUT_OF_SCOPE"
_OUT_OF_SCOPE_RESPONSE = (
    "Solo puedo ayudarte con información sobre nuestros productos. 😊 "
    "¿Te interesa ver lo que tenemos disponible?"
)

# Nombre del asesor para el saludo inbound (no viene de DB ni de env).
SALES_REP_NAME = "David"


def _inbound_greeting(customer_name: str, business_name: str) -> str:
    first_line = (
        f"Hola 👋 {customer_name}, ¿cómo estás?"
        if (customer_name or "").strip()
        else "Hola 👋 ¿cómo estás?"
    )
    return "\n\n".join([
        first_line,
        f"Te habla {SALES_REP_NAME} de {business_name} 🙌",
        "Gracias por escribirnos. Cuéntame, ¿en qué te podemos ayudar?",
    ])


def _inbound_greeting_followup(customer_name: str, business_name: str) -> str:
    if (customer_name or "").strip():
        return (
            f"¡Hola de nuevo, {customer_name}! 😊\n\n"
            f"Cuéntame, ¿en qué te podemos ayudar o qué fue lo que te llamó la atención de {business_name}?"
        )
    return (
        "¡Hola de nuevo! 😊\n\n"
        f"Cuéntame, ¿en qué te podemos ayudar o qué te interesa de {business_name}?"
    )


class SalesAgent(BaseAgent):
    """Agent for product sales: catalog browsing, product info, and purchase facilitation."""

    agent_type = "sales"

    def __init__(self):
        self._llm = None
        self._llm_with_tools = None
        self._intent_classifier = None
        logging.info("[SALES_AGENT] Initialized with sales tools (LLM lazy)")

    @property
    def llm(self) -> ChatOpenAI:
        # GPT-4o for vision support (ad images) and better sales reasoning
        if self._llm is None:
            self._llm = ChatOpenAI(
                model="gpt-4o",
                temperature=0.7,
                api_key=os.getenv("OPENAI_API_KEY"),
            )
        return self._llm

    @property
    def llm_with_tools(self):
        if self._llm_with_tools is None:
            self._llm_with_tools = self.llm.bind_tools(sales_tools)
        return self._llm_with_tools

    @property
    def intent_classifier(self) -> ChatOpenAI:
        # Lightweight classifier: fast and cheap
        if self._intent_classifier is None:
            self._intent_classifier = ChatOpenAI(
                model="gpt-4o-mini",
                temperature=0,
                api_key=os.getenv("OPENAI_API_KEY"),
            )
        return self._intent_classifier

    def _classify_intent(self, message: str, business_name: str) -> str:
        """
        Classify message intent. Returns one of:
        GREET, ASK_PRODUCT, ASK_PRICE, BUY_INTENT, GENERAL_INQUIRY, OUT_OF_SCOPE
        """
        try:
            prompt = (
                f'Classify this WhatsApp message sent to a business called "{business_name}".\n\n'
                f'Message: "{message}"\n\n'
                "Classify as exactly one of:\n"
                "- GREET: greeting or salutation\n"
                "- ASK_PRODUCT: asking about products, catalog, what they sell\n"
                "- ASK_PRICE: asking about prices or costs\n"
                "- BUY_INTENT: wants to buy, pay, or place an order\n"
                "- GENERAL_INQUIRY: general question about the business (hours, location, contact)\n"
                "- OUT_OF_SCOPE: anything unrelated to the business or its products\n\n"
                "Respond with only the classification label, nothing else."
            )
            result = self.intent_classifier.invoke([HumanMessage(content=prompt)])
            intent = (result.content or "").strip().upper()
            if intent not in (_ALLOWED_INTENTS | {_OUT_OF_SCOPE_INTENT}):
                return "GENERAL_INQUIRY"
            return intent
        except Exception as e:
            logging.warning(f"[SALES_AGENT] Intent classification failed: {e}, defaulting to GENERAL_INQUIRY")
            return "GENERAL_INQUIRY"

    def get_system_prompt(
        self,
        business_context: Optional[Dict],
        current_date: str,
        current_year: int,
        wa_id: str,
        name: str,
    ) -> str:
        business_info = business_config_service.get_business_info(business_context)
        business_name = business_info.get("business_name", "el negocio")
        city = business_info.get("city", "")
        payment_methods = business_info.get("payment_methods", [])
        promotions = business_info.get("promotions", [])

        settings = (business_context or {}).get("business", {}).get("settings", {})
        # Prefer a sales-specific prompt; fall back to generic ai_prompt
        admin_prompt = settings.get("sales_ai_prompt", "") or settings.get("ai_prompt", "")

        payment_text = f"Métodos de pago: {', '.join(payment_methods)}" if payment_methods else ""
        promotions_text = (
            "Promociones activas:\n" + "\n".join(f"- {p}" for p in promotions)
        ) if promotions else ""

        default_prompt = "\n".join(filter(None, [
            f"Eres un agente de ventas de {business_name}{f', {city}' if city else ''}.",
            "Tu objetivo es ayudar a los clientes a encontrar los productos que buscan y facilitar la compra.",
            "",
            "Comportamiento:",
            "- Responde de forma amigable y profesional en español",
            "- Usa las herramientas para buscar en el catálogo antes de responder sobre productos",
            "- Presenta los productos con nombre, precio y descripción cuando sea relevante",
            "- Cuando el cliente quiera comprar, pagar o cerrar: llama get_purchase_contact y sigue "
            "exactamente lo que devuelve (datos a pedir, enlace de pago si viene, política y envíos)",
            "- Debes pedir nombre completo, dirección, ciudad y teléfono; enviar el enlace de pago "
            "cuando la herramienta lo proporcione; no inventes enlaces",
            "- No crees ni registres pedidos en ningún sistema: la venta termina con los datos por "
            "WhatsApp + pago con el enlace",
            "- Si el cliente muestra interés, intenta cerrar: pregunta si quiere proceder",
            "- No inventes productos ni precios; consulta siempre el catálogo con las herramientas",
            payment_text,
            promotions_text,
        ]))

        base_prompt = admin_prompt if admin_prompt else default_prompt

        return f"{base_prompt}\n\n---\nCliente: {name} | Fecha: {current_date}"

    def get_tools(self) -> List:
        return sales_tools

    def _execute_tool_calls(
        self,
        tool_calls: List,
        business_context: Optional[Dict],
        run_id: Optional[str],
    ) -> List[ToolMessage]:
        tool_messages = []
        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            tool_call_id = tool_call.get("id", "unknown")
            logging.warning(f"[TOOL] Executing tool: {tool_name} with args: {tool_args}")

            if run_id:
                tracer.log_event(
                    run_id, "tool_call",
                    {"tool_name": tool_name, "tool_call_id": tool_call_id, "args": tool_args},
                )

            tool_found = False
            tool_start = time.time()
            for tool in sales_tools:
                if tool.name == tool_name:
                    tool_found = True
                    try:
                        result = tool.invoke({**tool_args, "injected_business_context": business_context})
                        latency_ms = (time.time() - tool_start) * 1000
                        if run_id:
                            tracer.log_event(
                                run_id, "tool_result",
                                {"tool_name": tool_name, "success": True, "latency_ms": latency_ms},
                            )
                        tool_messages.append(
                            ToolMessage(content=str(result), tool_call_id=tool_call_id, name=tool_name)
                        )
                    except Exception as e:
                        error_msg = str(e)
                        logging.error(f"[TOOL] Error executing {tool_name}: {error_msg}")
                        if run_id:
                            tracer.log_event(
                                run_id, "tool_result",
                                {"tool_name": tool_name, "success": False, "error": error_msg},
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
        """Run sales agent: intent filter → LLM + tool loop. Return AgentOutput."""
        run_id = str(uuid.uuid4())
        start_time = time.time()
        business_id = business_context.get("business_id") if business_context else None

        try:
            tracer.start_run(
                run_id=run_id,
                user_id=wa_id,
                message_id=message_id,
                business_id=str(business_id) if business_id else None,
            )

            business_info = business_config_service.get_business_info(business_context)
            business_name = business_info.get("business_name", "el negocio")

            # Lightweight intent filter — block out-of-scope messages early
            intent = self._classify_intent(message_body, business_name)
            logging.info(f"[SALES_AGENT] Intent classified: {intent}")

            if intent == _OUT_OF_SCOPE_INTENT:
                tracer.end_run(run_id, success=True, latency_ms=(time.time() - start_time) * 1000)
                return {
                    "agent_type": self.agent_type,
                    "message": _OUT_OF_SCOPE_RESPONSE,
                    "state_update": {"active_agents": ["sales_agent"]},
                }

            # Saludo inbound fijo (sin LLM): siempre que el intent sea GREET, aunque ya haya historial.
            if intent == "GREET":
                greeting = (
                    _inbound_greeting(name, business_name)
                    if not conversation_history
                    else _inbound_greeting_followup(name, business_name)
                )
                conversation_service.store_conversation_message(
                    wa_id, greeting, "assistant", business_id=business_id
                )
                tracer.end_run(run_id, success=True, latency_ms=(time.time() - start_time) * 1000)
                return {
                    "agent_type": self.agent_type,
                    "message": greeting,
                    "state_update": {"active_agents": ["sales_agent"]},
                }

            current_date_obj = date.today()
            current_date = f"{current_date_obj.day}/{current_date_obj.month}/{current_date_obj.year}"
            current_year = current_date_obj.year

            system_prompt = self.get_system_prompt(
                business_context=business_context,
                current_date=current_date,
                current_year=current_year,
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
                logging.info(f"[SALES_AGENT] Iteration {iteration}/{max_iterations}")
                response = self.llm_with_tools.invoke(messages)
                messages.append(response)

                if hasattr(response, "tool_calls") and response.tool_calls:
                    tool_messages = self._execute_tool_calls(response.tool_calls, business_context, run_id)
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
                wa_id, final_response_text, "assistant", business_id=business_id
            )

            tracer.end_run(run_id, success=True, latency_ms=(time.time() - start_time) * 1000)

            return {
                "agent_type": self.agent_type,
                "message": final_response_text,
                "state_update": {"active_agents": ["sales_agent"]},
            }

        except Exception as e:
            error_msg = str(e)
            logging.error(f"[SALES_AGENT] Error: {error_msg}")
            tracer.end_run(run_id, success=False, error=error_msg, latency_ms=(time.time() - start_time) * 1000)
            return {
                "agent_type": self.agent_type,
                "message": "Lo siento, tuve un problema procesando tu mensaje. ¿Podrías intentar de nuevo?",
                "state_update": {},
            }
