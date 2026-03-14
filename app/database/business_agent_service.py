"""
Service for managing business-agent relationships.
Returns enabled agents per business for the agent router.
"""

import logging
import uuid
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_

from .models import BusinessAgent, get_db_session


class BusinessAgentService:
    """Service for business-agent operations."""

    def get_enabled_agents(self, business_id: str) -> List[Dict]:
        """
        Get all enabled agents for a business, ordered by priority.

        Args:
            business_id: Business UUID

        Returns:
            List of dicts with agent_type, priority, config
        """
        try:
            session: Session = get_db_session()
            rows = (
                session.query(BusinessAgent)
                .filter(
                    and_(
                        BusinessAgent.business_id == uuid.UUID(business_id),
                        BusinessAgent.enabled == True,
                    )
                )
                .order_by(BusinessAgent.priority.asc())
                .all()
            )
            result = [r.to_dict() for r in rows]
            session.close()
            logging.debug(f"[BUSINESS_AGENTS] Loaded {len(result)} enabled agents for business {business_id}")
            return result
        except Exception as e:
            logging.error(f"[BUSINESS_AGENTS] Error loading enabled agents: {e}")
            return []

    def is_agent_enabled(self, business_id: str, agent_type: str) -> bool:
        """Check if a specific agent is enabled for a business."""
        agents = self.get_enabled_agents(business_id)
        return any(a["agent_type"] == agent_type for a in agents)


# Global instance
business_agent_service = BusinessAgentService()
