"""baseline: raw SQL migrations 000-023 already applied

Revision ID: 44a057c1f6eb
Revises:
Create Date: 2026-04-12 12:46:03.140041

This is the Alembic baseline revision. It represents the database state
AFTER the raw SQL migrations 000-023 in /migrations/ have been applied
(the pre-Alembic history of this project).

upgrade() and downgrade() are intentionally empty. The schema they would
represent has already been applied via `psql -f migrations/NNN_*.sql`,
either via scripts/setup_local.sh (local) or manually (prod).

To adopt this revision against an already-migrated database:
    DATABASE_URL=... alembic stamp head

That writes "44a057c1f6eb" into alembic_version without running any SQL.
From this point forward, new migrations go through Alembic only:
    alembic revision --autogenerate -m "description"
    alembic upgrade head

The raw SQL files in /migrations/ stay as historical archive + bootstrap
for fresh dev environments (setup_local.sh applies them then stamps).
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "44a057c1f6eb"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: schema already at this state via /migrations/000-023.sql."""
    pass


def downgrade() -> None:
    """No-op: downgrading the baseline would require running all the raw SQL
    rollback files manually, which is not supported here. Roll back in prod
    by restoring from a Supabase backup instead."""
    pass
