from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.security.rate_limit import RateLimiter


class ThrottleMiddleware(BaseMiddleware):
    """Per-user flood protection (outermost middleware)."""

    def __init__(self, max_events: int = 12, per_seconds: float = 5.0) -> None:
        self.limiter = RateLimiter(max_events, per_seconds)

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if user is not None and not self.limiter.allow(user.id):
            if isinstance(event, CallbackQuery):
                await event.answer("Slow down ✋", show_alert=False)
            return None
        return await handler(event, data)
