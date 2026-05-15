"""Orchestrator for the daily conversation post-mortem cron.

Phase 1 (this file): SQL classification → UPSERT rows → return summary
counts. No LLM, no Slack — those are layered in once the classification
is verified against real data.

Wakes up at 23:00 Bogotá (04:00 UTC next day) for businesses where
``settings.daily_analysis_enabled`` is true. Runs over **today** in
Bogotá (the day that's about to close), not yesterday.
"""

import logging
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from typing import List

from sqlalchemy.orm import Session

from app.database.models import Business, get_db_session
from app.database.daily_analysis_service import (
    list_analyses_for_day,
    upsert_daily_analysis,
)
from app.jobs.conversation_classifier import (
    DailyConversation,
    classify_day,
)
from app.jobs.conversation_llm_analyzer import analyze_conversation
from app.jobs.slack_notifier import post_daily_summary


logger = logging.getLogger(__name__)


# Bogotá is UTC-5 year-round, no DST — fixed offset is correct.
BOGOTA_OFFSET = timezone(timedelta(hours=-5))


@dataclass
class BusinessAnalysisResult:
    business_id: str
    business_name: str
    analysis_date: date
    total: int
    counts_by_category: dict
    rows: List[DailyConversation]


def today_in_bogota() -> date:
    """Today's calendar date in Colombia. Used so the 23:00 cron reports
    on the day that's about to close, not on yesterday."""
    return datetime.now(BOGOTA_OFFSET).date()


def list_opted_in_businesses(session: Session) -> List[Business]:
    """Businesses with ``settings.daily_analysis_enabled = true``."""
    return (
        session.query(Business)
        .filter(
            Business.is_active.is_(True),
            # JSONB ?? operator would also work but `->>` keeps SQLAlchemy
            # comparison simple and lets us match on the literal string.
            Business.settings["daily_analysis_enabled"].astext == "true",
        )
        .all()
    )


def analyze_business_day(
    *,
    business: Business,
    analysis_date: date,
    session: Session,
    run_llm: bool = True,
    post_to_slack: bool = True,
) -> BusinessAnalysisResult:
    """Classify + LLM-summarize + UPSERT for one business+day. Idempotent.

    Two-phase write: SQL classification is upserted first so the LLM call
    can fail per-conversation without losing the deterministic facts. The
    LLM-derived ``summary``/``drop_off_reason`` then patch the same row.
    """
    rows = classify_day(
        business_id=str(business.id),
        analysis_date=analysis_date,
        session=session,
    )

    # Phase A — persist deterministic classification (always safe to commit).
    for row in rows:
        upsert_daily_analysis(session=session, **row.to_upsert_kwargs())
    session.commit()

    # Phase B — LLM summary for every conversation. Even successful orders
    # get analyzed so inconsistencies (wrong item, repeated info, friction)
    # surface in summary + has_issues even when the order closed cleanly.
    # Per-conversation try/except: one bad call doesn't poison the whole day.
    if run_llm:
        for row in rows:
            try:
                llm_result = analyze_conversation(
                    session=session,
                    business_id=row.business_id,
                    whatsapp_id=row.whatsapp_id,
                    analysis_date=row.analysis_date,
                    category=row.category,
                )
            except Exception:
                logger.exception(
                    "[DAILY_ANALYSIS] LLM analyze failed for %s",
                    row.whatsapp_id,
                )
                continue
            if not llm_result:
                continue
            upsert_daily_analysis(
                session=session,
                **row.to_upsert_kwargs(),
                summary=llm_result.get("summary"),
                drop_off_reason=llm_result.get("drop_off_reason"),
                has_issues=llm_result.get("has_issues", False),
                model=llm_result.get("model"),
            )
        session.commit()

    # Phase C — Slack notification. Read back the persisted rows (so the
    # post reflects exactly what was stored, including LLM fields). Failure
    # is non-fatal; the analysis rows are already safe in the DB.
    if post_to_slack:
        try:
            persisted = list_analyses_for_day(
                business_id=str(business.id),
                analysis_date=analysis_date,
                session=session,
            )
            ok = post_daily_summary(
                business_name=business.name,
                analysis_date=analysis_date,
                rows=persisted,
            )
            if ok:
                logger.info(
                    "[DAILY_ANALYSIS] Slack summary posted for %s", business.name
                )
        except Exception:
            logger.exception("[DAILY_ANALYSIS] Slack post failed for %s", business.id)

    counts = Counter(r.category for r in rows)
    return BusinessAnalysisResult(
        business_id=str(business.id),
        business_name=business.name,
        analysis_date=analysis_date,
        total=len(rows),
        counts_by_category=dict(counts),
        rows=rows,
    )


def run_daily_analysis(
    analysis_date: date | None = None,
    run_llm: bool = True,
    post_to_slack: bool = True,
) -> List[BusinessAnalysisResult]:
    """Entrypoint called by the Railway cron.

    Loops over opted-in businesses and analyzes ``analysis_date`` (defaults
    to today in Bogotá). Returns one result per business so the caller can
    log a summary or post to Slack.
    """
    target_date = analysis_date or today_in_bogota()
    session = get_db_session()
    try:
        businesses = list_opted_in_businesses(session)
        if not businesses:
            logger.info(
                "[DAILY_ANALYSIS] No businesses opted in for %s — nothing to do",
                target_date,
            )
            return []

        results: List[BusinessAnalysisResult] = []
        for business in businesses:
            try:
                logger.info(
                    "[DAILY_ANALYSIS] Analyzing %s (%s) for %s",
                    business.name, business.id, target_date,
                )
                result = analyze_business_day(
                    business=business,
                    analysis_date=target_date,
                    session=session,
                    run_llm=run_llm,
                    post_to_slack=post_to_slack,
                )
                logger.info(
                    "[DAILY_ANALYSIS] %s: %d conversations — %s",
                    business.name, result.total, result.counts_by_category,
                )
                results.append(result)
            except Exception:
                # Don't let one tenant's failure poison the whole run.
                logger.exception(
                    "[DAILY_ANALYSIS] Failed for business %s", business.id
                )
                session.rollback()
        return results
    finally:
        session.close()
