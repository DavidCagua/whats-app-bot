import os
import logging
import json
from typing import List, Dict, Optional
from datetime import datetime, date
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from .calendar_tools import calendar_tools
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

    def _execute_tool_calls(self, tool_calls: List, business_context: Optional[Dict] = None) -> List[ToolMessage]:
        """
        Execute tool calls and return proper ToolMessage objects.

        Args:
            tool_calls: List of tool calls from the LLM
            business_context: Business context to pass to tools

        Returns:
            List of ToolMessage objects with tool results
        """
        tool_messages = []

        for tool_call in tool_calls:
            tool_name = tool_call['name']
            tool_args = tool_call['args']
            tool_call_id = tool_call.get('id', 'unknown')

            logging.warning(f"[TOOL] Executing tool: {tool_name} with args: {tool_args}")

            # Find and execute the tool
            tool_found = False
            for tool in calendar_tools:
                if tool.name == tool_name:
                    tool_found = True
                    try:
                        # Inject business_context into tool args (manual approach since we're not using create_agent())
                        # Note: Don't use underscore prefix - LangChain filters those out
                        tool_args_with_context = {**tool_args, "injected_business_context": business_context}
                        logging.warning(f"[DEBUG] Invoking {tool_name} with args: {list(tool_args_with_context.keys())}, business_context type: {type(business_context)}")
                        result = tool.invoke(tool_args_with_context)
                        logging.warning(f"[TOOL] Tool {tool_name} executed successfully")

                        # Create proper ToolMessage
                        tool_messages.append(ToolMessage(
                            content=str(result),
                            tool_call_id=tool_call_id,
                            name=tool_name
                        ))
                    except Exception as e:
                        logging.error(f"[TOOL] Error executing tool {tool_name}: {str(e)}")
                        tool_messages.append(ToolMessage(
                            content=f"Error: {str(e)}",
                            tool_call_id=tool_call_id,
                            name=tool_name,
                            additional_kwargs={"error": True}
                        ))
                    break

            if not tool_found:
                logging.warning(f"[TOOL] Tool '{tool_name}' not found")
                tool_messages.append(ToolMessage(
                    content=f"Tool {tool_name} not found",
                    tool_call_id=tool_call_id,
                    name=tool_name,
                    additional_kwargs={"error": True}
                ))

        return tool_messages

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

            # Agent loop: Allow multi-turn tool calling (max 5 iterations to prevent infinite loops)
            max_iterations = 5
            iteration = 0

            while iteration < max_iterations:
                iteration += 1
                logging.info(f"[AGENT] Iteration {iteration}/{max_iterations}")

                # Generate response with tool calling
                response = self.llm_with_tools.invoke(messages)

                # Add AI response to messages
                messages.append(response)

                # Check if there are tool calls
                if hasattr(response, 'tool_calls') and response.tool_calls:
                    logging.warning(f"[TOOL] Tool calls detected: {len(response.tool_calls)} tools")

                    # Execute tools and get ToolMessage objects
                    tool_messages = self._execute_tool_calls(response.tool_calls, business_context)

                    # Add tool results to messages
                    messages.extend(tool_messages)

                    # Continue loop to let LLM process tool results
                    continue
                else:
                    # No more tool calls, we have the final response
                    logging.warning(f"[AGENT] Agent completed after {iteration} iterations")
                    final_response_text = response.content
                    break

            # If we exhausted iterations, use the last response
            if iteration >= max_iterations:
                logging.warning(f"[AGENT] Max iterations reached, using last response")
                final_response_text = response.content if hasattr(response, 'content') else "Lo siento, necesito más tiempo para procesar tu solicitud."
            logging.warning(f"[AGENT] Final response: {final_response_text}")
            # Store the conversation (scoped to business)
            logging.info(f"[STORAGE] Storing conversation for user {wa_id}")
            self.add_to_conversation_history(wa_id, "user", message_body, business_id=business_id)
            self.add_to_conversation_history(wa_id, "assistant", final_response_text, business_id=business_id)
            logging.info(f"[SUCCESS] Conversation stored successfully")

            return final_response_text

        except Exception as e:
            logging.error(f"Error generating response: {e}")
            import traceback
            traceback.print_exc()
            return f"I'm sorry, I encountered an error while processing your request. Please try again later. Error: {str(e)}"

    def process_calendar_request(self, message: str) -> str:
        """
        Process calendar-related requests specifically.

        Note: This method is deprecated. Use generate_response() instead,
        which now handles calendar operations through the unified agent loop.

        Args:
            message: The user's message

        Returns:
            Response string
        """
        logging.warning("[DEPRECATED] process_calendar_request is deprecated, redirecting to generate_response")
        return self.generate_response(message, wa_id="unknown", name="User")

# Global instance
langchain_service = LangChainService()