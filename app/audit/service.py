from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.enums import Category
from app.database import Database
from app.logging import get_logger
from app.models import AuditEvent
from app.security.masking import masker

log = get_logger(Category.SECURITY)


class AuditService:
    """Append-only audit trail. Failure to audit is logged loudly but never hides the action's own result."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def record(self, *, action: str, user_id: int | None = None, target: str | None = None,
                     previous_state: Any = None, new_state: Any = None, result: str = "OK",
                     error: str | None = None, correlation_id: str | None = None,
                     meta: dict[str, Any] | None = None) -> None:
        try:
            async with self.db.session() as s:
                s.add(AuditEvent(
                    user_id=user_id, action=action, target=target,
                    previous_state=masker.mask_obj(previous_state) if previous_state is not None else None,
                    new_state=masker.mask_obj(new_state) if new_state is not None else None,
                    result=result, error=masker.mask(error) if error else None,
                    correlation_id=correlation_id, meta=masker.mask_obj(meta) if meta else None))
        except Exception as exc:
            log.error("AUDIT WRITE FAILED action=%s err=%s", action, type(exc).__name__)
        log.notice("AUDIT %s user=%s target=%s result=%s", action, user_id, target, result)

    async def recent(self, limit: int = 10, offset: int = 0, actions_prefix: str | None = None) -> list[AuditEvent]:
        async with self.db.session() as s:
            q = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit).offset(offset)
            if actions_prefix:
                q = q.where(AuditEvent.action.like(actions_prefix + "%"))
            return list((await s.execute(q)).scalars())

    async def count(self, actions_prefix: str | None = None) -> int:
        from sqlalchemy import func
        async with self.db.session() as s:
            q = select(func.count()).select_from(AuditEvent)
            if actions_prefix:
                q = q.where(AuditEvent.action.like(actions_prefix + "%"))
            return int((await s.execute(q)).scalar_one())
