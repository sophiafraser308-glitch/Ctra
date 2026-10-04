"""Persistent risk locks (daily loss, drawdown, consecutive losses). Reset is always explicit."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable

from sqlalchemy import or_, select

from app.audit import AuditService
from app.core.enums import Category, LockScope, Severity
from app.core.utils import utcnow
from app.database import Database
from app.logging import get_logger
from app.models import RiskLock
from app.notifications.service import Notifier

log = get_logger(Category.RISK)
LockHook = Callable[[RiskLock], Awaitable[None]]


class LockService:
    def __init__(self, db: Database, audit: AuditService, notifier: Notifier) -> None:
        self.db, self.audit, self.notifier = db, audit, notifier
        self.on_lock: list[LockHook] = []
        self.on_release: list[LockHook] = []

    async def create(self, scope_type: LockScope, scope_id: str | None, reason: str, detail: str = "",
                     expires_at: datetime | None = None) -> RiskLock:
        async with self.db.session() as s:
            existing = (await s.execute(select(RiskLock).where(
                RiskLock.active.is_(True), RiskLock.scope_type == scope_type.value,
                RiskLock.scope_id == scope_id, RiskLock.reason == reason))).scalars().first()
            if existing:
                return existing
            lock = RiskLock(scope_type=scope_type.value, scope_id=scope_id, reason=reason, detail=detail[:500], expires_at=expires_at)
            s.add(lock)
            await s.flush()
        log.warning("RISK LOCK %s %s %s: %s", scope_type.value, scope_id, reason, detail)
        await self.audit.record(action="RISK_LOCK", target=f"{scope_type.value}:{scope_id}", new_state={"reason": reason, "detail": detail, "expires_at": str(expires_at)})
        kind = {"RISK_REJECTED_DAILY_LOSS": "DAILY_LOSS", "RISK_REJECTED_CONSECUTIVE_LOSSES": "CONSECUTIVE_LOSS"}.get(reason, "RISK_LOCK")
        await self.notifier.notify(kind, f"🔒 LOCK {scope_type.value} {scope_id or ''}: {reason}\n{detail}\nNew orders are blocked; existing positions keep being monitored. Manual reset required." if expires_at is None else f"🔒 LOCK {scope_type.value} {scope_id or ''}: {reason}\n{detail}\nAuto-expires {expires_at:%Y-%m-%d %H:%M} UTC.", Severity.CRITICAL, dedup_key=f"lock:{lock.id}")
        for h in self.on_lock:
            try:
                await h(lock)
            except Exception as exc:
                log.error("lock hook failed: %s", type(exc).__name__)
        return lock

    async def _expire(self, s) -> list[RiskLock]:
        rows = (await s.execute(select(RiskLock).where(RiskLock.active.is_(True), RiskLock.expires_at.is_not(None), RiskLock.expires_at <= utcnow()))).scalars().all()
        for r in rows:
            r.active, r.released_at = False, utcnow()
        return list(rows)

    async def active(self, *, account_id: str | None = None, bot_id: str | None = None, strategy_id: str | None = None) -> list[RiskLock]:
        async with self.db.session() as s:
            expired = await self._expire(s)
            conds = [RiskLock.scope_type == LockScope.GLOBAL.value]
            if account_id:
                conds.append((RiskLock.scope_type == LockScope.ACCOUNT.value) & (RiskLock.scope_id == account_id))
            if bot_id:
                conds.append((RiskLock.scope_type == LockScope.BOT.value) & (RiskLock.scope_id == bot_id))
            if strategy_id:
                conds.append((RiskLock.scope_type == LockScope.STRATEGY.value) & (RiskLock.scope_id == strategy_id))
            rows = list((await s.execute(select(RiskLock).where(RiskLock.active.is_(True), or_(*conds)))).scalars())
        for r in expired:
            for h in self.on_release:
                await h(r)
        return rows

    async def list_active(self) -> list[RiskLock]:
        async with self.db.session() as s:
            expired = await self._expire(s)
            rows = list((await s.execute(select(RiskLock).where(RiskLock.active.is_(True)).order_by(RiskLock.created_at.desc()))).scalars())
        for r in expired:
            for h in self.on_release:
                await h(r)
        return rows

    async def release(self, lock_id: str, user_id: int) -> RiskLock:
        async with self.db.session() as s:
            lock = await s.get(RiskLock, lock_id)
            if lock is None or not lock.active:
                from app.core.exceptions import NotFoundError
                raise NotFoundError("Lock not found or already released")
            lock.active, lock.released_at, lock.released_by = False, utcnow(), user_id
        await self.audit.record(action="RISK_LOCK_RESET", user_id=user_id, target=f"{lock.scope_type}:{lock.scope_id}", previous_state={"reason": lock.reason})
        for h in self.on_release:
            await h(lock)
        return lock

    async def release_all(self, user_id: int) -> int:
        n = 0
        for lock in await self.list_active():
            await self.release(lock.id, user_id)
            n += 1
        return n
