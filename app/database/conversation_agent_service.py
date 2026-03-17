"""
Service for per-conversation agent enable/disable overrides.
Default behavior: if no row exists for (business_id, whatsapp_id), agent is enabled.
"""

import logging
import uuid
from typing import Optional
from sqlalchemy.orm import Session

from .models import ConversationAgentSetting, get_db_session


class ConversationAgentService:
    """Service for conversation-level agent settings."""

    def get_agent_enabled(self, business_id: str, whatsapp_id: str) -> bool:
        """Return True if agent is enabled for the conversation."""
        try:
            session: Session = get_db_session()
            row = (
                session.query(ConversationAgentSetting)
                .filter(
                    ConversationAgentSetting.business_id == uuid.UUID(business_id),
                    ConversationAgentSetting.whatsapp_id == whatsapp_id,
                )
                .first()
            )
            session.close()
            if row is None:
                return True
            return bool(row.agent_enabled)
        except Exception as e:
            logging.error(f"[CONVERSATION_AGENT] Error reading setting: {e}")
            return True

    def set_agent_enabled(self, business_id: str, whatsapp_id: str, agent_enabled: bool) -> Optional[dict]:
        """Upsert setting for the conversation. Returns dict or None on failure."""
        try:
            session: Session = get_db_session()
            row = (
                session.query(ConversationAgentSetting)
                .filter(
                    ConversationAgentSetting.business_id == uuid.UUID(business_id),
                    ConversationAgentSetting.whatsapp_id == whatsapp_id,
                )
                .first()
            )
            if row is None:
                row = ConversationAgentSetting(
                    business_id=uuid.UUID(business_id),
                    whatsapp_id=whatsapp_id,
                    agent_enabled=bool(agent_enabled),
                )
                session.add(row)
            else:
                row.agent_enabled = bool(agent_enabled)
            session.commit()
            result = row.to_dict()
            session.close()
            return result
        except Exception as e:
            logging.error(f"[CONVERSATION_AGENT] Error writing setting: {e}")
            return None


conversation_agent_service = ConversationAgentService()

