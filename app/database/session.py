from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings


class Database:
    """Owns the async engine; `session()` is a transactional unit of work (commit/rollback)."""

    def __init__(self, settings: Settings) -> None:
        url = settings.async_database_url
        kwargs: dict = {"pool_pre_ping": True}
        if settings.is_sqlite:
            path = url.split("///", 1)[-1]
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        else:
            kwargs.update(pool_size=10, max_overflow=10, pool_recycle=1800)
        self.engine: AsyncEngine = create_async_engine(url, **kwargs)
        if settings.is_sqlite:
            @event.listens_for(self.engine.sync_engine, "connect")
            def _pragmas(dbapi_conn, _):  # pragma: no cover
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=5000")
                cur.close()
        self._factory = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._factory() as s:
            try:
                yield s
                await s.commit()
            except BaseException:
                await s.rollback()
                raise

    async def ping(self) -> bool:
        try:
            async with self.engine.connect() as c:
                await c.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def create_all_if_empty(self) -> None:
        """Dev/Termux convenience when Alembic isn't run. Production uses `alembic upgrade head`."""
        from app.database.base import Base
        import app.models  # noqa: F401
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()
