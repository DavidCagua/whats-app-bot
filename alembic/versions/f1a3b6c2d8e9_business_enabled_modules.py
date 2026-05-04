"""businesses.enabled_modules — per-business sidebar/feature gating

Revision ID: f1a3b6c2d8e9
Revises: e8b2c1f9d4a3
Create Date: 2026-05-03

Adds a text[] column on businesses listing which optional admin-console
modules each business has access to. Required modules (overview, inbox,
team/access, settings) are not stored here — they are always available.

Existing businesses default to the full optional set so behaviour is
unchanged on rollout. Super admins can opt out of specific modules per
business afterward (e.g. Biela doesn't need bookings or services).

This is a stepping stone toward subscription-tier gating; future work
can derive defaults from a plan column while keeping this column as a
per-business override list.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f1a3b6c2d8e9"
down_revision: Union[str, Sequence[str], None] = "e8b2c1f9d4a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEFAULT_MODULES = [
    "bookings",
    "orders",
    "products",
    "promotions",
    "services",
    "staff",
]


def upgrade() -> None:
    op.add_column(
        "businesses",
        sa.Column(
            "enabled_modules",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text(
                "ARRAY["
                + ", ".join(f"'{m}'" for m in DEFAULT_MODULES)
                + "]::text[]"
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("businesses", "enabled_modules")
