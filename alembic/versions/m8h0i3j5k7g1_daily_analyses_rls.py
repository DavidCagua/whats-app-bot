"""conversation_daily_analyses — enable RLS, restrict to service_role

Revision ID: m8h0i3j5k7g1
Revises: l7g9h2i4j6f0
Create Date: 2026-05-10

Matches the convention from migrations/020_enable_rls_all_tables.sql:
every table exposed to Supabase's Data API must have RLS enabled, with
an explicit ``service_role`` policy. The Flask backend uses service_role
so it's unaffected; anonymous + authenticated JWT users get no access.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "m8h0i3j5k7g1"
down_revision: Union[str, Sequence[str], None] = "l7g9h2i4j6f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE conversation_daily_analyses ENABLE ROW LEVEL SECURITY"
    )
    # service_role bypasses RLS by default in Supabase, but the explicit
    # policy keeps parity with migration 020 and survives any future
    # change to that default.
    op.execute(
        """
        CREATE POLICY "service_role_all_conversation_daily_analyses"
          ON conversation_daily_analyses
          FOR ALL TO service_role
          USING (true) WITH CHECK (true)
        """
    )


def downgrade() -> None:
    op.execute(
        'DROP POLICY IF EXISTS "service_role_all_conversation_daily_analyses" '
        "ON conversation_daily_analyses"
    )
    op.execute(
        "ALTER TABLE conversation_daily_analyses DISABLE ROW LEVEL SECURITY"
    )
