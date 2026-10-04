"""Alembic environment (async engine). URL is taken from DATABASE_URL."""
import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import load_settings
from app.database.base import Base
import app.models  # noqa: F401  (register tables)

config = context.config
target_metadata = Base.metadata
URL = load_settings().async_database_url


def run_migrations_offline() -> None:
    context.configure(url=URL, target_metadata=target_metadata, literal_binds=True, render_as_batch=URL.startswith("sqlite"))
    with context.begin_transaction():
        context.run_migrations()


def do_run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata,
                      compare_type=True, render_as_batch=URL.startswith("sqlite"))
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(URL, poolclass=pool.NullPool)
    async with engine.connect() as conn:
        await conn.run_sync(do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_online())
