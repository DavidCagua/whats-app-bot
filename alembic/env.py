"""
Alembic migration environment.

Wires Alembic to the SQLAlchemy Base.metadata (for autogenerate) and pulls
DATABASE_URL from the environment (.env locally, GitHub secrets in CI).

Conventions:
- Use `alembic revision --autogenerate -m "description"` to scaffold a
  migration from model diffs, then hand-edit the generated upgrade/downgrade.
- Use `alembic upgrade head` to apply all pending revisions.
- Use `alembic stamp head` to mark the current DB as up-to-date without
  running any SQL (used for bootstrap after the raw SQL migrations).
"""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Load .env so DATABASE_URL is available locally. In CI/Railway/Supabase
# the env var is provided by the platform and load_dotenv is a no-op.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Make the app package importable so we can pull Base.metadata.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.database.models import Base  # noqa: E402

config = context.config

# Inject DATABASE_URL into the config so alembic doesn't rely on
# sqlalchemy.url in alembic.ini (we leave that blank for safety).
_db_url = os.getenv("DATABASE_URL")
if _db_url:
    # psycopg2 driver; Alembic uses sync SQLAlchemy.
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql://", 1)
    config.set_main_option("sqlalchemy.url", _db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# ---------- object filters ----------
#
# We ignore a few kinds of diffs that Alembic autogenerate treats as drift
# but that are cosmetic or historical noise from the pre-Alembic raw-SQL
# migrations in /migrations/*.sql:
#
#   * indexes            — prod uses inconsistent naming (idx_bookings_*,
#                          idx_staff_*, idx_processed_at). Matching every
#                          legacy name in the model is fighting archaeology.
#   * unique constraints — Postgres auto-named (foo_bar_key) vs SQLAlchemy
#                          auto-named, same kind of noise.
#   * comments           — column comments are not modeled today.
#   * unmodeled tables   — schema objects that exist in prod but have no
#                          SQLAlchemy declaration yet (e.g. processed_messages).
#   * alembic's own metadata table
#
# What we DO check: column adds/removes, column type changes, table
# adds/removes for modeled tables. That's the drift that actually breaks
# runtime.

_UNMODELED_TABLES = {
    "alembic_version",
    "schema_migrations",
    "processed_messages",  # used at runtime via raw SQL, no SQLAlchemy model yet
}


def include_object(object_, name, type_, reflected, compare_to):
    if type_ == "index":
        return False
    if type_ == "unique_constraint":
        return False
    if type_ == "table" and name in _UNMODELED_TABLES:
        return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emit SQL to stdout, no DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=False,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — connect to DATABASE_URL and apply."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=False,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
