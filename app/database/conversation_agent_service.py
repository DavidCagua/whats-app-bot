"""
Service for per-conversation agent enable/disable overrides.
Default behavior: if no row exists for (business_id, whatsapp_id), agent is enabled.
"""

import logging
import uuid
from typing import Optional
from sqlalchemy.orm import Session

from .models import ConversationAgentSetting, get_db_session


# Reason tag the customer-service flow writes when the 50-min order-status
# threshold disables the bot for delivery follow-up.
HANDOFF_REASON_DELIVERY = "delivery_handoff"

# Reason tag written when the customer sends an image classified by the CS
# planner as a payment-proof receipt. Bot thanks the customer and disables
# itself so a human can verify the transfer against the order.
HANDOFF_REASON_PAYMENT_PROOF = "payment_proof"

# Reason tag written when the customer explicitly asks to speak with a
# human / advisor ("quiero hablar con un asesor", "comunícame con un
# humano", etc.). Bot acknowledges and disables itself so an operator
# can pick up the conversation.
HANDOFF_REASON_HUMAN_REQUEST = "human_request"


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

    def set_agent_enabled(
        self,
        business_id: str,
        whatsapp_id: str,
        agent_enabled: bool,
        handoff_reason: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Upsert setting for the conversation. Returns dict or None on failure.

        ``handoff_reason`` is recorded only when ``agent_enabled`` is False.
        Re-enabling always clears any prior reason so a stale tag doesn't
        survive a manual flip from the admin console.
        """
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
            stored_reason = handoff_reason if not agent_enabled else None
            if row is None:
                row = ConversationAgentSetting(
                    business_id=uuid.UUID(business_id),
                    whatsapp_id=whatsapp_id,
                    agent_enabled=bool(agent_enabled),
                    handoff_reason=stored_reason,
                )
                session.add(row)
            else:
                row.agent_enabled = bool(agent_enabled)
                row.handoff_reason = stored_reason
            session.commit()
            result = row.to_dict()
            session.close()
            return result
        except Exception as e:
            logging.error(f"[CONVERSATION_AGENT] Error writing setting: {e}")
            return None


conversation_agent_service = ConversationAgentService()
