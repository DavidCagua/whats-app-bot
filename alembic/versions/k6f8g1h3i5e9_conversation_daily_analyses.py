"""conversation_daily_analyses — daily per-conversation classification + summary

Revision ID: k6f8g1h3i5e9
Revises: j5e7f0g2h4d8
Create Date: 2026-05-09

One row per (business_id, whatsapp_id, analysis_date) capturing the daily
post-mortem of a single customer's WhatsApp activity. Written by the
Railway cron at 23:00 Bogotá (04:00 UTC next day) which:

  1. Buckets the day's conversations into deterministic categories from
     existing tables (conversations, conversation_agent_settings, orders).
  2. Asks GPT-4o-mini for a 1-line summary on the ambiguous "handled but
     no order" subset (drop-off vs informational query). Cheap, scoped.
  3. Posts an aggregated count to a Slack incoming webhook.

The unique constraint (business_id, whatsapp_id, analysis_date) makes the
job idempotent — same-day reruns UPSERT and won't duplicate rows.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "k6f8g1h3i5e9"
down_revision: Union[str, Sequence[str], None] = "j5e7f0g2h4d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_daily_analyses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "business_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("whatsapp_id", sa.String(50), nullable=False),
        sa.Column("analysis_date", sa.Date(), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column(
            "converted_to_order",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "had_human_intervention",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("handoff_reason", sa.Text(), nullable=True),
        sa.Column(
            "message_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("first_msg_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_msg_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("drop_off_reason", sa.Text(), nullable=True),
        sa.Column("model", sa.String(50), nullable=True),
        sa.Column(
            "analyzed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "business_id",
            "whatsapp_id",
            "analysis_date",
            name="uq_daily_analysis_per_convo_per_day",
        ),
    )
    op.create_index(
        "idx_conversation_daily_analyses_business_id",
        "conversation_daily_analyses",
        ["business_id"],
    )
    op.create_index(
        "idx_conversation_daily_analyses_business_date",
        "conversation_daily_analyses",
        ["business_id", "analysis_date"],
    )
    op.create_index(
        "idx_conversation_daily_analyses_category",
        "conversation_daily_analyses",
        ["category"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_conversation_daily_analyses_category",
        table_name="conversation_daily_analyses",
    )
    op.drop_index(
        "idx_conversation_daily_analyses_business_date",
        table_name="conversation_daily_analyses",
    )
    op.drop_index(
        "idx_conversation_daily_analyses_business_id",
        table_name="conversation_daily_analyses",
    )
    op.drop_table("conversation_daily_analyses")
