"""Alembic environment for the app-metadata DB.

Wired to application settings and the ORM metadata so migrations never carry a
hardcoded URL (issue #23). Supports both offline and online modes and enables
batch mode on SQLite (required for ALTER TABLE).
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.models import Base

config = context.config

# Resolve the DB URL from app settings unless one was injected (init_db() sets
# it via cfg.set_main_option before invoking command.upgrade/stamp).
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata

# NOTE: we deliberately do NOT call logging.config.fileConfig(config.config_file_name).
# The app configures logging in app/observability.py and alembic runs in-process
# during startup; reconfiguring loggers here would clobber that.


def _is_sqlite() -> bool:
    return (config.get_main_option("sqlalchemy.url") or "").startswith("sqlite")


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_is_sqlite(),
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=_is_sqlite(),
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
