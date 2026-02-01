from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.db import Base
import app.models  # noqa: F401

# ensure all model modules are imported so Base.metadata is complete
import pkgutil
import importlib
import app.models as models_pkg

for m in pkgutil.walk_packages(models_pkg.__path__, models_pkg.__name__ + "."):
    importlib.import_module(m.name)


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _get_db_url() -> str | None:
    return (
        getattr(settings, "database_url", None)
        or getattr(settings, "db_url", None)
        or getattr(settings, "sqlalchemy_url", None)
        or os.getenv("DATABASE_URL")
        or os.getenv("DB_URL")
        or os.getenv("SQLALCHEMY_URL")
    )


db_url = _get_db_url()
if not db_url:
    raise RuntimeError(
        "Database URL is not configured. Provide settings.db_url/database_url or set DATABASE_URL env variable."
    )

config.set_main_option("sqlalchemy.url", str(db_url))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
