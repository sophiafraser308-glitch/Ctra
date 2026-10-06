from __future__ import annotations

from typing import Iterable, Sequence, TypeVar

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks.data import cb

T = TypeVar("T")
PER_PAGE = 6


def btn(text: str, data: str | None = None, url: str | None = None) -> InlineKeyboardButton:
    if url:
        return InlineKeyboardButton(text=text, url=url)
    return InlineKeyboardButton(text=text, callback_data=data)


def kb(rows: Iterable[Sequence[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows if r])


def back_home(back: str | None = None) -> list[InlineKeyboardButton]:
    row = []
    if back:
        row.append(btn("⬅️ Back", back))
    row.append(btn("🏠 Menu", "home"))
    return row


def paginate(items: Sequence[T], page: int, per: int = PER_PAGE) -> tuple[list[T], int, int]:
    pages = max(1, (len(items) + per - 1) // per)
    page = max(0, min(page, pages - 1))
    return list(items[page * per:(page + 1) * per]), page, pages


def nav_row(prefix: str, page: int, pages: int) -> list[InlineKeyboardButton]:
    """prefix like 'acc:list' -> buttons 'acc:list:{page}'."""
    if pages <= 1:
        return []
    row = []
    if page > 0:
        row.append(btn("◀️", cb(prefix, page - 1)))
    row.append(btn(f"{page + 1}/{pages}", cb(prefix, page)))
    if page < pages - 1:
        row.append(btn("▶️", cb(prefix, page + 1)))
    return row


def confirm_kb(yes: str, no: str = "home", yes_text: str = "✅ Confirm", no_text: str = "❌ Cancel") -> InlineKeyboardMarkup:
    return kb([[btn(yes_text, yes), btn(no_text, no)]])


def main_menu_kb() -> InlineKeyboardMarkup:
    return kb([
        [btn("📊 Dashboard", "dash"), btn("🏦 Accounts", "acc:menu")],
        [btn("🤖 Trading Bots", "bot:menu"), btn("🧠 Strategies", "str:menu")],
        [btn("📈 Positions", "pos:menu"), btn("🧾 Orders", "ord:menu")],
        [btn("🛡 Risk", "risk:menu"), btn("🔌 Connections", "conn:menu")],
        [btn("🩺 System Health", "health:menu"), btn("📜 Logs & Audit", "logs:menu")],
        [btn("🔔 Notifications", "notif:menu"), btn("⚙️ Settings", "set:menu")],
        [btn("🧪 Backtest", "bt:menu"), btn("🎚 TP / SL / Lot", "ts:menu")],
        [btn("🚨 Emergency", "em:menu")],
    ])
