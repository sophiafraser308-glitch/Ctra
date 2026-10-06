"""Trade settings (lot size, stop loss, take profit — per timeframe) for live, demo and backtest.

Commands:  /set tp 1m 50   /set sl 1m 90   /set tp 15m 150   /set tp all 100   /set lot 0.1   /set multi on|off   /tpsl
Menu:      Settings -> 🎚 Trade settings  (also reachable from the backtest options)
"""
from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks.data import cb as C, split
from app.bot.handlers.common import confirm_gate, esc, need, show, toast
from app.bot.keyboards.common import btn, kb
from app.bot.states.forms import TradeSettingsInput
from app.core import trade_settings as T
from app.core.enums import Role
from app.core.exceptions import ValidationFailed
from app.core.timeframes import TF_ORDER
from app.security.rbac import Permission

router = Router(name="trade_settings")
COMMON_TFS = ["M1", "M5", "M15", "M30", "H1", "H4"]
ALL_WORDS = {"all", "default", "*", "كل", "الكل", "الجميع"}
CLEAR_WORDS = {"default", "reset", "clear", "inherit", "افتراضي"}
BACK_DEFAULT = "set:menu"


async def _tfs_in_use(ctx: Any) -> list[str]:
    try:
        return sorted({tf for b in await ctx.bots.list() for tf in (b.timeframes or [])}, key=TF_ORDER.index)
    except Exception:
        return []


async def _back(state: FSMContext) -> str:
    return (await state.get_data()).get("ts_back") or BACK_DEFAULT


async def _panel(event: Any, ctx: Any, state: FSMContext, note: str = "") -> None:
    ts = ctx.trade_settings.get()
    in_use = await _tfs_in_use(ctx)
    tfs = [tf for tf in TF_ORDER if tf in set(in_use) | set(ts["sl"]) | set(ts["tp"])] or COMMON_TFS
    rows = [[btn(tf + ("✏️" if (tf in ts["tp"] or tf in ts["sl"]) else ""), C("ts", "tf", tf)) for tf in tfs[i:i + 4]] for i in range(0, len(tfs), 4)]
    rows += [[btn("➕ Other timeframe", "ts:other")],
             [btn("📦 Lot size", "ts:lot"), btn("🎯 Default TP", C("ts", "set", "tp", "ALL")), btn("🛑 Default SL", C("ts", "set", "sl", "ALL"))],
             [btn(f"🧩 One trade per timeframe: {'ON' if ts['multi_tf'] else 'OFF'}", "ts:multi")],
             [btn("♻️ Reset all to defaults", "ts:reset")], [btn("⬅️ Back", await _back(state)), btn("🏠 Menu", "home")]]
    text = (note + "\n\n" if note else "") + T.panel_text(ts, tfs_in_use=in_use) + "\n\n<i>Applies from the next signal (live/demo bots) and the next backtest. Tap a timeframe to edit it.</i>"
    await show(event, text, kb(rows))


# ---- commands ------------------------------------------------------------------------------------------------
@router.message(Command("tpsl", "levels"))
async def cmd_panel(m: Message, ctx: Any, state: FSMContext) -> None:
    await state.update_data(ts_back=BACK_DEFAULT)
    await _panel(m, ctx, state)


@router.message(Command("set"))
async def cmd_set(m: Message, command: CommandObject, ctx: Any, state: FSMContext, role: Role) -> None:
    args = (command.args or "").split()
    if not args:
        await m.answer(T.HELP, parse_mode="HTML")
        await state.update_data(ts_back=BACK_DEFAULT)
        await _panel(m, ctx, state)
        return
    need(role, Permission.BOT_MANAGE)
    uid, what = m.from_user.id, args[0].lower()
    if what in ("tp", "sl"):
        if len(args) != 3:
            raise ValidationFailed(f"Usage: /set {what} <timeframe|all> <points>   e.g.  /set {what} 15m 150")
        tf = None if args[1].lower() in ALL_WORDS else T.parse_tf(args[1])
        if args[2].lower() in CLEAR_WORDS:
            if tf is None:
                raise ValidationFailed("Give a number for 'all' (0 = none)")
            await ctx.trade_settings.set_level(what, tf, None, uid)
            note = f"✅ {what.upper()} {tf} → back to the default"
        else:
            v = T.parse_points(args[2])
            await ctx.trade_settings.set_level(what, tf, v, uid)
            note = f"✅ {what.upper()} {tf or 'default (all timeframes)'} = <b>{v:g}</b> points" if v else f"✅ {what.upper()} {tf or 'default'} disabled (no {what.upper()})"
    elif what in ("lot", "lots", "volume"):
        if len(args) != 2:
            raise ValidationFailed("Usage: /set lot 0.1   (0 = size by risk %)")
        v = T.parse_lot(args[1])
        await ctx.trade_settings.set_lot(v, uid)
        note = f"✅ Lot size = <b>{v:g}</b>"
    elif what in ("multi", "multitf", "per_tf"):
        if len(args) != 2:
            raise ValidationFailed("Usage: /set multi on|off")
        on = T.parse_bool(args[1])
        await ctx.trade_settings.set_multi(on, uid)
        note = f"✅ One trade per timeframe: <b>{'ON' if on else 'OFF'}</b>"
    else:
        await m.answer("⚠️ Unknown setting.\n\n" + T.HELP, parse_mode="HTML")
        return
    await state.update_data(ts_back=BACK_DEFAULT)
    await _panel(m, ctx, state, note)


# ---- menu ------------------------------------------------------------------------------------------------------
@router.callback_query(F.data.startswith("ts:menu"))
async def menu(cb: CallbackQuery, ctx: Any, state: FSMContext) -> None:
    parts = split(cb.data)
    await state.set_state(None)
    if len(parts) > 2 and parts[2] == "bt":
        await state.update_data(ts_back="bt:opts")           # opened from the backtest options
    elif len(parts) <= 2:
        await state.update_data(ts_back=BACK_DEFAULT)
    await _panel(cb, ctx, state)
    await toast(cb, "")


@router.callback_query(F.data.startswith("ts:tf:"))
async def tf_screen(cb: CallbackQuery, ctx: Any, state: FSMContext) -> None:
    tf = split(cb.data)[2]
    e = T.effective(ctx.trade_settings.get(), tf)
    src = lambda k: "own" if e[f"{k}_src"] == "tf" else "default"      # noqa: E731
    fm = lambda v: f"{v:g}" if v else "none"                           # noqa: E731
    await show(cb, f"⏱ <b>{esc(tf)}</b>\n🎯 TP: <b>{fm(e['tp'])}</b> ({src('tp')})\n🛑 SL: <b>{fm(e['sl'])}</b> ({src('sl')})\n\nPoints (gold: 1 point = 0.1). <code>0</code> = none.",
               kb([[btn("🎯 Set TP", C("ts", "set", "tp", tf)), btn("🛑 Set SL", C("ts", "set", "sl", tf))],
                   [btn("↩️ TP → default", C("ts", "clr", "tp", tf)), btn("↩️ SL → default", C("ts", "clr", "sl", tf))],
                   [btn("⬅️ Back", "ts:menu:keep")]]))
    await toast(cb, "")


@router.callback_query(F.data.startswith("ts:set:"))
async def ask_value(cb: CallbackQuery, state: FSMContext, role: Role) -> None:
    need(role, Permission.BOT_MANAGE)
    _, _, kind, tf = split(cb.data)[:4]
    await state.set_state(TradeSettingsInput.value)
    await state.update_data(ts_kind=kind, ts_tf=None if tf == "ALL" else tf)
    label = "Take profit" if kind == "tp" else "Stop loss"
    await show(cb, f"✏️ <b>{label} — {'default (all timeframes)' if tf == 'ALL' else esc(tf)}</b>\nSend the number of points (<code>0</code> = none). /cancel to abort.")
    await toast(cb, "")


@router.callback_query(F.data.startswith("ts:clr:"))
async def clear_value(cb: CallbackQuery, ctx: Any, state: FSMContext, role: Role) -> None:
    need(role, Permission.BOT_MANAGE)
    _, _, kind, tf = split(cb.data)[:4]
    await ctx.trade_settings.set_level(kind, tf, None, cb.from_user.id)
    await _panel(cb, ctx, state, f"✅ {kind.upper()} {tf} → back to the default")
    await toast(cb, "")


@router.callback_query(F.data == "ts:lot")
async def ask_lot(cb: CallbackQuery, state: FSMContext, role: Role) -> None:
    need(role, Permission.BOT_MANAGE)
    await state.set_state(TradeSettingsInput.value)
    await state.update_data(ts_kind="lot", ts_tf=None)
    await show(cb, "📦 <b>Lot size</b> for every trade (live, demo, backtest).\nSend e.g. <code>0.10</code>. <code>0</code> = size by the risk profile's risk % (needs a stop loss). /cancel to abort.")
    await toast(cb, "")


@router.callback_query(F.data == "ts:other")
async def ask_other(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TradeSettingsInput.tf)
    await show(cb, "Send the timeframe: <code>1m 2m 3m 4m 5m 10m 15m 30m 1h 4h 12h 1d 1w</code> (or M15, H1 ...). /cancel to abort.")
    await toast(cb, "")


@router.callback_query(F.data == "ts:multi")
async def toggle_multi(cb: CallbackQuery, ctx: Any, state: FSMContext, role: Role) -> None:
    need(role, Permission.BOT_MANAGE)
    on = not ctx.trade_settings.get()["multi_tf"]
    await ctx.trade_settings.set_multi(on, cb.from_user.id)
    await _panel(cb, ctx, state, f"✅ One trade per timeframe: <b>{'ON' if on else 'OFF'}</b>")
    await toast(cb, "")


@router.callback_query(F.data.startswith("ts:reset"))
async def reset(cb: CallbackQuery, ctx: Any, state: FSMContext, role: Role) -> None:
    need(role, Permission.BOT_MANAGE)
    if not await confirm_gate(cb, title="Reset ALL trade settings (lot, TP, SL, per-timeframe values) to the defaults? Affects live bots from their next signal.", live=False, cancel="ts:menu:keep"):
        return
    await ctx.trade_settings.reset(cb.from_user.id)
    await _panel(cb, ctx, state, "♻️ Reset to defaults")


# ---- typed input ---------------------------------------------------------------------------------------------------
@router.message(TradeSettingsInput.value)
async def value_in(m: Message, state: FSMContext, ctx: Any, role: Role) -> None:
    need(role, Permission.BOT_MANAGE)
    d = await state.get_data()
    kind, tf = d["ts_kind"], d.get("ts_tf")
    if kind == "lot":
        v = T.parse_lot(m.text or "")
        await ctx.trade_settings.set_lot(v, m.from_user.id)
        note = f"✅ Lot size = <b>{v:g}</b>"
    else:
        v = T.parse_points(m.text or "")
        await ctx.trade_settings.set_level(kind, tf, v, m.from_user.id)
        note = f"✅ {kind.upper()} {tf or 'default'} = <b>{v:g}</b> points" if v else f"✅ {kind.upper()} {tf or 'default'} disabled"
    await state.set_state(None)
    await _panel(m, ctx, state, note)


@router.message(TradeSettingsInput.tf)
async def tf_in(m: Message, state: FSMContext, ctx: Any) -> None:
    tf = T.parse_tf(m.text or "")
    await state.set_state(None)
    e = T.effective(ctx.trade_settings.get(), tf)
    await m.answer(f"⏱ <b>{esc(tf)}</b> — TP {e['tp'] or 'none'} · SL {e['sl'] or 'none'}\nEdit:", parse_mode="HTML",
                   reply_markup=kb([[btn("🎯 Set TP", C("ts", "set", "tp", tf)), btn("🛑 Set SL", C("ts", "set", "sl", tf))], [btn("⬅️ Back", "ts:menu:keep")]]))
