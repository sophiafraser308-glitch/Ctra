"""Database-backed command idempotency (Telegram redeliveries, double-taps, retries)."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.utils import new_correlation_id, new_id, utcnow
from app.database import Database
from app.models import Command


class CommandService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def execute(self, *, ctype: str, key: str, user_id: int | None, fn: Callable[[str], Awaitable[Any]],
                      target_type: str | None = None, target_id: str | None = None,
                      payload: dict | None = None) -> tuple[bool, Any]:
        """Run `fn(correlation_id)` at most once for `key`.

        Returns (executed_now, result). A repeated key returns the stored result (or an IN_PROGRESS marker).
        """
        cid = new_correlation_id()
        cmd_id = new_id()
        try:
            async with self.db.session() as s:
                s.add(Command(id=cmd_id, idempotency_key=key, correlation_id=cid, type=ctype, target_type=target_type,
                              target_id=target_id, user_id=user_id, payload=payload, status="RUNNING"))
        except IntegrityError:
            async with self.db.session() as s:
                row = (await s.execute(select(Command).where(Command.idempotency_key == key))).scalar_one()
                return False, (row.result if row.status != "RUNNING" else {"status": "IN_PROGRESS"})
        try:
            result = await fn(cid)
            stored = {"ok": True, "value": result if isinstance(result, (str, int, float, bool, dict, list, type(None))) else str(result)}
            status = "DONE"
            return True, result
        except Exception as exc:
            stored = {"ok": False, "error": type(exc).__name__}
            status = "FAILED"
            raise
        finally:
            try:
                async with self.db.session() as s:
                    row = await s.get(Command, cmd_id)
                    if row:
                        row.status, row.result, row.finished_at = status, stored, utcnow()
            except Exception:  # pragma: no cover
                pass
