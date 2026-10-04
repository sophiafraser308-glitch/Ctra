"""Structured logging: JSON to stdout, secret masking, optional DB sink (async, batched)."""
from __future__ import annotations

import asyncio
import json
import logging as pylog
import sys
import time
from datetime import datetime, timezone
from typing import Any

from app.core.enums import Category
from app.security.masking import masker

NOTICE = 25
pylog.addLevelName(NOTICE, "NOTICE")
_LEVELS = {"DEBUG": 10, "INFO": 20, "NOTICE": NOTICE, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
_RESERVED = set(pylog.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "category", "ctx"}


class MaskingFilter(pylog.Filter):
    def filter(self, record: pylog.LogRecord) -> bool:
        try:
            record.msg = masker.mask(record.getMessage())
            record.args = ()
            if record.exc_info and not record.exc_text:
                record.exc_text = pylog.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = masker.mask(record.exc_text)
            ctx = getattr(record, "ctx", None)
            if ctx:
                record.ctx = masker.mask_obj(ctx)
        except Exception:  # never break logging
            record.msg = "<log masking failure>"
            record.args = ()
        return True


class JsonFormatter(pylog.Formatter):
    def format(self, record: pylog.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "severity": record.levelname,
            "category": getattr(record, "category", Category.SYSTEM.value),
            "message": record.getMessage(),
        }
        ctx = getattr(record, "ctx", None)
        if ctx:
            payload["ctx"] = ctx
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, default=str, ensure_ascii=False)


class CategoryLogger(pylog.LoggerAdapter):
    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
        extra = kwargs.setdefault("extra", {})
        extra.setdefault("category", self.extra["category"])  # type: ignore[index]
        ctx = kwargs.pop("ctx", None)
        if ctx:
            extra["ctx"] = ctx
        return msg, kwargs

    def notice(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.log(NOTICE, msg, *args, **kwargs)


def get_logger(category: Category | str) -> CategoryLogger:
    cat = category.value if isinstance(category, Category) else str(category)
    return CategoryLogger(pylog.getLogger("app." + cat.lower()), {"category": cat})


class DbQueueHandler(pylog.Handler):
    """Enqueue-only handler (never touches the DB itself, so no recursion)."""

    def __init__(self, level: int) -> None:
        super().__init__(level)
        self.queue: asyncio.Queue[dict[str, Any]] | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.error_times: list[float] = []

    def emit(self, record: pylog.LogRecord) -> None:
        if record.levelno >= 40:
            now = time.monotonic()
            self.error_times = [t for t in self.error_times if now - t < 600] + [now]
        if self.queue is None or self.loop is None:
            return
        item = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc),
            "category": getattr(record, "category", "SYSTEM"),
            "severity": record.levelname,
            "message": record.getMessage()[:4000],
            "context": getattr(record, "ctx", None),
        }
        try:
            self.loop.call_soon_threadsafe(self._put, item)
        except RuntimeError:
            pass

    def _put(self, item: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(item)  # type: ignore[union-attr]
        except asyncio.QueueFull:
            pass

    def recent_error_count(self, window: float = 300.0) -> int:
        now = time.monotonic()
        return sum(1 for t in self.error_times if now - t < window)


_db_handler: DbQueueHandler | None = None


def setup_logging(level: str = "INFO", db_level: str = "INFO") -> DbQueueHandler:
    global _db_handler
    root = pylog.getLogger()
    root.handlers.clear()
    root.setLevel(_LEVELS.get(level.upper(), 20))
    sh = pylog.StreamHandler(sys.stdout)
    sh.setFormatter(JsonFormatter())
    sh.addFilter(MaskingFilter())
    root.addHandler(sh)
    _db_handler = DbQueueHandler(_LEVELS.get(db_level.upper(), 20))
    _db_handler.addFilter(MaskingFilter())
    root.addHandler(_db_handler)
    for noisy in ("aiogram.event", "httpx", "httpcore", "aiohttp.access", "sqlalchemy.engine"):
        pylog.getLogger(noisy).setLevel(pylog.WARNING)
    return _db_handler


def db_handler() -> DbQueueHandler | None:
    return _db_handler


class DbLogWriter:
    """Background task persisting queued log records in batches."""

    def __init__(self, db: Any, handler: DbQueueHandler) -> None:
        self.db = db
        self.handler = handler
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self.handler.queue = asyncio.Queue(maxsize=5000)
        self.handler.loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._run(), name="db-log-writer")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, 5)
            except Exception:
                self._task.cancel()

    async def _run(self) -> None:
        from app.models.system import LogEntry
        q = self.handler.queue
        assert q is not None
        while not (self._stop.is_set() and q.empty()):
            batch: list[dict[str, Any]] = []
            try:
                batch.append(await asyncio.wait_for(q.get(), 1.0))
            except asyncio.TimeoutError:
                continue
            while not q.empty() and len(batch) < 200:
                batch.append(q.get_nowait())
            try:
                async with self.db.session() as s:
                    s.add_all([LogEntry(**b) for b in batch])
            except Exception as exc:  # DB down: report on stderr, drop batch
                print(f"[db-log-writer] dropped {len(batch)} log rows: {type(exc).__name__}", file=sys.stderr)
                await asyncio.sleep(2)
