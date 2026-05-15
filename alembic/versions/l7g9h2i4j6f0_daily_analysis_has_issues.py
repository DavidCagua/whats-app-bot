"""conversation_daily_analyses.has_issues — flag inconsistencies/friction

Revision ID: l7g9h2i4j6f0
Revises: k6f8g1h3i5e9
Create Date: 2026-05-10

Boolean set by the LLM pass when it spots inconsistencies in the
conversation even on otherwise-successful orders — wrong item entered,
customer had to repeat themselves, bot misunderstood a price, etc. The
summary text already mentions these in prose; this column makes them
queryable so the Slack post can call out e.g. "1 completed with issues".

Defaults to FALSE so existing rows back-fill correctly without a separate
backfill statement.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "l7g9h2i4j6f0"
down_revision: Union[str, Sequence[str], None] = "k6f8g1h3i5e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversation_daily_analyses",
        sa.Column(
            "has_issues",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "idx_conversation_daily_analyses_has_issues",
        "conversation_daily_analyses",
        ["has_issues"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_conversation_daily_analyses_has_issues",
        table_name="conversation_daily_analyses",
    )
    op.drop_column("conversation_daily_analyses", "has_issues")
