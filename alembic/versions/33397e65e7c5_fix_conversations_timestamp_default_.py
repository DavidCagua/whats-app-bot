"""fix conversations.timestamp default + processed_messages unique

Revision ID: 33397e65e7c5
Revises: 2b65a1a1c010
Create Date: 2026-04-12 18:58:13.548507

Two prod failures from raw-SQL-era schema gaps:

1. conversations.timestamp had NOT NULL but no DEFAULT. The previous
   alignment migration (596a0514423a) assumed every NOT NULL timestamp
   column already had a DB-level default of NOW() and used
   `existing_server_default=text("now()")` -- but that parameter only
   tells alembic what's already there for diff calculation, it does
   NOT actually set the default. Columns lacking the default crashed
   on inserts after the model refactor removed the Python-side
   `default=datetime.utcnow`.

   This revision idempotently sets DEFAULT NOW() on every timestamp
   column we expect to have one. Postgres `ALTER COLUMN ... SET
   DEFAULT` is idempotent (replaces the existing default) so columns
   that already had it are unaffected.

2. processed_messages.message_id was missing the UNIQUE constraint in
   prod. The bootstrap SQL (000_greenfield_bootstrap.sql) declares it,
   but prod predates that file and the constraint was never added by
   any individual migration. Without it, the bot's
   `INSERT ... ON CONFLICT (message_id)` dedupe path fails with
   InvalidColumnReference and falls back to in-memory cache (lost on
   restart).

   This revision adds the constraint via a CREATE UNIQUE INDEX IF NOT
   EXISTS (so re-running on environments where it already exists is a
   no-op). Postgres treats unique indexes and unique constraints as
   interchangeable for ON CONFLICT.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "33397e65e7c5"
down_revision: Union[str, Sequence[str], None] = "2b65a1a1c010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Every timestamp column managed by SQLAlchemy with server_default=NOW().
# Ordered for readability; idempotent so order doesn't matter.
TIMESTAMP_COLUMNS_NEEDING_DEFAULT = [
    ("agent_types", "created_at"),
    ("bookings", "created_at"),
    ("bookings", "updated_at"),
    ("business_agents", "created_at"),
    ("business_agents", "updated_at"),
    ("business_availability", "created_at"),
    ("business_availability", "updated_at"),
    ("businesses", "created_at"),
    ("businesses", "updated_at"),
    ("conversation_agent_settings", "created_at"),
    ("conversation_agent_settings", "updated_at"),
    ("conversation_attachments", "created_at"),
    ("conversation_attachments", "updated_at"),
    ("conversation_sessions", "last_activity_at"),
    ("conversation_sessions", "updated_at"),
    ("conversations", "timestamp"),
    ("conversations", "created_at"),
    ("customers", "created_at"),
    ("customers", "updated_at"),
    ("order_items", "created_at"),
    ("orders", "created_at"),
    ("orders", "updated_at"),
    ("processed_messages", "processed_at"),
    ("products", "created_at"),
    ("products", "updated_at"),
    ("services", "created_at"),
    ("services", "updated_at"),
    ("staff_members", "created_at"),
    ("staff_members", "updated_at"),
    ("user_businesses", "created_at"),
    ("users", "created_at"),
    ("users", "updated_at"),
    ("whatsapp_numbers", "created_at"),
    ("whatsapp_numbers", "updated_at"),
]


def upgrade() -> None:
    # 1) Ensure DEFAULT NOW() on all timestamp columns. Idempotent.
    for table, column in TIMESTAMP_COLUMNS_NEEDING_DEFAULT:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT NOW()"
        )

    # 2) Backfill any existing NULLs in conversations.timestamp before
    #    the column starts being trusted by inserts. Should be a no-op
    #    in practice but covers history.
    op.execute("UPDATE conversations SET timestamp = NOW() WHERE timestamp IS NULL")

    # 3) Add UNIQUE constraint on processed_messages.message_id if missing.
    #    Use CREATE UNIQUE INDEX IF NOT EXISTS so the migration is safe
    #    in environments where the constraint is already present (local
    #    dev applies the bootstrap SQL which already creates it).
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS processed_messages_message_id_key "
        "ON processed_messages(message_id)"
    )


def downgrade() -> None:
    # The DEFAULT NOW() additions are conceptually irreversible (we can't
    # know what each column's default was before — most likely none). The
    # unique index is reversible.
    op.execute("DROP INDEX IF EXISTS processed_messages_message_id_key")
    # Intentionally not removing DEFAULT NOW() — there's no scenario
    # where reverting the default helps anything.
