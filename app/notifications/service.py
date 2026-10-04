"""Throttled, persistent Telegram notifications with retry (survives Telegram outages)."""
from __future__ import annotations

import asyncio
import html
import time
from typing import Any

from sqlalchemy import select

from app.config import Settings
from app.core.enums import Category, Severity
from app.core.utils import new_id, utcnow
from app.database import Database
from app.logging import get_logger
from app.models import Notification, User, UserRole
from app.security.masking import masker
from app.services.settings_service import SystemSettingsService

log = get_logger(Category.NOTIFICATION)

ICONS = {"DEBUG": "🔹", "INFO": "ℹ️", "NOTICE": "🔔", "WARNING": "⚠️", "ERROR": "❌", "CRITICAL": "🚨"}
# kinds that are never suppressed by mute / throttle
ALWAYS = {"EMERGENCY", "RISK_LOCK", "DAILY_LOSS", "CONSECUTIVE_LOSS", "CRITICAL_ERROR"}


class Notifier:
    def __init__(self, db: Database, settings: Settings, sys_settings: SystemSettingsService) -> None:
        self.db, self.settings, self.sys = db, settings, sys_settings
        self.bot: Any = None            # aiogram Bot, injected once available
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        self._last_sent: dict[str, float] = {}
        self._suppressed: dict[str, int] = {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.last_error: str | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="notifier")
        # re-queue undelivered notifications from before a restart
        async with self.db.session() as s:
            rows = (await s.execute(select(Notification.id).where(Notification.delivered.is_(False))
                                    .order_by(Notification.ts).limit(50))).scalars().all()
        for nid in rows:
            self._queue.put_nowait(nid)

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def notify(self, kind: str, message: str, severity: Severity | str = Severity.INFO,
                     dedup_key: str | None = None, throttle_seconds: int | None = None) -> bool:
        """Persist + enqueue. Returns False if throttled/muted."""
        sev = severity.value if isinstance(severity, Severity) else str(severity)
        message = masker.mask(message)
        if kind not in ALWAYS:
            if self.sys.get("notifications_muted") or kind in (self.sys.get("muted_notification_kinds") or []):
                return False
            key = dedup_key or kind
            window = self.settings.notification_throttle_seconds if throttle_seconds is None else throttle_seconds
            now = time.monotonic()
            last = self._last_sent.get(key)
            if last is not None and now - last < window:
                self._suppressed[key] = self._suppressed.get(key, 0) + 1
                return False
            self._last_sent[key] = now
            n = self._suppressed.pop(key, 0)
            if n:
                message += f"\n(+{n} similar suppressed)"
        nid = new_id()
        try:
            async with self.db.session() as s:
                s.add(Notification(id=nid, kind=kind, severity=sev, message=message, dedup_key=dedup_key))
        except Exception as exc:
            log.error("notification persist failed: %s", type(exc).__name__)
            return False
        try:
            self._queue.put_nowait(nid)
        except asyncio.QueueFull:
            log.warning("notification queue full; row %s stays pending in DB", nid)
        return True

    async def recipients(self) -> list[int]:
        ids = set(self.settings.admin_ids)
        try:
            async with self.db.session() as s:
                rows = (await s.execute(select(UserRole.user_id).join(User, User.telegram_id == UserRole.user_id)
                                        .where(UserRole.role.in_(["ADMIN", "OPERATOR"]), User.is_active.is_(True)))).scalars()
                ids.update(rows)
        except Exception:
            pass
        return sorted(ids)

    async def _run(self) -> None:
        while not self._stop.is_set():
            nid = await self._queue.get()
            backoff = 2.0
            for attempt in range(6):
                if await self._deliver(nid):
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            else:
                log.warning("notification %s undelivered after retries; will retry on restart", nid)

    async def _deliver(self, nid: str) -> bool:
        if self.bot is None:
            return False
        async with self.db.session() as s:
            n = await s.get(Notification, nid)
            if n is None or n.delivered:
                return True
            n.attempts += 1
            text = f"{ICONS.get(n.severity, 'ℹ️')} <b>{html.escape(n.kind)}</b>\n{html.escape(n.message)}"
        ok_any = False
        for chat_id in await self.recipients():
            try:
                await self.bot.send_message(chat_id, text[:4000], parse_mode="HTML")
                ok_any = True
            except Exception as exc:
                self.last_error = type(exc).__name__
                log.warning("telegram send failed chat=%s err=%s", chat_id, type(exc).__name__)
        if ok_any:
            async with self.db.session() as s:
                n = await s.get(Notification, nid)
                if n:
                    n.delivered, n.delivered_at = True, utcnow()
        return ok_any

    # ---- convenience ---------------------------------------------------
    async def critical(self, kind: str, message: str, dedup_key: str | None = None) -> None:
        await self.notify(kind, message, Severity.CRITICAL, dedup_key)
