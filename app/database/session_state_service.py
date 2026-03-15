"""
Service for conversation session state (multi-turn flows).
Loads and saves session data; handles expiration on read.
"""

import logging
import uuid
from typing import Dict, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session as SASession

from .models import ConversationSession, get_db_session

# Default session timeout: 2 hours
DEFAULT_SESSION_TIMEOUT_MINUTES = 120

# Order flow states (stored in order_context["state"])
ORDER_STATE_GREETING = "GREETING"
ORDER_STATE_ORDERING = "ORDERING"
ORDER_STATE_COLLECTING_DELIVERY = "COLLECTING_DELIVERY"
ORDER_STATE_READY_TO_PLACE = "READY_TO_PLACE"


def derive_order_state(order_context: Optional[Dict]) -> str:
    """
    Derive order state from order_context when not explicitly set.
    In-progress cart lives only in session (order_context); no separate DB cart.
    """
    if not order_context:
        return ORDER_STATE_GREETING
    items = order_context.get("items") or []
    delivery_info = order_context.get("delivery_info") or {}
    if order_context.get("state") in (
        ORDER_STATE_GREETING,
        ORDER_STATE_ORDERING,
        ORDER_STATE_COLLECTING_DELIVERY,
        ORDER_STATE_READY_TO_PLACE,
    ):
        return order_context["state"]
    if not items:
        return ORDER_STATE_GREETING
    name = (delivery_info.get("name") or "").strip()
    address = (delivery_info.get("address") or "").strip()
    phone = (delivery_info.get("phone") or "").strip()
    payment = (delivery_info.get("payment_method") or "").strip()
    if name and address and phone and payment:
        return ORDER_STATE_READY_TO_PLACE
    return ORDER_STATE_ORDERING


class SessionStateService:
    """Service for conversation session CRUD and expiration."""

    def load(
        self,
        wa_id: str,
        business_id: str,
        timeout_minutes: Optional[int] = None,
    ) -> Dict:
        """
        Load session for (wa_id, business_id). Handles expiration.

        Args:
            wa_id: Customer WhatsApp ID
            business_id: Business UUID
            timeout_minutes: Override default timeout (from business settings)

        Returns:
            {
                "session": { active_agents, order_context, booking_context, ... },
                "is_new": bool,  # True if no session or expired
                "is_expired": bool,  # True if we just reset expired session
            }
        """
        timeout = timeout_minutes or DEFAULT_SESSION_TIMEOUT_MINUTES
        try:
            db_session: SASession = get_db_session()
            row = (
                db_session.query(ConversationSession)
                .filter(
                    ConversationSession.wa_id == wa_id,
                    ConversationSession.business_id == uuid.UUID(business_id),
                )
                .first()
            )
            db_session.close()

            if not row:
                sess = self._empty_session()
                sess["order_context"] = dict(sess.get("order_context") or {}, state=ORDER_STATE_GREETING)
                return {"session": sess, "is_new": True, "is_expired": False}

            last_activity = row.last_activity_at
            if last_activity:
                if isinstance(last_activity, str):
                    try:
                        last_activity = datetime.fromisoformat(
                            last_activity.replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        last_activity = datetime.utcnow()
                cutoff = datetime.utcnow() - timedelta(minutes=timeout)
                if last_activity.tzinfo:
                    from datetime import timezone
                    cutoff = cutoff.replace(tzinfo=timezone.utc)
                if last_activity < cutoff:
                    self._reset_session(wa_id, business_id)
                    sess = self._empty_session()
                    sess["order_context"] = dict(sess.get("order_context") or {}, state=ORDER_STATE_GREETING)
                    return {"session": sess, "is_new": True, "is_expired": True}

            sess = row.to_dict()
            oc = sess.get("order_context") or {}
            if "state" not in oc:
                oc = {**oc, "state": derive_order_state(oc)}
                sess["order_context"] = oc
            return {"session": sess, "is_new": False, "is_expired": False}
        except Exception as e:
            logging.error(f"[SESSION] Error loading session: {e}")
            sess = self._empty_session()
            sess["order_context"] = dict(sess.get("order_context") or {}, state=ORDER_STATE_GREETING)
            return {"session": sess, "is_new": True, "is_expired": False}

    def save(self, wa_id: str, business_id: str, state_update: Dict) -> None:
        """
        Merge state_update into session. Upsert. Updates last_activity_at.

        Semantics:
        - order_context/booking_context: None = clear (set to {}), dict = merge
        - active_agents: replace
        - last_order_id, last_booking_id: set
        """
        try:
            db_session: SASession = get_db_session()
            row = (
                db_session.query(ConversationSession)
                .filter(
                    ConversationSession.wa_id == wa_id,
                    ConversationSession.business_id == uuid.UUID(business_id),
                )
                .first()
            )
            now = datetime.utcnow()

            def _init_oc():
                oc = state_update.get("order_context")
                return {} if oc is None else (oc if isinstance(oc, dict) else {})

            def _init_bc():
                bc = state_update.get("booking_context")
                return {} if bc is None else (bc if isinstance(bc, dict) else {})

            if not row:
                new_row = ConversationSession(
                    wa_id=wa_id,
                    business_id=uuid.UUID(business_id),
                    active_agents=state_update.get("active_agents", []),
                    order_context=_init_oc(),
                    booking_context=_init_bc(),
                    agent_contexts=state_update.get("agent_contexts", {}),
                    last_order_id=state_update.get("last_order_id"),
                    last_booking_id=state_update.get("last_booking_id"),
                    last_activity_at=now,
                    updated_at=now,
                )
                db_session.add(new_row)
            else:
                if "active_agents" in state_update:
                    row.active_agents = state_update["active_agents"]
                if "order_context" in state_update:
                    val = state_update["order_context"]
                    row.order_context = {} if val is None else ({**(row.order_context or {}), **val} if isinstance(val, dict) else row.order_context or {})
                if "booking_context" in state_update:
                    val = state_update["booking_context"]
                    row.booking_context = {} if val is None else ({**(row.booking_context or {}), **val} if isinstance(val, dict) else row.booking_context or {})
                if "agent_contexts" in state_update:
                    row.agent_contexts = {**(row.agent_contexts or {}), **state_update["agent_contexts"]}
                if "last_order_id" in state_update:
                    row.last_order_id = state_update["last_order_id"]
                if "last_booking_id" in state_update:
                    row.last_booking_id = state_update["last_booking_id"]
                row.last_activity_at = now
                row.updated_at = now

            db_session.commit()
            db_session.close()
            logging.debug(f"[SESSION] Saved session for {wa_id} / {business_id}")
        except Exception as e:
            logging.error(f"[SESSION] Error saving session: {e}")
            try:
                db_session.rollback()
            except Exception:
                pass
            raise

    def _reset_session(self, wa_id: str, business_id: str) -> None:
        """Clear session contexts, keep row."""
        try:
            db_session: SASession = get_db_session()
            row = (
                db_session.query(ConversationSession)
                .filter(
                    ConversationSession.wa_id == wa_id,
                    ConversationSession.business_id == uuid.UUID(business_id),
                )
                .first()
            )
            if row:
                row.active_agents = []
                row.order_context = {}
                row.booking_context = {}
                row.agent_contexts = {}
                row.last_activity_at = datetime.utcnow()
                row.updated_at = datetime.utcnow()
                db_session.commit()
            db_session.close()
        except Exception as e:
            logging.error(f"[SESSION] Error resetting session: {e}")
            try:
                db_session.rollback()
            except Exception:
                pass

    def _empty_session(self) -> Dict:
        return {
            "active_agents": [],
            "order_context": {},
            "booking_context": {},
            "agent_contexts": {},
            "last_order_id": None,
            "last_booking_id": None,
        }


# Global instance
session_state_service = SessionStateService()
