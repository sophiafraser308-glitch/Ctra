"""Heartbeat registry: every critical component reports status; persisted + shown in Telegram."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from app import __version__
from app.config import Settings
from app.core.enums import Category
from app.core.utils import utcnow
from app.database import Database
from app.logging import get_logger
from app.models import Heartbeat, Worker

log = get_logger(Category.HEARTBEAT)


@dataclass
class ComponentState:
    component: str
    status: str = "STARTING"                 # OK | DEGRADED | DOWN | STARTING | STOPPED
    started_monotonic: float = field(default_factory=time.monotonic)
    last_operation: str | None = None
    last_signal_at: datetime | None = None
    last_order_at: datetime | None = None
    last_ctrader_event_at: datetime | None = None
    last_market_data_at: datetime | None = None
    last_error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    ts: datetime = field(default_factory=utcnow)


class HeartbeatService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db, self.settings = db, settings
        self.components: dict[str, ComponentState] = {}
        self._providers: dict[str, Callable[[], tuple[str, dict[str, Any]]]] = {}
        self._task: asyncio.Task | None = None
        self.last_beat_at: datetime | None = None

    def component(self, name: str) -> ComponentState:
        return self.components.setdefault(name, ComponentState(component=name))

    def register_provider(self, name: str, fn: Callable[[], tuple[str, dict[str, Any]]]) -> None:
        self._providers[name] = fn
        self.component(name)

    def update(self, name: str, status: str | None = None, *, operation: str | None = None, error: str | None = None,
               signal: bool = False, order: bool = False, ctrader_event: bool = False, market_data: bool = False,
               **details: Any) -> None:
        c = self.component(name)
        now = utcnow()
        if status:
            c.status = status
        if operation:
            c.last_operation = operation
        if error:
            c.last_error = error[:500]
        if signal:
            c.last_signal_at = now
        if order:
            c.last_order_at = now
        if ctrader_event:
            c.last_ctrader_event_at = now
        if market_data:
            c.last_market_data_at = now
        if details:
            c.details.update(details)
        c.ts = now

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="heartbeat")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        for c in self.components.values():
            c.status = "STOPPED"
        await self.flush()

    async def _loop(self) -> None:
        while True:
            try:
                await self.flush()
            except Exception as exc:
                log.error("heartbeat flush failed: %s", type(exc).__name__)
            await asyncio.sleep(self.settings.heartbeat_interval_seconds)

    async def flush(self) -> None:
        for name, fn in self._providers.items():
            try:
                status, details = fn()
                self.update(name, status, **details)
            except Exception as exc:
                self.update(name, "DEGRADED", error=f"provider: {type(exc).__name__}")
        now = utcnow()
        async with self.db.session() as s:
            for c in self.components.values():
                row = await s.get(Heartbeat, c.component)
                vals = dict(status=c.status, ts=now, version=__version__,
                            uptime_seconds=time.monotonic() - c.started_monotonic, last_operation=c.last_operation,
                            last_signal_at=c.last_signal_at, last_order_at=c.last_order_at,
                            last_ctrader_event_at=c.last_ctrader_event_at, last_market_data_at=c.last_market_data_at,
                            last_error=c.last_error, details=c.details or None)
                if row:
                    for k, v in vals.items():
                        setattr(row, k, v)
                else:
                    s.add(Heartbeat(component=c.component, **vals))
                w = await s.get(Worker, c.component)
                if w is None:
                    s.add(Worker(name=c.component, kind="component", status=c.status, started_at=now, last_heartbeat=now))
                else:
                    w.status, w.last_heartbeat = c.status, now
        self.last_beat_at = now

    def snapshot(self) -> list[ComponentState]:
        return sorted(self.components.values(), key=lambda c: c.component)
