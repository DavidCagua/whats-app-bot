"""conversation_agent_settings.handoff_reason — record why the bot was disabled

Revision ID: j5e7f0g2h4d8
Revises: i4d6e9f1g3c7
Create Date: 2026-05-09

Nullable text column. Set by the customer-service flow to
"delivery_handoff" when the 50-minute order-status threshold trips and
the bot disables itself. Cleared back to NULL when staff flips
agent_enabled back to true from the admin console.

Drives the colored row treatment in the admin UI (conversation list,
orders table, global attention banner) so staff can tell auto-handoffs
apart from manual disables.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "j5e7f0g2h4d8"
down_revision: Union[str, Sequence[str], None] = "i4d6e9f1g3c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversation_agent_settings",
        sa.Column("handoff_reason", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_conversation_agent_settings_handoff_reason",
        "conversation_agent_settings",
        ["handoff_reason"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_conversation_agent_settings_handoff_reason",
        table_name="conversation_agent_settings",
    )
    op.drop_column("conversation_agent_settings", "handoff_reason")
