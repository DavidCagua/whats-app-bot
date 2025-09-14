"""
Database service for managing conversation history.
Replaces the shelve-based storage with PostgreSQL.
"""

import logging
from typing import List, Dict
from sqlalchemy.orm import Session
from sqlalchemy import desc
from .models import Conversation, get_db_session, create_tables

class ConversationService:
    """Service for managing conversation history in PostgreSQL."""

    def __init__(self):
        """Initialize the conversation service."""
        self.ensure_tables_exist()
        logging.info("ConversationService initialized with PostgreSQL backend")

    def ensure_tables_exist(self):
        """Ensure database tables are created."""
        try:
            create_tables()
            logging.info("Database tables verified/created successfully")
        except Exception as e:
            logging.error(f"Error creating database tables: {e}")
            raise

    def get_conversation_history(self, wa_id: str, limit: int = 10) -> List[Dict]:
        """
        Get conversation history for a WhatsApp ID.

        Args:
            wa_id: WhatsApp ID
            limit: Maximum number of messages to return (default: 10)

        Returns:
            List of conversation messages as dictionaries
        """
        try:
            session: Session = get_db_session()

            # Get recent messages for this WhatsApp ID, ordered by timestamp
            conversations = session.query(Conversation)\
                .filter(Conversation.whatsapp_id == wa_id)\
                .order_by(desc(Conversation.timestamp))\
                .limit(limit)\
                .all()

            # Convert to dictionaries and reverse order (oldest first)
            history = [conv.to_dict() for conv in reversed(conversations)]

            session.close()

            logging.debug(f"Retrieved {len(history)} messages from conversation history for user {wa_id}")
            return history

        except Exception as e:
            logging.error(f"Error getting conversation history for {wa_id}: {e}")
            return []

    def store_conversation_message(self, wa_id: str, message: str, role: str) -> bool:
        """
        Store a single conversation message.

        Args:
            wa_id: WhatsApp ID
            message: Message content
            role: 'user' or 'assistant'

        Returns:
            True if stored successfully, False otherwise
        """
        try:
            session: Session = get_db_session()

            # Create new conversation record
            conversation = Conversation(
                whatsapp_id=wa_id,
                message=message,
                role=role
            )

            session.add(conversation)
            session.commit()
            session.close()

            logging.debug(f"Stored {role} message for user {wa_id}")
            return True

        except Exception as e:
            logging.error(f"Error storing conversation message for {wa_id}: {e}")
            return False

    def store_conversation_history(self, wa_id: str, history: List[Dict]) -> bool:
        """
        Store complete conversation history (for compatibility with existing code).
        This method updates the conversation by adding new messages.

        Args:
            wa_id: WhatsApp ID
            history: List of conversation messages

        Returns:
            True if stored successfully, False otherwise
        """
        try:
            # Get existing message count to avoid duplicates
            session: Session = get_db_session()
            existing_count = session.query(Conversation)\
                .filter(Conversation.whatsapp_id == wa_id)\
                .count()
            session.close()

            # Only store new messages (those beyond existing_count)
            new_messages = history[existing_count:] if existing_count < len(history) else []

            success = True
            for msg in new_messages:
                if isinstance(msg, dict) and 'role' in msg:
                    # Handle both 'message' and 'content' field names for compatibility
                    message_content = msg.get('message') or msg.get('content', '')
                    if message_content:
                        message_success = self.store_conversation_message(
                            wa_id,
                            message_content,
                            msg['role']
                        )
                        success = success and message_success

            logging.debug(f"Stored {len(new_messages)} new messages for user {wa_id}")
            return success

        except Exception as e:
            logging.error(f"Error storing conversation history for {wa_id}: {e}")
            return False

    def clear_conversation_history(self, wa_id: str) -> bool:
        """
        Clear conversation history for a WhatsApp ID.

        Args:
            wa_id: WhatsApp ID

        Returns:
            True if cleared successfully, False otherwise
        """
        try:
            session: Session = get_db_session()

            # Delete all conversations for this WhatsApp ID
            deleted_count = session.query(Conversation)\
                .filter(Conversation.whatsapp_id == wa_id)\
                .delete()

            session.commit()
            session.close()

            logging.info(f"Cleared {deleted_count} messages for user {wa_id}")
            return True

        except Exception as e:
            logging.error(f"Error clearing conversation history for {wa_id}: {e}")
            return False

    def get_conversation_count(self, wa_id: str) -> int:
        """
        Get total message count for a WhatsApp ID.

        Args:
            wa_id: WhatsApp ID

        Returns:
            Number of messages in conversation history
        """
        try:
            session: Session = get_db_session()
            count = session.query(Conversation)\
                .filter(Conversation.whatsapp_id == wa_id)\
                .count()
            session.close()
            return count

        except Exception as e:
            logging.error(f"Error getting conversation count for {wa_id}: {e}")
            return 0

# Global instance
conversation_service = ConversationService()