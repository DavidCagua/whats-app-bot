"""drop NOT NULL on whatsapp_numbers.phone_number_id (twilio support)

Revision ID: 2b65a1a1c010
Revises: 596a0514423a
Create Date: 2026-04-12 15:51:37.326516

The previous revision (596a0514423a) added NOT NULL to
whatsapp_numbers.phone_number_id as part of the schema-alignment cleanup.
That was wrong: migration 005 (`005_make_phone_number_id_optional.sql`)
intentionally made the column optional so businesses using Twilio (or
any non-Meta provider) can register a WhatsApp number without a Meta
phone_number_id. The alignment migration regressed that.

This revision drops the NOT NULL constraint and restores the original
intent. The partial unique index `whatsapp_numbers_phone_number_id_key`
(unique WHERE phone_number_id IS NOT NULL) added by migration 005
remains in place — it was never touched by 596a0514423a.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2b65a1a1c010"
down_revision: Union[str, Sequence[str], None] = "596a0514423a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "whatsapp_numbers",
        "phone_number_id",
        existing_type=sa.String(length=255),
        nullable=True,
    )


def downgrade() -> None:
    """Re-apply the (incorrect) NOT NULL. Provided only for symmetry —
    don't run this in prod, it'll break Twilio provisioning."""
    op.execute(
        "UPDATE whatsapp_numbers SET phone_number_id = '' WHERE phone_number_id IS NULL"
    )
    op.alter_column(
        "whatsapp_numbers",
        "phone_number_id",
        existing_type=sa.String(length=255),
        nullable=False,
    )
