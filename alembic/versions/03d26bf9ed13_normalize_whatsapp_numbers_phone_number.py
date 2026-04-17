"""normalize whatsapp_numbers.phone_number + unique index

Revision ID: 03d26bf9ed13
Revises: 33397e65e7c5
Create Date: 2026-04-15

Inbound webhook routing was paying ~3-5s per message because
get_whatsapp_number_by_phone_number loaded every active whatsapp_numbers
row into Python and scanned them with a normalization helper. The reason
for the scan: phone_number was stored in inconsistent formats (with and
without the "whatsapp:" prefix, with/without a leading "+", digits-only
in some rows). A direct `WHERE phone_number = :x` couldn't match across
those variants.

This revision:

1. Canonicalizes every existing row in place to "+<digits>":
     - strips the "whatsapp:" prefix
     - strips every non-digit
     - prepends a single "+"

2. Adds a partial unique index on whatsapp_numbers.phone_number WHERE
   is_active = TRUE, so the routing hot path becomes a single indexed
   equality lookup. Partial (not full) so deactivated historical rows
   don't block reactivation or cause false conflicts.

Deploy note: the application code change that assumes canonical form
(get_whatsapp_number_by_phone_number doing direct equality + the
joinedload on Business) ships in the same commit as this revision. CI
runs alembic upgrade before switching traffic, so the index exists
before the new code path executes.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "03d26bf9ed13"
down_revision: Union[str, Sequence[str], None] = "33397e65e7c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Canonicalize every row. Idempotent: if a row is already canonical
    # the WHERE clause filters it out so Postgres does no work on it.
    op.execute(
        """
        UPDATE whatsapp_numbers
        SET phone_number = '+' || regexp_replace(
            replace(lower(phone_number), 'whatsapp:', ''),
            '[^0-9]', '', 'g'
        )
        WHERE phone_number IS NOT NULL
          AND phone_number <> '+' || regexp_replace(
              replace(lower(phone_number), 'whatsapp:', ''),
              '[^0-9]', '', 'g'
          )
        """
    )

    # Partial unique index: one active binding per phone_number.
    # IF NOT EXISTS keeps the upgrade safe to re-run.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS whatsapp_numbers_phone_number_active_unique
            ON whatsapp_numbers(phone_number)
            WHERE is_active = TRUE
        """
    )


def downgrade() -> None:
    # Drop the index. We do NOT un-canonicalize phone_number values —
    # there's no original form to restore to, and the canonical form is
    # the correct one anyway.
    op.execute(
        "DROP INDEX IF EXISTS whatsapp_numbers_phone_number_active_unique"
    )
