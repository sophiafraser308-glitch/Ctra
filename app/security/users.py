"""Telegram user authorisation: numeric-ID allowlist. Env admins are always ADMIN; others come from the DB."""
from __future__ import annotations

from sqlalchemy import delete, select

from app.audit import AuditService
from app.config import Settings
from app.core.enums import Role
from app.core.exceptions import ConflictError, NotFoundError, ValidationFailed
from app.core.utils import utcnow
from app.database import Database
from app.models import User, UserRole

_ORDER = {Role.ADMIN: 3, Role.OPERATOR: 2, Role.READ_ONLY: 1}


class UserService:
    def __init__(self, db: Database, settings: Settings, audit: AuditService) -> None:
        self.db, self.s, self.audit = db, settings, audit
        self._cache: dict[int, Role | None] = {}

    async def role_of(self, telegram_id: int) -> Role | None:
        if telegram_id in self.s.admin_ids:
            return Role.ADMIN
        if telegram_id in self._cache:
            return self._cache[telegram_id]
        async with self.db.session() as s:
            u = await s.get(User, telegram_id)
            if u is None or not u.is_active:
                self._cache[telegram_id] = None
                return None
            roles = (await s.execute(select(UserRole.role).where(UserRole.user_id == telegram_id))).scalars().all()
        best = max((Role(r) for r in roles), key=lambda r: _ORDER[r], default=None)
        self._cache[telegram_id] = best
        return best

    async def touch(self, telegram_id: int, username: str | None, name: str | None) -> None:
        async with self.db.session() as s:
            u = await s.get(User, telegram_id)
            if u is None:
                s.add(User(telegram_id=telegram_id, username=username, display_name=name, last_seen_at=utcnow()))
            else:
                u.username, u.display_name, u.last_seen_at = username, name, utcnow()

    async def add(self, telegram_id: int, role: Role, by: int) -> None:
        if telegram_id <= 0:
            raise ValidationFailed("Telegram ID must be a positive number")
        async with self.db.session() as s:
            u = await s.get(User, telegram_id)
            if u is None:
                s.add(User(telegram_id=telegram_id, is_active=True))
                await s.flush()
            else:
                u.is_active = True
            await s.execute(delete(UserRole).where(UserRole.user_id == telegram_id))
            s.add(UserRole(user_id=telegram_id, role=role.value, granted_by=by))
        self._cache.pop(telegram_id, None)
        await self.audit.record(action="USER_ADDED", user_id=by, target=str(telegram_id), new_state={"role": role.value})

    async def remove(self, telegram_id: int, by: int) -> None:
        if telegram_id in self.s.admin_ids:
            raise ConflictError("Environment admins cannot be removed from Telegram (edit ADMIN_TELEGRAM_IDS)")
        async with self.db.session() as s:
            u = await s.get(User, telegram_id)
            if u is None:
                raise NotFoundError("User not found")
            u.is_active = False
            await s.execute(delete(UserRole).where(UserRole.user_id == telegram_id))
        self._cache.pop(telegram_id, None)
        await self.audit.record(action="USER_REMOVED", user_id=by, target=str(telegram_id))

    async def list(self) -> list[tuple[int, str | None, str]]:
        out = [(i, None, "ADMIN (env)") for i in self.s.admin_ids]
        async with self.db.session() as s:
            rows = (await s.execute(select(User, UserRole.role).join(UserRole, UserRole.user_id == User.telegram_id).where(User.is_active.is_(True)))).all()
        out += [(u.telegram_id, u.username, role) for u, role in rows if u.telegram_id not in self.s.admin_ids]
        return out
