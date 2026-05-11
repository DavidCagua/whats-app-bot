"""Post the daily-analysis summary to Slack via an incoming webhook.

Single channel, one POST per (business, day). Uses Block Kit so the
counts, issues list, and drop-offs each render as their own section.
Failure to post is *not* fatal for the cron — analysis rows are already
persisted by the time we get here; missing a Slack post is recoverable
(re-run the job, or read directly from the DB).
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from typing import Iterable, List, Optional

import requests


logger = logging.getLogger(__name__)


SLACK_WEBHOOK_ENV = "SLACK_DAILY_ANALYSIS_WEBHOOK_URL"
SLACK_TIMEOUT_SEC = 10


# Human-readable label + emoji per category. Order matters — drives the
# order they appear in the Slack summary.
_CATEGORY_DISPLAY = [
    ("automatic_completed_clean", "✅", "automáticas con pedido"),
    ("automatic_completed_issues", "⚠️", "con pedido pero hubo fricción"),
    ("automatic_no_order", "🤖", "sin pedido (consulta)"),
    ("automatic_dropped_off", "⏳", "clientes se cayeron"),
    ("human_intervention", "👤", "con intervención humana"),
    ("delivery_handoff", "🚚", "handoff de delivery"),
]


def _split_completed(rows: Iterable[dict]) -> Counter:
    """Split automatic_completed by has_issues so 'completed with friction'
    gets its own line in the Slack summary."""
    counts: Counter = Counter()
    for r in rows:
        cat = r["category"]
        if cat == "automatic_completed":
            cat = (
                "automatic_completed_issues"
                if r.get("has_issues")
                else "automatic_completed_clean"
            )
        counts[cat] += 1
    return counts


def _format_phone(wa_id: str) -> str:
    """Trim long WhatsApp IDs so they don't dominate the line."""
    if not wa_id:
        return "(sin número)"
    # Mask the middle digits, keep prefix + last 4 (e.g. +573...1234)
    if len(wa_id) > 7:
        return f"{wa_id[:4]}…{wa_id[-4:]}"
    return wa_id


def _counts_block(counts: Counter, total: int) -> dict:
    lines = [f"*Total:* {total} conversaciones"]
    for key, emoji, label in _CATEGORY_DISPLAY:
        n = counts.get(key, 0)
        if n > 0:
            lines.append(f"{emoji} {n} {label}")
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n".join(lines)},
    }


def _issues_block(rows: List[dict]) -> Optional[dict]:
    flagged = [
        r for r in rows
        if r.get("has_issues") and (r.get("summary") or "").strip()
    ]
    if not flagged:
        return None
    body = ["*🚨 Conversaciones con issues:*"]
    for r in flagged:
        body.append(f"• `{_format_phone(r['whatsapp_id'])}` — {r['summary']}")
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n".join(body)},
    }


def _drop_off_block(rows: List[dict]) -> Optional[dict]:
    drops = [r for r in rows if r["category"] == "automatic_dropped_off"]
    if not drops:
        return None
    body = ["*⏳ Drop-offs:*"]
    for r in drops:
        reason = (r.get("drop_off_reason") or r.get("summary") or "").strip()
        if not reason:
            reason = "(sin detalle)"
        body.append(f"• `{_format_phone(r['whatsapp_id'])}` — {reason}")
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n".join(body)},
    }


def build_payload(
    *,
    business_name: str,
    analysis_date,
    rows: List[dict],
) -> dict:
    """Compose the Block Kit payload Slack will render."""
    counts = _split_completed(rows)
    total = len(rows)
    blocks: List[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📊 Resumen diario — {business_name} — {analysis_date.isoformat()}",
            },
        },
    ]
    if total == 0:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "_Sin conversaciones hoy._",
            },
        })
    else:
        blocks.append(_counts_block(counts, total))
        blocks.append({"type": "divider"})
        for extra in (_issues_block(rows), _drop_off_block(rows)):
            if extra:
                blocks.append(extra)

    # Fallback ``text`` is required — clients without Block Kit support
    # (and notification previews on mobile) use it as the preview line.
    fallback = f"Resumen diario {business_name} {analysis_date.isoformat()}: {total} conversaciones"
    return {"text": fallback, "blocks": blocks}


def post_daily_summary(
    *,
    business_name: str,
    analysis_date,
    rows: List[dict],
    webhook_url: Optional[str] = None,
) -> bool:
    """Send the daily summary to Slack. Returns True on success."""
    url = webhook_url or os.getenv(SLACK_WEBHOOK_ENV)
    if not url:
        logger.warning(
            "[SLACK_NOTIFIER] %s not set — skipping daily summary post",
            SLACK_WEBHOOK_ENV,
        )
        return False
    payload = build_payload(
        business_name=business_name,
        analysis_date=analysis_date,
        rows=rows,
    )
    try:
        resp = requests.post(url, json=payload, timeout=SLACK_TIMEOUT_SEC)
    except requests.RequestException as exc:
        logger.error("[SLACK_NOTIFIER] request failed: %s", exc)
        return False
    if resp.status_code >= 300:
        logger.error(
            "[SLACK_NOTIFIER] non-2xx from Slack: %s %s",
            resp.status_code, resp.text[:200],
        )
        return False
    return True
