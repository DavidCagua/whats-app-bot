"""align schema: not null timestamps + staff_members timestamptz + phone_number_id not null

Revision ID: 596a0514423a
Revises: 44a057c1f6eb
Create Date: 2026-04-12 13:23:31.808243

The raw SQL migrations in /migrations/ created timestamp columns with a
server default of NOW() but forgot to add NOT NULL. This is a real drift
between the SQLAlchemy models (which declare nullable=False) and prod.
This revision brings the DB up to the model's expectations.

Also:
- staff_members.created_at / updated_at were created as TIMESTAMP (no tz)
  in migration 014. Every other table uses TIMESTAMPTZ. Aligning them.
- whatsapp_numbers.phone_number_id should be NOT NULL (the model declares
  it) but the table was created without the constraint.

Safe to apply: every column has server_default NOW() so no existing row
can be NULL. The TIMESTAMPTZ conversion for staff_members interprets
existing values as UTC, matching how the admin console writes them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "596a0514423a"
down_revision: Union[str, Sequence[str], None] = "44a057c1f6eb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Tables and columns that need NOT NULL added. Each has a server_default
# of NOW() so existing rows already have a value.
NULLABLE_TIMESTAMP_COLUMNS = [
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
    ("order_items", "created_at"),
    ("orders", "created_at"),
    ("orders", "updated_at"),
    ("products", "created_at"),
    ("products", "updated_at"),
    ("services", "created_at"),
    ("services", "updated_at"),
    ("user_businesses", "created_at"),
    ("users", "created_at"),
    ("users", "updated_at"),
    ("whatsapp_numbers", "created_at"),
    ("whatsapp_numbers", "updated_at"),
]


def upgrade() -> None:
    """Apply NOT NULL + timestamp + type alignments."""
    # Defensive: backfill any NULLs before adding NOT NULL. Should be a
    # no-op because server_default=NOW() fires at insert time, but this
    # protects against manual inserts that bypassed it.
    for table, column in NULLABLE_TIMESTAMP_COLUMNS:
        op.execute(
            sa.text(
                f"UPDATE {table} SET {column} = NOW() WHERE {column} IS NULL"
            )
        )

    for table, column in NULLABLE_TIMESTAMP_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
            existing_server_default=sa.text("now()"),
        )

    # staff_members.created_at / updated_at: TIMESTAMP -> TIMESTAMPTZ.
    op.execute(
        "ALTER TABLE staff_members "
        "ALTER COLUMN created_at TYPE TIMESTAMP WITH TIME ZONE "
        "USING created_at AT TIME ZONE 'UTC'"
    )
    op.execute(
        "ALTER TABLE staff_members "
        "ALTER COLUMN updated_at TYPE TIMESTAMP WITH TIME ZONE "
        "USING updated_at AT TIME ZONE 'UTC'"
    )

    # whatsapp_numbers.phone_number_id: allow NOT NULL.
    op.execute(
        "UPDATE whatsapp_numbers SET phone_number_id = '' WHERE phone_number_id IS NULL"
    )
    op.alter_column(
        "whatsapp_numbers",
        "phone_number_id",
        existing_type=sa.String(length=255),
        nullable=False,
    )


def downgrade() -> None:
    """Revert NOT NULL and type changes. Data is preserved."""
    op.alter_column(
        "whatsapp_numbers",
        "phone_number_id",
        existing_type=sa.String(length=255),
        nullable=True,
    )

    op.execute(
        "ALTER TABLE staff_members "
        "ALTER COLUMN updated_at TYPE TIMESTAMP WITHOUT TIME ZONE"
    )
    op.execute(
        "ALTER TABLE staff_members "
        "ALTER COLUMN created_at TYPE TIMESTAMP WITHOUT TIME ZONE"
    )

    for table, column in reversed(NULLABLE_TIMESTAMP_COLUMNS):
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
            existing_server_default=sa.text("now()"),
        )
