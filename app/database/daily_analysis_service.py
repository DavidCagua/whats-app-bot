"""Upsert + query helpers for ``conversation_daily_analyses``.

Idempotent by design: ``upsert_daily_analysis`` ON CONFLICT updates the
existing row so the cron can be re-run safely (e.g. after a partial
failure or a manual backfill).
"""

import logging
import uuid
from datetime import date, datetime
from typing import Optional, Iterable, List

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .models import ConversationDailyAnalysis, get_db_session


logger = logging.getLogger(__name__)


def upsert_daily_analysis(
    *,
    business_id: str,
    whatsapp_id: str,
    analysis_date: date,
    category: str,
    converted_to_order: bool,
    order_id: Optional[str],
    had_human_intervention: bool,
    handoff_reason: Optional[str],
    message_count: int,
    first_msg_at: Optional[datetime],
    last_msg_at: Optional[datetime],
    summary: Optional[str] = None,
    drop_off_reason: Optional[str] = None,
    has_issues: bool = False,
    model: Optional[str] = None,
    session: Optional[Session] = None,
) -> dict:
    """UPSERT a daily analysis row. Returns the persisted row as a dict."""
    own_session = session is None
    if own_session:
        session = get_db_session()
    try:
        values = {
            "business_id": uuid.UUID(business_id),
            "whatsapp_id": whatsapp_id,
            "analysis_date": analysis_date,
            "category": category,
            "converted_to_order": converted_to_order,
            "order_id": uuid.UUID(order_id) if order_id else None,
            "had_human_intervention": had_human_intervention,
            "handoff_reason": handoff_reason,
            "message_count": message_count,
            "first_msg_at": first_msg_at,
            "last_msg_at": last_msg_at,
            "summary": summary,
            "drop_off_reason": drop_off_reason,
            "has_issues": has_issues,
            "model": model,
        }
        stmt = pg_insert(ConversationDailyAnalysis).values(**values)
        update_cols = {
            k: stmt.excluded[k]
            for k in values.keys()
            if k not in ("business_id", "whatsapp_id", "analysis_date")
        }
        # Refresh analyzed_at on every UPSERT so we know when the row was
        # last touched, not just when it was first created.
        update_cols["analyzed_at"] = stmt.excluded.analyzed_at
        stmt = stmt.on_conflict_do_update(
            constraint="uq_daily_analysis_per_convo_per_day",
            set_=update_cols,
        ).returning(ConversationDailyAnalysis)

        row = session.execute(stmt).scalar_one()
        if own_session:
            session.commit()
        return row.to_dict()
    except Exception:
        if own_session:
            session.rollback()
        raise
    finally:
        if own_session:
            session.close()


def list_analyses_for_day(
    business_id: str,
    analysis_date: date,
    session: Optional[Session] = None,
) -> List[dict]:
    """All daily analyses for a (business, day). Used to format the Slack post."""
    own_session = session is None
    if own_session:
        session = get_db_session()
    try:
        rows = (
            session.query(ConversationDailyAnalysis)
            .filter(
                ConversationDailyAnalysis.business_id == uuid.UUID(business_id),
                ConversationDailyAnalysis.analysis_date == analysis_date,
            )
            .order_by(ConversationDailyAnalysis.first_msg_at.asc().nullslast())
            .all()
        )
        return [r.to_dict() for r in rows]
    finally:
        if own_session:
            session.close()
