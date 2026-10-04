from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.handlers.common import esc, fmt_ago, num, pnl, show, status_icon, toast
from app.bot.keyboards.common import btn, kb, main_menu_kb
from app.core.enums import Role

router = Router(name="start")


@router.message(Command("start", "menu"))
async def cmd_start(m: Message, state: FSMContext, role: Role) -> None:
    await state.clear()
    await m.answer(f"🤖 <b>cTrader Trading Platform</b>\nRole: <b>{role.value}</b>\nChoose a section:", reply_markup=main_menu_kb(), parse_mode="HTML")


@router.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext) -> None:
    await state.clear()
    await m.answer("Cancelled.", reply_markup=main_menu_kb())


@router.callback_query(F.data == "home")
async def home(cb: CallbackQuery, state: FSMContext, role: Role) -> None:
    await state.clear()
    await show(cb, f"🤖 <b>Main menu</b> — role <b>{role.value}</b>", main_menu_kb())
    await toast(cb, "")


@router.callback_query(F.data == "dash")
async def dashboard(cb: CallbackQuery, ctx: Any) -> None:
    d = await ctx.dashboard.snapshot()
    comp = " ".join(f"{status_icon(s)}{n}" for n, (s, _e) in sorted(d["components"].items()))
    ct = ", ".join(f"{e}:{status_icon('OK' if v['state'] == 'READY' else 'DOWN')}" for e, v in d["ctrader"].items()) or "idle"
    money = "\n".join(f"  {ccy}: bal {num(v['balance'])} | eq {num(v['equity'])} | free {num(v['free'])} | used {num(v['used'])} | float {pnl(v['unreal'])}" for ccy, v in d["money"].items()) or "  —"
    bots = ", ".join(f"{k.lower()} {v}" for k, v in sorted(d["bots"].items())) or "none"
    locks = "\n".join(f"  🔒 {t} {i or ''} {r}" for t, i, r in d["locks"]) or "  none"
    lasterr = f"{fmt_ago(d['last_error'][0])}: {esc(d['last_error'][1])}" if d["last_error"] else "none"
    flags = []
    if d["new_trading_disabled"]:
        flags.append("⛔ NEW TRADING DISABLED")
    flags.append("🔴 LIVE enabled" if d["live_allowed"] else "🟢 LIVE disabled")
    text = (f"📊 <b>Dashboard</b> v{d['version']} · {esc(d['env'])}\n{' · '.join(flags)}\n\n"
            f"<b>System</b> TG {status_icon(d['telegram'])} DB {status_icon(d['db'])} cTrader {ct}\n{comp}\n"
            f"Heartbeat {fmt_ago(d['last_heartbeat'])} · Watchdog {fmt_ago(d['watchdog'][0])} ({d['watchdog'][1]} findings)\n\n"
            f"<b>Accounts</b> {d['accounts']['connected']}/{d['accounts']['total']} connected (demo {d['accounts']['demo']}, live {d['accounts']['live']}) · sync {fmt_ago(d['last_sync'])}\n"
            f"<b>Money</b>\n{money}\n"
            f"<b>Today realized</b> {pnl(d['realized_today'])} · <b>Max DD</b> {d['max_drawdown_pct']:.2f}%\n"
            f"<b>Bots</b> {bots}\n<b>Open positions</b> {d['open_positions']} · <b>Pending orders</b> {d['pending_orders']} · <b>UNKNOWN</b> {d['unknown_orders']}\n"
            f"<b>Locks</b>\n{locks}\n<b>Last error</b> {lasterr}")
    await show(cb, text, kb([[btn("🔄 Refresh", "dash")], [btn("🏠 Menu", "home")]]))
    await toast(cb, "")
