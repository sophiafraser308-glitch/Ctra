"""Dispatcher assembly: middlewares, routers, global error handling."""
from __future__ import annotations

import uuid
from typing import Any

from aiogram import Dispatcher, F, Router
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, ErrorEvent, Message

from app.bot.handlers import accounts, backtest, bots, instruments, multiselect, orders, positions, risk, settings, start, strategies, system, trade_settings
from app.bot.keyboards.common import main_menu_kb
from app.bot.middlewares.auth import AuthMiddleware
from app.bot.middlewares.throttle import ThrottleMiddleware
from app.core.enums import Category
from app.core.exceptions import (AuthorizationError, ConflictError, CTraderError, NotFoundError, PlatformError, SafetyError,
                                 ValidationFailed)
from app.logging import get_logger
from app.security.masking import masker

log = get_logger(Category.TELEGRAM)


def _user_message(exc: BaseException) -> str:
    if isinstance(exc, AuthorizationError):
        return "⛔ Your role does not allow this action."
    if isinstance(exc, (ValidationFailed, NotFoundError, ConflictError, SafetyError)):
        return f"⚠️ {masker.mask(str(exc))[:300]}"
    if isinstance(exc, CTraderError):
        return f"⚠️ cTrader: {masker.mask(str(exc))[:300]}"
    if isinstance(exc, PlatformError):
        return f"⚠️ {masker.mask(str(exc))[:300]}"
    return ""


def build_dispatcher(ctx: Any) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage(), ctx=ctx)
    throttle = ThrottleMiddleware()
    auth = AuthMiddleware(ctx)
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(throttle)
        observer.outer_middleware(auth)

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        exc = event.exception
        msg = _user_message(exc)
        if not msg:
            ref = uuid.uuid4().hex[:8]
            log.error("unhandled handler error ref=%s: %s", ref, type(exc).__name__, exc_info=exc)
            msg = f"❌ Unexpected error (ref {ref}). It was logged."
        upd = event.update
        try:
            if upd.callback_query:
                await upd.callback_query.answer(msg[:190], show_alert=True)
            elif upd.message:
                await upd.message.answer(msg)
        except Exception:
            pass
        return True

    for r in (start.router, trade_settings.router, accounts.router, bots.router, strategies.router, positions.router, orders.router,
              risk.router, system.router, settings.router, backtest.router, instruments.router, multiselect.router):
        dp.include_router(r)

    fallback = Router(name="fallback")

    @fallback.message(F.text)
    async def unknown_text(m: Message) -> None:
        await m.answer("Use the menu below (or /start).", reply_markup=main_menu_kb())

    @fallback.callback_query()
    async def stale_button(cb: CallbackQuery) -> None:
        await cb.answer("This button is no longer valid. Open /start.", show_alert=False)

    dp.include_router(fallback)
    ctx.accounts.on_oauth_authorized = lambda row: accounts.notify_oauth_authorized(ctx, row)
    return dp
