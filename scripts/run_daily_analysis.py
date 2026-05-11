"""CLI entrypoint for the Railway daily analysis cron.

Usage (production, called by Railway Cron at 04:00 UTC = 23:00 Bogotá):
    python scripts/run_daily_analysis.py

Usage (local backfill / dry-run for a specific Bogotá day):
    python scripts/run_daily_analysis.py --date 2026-05-09

Exits non-zero on any unrecoverable error so Railway marks the run failed.
Per-business failures are caught inside the orchestrator and logged but
don't fail the whole run.
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

# Make ``app`` importable when invoked as ``python scripts/run_daily_analysis.py``
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.jobs.daily_conversation_analyzer import (  # noqa: E402
    run_daily_analysis,
    today_in_bogota,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Daily conversation post-mortem")
    p.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="Bogotá-local date to analyze (YYYY-MM-DD). Defaults to today in Bogotá.",
    )
    p.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip the LLM summary pass (classification only). Cheap re-runs / debugging.",
    )
    p.add_argument(
        "--no-slack",
        action="store_true",
        help="Skip the Slack post. Useful for local testing without spamming the channel.",
    )
    return p.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = _parse_args()
    target_date = args.date or today_in_bogota()
    logging.info("[DAILY_ANALYSIS] Starting for date=%s", target_date)

    try:
        results = run_daily_analysis(
            analysis_date=target_date,
            run_llm=not args.no_llm,
            post_to_slack=not args.no_slack,
        )
    except Exception:
        logging.exception("[DAILY_ANALYSIS] Fatal error")
        return 1

    if not results:
        logging.info("[DAILY_ANALYSIS] No businesses opted in. Done.")
        return 0

    for r in results:
        print(
            f"\n=== {r.business_name} — {r.analysis_date} ===\n"
            f"Total conversations: {r.total}\n"
            f"By category: {r.counts_by_category}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
