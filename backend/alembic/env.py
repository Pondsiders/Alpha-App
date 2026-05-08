"""Alembic environment.

Reads `database_url` from `alpha.settings`, which loads from `settings.toml`.
The same TOML file the app reads is the only source for migration targets,
so `alembic upgrade head` and `uv run alpha` are guaranteed to be talking
to the same cluster.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from alpha.settings import settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject database_url into the SQLAlchemy config. SQLAlchemy 2.0 expects
# `postgresql+psycopg://...` to pick up psycopg3; our settings.database_url
# uses the bare `postgresql://...` shape that asyncpg likes, so swap the
# scheme for Alembic's purposes.
_url = settings.database_url
if _url.startswith("postgresql://"):
    _url = "postgresql+psycopg://" + _url[len("postgresql://") :]
config.set_main_option("sqlalchemy.url", _url)

# Migrations are hand-written SQL — no autogenerate, no model metadata.
target_metadata = None


def run_migrations_offline() -> None:
    """Generate SQL output without connecting to a database."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the database and run migrations."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
