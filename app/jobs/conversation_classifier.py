"""SQL pass 1: deterministic classification of a day's conversations.

Group ``conversations`` rows by (business_id, whatsapp_id, Bogotá-day),
join in ``conversation_agent_settings`` and ``orders`` to bucket each
conversation into one of:

  - ``human_intervention`` — staff replied directly (assistant message
    with NULL agent_type) OR agent_enabled=false at end of day with no
    automated handoff_reason set.
  - ``delivery_handoff`` — agent_enabled=false with handoff_reason set
    (currently only ``delivery_handoff`` from customer_service_flow).
    Treated as a separate bucket because it's an *automatic* disable,
    not a true human takeover — the bot did its job and stepped aside.
  - ``automatic_completed`` — bot handled it AND an order was created
    for this whatsapp_id during the day.
  - ``automatic_no_order`` — bot handled it, no order created, last
    message was from the assistant (informational query, ended cleanly).
  - ``automatic_dropped_off`` — bot handled it, no order, last message
    was from the user (user sent something and never got a reply, or
    bot replied and user came back but conversation petered out).

Returns a list of dicts, one per (whatsapp_id) for the requested day.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)


# Rough categories — kept as strings (not an Enum) so cron output stays
# decoupled from any client (admin console, Slack template, future BI).
CATEGORY_HUMAN_INTERVENTION = "human_intervention"
CATEGORY_DELIVERY_HANDOFF = "delivery_handoff"
CATEGORY_AUTOMATIC_COMPLETED = "automatic_completed"
CATEGORY_AUTOMATIC_NO_ORDER = "automatic_no_order"
CATEGORY_AUTOMATIC_DROPPED_OFF = "automatic_dropped_off"


@dataclass
class DailyConversation:
    """Pass-1 classification result for a single (whatsapp_id, day)."""
    business_id: str
    whatsapp_id: str
    analysis_date: date
    category: str
    converted_to_order: bool
    order_id: Optional[str]
    had_human_intervention: bool
    handoff_reason: Optional[str]
    message_count: int
    first_msg_at: Optional[datetime]
    last_msg_at: Optional[datetime]
    last_role: Optional[str]
    has_assistant_null_agent: bool

    def to_upsert_kwargs(self) -> dict:
        return {
            "business_id": self.business_id,
            "whatsapp_id": self.whatsapp_id,
            "analysis_date": self.analysis_date,
            "category": self.category,
            "converted_to_order": self.converted_to_order,
            "order_id": self.order_id,
            "had_human_intervention": self.had_human_intervention,
            "handoff_reason": self.handoff_reason,
            "message_count": self.message_count,
            "first_msg_at": self.first_msg_at,
            "last_msg_at": self.last_msg_at,
        }


# Single SQL query — group conversations by (whatsapp_id) for the target
# Bogotá day, compute aggregates, LEFT JOIN the day's first order and the
# current agent_setting row. Cheap (one round-trip, indexed lookups).
_CLASSIFY_SQL = text(
    """
    WITH day_messages AS (
        SELECT
            c.business_id,
            c.whatsapp_id,
            c.role,
            c.agent_type,
            c.timestamp,
            (c.timestamp AT TIME ZONE 'America/Bogota')::date AS bogota_day
        FROM conversations c
        WHERE c.business_id = :business_id
          AND (c.timestamp AT TIME ZONE 'America/Bogota')::date = :analysis_date
    ),
    aggregated AS (
        SELECT
            business_id,
            whatsapp_id,
            COUNT(*) AS message_count,
            MIN(timestamp) AS first_msg_at,
            MAX(timestamp) AS last_msg_at,
            BOOL_OR(role = 'assistant' AND agent_type IS NULL) AS has_assistant_null_agent,
            (
                SELECT role FROM day_messages dm2
                WHERE dm2.business_id = day_messages.business_id
                  AND dm2.whatsapp_id = day_messages.whatsapp_id
                ORDER BY timestamp DESC
                LIMIT 1
            ) AS last_role
        FROM day_messages
        GROUP BY business_id, whatsapp_id
    ),
    day_order AS (
        SELECT DISTINCT ON (business_id, whatsapp_id)
            business_id,
            whatsapp_id,
            id AS order_id
        FROM orders
        WHERE business_id = :business_id
          AND (created_at AT TIME ZONE 'America/Bogota')::date = :analysis_date
        ORDER BY business_id, whatsapp_id, created_at ASC
    )
    SELECT
        a.business_id,
        a.whatsapp_id,
        a.message_count,
        a.first_msg_at,
        a.last_msg_at,
        a.has_assistant_null_agent,
        a.last_role,
        o.order_id,
        s.agent_enabled,
        s.handoff_reason
    FROM aggregated a
    LEFT JOIN day_order o
      ON o.business_id = a.business_id AND o.whatsapp_id = a.whatsapp_id
    LEFT JOIN conversation_agent_settings s
      ON s.business_id = a.business_id AND s.whatsapp_id = a.whatsapp_id
    ORDER BY a.first_msg_at ASC;
    """
)


def classify_day(
    *,
    business_id: str,
    analysis_date: date,
    session: Session,
) -> List[DailyConversation]:
    """Run pass-1 classification for one business + Bogotá-day."""
    rows = session.execute(
        _CLASSIFY_SQL,
        {"business_id": uuid.UUID(business_id), "analysis_date": analysis_date},
    ).mappings().all()

    results: List[DailyConversation] = []
    for r in rows:
        converted_to_order = r["order_id"] is not None
        agent_enabled = r["agent_enabled"]  # may be None when no row exists
        handoff_reason = r["handoff_reason"]
        had_assistant_null = bool(r["has_assistant_null_agent"])
        last_role = r["last_role"]

        # had_human_intervention is broader than just the category bucket:
        # it's true even on conversations the bot ultimately recovered.
        # Bucket assignment uses the more specific rule below.
        had_human_intervention = (
            had_assistant_null
            or (agent_enabled is False and not handoff_reason)
        )

        category = _bucket(
            had_assistant_null=had_assistant_null,
            agent_enabled=agent_enabled,
            handoff_reason=handoff_reason,
            converted_to_order=converted_to_order,
            last_role=last_role,
        )

        results.append(
            DailyConversation(
                business_id=str(r["business_id"]),
                whatsapp_id=r["whatsapp_id"],
                analysis_date=analysis_date,
                category=category,
                converted_to_order=converted_to_order,
                order_id=str(r["order_id"]) if r["order_id"] else None,
                had_human_intervention=had_human_intervention,
                handoff_reason=handoff_reason,
                message_count=int(r["message_count"]),
                first_msg_at=r["first_msg_at"],
                last_msg_at=r["last_msg_at"],
                last_role=last_role,
                has_assistant_null_agent=had_assistant_null,
            )
        )
    return results


def _bucket(
    *,
    had_assistant_null: bool,
    agent_enabled: Optional[bool],
    handoff_reason: Optional[str],
    converted_to_order: bool,
    last_role: Optional[str],
) -> str:
    # Order matters: handoff_reason wins over generic agent_enabled=false
    # so an automated delivery handoff doesn't get mis-bucketed as human.
    if handoff_reason:
        return CATEGORY_DELIVERY_HANDOFF
    if had_assistant_null or agent_enabled is False:
        return CATEGORY_HUMAN_INTERVENTION
    if converted_to_order:
        return CATEGORY_AUTOMATIC_COMPLETED
    if last_role == "user":
        return CATEGORY_AUTOMATIC_DROPPED_OFF
    return CATEGORY_AUTOMATIC_NO_ORDER
