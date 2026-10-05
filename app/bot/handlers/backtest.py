"""Backtest wizard: strategy -> account (history source) -> symbols (multi) -> timeframe -> period -> options -> run."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app.backtest.service import DEFAULT_OPTIONS, BtOutcome, BtRequest
from app.bot.callbacks.data import cb as C, split
from app.bot.handlers.common import esc, fmt_ago, need, show, toast
from app.bot.handlers.multiselect import register_flow, start_symbol_pick, start_tf_pick
from app.bot.keyboards.common import back_home, btn, kb
from app.bot.states.forms import BacktestForm
from app.core.enums import Role
from app.core.exceptions import ValidationFailed
from app.core.timeframes import TF_LABEL
from app.security.rbac import Permission

router = Router(name="backtest")

# key -> (label, type, min, max)
OPTS: dict[str, tuple[str, type, float, float]] = {
    "initial_balance": ("Initial balance", float, 100, 10_000_000),
    "commission_per_lot": ("Commission per lot (round turn)", float, 0, 1000),
    "risk_pct": ("Risk % per trade (when the strategy sends no lots)", float, 0.01, 10),
    "max_open_positions": ("Max open positions", int, 1, 100),
    "daily_loss_limit": ("Daily loss limit $ (0=off)", float, 0, 10_000_000),
    "daily_profit_target": ("Daily profit target $ (0=off)", float, 0, 10_000_000),
    "tz_offset": ("Timezone offset hours (reports + day boundary)", float, -12, 14),
}


async def _bt(state: FSMContext) -> dict[str, Any]:
    d = await state.get_data()
    bt = d.get("bt") or {"opts": dict(DEFAULT_OPTIONS), "params": {}}
    bt.setdefault("opts", dict(DEFAULT_OPTIONS))
    bt.setdefault("params", {})
    return bt


async def _save(state: FSMContext, bt: dict[str, Any]) -> None:
    await state.update_data(bt=bt)


# ---- menu ---------------------------------------------------------------------------------------------------
@router.message(Command("backtest"))
async def cmd_backtest(m: Message, ctx: Any, state: FSMContext) -> None:
    await _render_menu(m, ctx, state)


@router.callback_query(F.data == "bt:menu")
async def menu(cb: CallbackQuery, ctx: Any, state: FSMContext) -> None:
    await _render_menu(cb, ctx, state)
    await toast(cb, "")


async def _render_menu(cb: Any, ctx: Any, state: FSMContext) -> None:
    await state.clear()
    user_id = cb.from_user.id
    runs = await ctx.backtests.recent(user_id, 5)
    lines = []
    for r in runs:
        s = r.summary or {}
        res = f"{s.get('net_profit', 0):+,.0f} · {s.get('total_trades', 0)} trades" if r.status == "DONE" else r.status
        lines.append(f"• {r.created_at:%m-%d %H:%M} {','.join(r.symbols)[:22]} {r.timeframe} → {res}")
    busy = "\n⏳ A backtest is running now." if ctx.backtests.busy else ""
    await show(cb, "🧪 <b>Backtest</b>\nTest a strategy on broker history: pick symbols (several), timeframe and dates. You get a summary message + Excel/CSV with every trade." + busy
               + ("\n\n<b>Recent</b>\n" + "\n".join(lines) if lines else ""),
               kb([[btn("▶️ New backtest", "bt:new")], [btn("🏠 Menu", "home")]]))


@router.callback_query(F.data == "bt:new")
async def new(cb: CallbackQuery, ctx: Any, state: FSMContext, role: Role) -> None:
    need(role, Permission.BOT_CONTROL)
    await state.clear()
    strategies = [s for s in await ctx.strategies.list()]
    if not strategies:
        await show(cb, "Upload a strategy first.", kb([back_home("bt:menu")]))
        return
    await _save(state, {"opts": dict(DEFAULT_OPTIONS), "params": {}})
    await show(cb, "🧪 <b>Step 1/5</b> — choose the strategy:", kb([[btn(("🟢 " if s.active_version_id else "⚪ ") + s.name[:40], C("bt", "s", s.id))] for s in strategies[:12]] + [back_home("bt:menu")]))
    await toast(cb, "")


@router.callback_query(F.data.startswith("bt:s:"))
async def pick_strategy(cb: CallbackQuery, ctx: Any, state: FSMContext) -> None:
    bt = await _bt(state)
    sid = split(cb.data)[2]
    st = await ctx.strategies.get(sid)
    bt["strategy_id"] = sid
    await _save(state, bt)
    accs = [a for a in await ctx.accounts.list() if ctx.gateway.is_connected(a.id)]
    if not accs:
        await show(cb, "No connected account. History is downloaded from a connected cTrader account (a DEMO account is fine) — connect one first.", kb([[btn("🏦 Accounts", "acc:menu")], back_home("bt:menu")]))
        return
    await show(cb, f"🧪 <b>Step 2/5</b> — account to download history from (<b>{esc(st.name)}</b>):",
               kb([[btn(f"{a.name[:26]} · {a.environment}", C("bt", "a", a.id))] for a in accs[:10]] + [back_home("bt:menu")]))
    await toast(cb, "")


@router.callback_query(F.data.startswith("bt:a:"))
async def pick_account(cb: CallbackQuery, ctx: Any, state: FSMContext) -> None:
    bt = await _bt(state)
    bt["account_id"] = split(cb.data)[2]
    await _save(state, bt)
    st = await ctx.strategies.get(bt["strategy_id"])
    vers = await ctx.strategies.versions(bt["strategy_id"])
    ver = next((v for v in vers if v.id == st.active_version_id), vers[0])
    await start_symbol_pick(cb, state, ctx, bt["account_id"], ver.meta.get("symbols", []), "bt_sym", "Step 3/5 — symbols (multi-select)")
    await toast(cb, "")


async def _sym_done(cb: CallbackQuery, state: FSMContext, ctx: Any, sel: list[str]) -> None:
    bt = await _bt(state)
    bt["symbols"] = sel
    await _save(state, bt)
    await start_tf_pick(cb, state, [], "bt_tf", True, "Step 4/5 — execution timeframe")


async def _tf_done(cb: CallbackQuery, state: FSMContext, ctx: Any, sel: list[str]) -> None:
    bt = await _bt(state)
    bt["timeframe"] = sel[0]
    await _save(state, bt)
    await show(cb, f"🧪 <b>Step 5/5</b> — period ({sel[0]} · {TF_LABEL[sel[0]]}):\nPick a quick range or enter custom dates.",
               kb([[btn("7 days", "bt:r:7"), btn("30 days", "bt:r:30"), btn("90 days", "bt:r:90")],
                   [btn("180 days", "bt:r:180"), btn("1 year", "bt:r:365")], [btn("✏️ Custom dates", "bt:cd")], [btn("❌ Cancel", "bt:menu")]]))


register_flow("bt_sym", _sym_done)
register_flow("bt_tf", _tf_done)


# ---- period -----------------------------------------------------------------------------------------------------
def _today(tz: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=tz)


@router.callback_query(F.data.startswith("bt:r:"))
async def quick_range(cb: CallbackQuery, ctx: Any, state: FSMContext) -> None:
    bt = await _bt(state)
    tz = float(bt["opts"]["tz_offset"])
    end = _today(tz).date()
    bt["date_to"] = end.isoformat()
    bt["date_from"] = (end - timedelta(days=int(split(cb.data)[2]))).isoformat()
    await _save(state, bt)
    await _options(cb, ctx, state)


@router.callback_query(F.data == "bt:cd")
async def custom_dates(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BacktestForm.date_from)
    await show(cb, "Send the <b>start date</b> as <code>YYYY-MM-DD</code> (e.g. 2026-06-01). /cancel to abort.")
    await toast(cb, "")


def _parse_date(txt: str, allow_today: bool = False) -> str:
    txt = (txt or "").strip().lower()
    if allow_today and txt in ("today", "now"):
        return _today(0).date().isoformat()
    try:
        return datetime.strptime(txt, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ValidationFailed("Date must look like 2026-06-01")


@router.message(BacktestForm.date_from)
async def date_from_in(m: Message, state: FSMContext) -> None:
    d = _parse_date(m.text or "")
    bt = await _bt(state)
    bt["date_from"] = d
    await _save(state, bt)
    await state.set_state(BacktestForm.date_to)
    await m.answer(f"Start: <b>{d}</b>\nNow send the <b>end date</b> (<code>YYYY-MM-DD</code> or <code>today</code>):", parse_mode="HTML")


@router.message(BacktestForm.date_to)
async def date_to_in(m: Message, state: FSMContext, ctx: Any) -> None:
    d = _parse_date(m.text or "", True)
    bt = await _bt(state)
    if d <= bt["date_from"]:
        raise ValidationFailed("End date must be after the start date")
    bt["date_to"] = d
    await _save(state, bt)
    await state.set_state(None)
    await _options(m, ctx, state)


# ---- options ----------------------------------------------------------------------------------------------------------
async def _options(event: Any, ctx: Any, state: FSMContext) -> None:
    bt = await _bt(state)
    o = bt["opts"]
    st = await ctx.strategies.get(bt["strategy_id"])
    acc = await ctx.accounts.get(bt["account_id"])
    lines = [f"🧪 <b>Backtest setup</b>", f"Strategy: <b>{esc(st.name)}</b>", f"Account (history): {esc(acc.name)} ({acc.environment})",
             f"Symbols: <b>{esc(', '.join(bt['symbols']))}</b>", f"Timeframe: <b>{bt['timeframe']}</b>", f"Period: <b>{bt['date_from']} → {bt['date_to']}</b>", ""]
    lines += [f"• {lbl}: <b>{o[k]}</b>" for k, (lbl, *_rest) in OPTS.items()]
    lines.append(f"• Spread: <b>{o['spread']}</b> (auto = gold 25p, oil 4p, forex 1.2p)")
    lines.append(f"• Output: <b>{o['output']}</b>")
    if bt["params"]:
        lines.append(f"• Strategy overrides: <code>{esc(str(bt['params']))}</code>")
    rows = []
    keys = list(OPTS)
    for i in range(0, len(keys), 2):
        rows.append([btn(OPTS[k][0].split(" (")[0][:24], C("bt", "o", k)) for k in keys[i:i + 2]])
    rows += [[btn("📏 Spread", C("bt", "o", "spread")), btn("📤 Output: " + o["output"], "bt:out")],
             [btn("⚙️ Strategy parameters", "bt:params"), btn("📅 Change period", "bt:cd")],
             [btn("🚀 RUN BACKTEST", "bt:go")], [btn("❌ Cancel", "bt:menu")]]
    await show(event, "\n".join(lines), kb(rows))


@router.callback_query(F.data == "bt:opts")
async def opts_cb(cb: CallbackQuery, ctx: Any, state: FSMContext) -> None:
    await _options(cb, ctx, state)
    await toast(cb, "")


@router.callback_query(F.data == "bt:out")
async def out_cycle(cb: CallbackQuery, ctx: Any, state: FSMContext) -> None:
    bt = await _bt(state)
    order = ["both", "xlsx", "csv"]
    bt["opts"]["output"] = order[(order.index(bt["opts"]["output"]) + 1) % 3]
    await _save(state, bt)
    await _options(cb, ctx, state)


@router.callback_query(F.data.startswith("bt:o:"))
async def opt_edit(cb: CallbackQuery, state: FSMContext) -> None:
    key = split(cb.data)[2]
    await state.update_data(opt_key=key)
    await state.set_state(BacktestForm.option)
    if key == "spread":
        await show(cb, "Send the spread in <b>pips</b> applied to every symbol (e.g. <code>1.5</code>), or <code>auto</code> for per-instrument defaults.")
    else:
        lbl, typ, lo, hi = OPTS[key]
        await show(cb, f"<b>{esc(lbl)}</b>\nSend a number between {lo:g} and {hi:g}.")
    await toast(cb, "")


@router.message(BacktestForm.option)
async def opt_value(m: Message, state: FSMContext, ctx: Any) -> None:
    d = await state.get_data()
    key, raw = d["opt_key"], (m.text or "").strip().replace(",", ".")
    bt = await _bt(state)
    if key == "spread":
        if raw.lower() == "auto":
            bt["opts"]["spread"] = "auto"
        else:
            try:
                v = float(raw)
            except ValueError:
                raise ValidationFailed("Send a number or 'auto'")
            if not 0 <= v <= 1000:
                raise ValidationFailed("Spread must be between 0 and 1000 pips")
            bt["opts"]["spread"] = v
    else:
        lbl, typ, lo, hi = OPTS[key]
        try:
            v = typ(float(raw)) if typ is int else float(raw)
        except ValueError:
            raise ValidationFailed("Send a number")
        if not lo <= v <= hi:
            raise ValidationFailed(f"Value must be between {lo:g} and {hi:g}")
        bt["opts"][key] = v
    await _save(state, bt)
    await state.set_state(None)
    await _options(m, ctx, state)


# ---- strategy parameter overrides --------------------------------------------------------------------------------------------
async def _active_version(ctx: Any, strategy_id: str) -> Any:
    st = await ctx.strategies.get(strategy_id)
    vers = await ctx.strategies.versions(strategy_id)
    return next((v for v in vers if v.id == st.active_version_id), vers[0])


@router.callback_query(F.data == "bt:params")
async def params_list(cb: CallbackQuery, ctx: Any, state: FSMContext) -> None:
    bt = await _bt(state)
    ver = await _active_version(ctx, bt["strategy_id"])
    eff = {**ver.parameters, **bt["params"]}
    rows = [[btn(f"{'✏️ ' if k in bt['params'] else ''}{k} = {eff[k]}"[:44], C("bt", "p", k))] for k in list(ver.parameters)[:20]]
    rows.append([btn("🧹 Reset overrides", "bt:pr"), btn("⬅️ Back", "bt:opts")])
    await show(cb, f"⚙️ <b>Strategy parameters for this backtest</b> (v{ver.version})\nOnly affects this run; defaults stay untouched.", kb(rows))
    await toast(cb, "")


@router.callback_query(F.data == "bt:pr")
async def params_reset(cb: CallbackQuery, ctx: Any, state: FSMContext) -> None:
    bt = await _bt(state)
    bt["params"] = {}
    await _save(state, bt)
    await params_list(cb, ctx, state)


@router.callback_query(F.data.startswith("bt:p:"))
async def param_edit(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(param_key=split(cb.data)[2])
    await state.set_state(BacktestForm.param)
    await show(cb, f"Send the value for <b>{esc(split(cb.data)[2])}</b> (/cancel to abort):")
    await toast(cb, "")


@router.message(BacktestForm.param)
async def param_value(m: Message, state: FSMContext, ctx: Any) -> None:
    d = await state.get_data()
    bt = await _bt(state)
    ver = await _active_version(ctx, bt["strategy_id"])
    key, cur, raw = d["param_key"], ver.parameters[d["param_key"]], (m.text or "").strip()
    try:
        if isinstance(cur, bool):
            if raw.lower() not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
                raise ValueError
            val: Any = raw.lower() in ("true", "1", "yes", "on")
        elif isinstance(cur, int):
            val = int(raw)
        elif isinstance(cur, float):
            val = float(raw.replace(",", "."))
        else:
            val = raw[:200]
    except ValueError:
        raise ValidationFailed(f"'{key}' expects {type(cur).__name__}")
    bt["params"][key] = val
    await _save(state, bt)
    await state.set_state(None)
    await m.answer(f"✅ {esc(key)} = <b>{esc(str(val))}</b> (this backtest only)", parse_mode="HTML", reply_markup=kb([[btn("⚙️ Parameters", "bt:params"), btn("🚀 Options", "bt:opts")]]))


# ---- run -----------------------------------------------------------------------------------------------------------------------------
def _to_ms(date_s: str, tz: float, end: bool = False) -> int:
    d = datetime.strptime(date_s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end:
        d += timedelta(days=1)
    return int((d.timestamp() - tz * 3600) * 1000)


@router.callback_query(F.data == "bt:go")
async def run(cb: CallbackQuery, ctx: Any, state: FSMContext, role: Role, bot: Bot) -> None:
    need(role, Permission.BOT_CONTROL)
    bt = await _bt(state)
    for k in ("strategy_id", "account_id", "symbols", "timeframe", "date_from", "date_to"):
        if k not in bt:
            await toast(cb, "Setup incomplete — start again", True)
            return
    tz = float(bt["opts"]["tz_offset"])
    to_ms = min(_to_ms(bt["date_to"], tz, True), int(time.time() * 1000))
    req = BtRequest(user_id=cb.from_user.id, strategy_id=bt["strategy_id"], account_id=bt["account_id"], symbols=bt["symbols"], timeframe=bt["timeframe"],
                    date_from_ms=_to_ms(bt["date_from"], tz), date_to_ms=to_ms, options=dict(bt["opts"]), params=dict(bt["params"]))
    ctx.backtests.preflight(req)               # raises ValidationFailed (shown to the user) before anything starts
    if ctx.backtests.busy:
        raise ValidationFailed("Another backtest is running. Wait for it to finish (or cancel it).")
    chat_id = cb.message.chat.id
    msg = await cb.message.edit_text("⏳ Backtest queued…", reply_markup=kb([[btn("⛔ Cancel run", "bt:cancel")]]))
    last = {"t": 0.0, "text": ""}

    async def progress(text: str) -> None:
        now = time.monotonic()
        if text == last["text"] or (now - last["t"] < 2.5 and "Simulating" in text):
            return
        last["t"], last["text"] = now, text
        try:
            await bot.edit_message_text(f"🧪 <b>Backtest running</b>\n{esc(text)}", chat_id=chat_id, message_id=msg.message_id, parse_mode="HTML",
                                        reply_markup=kb([[btn("⛔ Cancel run", "bt:cancel")]]))
        except Exception:
            pass

    async def done(outcome: BtOutcome | None, err: str | None) -> None:
        try:
            await bot.delete_message(chat_id, msg.message_id)
        except Exception:
            pass
        if outcome is None:
            await bot.send_message(chat_id, "⛔ Backtest cancelled." if err == "cancelled" else f"❌ Backtest failed: {esc(err or 'unknown')}", parse_mode="HTML",
                                   reply_markup=kb([[btn("🧪 Backtest menu", "bt:menu")]]))
            return
        await bot.send_message(chat_id, outcome.summary_text, parse_mode="HTML")
        if outcome.xlsx:
            await bot.send_document(chat_id, BufferedInputFile(outcome.xlsx, filename=f"{outcome.filename_base}.xlsx"), caption="📊 Excel: الملخص · الصفقات · حسب الرمز · يومي · شهري · أيام الإيقاف · منحنى الرصيد · الإعدادات")
        if outcome.csv:
            await bot.send_document(chat_id, BufferedInputFile(outcome.csv, filename=f"{outcome.filename_base}.csv"), caption="🧾 CSV: all trades")
        await bot.send_message(chat_id, "Done ✅", reply_markup=kb([[btn("🧪 Another backtest", "bt:new")], [btn("🏠 Menu", "home")]]))

    job = ctx.backtests.start_job(req, chat_id, progress, done)       # raises ValidationFailed (shown to user) on bad input
    await state.update_data(job_id=job.job_id)
    await ctx.audit.record(action="BACKTEST_START", user_id=cb.from_user.id, target=bt["strategy_id"],
                           new_state={"symbols": bt["symbols"], "tf": bt["timeframe"], "from": bt["date_from"], "to": bt["date_to"]})
    await toast(cb, "Started")


@router.callback_query(F.data == "bt:cancel")
async def cancel(cb: CallbackQuery, ctx: Any) -> None:
    n = 0
    for job in list(ctx.backtests.jobs.values()):
        if job.user_id == cb.from_user.id:
            job.cancel.set()
            n += 1
    await toast(cb, "Cancelling…" if n else "No running backtest", False)
