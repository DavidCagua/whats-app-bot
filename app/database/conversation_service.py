"""
Database service for managing conversation history.
Replaces the shelve-based storage with PostgreSQL.
"""

import logging
import uuid
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from sqlalchemy import desc
from .models import Conversation, ConversationAttachment, get_db_session

# Default business ID for backward compatibility
DEFAULT_BUSINESS_ID = "00000000-0000-0000-0000-000000000001"

class ConversationService:
    """Service for managing conversation history in PostgreSQL."""

    def __init__(self):
        """Initialize the conversation service. Schema is managed by Alembic,
        not by create_all at runtime — import is side-effect-free so unit
        tests don't need a live database."""
        logging.info("ConversationService initialized with PostgreSQL backend")

    def get_conversation_history(self, wa_id: str, limit: int = 10, business_id: Optional[str] = None) -> List[Dict]:
        """
        Get conversation history for a WhatsApp ID.

        Args:
            wa_id: WhatsApp ID
            limit: Maximum number of messages to return (default: 10)
            business_id: Business UUID (optional, defaults to default business)

        Returns:
            List of conversation messages as dictionaries
        """
        try:
            session: Session = get_db_session()

            # Use default business if not provided
            if business_id is None:
                business_id = DEFAULT_BUSINESS_ID

            # Get recent messages for this WhatsApp ID and business, ordered by timestamp
            conversations = session.query(Conversation)\
                .filter(
                    Conversation.whatsapp_id == wa_id,
                    Conversation.business_id == uuid.UUID(business_id)
                )\
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

    def store_conversation_message(self, wa_id: str, message: str, role: str,
                                   business_id: Optional[str] = None,
                                   whatsapp_number_id: Optional[str] = None,
                                   agent_type: Optional[str] = None) -> bool:
        """
        Store a single conversation message.

        Args:
            wa_id: WhatsApp ID
            message: Message content
            role: 'user' or 'assistant'
            business_id: Business UUID (optional, defaults to default business)
            whatsapp_number_id: WhatsApp number UUID (optional)
            agent_type: Source tag. ``'operator'`` for admin-console manual
                sends so planners can distinguish them from real bot turns.
                Leave None for bot-generated assistant messages.

        Returns:
            True if stored successfully, False otherwise
        """
        try:
            session: Session = get_db_session()

            # Use default business if not provided
            if business_id is None:
                business_id = DEFAULT_BUSINESS_ID

            # Create new conversation record
            conversation = Conversation(
                business_id=uuid.UUID(business_id),
                whatsapp_number_id=uuid.UUID(whatsapp_number_id) if whatsapp_number_id else None,
                whatsapp_id=wa_id,
                message=message,
                role=role,
                agent_type=agent_type,
            )

            session.add(conversation)
            session.commit()
            session.close()

            logging.debug(f"Stored {role} message for user {wa_id}")
            return True

        except Exception as e:
            logging.error(f"Error storing conversation message for {wa_id}: {e}")
            return False

    def store_conversation_message_with_attachments(
        self,
        wa_id: str,
        message_text: str,
        role: str,
        attachments: List[Dict],
        business_id: Optional[str] = None,
        whatsapp_number_id: Optional[str] = None,
        agent_type: Optional[str] = None,
    ) -> Optional[int]:
        """
        Store one conversation message and N attachment rows (provider URLs only).
        Used for voice/media: worker will later fill url and transcript.

        Returns:
            conversation id (conversations.id) for enqueueing media job, or None on failure.
        """
        if business_id is None:
            business_id = DEFAULT_BUSINESS_ID
        try:
            session: Session = get_db_session()
            message_type = "text"
            if attachments:
                first_type = attachments[0].get("type") or "document"
                message_type = first_type
            if not message_text.strip() and attachments:
                message_text = "[audio]" if message_type == "audio" else "[media]"

            conv = Conversation(
                business_id=uuid.UUID(business_id),
                whatsapp_number_id=uuid.UUID(whatsapp_number_id) if whatsapp_number_id else None,
                whatsapp_id=wa_id,
                message=message_text or "",
                message_type=message_type,
                role=role,
                agent_type=agent_type,
            )
            session.add(conv)
            session.flush()
            conv_id = conv.id

            for a in attachments:
                att = ConversationAttachment(
                    conversation_id=conv_id,
                    type=a.get("type") or "document",
                    content_type=a.get("content_type") or None,
                    provider_media_url=a.get("provider_media_url"),
                    provider_media_id=a.get("provider_media_id"),
                    url=a.get("url"),  # Outbound: we have URL up front; inbound: worker fills later
                    size_bytes=a.get("size"),
                    duration_sec=a.get("duration_sec"),
                    provider_metadata=a.get("provider_metadata") or {},
                )
                session.add(att)
            session.commit()
            session.close()
            logging.debug(f"Stored {role} message with {len(attachments)} attachments for user {wa_id}")
            return conv_id
        except Exception as e:
            logging.error(f"Error storing conversation message with attachments for {wa_id}: {e}")
            return None

    def store_conversation_history(self, wa_id: str, history: List[Dict],
                                   business_id: Optional[str] = None,
                                   whatsapp_number_id: Optional[str] = None) -> bool:
        """
        Store complete conversation history (for compatibility with existing code).
        This method updates the conversation by adding new messages.

        Args:
            wa_id: WhatsApp ID
            history: List of conversation messages
            business_id: Business UUID (optional, defaults to default business)
            whatsapp_number_id: WhatsApp number UUID (optional)

        Returns:
            True if stored successfully, False otherwise
        """
        try:
            # Use default business if not provided
            if business_id is None:
                business_id = DEFAULT_BUSINESS_ID

            # Get existing message count to avoid duplicates
            session: Session = get_db_session()
            existing_count = session.query(Conversation)\
                .filter(
                    Conversation.whatsapp_id == wa_id,
                    Conversation.business_id == uuid.UUID(business_id)
                )\
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
                            msg['role'],
                            business_id=business_id,
                            whatsapp_number_id=whatsapp_number_id
                        )
                        success = success and message_success

            logging.debug(f"Stored {len(new_messages)} new messages for user {wa_id}")
            return success

        except Exception as e:
            logging.error(f"Error storing conversation history for {wa_id}: {e}")
            return False

    def clear_conversation_history(self, wa_id: str, business_id: Optional[str] = None) -> bool:
        """
        Clear conversation history for a WhatsApp ID.

        Args:
            wa_id: WhatsApp ID
            business_id: Business UUID (optional, defaults to default business)

        Returns:
            True if cleared successfully, False otherwise
        """
        try:
            session: Session = get_db_session()

            # Use default business if not provided
            if business_id is None:
                business_id = DEFAULT_BUSINESS_ID

            # Delete all conversations for this WhatsApp ID and business
            deleted_count = session.query(Conversation)\
                .filter(
                    Conversation.whatsapp_id == wa_id,
                    Conversation.business_id == uuid.UUID(business_id)
                )\
                .delete()

            session.commit()
            session.close()

            logging.info(f"Cleared {deleted_count} messages for user {wa_id}")
            return True

        except Exception as e:
            logging.error(f"Error clearing conversation history for {wa_id}: {e}")
            return False

    def get_conversation_count(self, wa_id: str, business_id: Optional[str] = None) -> int:
        """
        Get total message count for a WhatsApp ID.

        Args:
            wa_id: WhatsApp ID
            business_id: Business UUID (optional, defaults to default business)

        Returns:
            Number of messages in conversation history
        """
        try:
            session: Session = get_db_session()

            # Use default business if not provided
            if business_id is None:
                business_id = DEFAULT_BUSINESS_ID

            count = session.query(Conversation)\
                .filter(
                    Conversation.whatsapp_id == wa_id,
                    Conversation.business_id == uuid.UUID(business_id)
                )\
                .count()
            session.close()
            return count

        except Exception as e:
            logging.error(f"Error getting conversation count for {wa_id}: {e}")
            return 0

# Global instance
conversation_service = ConversationService()