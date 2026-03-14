"""
LangChain Service - Backward compatibility layer.

The agent loop and tool execution now live in app/agents/booking_agent.py.
This service delegates to conversation_manager for backward compatibility
with tests and scripts that call langchain_service.generate_response().
"""

import logging
from typing import List, Dict, Optional
from datetime import datetime

from ..database.conversation_service import conversation_service
from ..orchestration.conversation_manager import conversation_manager


class LangChainService:
    """
    Backward-compatible facade. Agent logic lives in BookingAgent.
    generate_response delegates to conversation_manager.
    """

    def __init__(self):
        # Preserve llm/llm_with_tools for tests that check these attributes
        from langchain_openai import ChatOpenAI
        from .calendar_tools import calendar_tools
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.7,
            api_key=__import__("os").getenv("OPENAI_API_KEY"),
        )
        self.llm_with_tools = self.llm.bind_tools(calendar_tools)
        logging.info("LangChain service (backward compat) initialized")

    def get_conversation_history(self, wa_id: str, business_id: str = None) -> List[Dict]:
        """Get conversation history for the given WhatsApp ID."""
        try:
            history = conversation_service.get_conversation_history(
                wa_id, limit=10, business_id=business_id
            )
            return history
        except Exception as e:
            logging.error(f"Error getting conversation history: {e}")
            return []

    def store_conversation_history(self, wa_id: str, history: List[Dict]):
        """Store conversation history."""
        try:
            conversation_service.store_conversation_history(wa_id, history)
        except Exception as e:
            logging.error(f"Error storing conversation history: {e}")

    def add_to_conversation_history(
        self, wa_id: str, role: str, content: str, business_id: str = None
    ):
        """Add a message to the conversation history."""
        try:
            conversation_service.store_conversation_message(
                wa_id, content, role, business_id=business_id
            )
        except Exception as e:
            logging.error(f"Error adding message: {e}")

    def has_recent_appointment_creation(self, wa_id: str, minutes: int = 5) -> bool:
        """Check if a calendar event was recently created for this user."""
        try:
            history = self.get_conversation_history(wa_id)
            current_time = datetime.now()
            for msg in reversed(history[-5:]):
                content = msg.get("content") or msg.get("message", "")
                if msg.get("role") == "assistant" and "agendada" in content.lower():
                    return True
            return False
        except Exception as e:
            logging.error(f"Error checking recent appointment: {e}")
            return False

    def generate_response(
        self,
        message_body: str,
        wa_id: str,
        name: str,
        business_context=None,
        message_id: Optional[str] = None,
    ) -> str:
        """
        Delegate to conversation_manager (BookingAgent). Backward compat.
        """
        return conversation_manager.process(
            message_body=message_body,
            wa_id=wa_id,
            name=name,
            business_context=business_context,
            message_id=message_id,
        )

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