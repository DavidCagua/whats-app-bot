"""
Booking agent: appointment scheduling and availability operations.
Wraps the logic from langchain_service (LLM + booking tools).

Confirmation flow (FASE 2 + 3):
  - If business requires confirmation (default: True), the LLM calls prepare_booking
    instead of schedule_appointment.
  - prepare_booking validates availability and returns a pending proposal (JSON).
  - The agent stores the proposal in booking_context.pending_booking via state_update.
  - Next turn: intent validator detects CONFIRM/CANCEL and the agent handles it
    WITHOUT running the LLM+tool loop.
"""

import json
import logging
import os
import time
import uuid
from datetime import date
from typing import Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from .base_agent import AgentOutput, BaseAgent
from .intent_validator import classify_intent
from ..services.business_config_loader import business_config_loader
from ..database.booking_service import booking_service
from ..services.calendar_tools import calendar_tools, calendar_tools_with_confirmation
from ..services.prompt_builder import prompt_builder
from ..database.conversation_service import conversation_service
from ..services.tracing import tracer


class BookingAgent(BaseAgent):
    """Agent for booking appointments via in-house booking tools."""

    agent_type = "booking"

    def __init__(self):
        # Lazy: constructed on first property access so importing this
        # module doesn't require OPENAI_API_KEY. Alembic, tests and
        # scripts can load app.* without tripping LLM init.
        self._llm = None
        self._llm_with_tools = None
        logging.info("[BOOKING_AGENT] Initialized with booking tools (LLM lazy)")

    @property
    def llm(self) -> ChatOpenAI:
        if self._llm is None:
            self._llm = ChatOpenAI(
                model="gpt-4o-mini",
                temperature=0.7,
                api_key=os.getenv("OPENAI_API_KEY"),
            )
        return self._llm

    @property
    def llm_with_tools(self):
        if self._llm_with_tools is None:
            self._llm_with_tools = self.llm.bind_tools(calendar_tools)
        return self._llm_with_tools

    def get_system_prompt(
        self,
        business_context: Optional[Dict],
        current_date: str,
        current_year: int,
        wa_id: str,
        name: str,
    ) -> str:
        return prompt_builder.build_system_prompt(
            business_context=business_context,
            current_date=current_date,
            current_year=current_year,
            wa_id=wa_id,
            name=name,
        )

    def get_tools(self):
        return calendar_tools

    def _execute_tool_calls(
        self,
        tool_calls: List,
        business_context: Optional[Dict],
        active_tools: List,
        run_id: Optional[str],
    ) -> List[ToolMessage]:
        """Execute tool calls and return ToolMessage objects."""
        tool_messages = []
        tool_map = {t.name: t for t in active_tools}

        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            tool_call_id = tool_call.get("id", "unknown")
            logging.warning(f"[TOOL] Executing tool: {tool_name} with args: {tool_args}")

            if run_id:
                tracer.log_event(
                    run_id,
                    "tool_call",
                    {"tool_name": tool_name, "tool_call_id": tool_call_id, "args": tool_args},
                )

            tool_start = time.time()
            tool = tool_map.get(tool_name)
            if tool:
                try:
                    tool_args_with_context = {**tool_args, "injected_business_context": business_context}
                    result = tool.invoke(tool_args_with_context)
                    tool_latency = (time.time() - tool_start) * 1000
                    if run_id:
                        tracer.log_event(
                            run_id,
                            "tool_result",
                            {"tool_name": tool_name, "success": True, "latency_ms": tool_latency},
                        )
                    tool_messages.append(
                        ToolMessage(content=str(result), tool_call_id=tool_call_id, name=tool_name)
                    )
                except Exception as e:
                    error_msg = str(e)
                    logging.error(f"[TOOL] Error executing tool {tool_name}: {error_msg}")
                    if run_id:
                        tracer.log_event(
                            run_id,
                            "tool_result",
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
            else:
                tool_messages.append(
                    ToolMessage(
                        content=f"Tool {tool_name} not found",
                        tool_call_id=tool_call_id,
                        name=tool_name,
                        additional_kwargs={"error": True},
                    )
                )
        return tool_messages

    def _extract_pending_booking(self, messages: List) -> Optional[Dict]:
        """
        Scan tool messages for a prepare_booking result.
        Returns the parsed pending booking dict or None.
        """
        for msg in reversed(messages):
            if isinstance(msg, ToolMessage) and msg.name == "prepare_booking":
                try:
                    data = json.loads(msg.content)
                    if data.get("status") == "pending_confirmation":
                        return data
                except (json.JSONDecodeError, AttributeError):
                    pass
        return None

    def _handle_confirm(
        self,
        pending: Dict,
        wa_id: str,
        business_id: str,
    ) -> str:
        """Create the booking from a pending proposal. Returns user-facing message."""
        booking = booking_service.create_booking({
            "business_id": business_id,
            "customer_id": pending.get("customer_id"),
            "service_name": pending.get("service_name", "Cita"),
            "start_at": pending["start_at"],
            "end_at": pending["end_at"],
            "status": "confirmed",
            "notes": pending.get("notes"),
            "created_via": "whatsapp",
            "staff_member_id": pending.get("staff_member_id"),
        })

        if not booking:
            return (
                "❌ No se pudo crear la cita — el horario puede ya no estar disponible. "
                "¿Quieres intentar con otro horario?"
            )

        display = pending.get("display", {})
        display_date = display.get("date", "")
        display_time = display.get("time", "")
        service = display.get("service", pending.get("service_name", "Cita"))
        staff_name = display.get("professional", pending.get("_staff_name", ""))
        prof_line = f"👤 Profesional: *{staff_name}*\n" if staff_name else ""

        logging.warning(f"[BOOKING_AGENT] Booking confirmed: {booking['id']} for {wa_id}")
        return (
            f"✅ ¡Cita confirmada!\n\n"
            f"📋 *{service}*\n"
            f"{prof_line}"
            f"📅 {display_date} a las {display_time}\n\n"
            "Si necesitas cancelar o reagendar, solo dímelo. ¡Hasta pronto!"
        )

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
        """Run booking agent. Returns AgentOutput."""
        run_id = str(uuid.uuid4())
        start_time = time.time()
        business_id = str(business_context.get("business_id")) if business_context else None

        try:
            tracer.start_run(
                run_id=run_id,
                user_id=wa_id,
                message_id=message_id,
                business_id=business_id,
            )

            # --- Load session booking context ---
            booking_context = (session or {}).get("booking_context") or {}
            pending_booking = booking_context.get("pending_booking") or None

            # --- Business config ---
            require_confirmation = business_config_loader.requires_confirmation(business_id)

            # --- Intent classification ---
            intent = classify_intent(message_body)
            logging.info(f"[BOOKING_AGENT] Intent: {intent} | pending: {bool(pending_booking)}")

            # --- Confirmation flow ---
            if require_confirmation and pending_booking:
                if intent == "CONFIRM":
                    reply = self._handle_confirm(pending_booking, wa_id, business_id)
                    conversation_service.store_conversation_message(
                        wa_id, reply, "assistant", business_id=business_id
                    )
                    tracer.end_run(run_id, success=True, latency_ms=(time.time() - start_time) * 1000)
                    return {
                        "agent_type": self.agent_type,
                        "message": reply,
                        "state_update": {
                            "active_agents": ["booking_agent"],
                            "booking_context": {"pending_booking": None},
                        },
                    }

                if intent == "CANCEL":
                    reply = "Entendido, cancelo la propuesta. ¿En qué más te puedo ayudar?"
                    conversation_service.store_conversation_message(
                        wa_id, reply, "assistant", business_id=business_id
                    )
                    tracer.end_run(run_id, success=True, latency_ms=(time.time() - start_time) * 1000)
                    return {
                        "agent_type": self.agent_type,
                        "message": reply,
                        "state_update": {
                            "active_agents": ["booking_agent"],
                            "booking_context": {"pending_booking": None},
                        },
                    }

                # Any other message — clear pending and continue to LLM
                logging.info("[BOOKING_AGENT] Non-confirm/cancel with pending booking — clearing pending")
                pending_booking = None
                booking_context = {**booking_context, "pending_booking": None}

            # --- Build tool list ---
            active_tools = (
                calendar_tools_with_confirmation if require_confirmation else calendar_tools
            )
            llm_with_tools = self.llm.bind_tools(active_tools)

            # --- Build prompt ---
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

            if not system_prompt or len(system_prompt) < 100:
                system_prompt = (
                    f"You are a helpful AI assistant for appointment scheduling.\n"
                    f"Customer: {name} (ID: {wa_id})\n"
                    f"Current date: {current_date}\n"
                    "Please help the customer with scheduling and questions."
                )

            if require_confirmation:
                system_prompt += (
                    "\n\n---\n\n"
                    "IMPORTANTE: Para agendar una cita, usa SIEMPRE la herramienta `prepare_booking` "
                    "(no `schedule_appointment`). Esta herramienta valida la disponibilidad y propone la cita "
                    "al cliente sin crearla en el sistema. Presenta el resultado de forma amigable y "
                    "pide confirmación al cliente ('¿Confirmas la cita?'). "
                    "La cita se crea en el sistema solo cuando el cliente confirma."
                )

            # --- Build message list ---
            messages = [SystemMessage(content=system_prompt)]
            for msg in conversation_history:
                content = msg.get("content") or msg.get("message", "")
                if msg.get("role") == "user":
                    messages.append(HumanMessage(content=content))
                elif msg.get("role") == "assistant":
                    messages.append(AIMessage(content=content))
            messages.append(HumanMessage(content=message_body))

            # --- LLM + tool loop ---
            max_iterations = 5
            iteration = 0
            response = None

            while iteration < max_iterations:
                iteration += 1
                logging.info(f"[AGENT] Iteration {iteration}/{max_iterations}")
                response = llm_with_tools.invoke(messages)
                messages.append(response)

                if hasattr(response, "tool_calls") and response.tool_calls:
                    tool_messages = self._execute_tool_calls(
                        response.tool_calls, business_context, active_tools, run_id
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

            # --- Detect pending booking from prepare_booking result ---
            new_pending = self._extract_pending_booking(messages) if require_confirmation else None
            state_update: Dict = {"active_agents": ["booking_agent"]}

            if new_pending:
                state_update["booking_context"] = {"pending_booking": new_pending}
                logging.info(f"[BOOKING_AGENT] Pending booking stored for {wa_id}")
            elif booking_context.get("pending_booking") is None and not pending_booking:
                # Nothing pending — ensure booking_context is clean
                pass
            else:
                # We cleared pending at the top (user changed subject)
                state_update["booking_context"] = {"pending_booking": None}

            # --- Persist conversation ---
            conversation_service.store_conversation_message(
                wa_id, final_response_text, "assistant", business_id=business_id
            )

            tracer.end_run(run_id, success=True, latency_ms=(time.time() - start_time) * 1000)

            return {
                "agent_type": self.agent_type,
                "message": final_response_text,
                "state_update": state_update,
            }

        except Exception as e:
            error_msg = str(e)
            logging.error(f"[BOOKING_AGENT] Error: {error_msg}")
            tracer.end_run(
                run_id,
                success=False,
                error=error_msg,
                latency_ms=(time.time() - start_time) * 1000,
            )
            return {
                "agent_type": self.agent_type,
                "message": "Lo siento, tuve un problema procesando tu mensaje. ¿Podrías intentar de nuevo?",
                "state_update": {},
            }
