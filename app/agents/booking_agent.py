"""
Booking agent: appointment scheduling and availability operations.
Wraps the logic from langchain_service (LLM + booking tools).
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
from ..services.calendar_tools import calendar_tools
from ..services.prompt_builder import prompt_builder
from ..database.conversation_service import conversation_service
from ..services.tracing import tracer


class BookingAgent(BaseAgent):
    """Agent for booking appointments via in-house booking tools."""

    agent_type = "booking"

    def __init__(self):
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.7,
            api_key=os.getenv("OPENAI_API_KEY"),
        )
        self.llm_with_tools = self.llm.bind_tools(calendar_tools)
        logging.info("[BOOKING_AGENT] Initialized with booking tools")

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
        run_id: Optional[str],
    ) -> List[ToolMessage]:
        """Execute tool calls and return ToolMessage objects."""
        tool_messages = []
        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            tool_call_id = tool_call.get("id", "unknown")
            logging.warning(f"[TOOL] Executing tool: {tool_name} with args: {tool_args}")

            if run_id:
                tracer.log_event(
                    run_id, "tool_call", {"tool_name": tool_name, "tool_call_id": tool_call_id, "args": tool_args}
                )

            tool_found = False
            tool_start = time.time()
            for tool in calendar_tools:
                if tool.name == tool_name:
                    tool_found = True
                    try:
                        tool_args_with_context = {**tool_args, "injected_business_context": business_context}
                        result = tool.invoke(tool_args_with_context)
                        tool_latency = (time.time() - tool_start) * 1000
                        if run_id:
                            tracer.log_event(
                                run_id, "tool_result", {"tool_name": tool_name, "success": True, "latency_ms": tool_latency}
                            )
                        tool_messages.append(ToolMessage(content=str(result), tool_call_id=tool_call_id, name=tool_name))
                    except Exception as e:
                        error_msg = str(e)
                        logging.error(f"[TOOL] Error executing tool {tool_name}: {error_msg}")
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
        """Run booking agent: LLM + tool loop. Return AgentOutput."""
        run_id = str(uuid.uuid4())
        start_time = time.time()
        business_id = business_context.get("business_id") if business_context else None

        try:
            tracer.start_run(run_id=run_id, user_id=wa_id, message_id=message_id, business_id=str(business_id) if business_id else None)

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
                system_prompt = f"""You are a helpful AI assistant for appointment scheduling.
Customer: {name} (ID: {wa_id})
Current date: {current_date}
Please help the customer with scheduling and questions."""

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
                logging.info(f"[AGENT] Iteration {iteration}/{max_iterations}")
                response = self.llm_with_tools.invoke(messages)
                messages.append(response)

                if hasattr(response, "tool_calls") and response.tool_calls:
                    tool_messages = self._execute_tool_calls(
                        response.tool_calls, business_context, run_id
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
                wa_id, final_response_text, "assistant", business_id=business_id
            )

            tracer.end_run(run_id, success=True, latency_ms=(time.time() - start_time) * 1000)

            return {
                "agent_type": self.agent_type,
                "message": final_response_text,
                "state_update": {"active_agents": ["booking_agent"]},
            }

        except Exception as e:
            error_msg = str(e)
            logging.error(f"[BOOKING_AGENT] Error: {error_msg}")
            tracer.end_run(run_id, success=False, error=error_msg, latency_ms=(time.time() - start_time) * 1000)
            return {
                "agent_type": self.agent_type,
                "message": "Lo siento, tuve un problema procesando tu mensaje. ¿Podrías intentar de nuevo?",
                "state_update": {},
            }
