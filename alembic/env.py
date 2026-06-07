import asyncio
from logging.config import fileConfig
from urllib.parse import quote_plus
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context

# Import your Base and settings
from core.config import settings
from models.base import Base

# Import all models here so Alembic can see them for autogenerate
import models

config = context.config

# Set the sqlalchemy.url dynamically from your settings
safe_url = f"postgresql+asyncpg://{quote_plus(settings.DB_USER)}:{quote_plus(settings.DB_PASSWORD)}@{settings.DB_HOST}:{settings.DB_PORT}/{quote_plus(settings.DB_NAME)}"

config.set_main_option("sqlalchemy.url", safe_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Provide the metadata for autogenerate support
target_metadata = Base.metadata


def include_object(object, name, type_, reflected, compare_to):
    # Tell Alembic to ignore TimescaleDB's auto-generated index
    if type_ == "index" and name == "telemetry_device_ts_idx":
        return False
    return True


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online():
    """Run migrations in 'online' mode using an async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    # Keep standard offline logic or implement if needed
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_migrations_online())
