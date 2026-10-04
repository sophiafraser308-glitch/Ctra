"""Authorization: only allow-listed numeric Telegram IDs; inject `role`; audit denied attempts."""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.core.enums import Category
from app.logging import get_logger

log = get_logger(Category.SECURITY)


class AuthMiddleware(BaseMiddleware):
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self._denied_log: dict[int, float] = {}

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if user is None or getattr(user, "is_bot", False):
            return None
        chat = data.get("event_chat")
        if chat is not None and getattr(chat, "type", "private") != "private":
            return None                                # private chats only
        role = await self.ctx.users.role_of(user.id)
        if role is None:
            now = time.monotonic()
            if now - self._denied_log.get(user.id, 0) > 300:
                self._denied_log[user.id] = now
                await self.ctx.audit.record(action="UNAUTHORIZED_ACCESS", user_id=user.id, result="DENIED", meta={"username": user.username})
                log.warning("unauthorized telegram user %s", user.id)
            if isinstance(event, Message):
                await event.answer("⛔ You are not authorized to use this bot.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Not authorized", show_alert=True)
            return None
        await self.ctx.users.touch(user.id, user.username, user.full_name)
        data["role"], data["ctx"] = role, self.ctx
        return await handler(event, data)
