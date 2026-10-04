"""Shared handler helpers: rendering, confirmation gates, idempotent command execution, error mapping."""
from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Awaitable, Callable

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.bot.callbacks.data import split_confirm
from app.bot.keyboards.common import btn, kb
from app.core.enums import Role
from app.core.exceptions import AuthorizationError
from app.core.utils import fmt_ago, fmt_dt
from app.security.rbac import Permission, require

esc = html.escape


def need(role: Role | None, perm: Permission) -> None:
    require(role, perm)


async def show(event: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    text = text[:4000]
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)  # type: ignore[union-attr]
        except TelegramBadRequest as exc:
            if "not modified" in str(exc):
                return
            await event.message.answer(text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)  # type: ignore[union-attr]
        except Exception:
            await event.message.answer(text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)  # type: ignore[union-attr]
    else:
        await event.answer(text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)


async def toast(cb: CallbackQuery, text: str, alert: bool = False) -> None:
    try:
        await cb.answer(text[:190], show_alert=alert)
    except Exception:
        pass


async def confirm_gate(cb: CallbackQuery, *, title: str, live: bool = False, cancel: str = "home") -> bool:
    """Two-step flow for LIVE targets, one step otherwise. Returns True only when fully confirmed."""
    base, token = split_confirm(cb.data or "")
    if token == "c2" or (token == "c1" and not live):
        return True
    if token == "c1" and live:
        await show(cb, f"🚨 <b>LIVE ACCOUNT — FINAL CONFIRMATION</b>\n\n{title}\n\n<b>This affects real money.</b> Press the red button to proceed.",
                   kb([[btn("🔴 YES, EXECUTE ON LIVE", f"{base}:c2")], [btn("❌ Cancel", cancel)]]))
        await toast(cb, "Final confirmation required")
        return False
    warn = "⚠️ <b>LIVE</b> — " if live else ""
    await show(cb, f"{warn}<b>Confirm</b>\n\n{title}", kb([[btn("✅ Confirm", f"{base}:c1"), btn("❌ Cancel", cancel)]]))
    return False


async def run_command(ctx: Any, cb: CallbackQuery, ctype: str, fn: Callable[[str], Awaitable[Any]], *, target_type: str | None = None,
                      target_id: str | None = None) -> tuple[bool, Any]:
    """Idempotent execution keyed by the Telegram callback id (duplicate deliveries run once)."""
    executed, result = await ctx.commands.execute(ctype=ctype, key=f"tg:{cb.id}", user_id=cb.from_user.id, fn=fn,
                                                  target_type=target_type, target_id=target_id)
    if not executed:
        await toast(cb, "Already processed")
    return executed, result


def status_icon(s: str) -> str:
    return {"CONNECTED": "🟢", "RUNNING": "🟢", "OK": "🟢", "FRESH": "🟢", "DISCONNECTED": "⚪", "STOPPED": "⚪", "CREATED": "⚪",
            "CONNECTING": "🟡", "STARTING": "🟡", "PAUSED": "🟡", "DEGRADED": "🟡", "STALE": "🟡", "LOCKED": "🔒", "RESTARTING": "🟡",
            "ERROR": "🔴", "CRASHED": "🔴", "AUTH_FAILED": "🔴", "DOWN": "🔴", "STOPPING": "🟡"}.get(s, "⚫")


def env_tag(env: str) -> str:
    return "🔴 LIVE" if env == "LIVE" else "🟢 DEMO"


def num(v: float | None, d: int = 2) -> str:
    return "—" if v is None else f"{v:,.{d}f}"


def pnl(v: float | None) -> str:
    return "—" if v is None else f"{v:+,.2f}"


__all__ = ["esc", "need", "show", "toast", "confirm_gate", "run_command", "status_icon", "env_tag", "num", "pnl", "fmt_ago", "fmt_dt"]
