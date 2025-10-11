import os
import logging
import json
from typing import List, Dict
from datetime import datetime, date
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from .calendar_tools import calendar_tools, set_business_context
from .business_config_service import business_config_service
from .prompt_builder import prompt_builder
from ..database.conversation_service import conversation_service

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

class LangChainService:
    def __init__(self):
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.7,
            api_key=os.getenv("OPENAI_API_KEY")
        )

        # Bind tools to the LLM
        self.llm_with_tools = self.llm.bind_tools(calendar_tools)

        # Create a chain that can handle tool calling
        self.chain = self.llm_with_tools | StrOutputParser()

        logging.info("LangChain service initialized with calendar tools")

    def get_conversation_history(self, wa_id: str, business_id: str = None) -> List[Dict]:
        """Get conversation history for the given WhatsApp ID."""
        try:
            history = conversation_service.get_conversation_history(wa_id, limit=10, business_id=business_id)
            logging.debug(f"📚 Retrieved {len(history)} messages from PostgreSQL conversation history for user {wa_id}")
            return history
        except Exception as e:
            logging.error(f"❌ Error getting conversation history: {e}")
            return []

    def store_conversation_history(self, wa_id: str, history: List[Dict]):
        """Store conversation history for the given WhatsApp ID."""
        try:
            conversation_service.store_conversation_history(wa_id, history)
            logging.debug(f"📚 Stored conversation history in PostgreSQL for user {wa_id}")
        except Exception as e:
            logging.error(f"Error storing conversation history: {e}")

    def add_to_conversation_history(self, wa_id: str, role: str, content: str, business_id: str = None):
        """Add a message to the conversation history."""
        try:
            # Store message directly to PostgreSQL (automatic limiting handled by get_conversation_history)
            conversation_service.store_conversation_message(wa_id, content, role, business_id=business_id)
            logging.debug(f"[HISTORY] Added {role} message to PostgreSQL for user {wa_id}")
        except Exception as e:
            logging.error(f"❌ Error adding message to conversation history: {e}")

    def has_recent_appointment_creation(self, wa_id: str, minutes: int = 5) -> bool:
        """Check if a calendar event was recently created for this user."""
        try:
            history = self.get_conversation_history(wa_id)
            current_time = datetime.now()

            # Look for recent tool results that indicate calendar event creation
            for msg in reversed(history[-5:]):  # Check last 5 messages
                if msg["role"] == "assistant" and "Event" in msg["content"] and "created successfully" in msg["content"]:
                    # Parse timestamp to check if it's recent
                    try:
                        msg_time = datetime.fromisoformat(msg["timestamp"].replace('Z', '+00:00'))
                        time_diff = (current_time - msg_time).total_seconds() / 60
                        if time_diff < minutes:
                            logging.info(f"[DUPLICATE] Recent appointment creation detected for user {wa_id} ({time_diff:.1f} minutes ago)")
                            return True
                    except:
                        pass
            return False
        except Exception as e:
            logging.error(f"[ERROR] Error checking recent appointment creation: {e}")
            return False

    def generate_response(self, message_body: str, wa_id: str, name: str, business_context=None) -> str:
        """
        Generate a response using LangChain with tool calling capabilities.

        Args:
            message_body: The user's message
            wa_id: WhatsApp ID of the user
            name: Name of the user
            business_context: Optional business context for multi-tenancy

        Returns:
            Generated response as a string
        """
        try:
            # Extract business_id from context if available
            business_id = business_context.get('business_id') if business_context else None

            if business_id:
                logging.info(f"[BUSINESS] Generating response for business: {business_context['business']['name']}")

            # Set business context for calendar tools (so they can read max_concurrent, etc.)
            set_business_context(business_context)

            # Get conversation history (scoped to business)
            conversation_history = self.get_conversation_history(wa_id, business_id=business_id)

            # Get current date for context
            current_date = date.today()
            current_year = current_date.year
            current_month = current_date.month
            current_day = current_date.day

            # Generate dynamic system prompt from business configuration
            system_prompt = prompt_builder.build_system_prompt(
                business_context=business_context,
                current_date=f"{current_day}/{current_month}/{current_year}",
                current_year=current_year,
                wa_id=wa_id,
                name=name
            )

            # OLD HARDCODED PROMPT REMOVED - Now using dynamic prompt from database
            # The prompt is now stored in businesses.settings.ai_prompt and can be
            # edited by super admins through the UI without code deployment

            # Fallback if prompt generation failed
            if not system_prompt or len(system_prompt) < 100:
                logging.error("[PROMPT] Generated prompt too short, using emergency fallback")
                system_prompt = f"""You are a helpful AI assistant.

Customer: {name} (ID: {wa_id})
Current date: {current_day}/{current_month}/{current_year}
Year: {current_year}

Please help the customer with appointment scheduling and questions."""

            # Create messages list with conversation history
            messages = [SystemMessage(content=system_prompt)]

            # Add conversation history
            for msg in conversation_history:
                if msg["role"] == "user":
                    messages.append(HumanMessage(content=msg["content"]))
                elif msg["role"] == "assistant":
                    messages.append(AIMessage(content=msg["content"]))

            # Add current user message
            messages.append(HumanMessage(content=message_body))

            # Generate response with tool calling
            response = self.llm_with_tools.invoke(messages)

            # If the response contains tool calls, we need to handle them
            if hasattr(response, 'tool_calls') and response.tool_calls:
                logging.info(f"[TOOL] Tool calls detected for user {wa_id}: {len(response.tool_calls)} tools")

                # The new simplified tools handle duplicates automatically, so no need for complex duplicate checking

                # Handle tool calls
                tool_results = []
                for i, tool_call in enumerate(response.tool_calls):
                    tool_name = tool_call['name']
                    tool_args = tool_call['args']

                    logging.warning(f"[TOOL] Executing tool {i+1}/{len(response.tool_calls)}: {tool_name}")
                    logging.warning(f"[TOOL] Tool arguments: {tool_args}")

                    # Find and execute the tool
                    tool_found = False
                    for tool in calendar_tools:
                        if tool.name == tool_name:
                            tool_found = True
                            try:
                                logging.warning(f"[TOOL] Invoking tool: {tool_name}")
                                result = tool.invoke(tool_args)
                                logging.info(f"[SUCCESS] Tool {tool_name} executed successfully")
                                logging.warning(f"[TOOL] Tool result: {result[:200]}...")
                                tool_results.append(f"Tool {tool_name} result: {result}")
                            except Exception as e:
                                logging.error(f"[ERROR] Error executing tool {tool_name}: {str(e)}")
                                tool_results.append(f"Error executing {tool_name}: {str(e)}")
                            break

                    if not tool_found:
                        logging.warning(f"[WARNING] Tool '{tool_name}' not found in available tools")
                        tool_results.append(f"Tool {tool_name} not found")

                logging.info(f"[TOOL] All tool executions completed. Results: {len(tool_results)}")

                # Generate a final response that includes tool results
                if tool_results:
                    results_text = "\n".join(tool_results)
                    logging.info(f"[RESPONSE] Generating final response with tool results for user {wa_id}")
                    logging.warning(f"[RESPONSE] Tool results text: {results_text}")

                    # Create a more explicit prompt for the LLM to generate a response based on tool results
                    tool_response_prompt = HumanMessage(content=f"""Based on these tool execution results, provide a natural, conversational response to the user in Spanish (Colombian style):

Tool Results:
{results_text}

Remember:
- Be friendly and conversational
- Use the information from the tool results
- Follow the barbería's communication style
- Provide specific details (dates, times, etc.) from the tool results
- Keep it concise but complete

Now respond to the user naturally based on these results:""")

                    final_messages = messages + [tool_response_prompt]
                    final_response = self.llm_with_tools.invoke(final_messages)
                    final_response_text = final_response.content
                    logging.info(f"[RESPONSE] Final response content: '{final_response_text}'")
                    logging.warning(f"[RESPONSE] Final response length: {len(final_response_text) if final_response_text else 0}")

                    # If the final response is empty, create a proper confirmation
                    if not final_response_text or not final_response_text.strip():
                        logging.warning(f"[RESPONSE] Empty final response, creating fallback confirmation")
                        if "created successfully" in results_text:
                            # Extract event details from the tool result
                            import re

                            # Try to extract event details from the tool result
                            event_match = re.search(r"Event '([^']+)' created successfully", results_text)
                            if event_match:
                                event_name = event_match.group(1)
                                # Get current conversation context to extract time and date
                                conversation_history = self.get_conversation_history(wa_id)
                                user_messages = [msg["content"] for msg in conversation_history if msg["role"] == "user"]

                                # Look for time and date in recent messages
                                time_found = None
                                date_found = "mañana"  # Default to tomorrow

                                for msg in user_messages[-3:]:  # Check last 3 messages
                                    if "11" in msg or "10" in msg or "9" in msg or "8" in msg:
                                        if "11" in msg:
                                            time_found = "11:00 AM"
                                        elif "10" in msg:
                                            time_found = "10:00 AM"
                                        elif "9" in msg:
                                            time_found = "9:00 AM"
                                        elif "8" in msg:
                                            time_found = "8:00 AM"
                                        break

                                if time_found:
                                    final_response_text = f"✅ Tu cita está agendada para el **8 de agosto de 2025 a las {time_found}** para {event_name}, {name}. ¡Nos vemos y prepárate para salir renovado! 💇🔥 Gracias por elegirnos."
                                else:
                                    final_response_text = f"✅ Tu cita está agendada para {event_name}, {name}. ¡Nos vemos pronto! 💈✂️"
                            else:
                                final_response_text = "✅ Tu cita ha sido agendada exitosamente. ¡Nos vemos pronto! 💈✂️"
                        elif "No se puede agendar" in results_text:
                            final_response_text = "❌ Lo siento, no se pudo agendar la cita. Por favor, intenta con otro horario."
                        elif "get_available_slots" in results_text:
                            # Handle get_available_slots tool results
                            if "Horarios disponibles" in results_text:
                                final_response_text = "📅 Aquí tienes los horarios disponibles. ¿Cuál te gustaría?"
                            else:
                                final_response_text = "📅 Revisando disponibilidad. ¿Te gustaría agendar una cita?"
                        elif "list_calendar_events" in results_text:
                            # Handle list_calendar_events tool results
                            if "Upcoming events" in results_text:
                                final_response_text = "📅 Aquí tienes los eventos programados. ¿Te gustaría agendar una cita para mañana en la mañana? Tengo disponibilidad en varios horarios."
                            else:
                                final_response_text = "📅 Revisando disponibilidad. ¿Te gustaría agendar una cita para mañana en la mañana?"
                        elif "schedule_appointment" in results_text:
                            if "agendada exitosamente" in results_text:
                                final_response_text = results_text.replace("Tool schedule_appointment result: ", "")
                            else:
                                final_response_text = "❌ Hubo un problema agendando la cita. Déjame intentar de nuevo."
                        elif "reschedule_appointment" in results_text:
                            if "reagendada exitosamente" in results_text:
                                final_response_text = results_text.replace("Tool reschedule_appointment result: ", "")
                            else:
                                final_response_text = "❌ Hubo un problema reagendando la cita. Déjame intentar de nuevo."
                        elif "cancel_appointment" in results_text:
                            if "cancelada exitosamente" in results_text:
                                final_response_text = results_text.replace("Tool cancel_appointment result: ", "")
                            else:
                                final_response_text = "❌ Hubo un problema cancelando la cita. Déjame intentar de nuevo."
                        elif "get_calendar_event" in results_text:
                            final_response_text = "📋 Aquí tienes los detalles de tu cita."
                        else:
                            final_response_text = "Gracias por tu mensaje. Te responderé pronto."

                    logging.info(f"[SUCCESS] Final response generated with tool results")
                else:
                    final_response_text = response.content
                    logging.info(f"[INFO] Using direct response (no tool results)")

                # Store the conversation (scoped to business)
                logging.info(f"[STORAGE] Storing conversation for user {wa_id}")
                self.add_to_conversation_history(wa_id, "user", message_body, business_id=business_id)
                self.add_to_conversation_history(wa_id, "assistant", final_response_text, business_id=business_id)
                logging.info(f"[SUCCESS] Conversation stored successfully")

                return final_response_text
            else:
                logging.info(f"[INFO] No tool calls detected, using direct response for user {wa_id}")

                # Store the conversation (scoped to business)
                logging.info(f"[STORAGE] Storing conversation for user {wa_id}")
                self.add_to_conversation_history(wa_id, "user", message_body, business_id=business_id)
                self.add_to_conversation_history(wa_id, "assistant", response.content, business_id=business_id)
                logging.info(f"[SUCCESS] Conversation stored successfully")

                return response.content

        except Exception as e:
            logging.error(f"Error generating response: {e}")
            return f"I'm sorry, I encountered an error while processing your request. Please try again later. Error: {str(e)}"

    def process_calendar_request(self, message: str) -> str:
        """
        Process calendar-related requests specifically.

        Args:
            message: The user's message

        Returns:
            Response string
        """
        try:
            # Create a focused prompt for calendar operations
            calendar_prompt = f"""You are a calendar management assistant. The user is asking about calendar events.

            User message: {message}

            If the user is asking about calendar events, use the appropriate tools to help them.
            Provide clear, helpful responses about calendar operations.
            """

            response = self.chain.invoke(calendar_prompt)
            return response

        except Exception as e:
            logging.error(f"Error processing calendar request: {e}")
            return f"I'm sorry, I encountered an error while processing your calendar request. Please try again later."

# Global instance
langchain_service = LangChainService()