from __future__ import annotations

from sqlalchemy import func, select

from app.database import Database
from app.models import Command, ConnectionEvent, LogEntry, Notification, ReconciliationEvent


class LogQueryService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def logs(self, category: str | None = None, severity: str | None = None, offset: int = 0, limit: int = 8):
        async with self.db.session() as s:
            q = select(LogEntry).order_by(LogEntry.id.desc()).offset(offset).limit(limit)
            cq = select(func.count()).select_from(LogEntry)
            if category:
                q, cq = q.where(LogEntry.category == category), cq.where(LogEntry.category == category)
            if severity:
                q, cq = q.where(LogEntry.severity == severity), cq.where(LogEntry.severity == severity)
            return list((await s.execute(q)).scalars()), int((await s.execute(cq)).scalar_one())

    async def commands(self, offset: int = 0, limit: int = 8):
        async with self.db.session() as s:
            rows = list((await s.execute(select(Command).order_by(Command.created_at.desc()).offset(offset).limit(limit))).scalars())
            n = int((await s.execute(select(func.count()).select_from(Command))).scalar_one())
        return rows, n

    async def notifications(self, offset: int = 0, limit: int = 8):
        async with self.db.session() as s:
            rows = list((await s.execute(select(Notification).order_by(Notification.ts.desc()).offset(offset).limit(limit))).scalars())
            n = int((await s.execute(select(func.count()).select_from(Notification))).scalar_one())
        return rows, n

    async def connection_events(self, account_id: str | None = None, limit: int = 10):
        async with self.db.session() as s:
            q = select(ConnectionEvent).order_by(ConnectionEvent.id.desc()).limit(limit)
            if account_id:
                q = q.where(ConnectionEvent.account_id == account_id)
            return list((await s.execute(q)).scalars())

    async def recon_events(self, limit: int = 10):
        async with self.db.session() as s:
            return list((await s.execute(select(ReconciliationEvent).order_by(ReconciliationEvent.id.desc()).limit(limit))).scalars())
