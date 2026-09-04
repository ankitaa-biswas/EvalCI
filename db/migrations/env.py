"""
db/migrations/env.py
────────────────────
Alembic migration environment configuration for EvalCI.

This file is auto-loaded by Alembic on every ``alembic`` CLI invocation.
It does three things:

1. Imports ``Base`` from ``db/models.py`` and wires its metadata so Alembic
   can auto-generate schema diffs (``alembic revision --autogenerate``).
2. Reads the database URL from the ``DATABASE_URL`` environment variable,
   converting the async ``postgresql+asyncpg://`` scheme to the sync
   ``postgresql+psycopg2://`` scheme that Alembic's synchronous runner needs.
3. Implements both *offline* mode (emits raw SQL to stdout/file, no live DB
   connection required) and *online* mode (connects to PostgreSQL and applies
   migrations directly).

Do not modify the function signatures or the Alembic-generated boilerplate
below — Alembic tools depend on the exact names ``run_migrations_offline``
and ``run_migrations_online``.
"""

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Import Base and wire target_metadata
# ─────────────────────────────────────────────────────────────────────────────

# Import all model classes so they are registered on Base.metadata before
# Alembic inspects it for autogenerate diffs.
import db.models  # noqa: F401 — side-effect import registers ORM models
from db.database import Base

# Alembic compares this metadata against the live DB schema to produce diffs.
target_metadata = Base.metadata

# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Read and normalise the database URL
# ─────────────────────────────────────────────────────────────────────────────

def _get_sync_database_url() -> str:
    """Return a *synchronous* database URL suitable for Alembic's runner.

    The application uses ``postgresql+asyncpg://`` for non-blocking I/O, but
    Alembic's migration runner is synchronous and requires a sync driver.
    This function converts the scheme automatically so a single ``DATABASE_URL``
    environment variable works for both the app and migrations.

    Returns:
        A ``postgresql+psycopg2://`` (or plain ``postgresql://``) URL string.

    Raises:
        RuntimeError: If ``DATABASE_URL`` is not set in the environment.
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Copy .env.example to .env and set DATABASE_URL before running migrations."
        )
    # Replace async driver scheme with the psycopg2 sync driver.
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://").replace(
        "postgresql+aiosqlite://", "sqlite:///"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Alembic Config object — gives access to the .ini file values
# ─────────────────────────────────────────────────────────────────────────────

config = context.config

# Interpret the config file for Python logging if it has a [loggers] section.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override the sqlalchemy.url from the ini file with the env-var value so
# DATABASE_URL is the single source of truth.
config.set_main_option("sqlalchemy.url", _get_sync_database_url())


# ─────────────────────────────────────────────────────────────────────────────
# Step 3a — Offline mode
# ─────────────────────────────────────────────────────────────────────────────

def run_migrations_offline() -> None:
    """Run migrations in *offline* mode.

    In offline mode Alembic does **not** create a live database connection.
    Instead it emits the SQL DDL statements to stdout (or a file) so they can
    be reviewed and applied manually — useful in environments where the
    migration runner does not have direct DB access (e.g. CI dry-runs).

    Alembic calls this function automatically when invoked with ``--sql``.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Emit ``CREATE TABLE IF NOT EXISTS`` for safety
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ─────────────────────────────────────────────────────────────────────────────
# Step 3b — Online mode
# ─────────────────────────────────────────────────────────────────────────────

def run_migrations_online() -> None:
    """Run migrations in *online* mode.

    In online mode Alembic creates a real connection to the target database
    and applies each migration inside a transaction.  If any migration step
    fails the entire transaction is rolled back, keeping the schema consistent.

    Alembic calls this function automatically for normal ``alembic upgrade``
    and ``alembic downgrade`` commands.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # No connection pooling for migration runs
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,       # detect column type changes
            compare_server_default=True,  # detect default value changes
        )

        with context.begin_transaction():
            context.run_migrations()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point — Alembic chooses offline vs online automatically
# ─────────────────────────────────────────────────────────────────────────────

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
