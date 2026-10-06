"""Backtest wizard: strategy -> account (history source) -> symbols (multi) -> timeframes (multi) -> period (calendar) -> options -> run."""
from __future__ import annotations

import calendar
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app.backtest.service import DEFAULT_OPTIONS, BtOutcome, BtRequest
from app.bot.callbacks.data import cb as C, split
from app.bot.handlers.common import esc, need, show, toast
from app.bot.handlers.multiselect import register_flow, start_symbol_pick, start_tf_pick
from app.bot.keyboards.common import back_home, btn, kb
from app.bot.states.forms import BacktestForm
from app.core.enums import Role
from app.core.trade_settings import backtest_levels
from app.core.exceptions import ValidationFailed
from app.security.rbac import Permission

router = Router(name="backtest")

# key -> (button label, prompt, type, min, max)
OPTS: dict[str, tuple[str, str, type, float, float]] = {
    "initial_balance": ("💰 Balance", "Initial balance", float, 100, 10_000_000),
    "lot_size": ("📦 Lot size", "Lot size for every trade of this run (0 = use the Trade settings: /set lot)", float, 0, 1000),
    "sl_points": ("🛑 Stop loss", "Stop loss in POINTS for this run (0 = use the Trade settings: /set sl 15m 90). A timeframe's own value wins. Gold: 1 point = 0.1", float, 0, 100_000),
    "tp_points": ("🎯 Take profit", "Take profit in POINTS for this run (0 = use the Trade settings: /set tp 15m 150). A timeframe's own value wins. Gold: 1 point = 0.1", float, 0, 100_000),
    "tz_offset": ("🕒 Timezone", "Timezone offset in hours for reports and the day boundary (Damascus = 3)", float, -12, 14),
}
AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


async def _bt(state: FSMContext) -> dict[str, Any]:
    d = await state.get_data()
    bt = d.get("bt") or {}
    bt.setdefault("opts", dict(DEFAULT_OPTIONS))
    for k, v in DEFAULT_OPTIONS.items():        # forward-compatible with sessions started before an update
        bt["opts"].setdefault(k, v)
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


async def _render_menu(event: Any, ctx: Any, state: FSMContext) -> None:
    await state.clear()
    runs = await ctx.backtests.recent(event.from_user.id, 5)
    lines = []
    for r in runs:
        s = r.summary or {}
        tfs = ",".join((r.config or {}).get("timeframes", [r.timeframe]))
        res = f"{s.get('net_profit', 0):+,.0f} · {s.get('total_trades', 0)} trades" if r.status == "DONE" else r.status
        lines.append(f"• {r.created_at:%m-%d %H:%M} {','.join(r.symbols)[:20]} {tfs} → {res}")
    busy = "\n⏳ A backtest is running now." if ctx.backtests.busy else ""
    await show(event, "🧪 <b>Backtest</b>\nApply a strategy to past days: choose symbols (several), timeframes (several) and the days. You get a summary message + Excel/CSV with every trade." + busy
               + ("\n\n<b>Recent</b>\n" + "\n".join(lines) if lines else ""),
               kb([[btn("▶️ New backtest", "bt:new")], [btn("📐 Points & decimals table", "inst:menu")], [btn("🏠 Menu", "home")]]))


@router.callback_query(F.data == "bt:new")
async def new(cb: CallbackQuery, ctx: Any, state: FSMContext, role: Role) -> None:
    need(role, Permission.BOT_CONTROL)
    await state.clear()
    strategies = await ctx.strategies.list()
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
    ver = await _active_version(ctx, bt["strategy_id"])
    await start_symbol_pick(cb, state, ctx, bt["account_id"], ver.meta.get("symbols", []), "bt_sym", "Step 3/5 — symbols (select several)")
    await toast(cb, "")


async def _sym_done(cb: CallbackQuery, state: FSMContext, ctx: Any, sel: list[str]) -> None:
    bt = await _bt(state)
    bt["symbols"] = sel
    await _save(state, bt)
    ver = await _active_version(ctx, bt["strategy_id"])
    await start_tf_pick(cb, state, ver.meta.get("timeframes", []), "bt_tf", False, "Step 4/5 — timeframes (select several)")


async def _tf_done(cb: CallbackQuery, state: FSMContext, ctx: Any, sel: list[str]) -> None:
    bt = await _bt(state)
    bt["timeframes"] = sel
    await _save(state, bt)
    await _period_menu(cb, sel)


async def _period_menu(event: Any, tfs: list[str]) -> None:
    await show(event, f"🧪 <b>Step 5/5</b> — which days to test? ({', '.join(tfs)})\nPick a quick range, or choose the exact days from the calendar.",
               kb([[btn("7 days", "bt:r:7"), btn("30 days", "bt:r:30"), btn("90 days", "bt:r:90")],
                   [btn("180 days", "bt:r:180"), btn("1 year", "bt:r:365")], [btn("📅 Choose days (calendar)", "bt:cd")], [btn("❌ Cancel", "bt:menu")]]))


register_flow("bt_sym", _sym_done)
register_flow("bt_tf", _tf_done)


# ---- period: quick ranges + calendar + typed dates -------------------------------------------------------------------
def _today(tz: float) -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=tz)).date()


@router.callback_query(F.data.startswith("bt:r:"))
async def quick_range(cb: CallbackQuery, ctx: Any, state: FSMContext) -> None:
    bt = await _bt(state)
    end = _today(float(bt["opts"]["tz_offset"]))
    bt["date_to"] = end.isoformat()
    bt["date_from"] = (end - timedelta(days=int(split(cb.data)[2]))).isoformat()
    await _save(state, bt)
    await _options(cb, ctx, state)
    await toast(cb, "")


def _calendar_kb(year: int, month: int, stage: str, min_d: date, max_d: date, start: date | None):
    prev_y, prev_m = (year, month - 1) if month > 1 else (year - 1, 12)
    next_y, next_m = (year, month + 1) if month < 12 else (year + 1, 1)
    rows = [[btn("◀️", f"bt:m:{prev_y}{prev_m:02d}"), btn(f"{MONTHS[month]} {year}", "bt:noop"),
             btn("▶️", f"bt:m:{next_y}{next_m:02d}")],
            [btn(x, "bt:noop") for x in ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")]]
    for week in calendar.monthcalendar(year, month):
        row = []
        for d in week:
            if d == 0:
                row.append(btn("·", "bt:noop"))
                continue
            dt = date(year, month, d)
            if dt < min_d or dt > max_d:
                row.append(btn("·", "bt:noop"))
            else:
                label = f"🟢{d}" if start == dt else str(d)
                row.append(btn(label, f"bt:d:{dt:%Y%m%d}"))
        rows.append(row)
    extra = [btn("📅 Today", f"bt:d:{max_d:%Y%m%d}")] if stage == "to" else []
    rows.append(extra + [btn("⌨️ Type dates", "bt:cdt"), btn("❌ Cancel", "bt:menu")])
    return kb(rows)


async def _show_calendar(cb: CallbackQuery, state: FSMContext, year: int | None = None, month: int | None = None) -> None:
    bt = await _bt(state)
    tz = float(bt["opts"]["tz_offset"])
    today = _today(tz)
    stage = bt.get("cal_stage", "from")
    start = date.fromisoformat(bt["date_from"]) if stage == "to" and bt.get("date_from") else None
    min_d = start if start else today - timedelta(days=3650)
    if year is None:
        ref = start or today
        year, month = ref.year, ref.month
    title = "📅 <b>Choose the START day</b>" if stage == "from" else f"📅 <b>Choose the END day</b>  (start: {start})"
    await show(cb, f"{title}\n<i>Days in the future are disabled. The end day is included.</i>",
               _calendar_kb(year, month, stage, min_d, today, start))


@router.callback_query(F.data == "bt:cd")
async def calendar_start(cb: CallbackQuery, state: FSMContext) -> None:
    bt = await _bt(state)
    bt["cal_stage"] = "from"
    bt.pop("date_from", None)
    bt.pop("date_to", None)
    await _save(state, bt)
    await _show_calendar(cb, state)
    await toast(cb, "")


@router.callback_query(F.data.startswith("bt:m:"))
async def calendar_month(cb: CallbackQuery, state: FSMContext) -> None:
    ym = split(cb.data)[2]
    await _show_calendar(cb, state, int(ym[:4]), int(ym[4:]))
    await toast(cb, "")


@router.callback_query(F.data == "bt:noop")
async def noop(cb: CallbackQuery) -> None:
    await toast(cb, "")


@router.callback_query(F.data.startswith("bt:d:"))
async def calendar_day(cb: CallbackQuery, ctx: Any, state: FSMContext) -> None:
    ds = split(cb.data)[2]
    picked = date(int(ds[:4]), int(ds[4:6]), int(ds[6:]))
    bt = await _bt(state)
    if bt.get("cal_stage", "from") == "from":
        bt["date_from"], bt["cal_stage"] = picked.isoformat(), "to"
        await _save(state, bt)
        await _show_calendar(cb, state)
        await toast(cb, f"Start: {picked}")
        return
    if picked < date.fromisoformat(bt["date_from"]):
        await toast(cb, "End day must not be before the start day", True)
        return
    bt["date_to"] = picked.isoformat()
    bt.pop("cal_stage", None)
    await _save(state, bt)
    await _options(cb, ctx, state)
    await toast(cb, f"{bt['date_from']} → {picked}")


def _dates_in(text: str) -> list[str]:
    """Extract dates from free text: 2026-06-01, 2026/6/1, 1/6/2026, 01.06.2026 (Arabic digits ok), or 'today'."""
    s = (text or "").translate(AR_DIGITS).lower()
    found: list[tuple[int, str]] = []
    for m in re.finditer(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s):
        found.append((m.start(), f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"))
    for m in re.finditer(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", s):
        found.append((m.start(), f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"))
    for m in re.finditer(r"today|now|اليوم", s):
        found.append((m.start(), _today(0).isoformat()))
    out = []
    for _, d in sorted(found):
        try:
            date.fromisoformat(d)
        except ValueError:
            raise ValidationFailed(f"'{d}' is not a valid date")
        out.append(d)
    return out


@router.callback_query(F.data == "bt:cdt")
async def type_dates(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BacktestForm.date_from)
    await show(cb, "⌨️ Send the days in ONE message, start then end:\n<code>2026-06-01 2026-06-30</code>\n(or <code>1/6/2026 30/6/2026</code>, or end = <code>today</code>). /cancel to abort.",
               kb([[btn("📅 Use calendar instead", "bt:cd")]]))
    await toast(cb, "")


async def _apply_typed(m: Message, ctx: Any, state: FSMContext, dates: list[str]) -> None:
    bt = await _bt(state)
    start, end = dates[0], dates[1]
    if start > end:
        start, end = end, start
    if date.fromisoformat(end) > _today(float(bt["opts"]["tz_offset"])) + timedelta(days=1):
        raise ValidationFailed("End date cannot be in the future")
    bt["date_from"], bt["date_to"] = start, end
    bt.pop("cal_stage", None)
    await _save(state, bt)
    await state.set_state(None)
    await _options(m, ctx, state)


@router.message(BacktestForm.date_from)
async def date_from_in(m: Message, state: FSMContext, ctx: Any) -> None:
    dates = _dates_in(m.text or "")
    if len(dates) >= 2:
        return await _apply_typed(m, ctx, state, dates)
    if not dates:
        raise ValidationFailed("Send dates like 2026-06-01 2026-06-30")
    bt = await _bt(state)
    bt["date_from"] = dates[0]
    await _save(state, bt)
    await state.set_state(BacktestForm.date_to)
    await m.answer(f"Start: <b>{dates[0]}</b>\nNow send the <b>end date</b> (<code>YYYY-MM-DD</code> or <code>today</code>):", parse_mode="HTML")


@router.message(BacktestForm.date_to)
async def date_to_in(m: Message, state: FSMContext, ctx: Any) -> None:
    dates = _dates_in(m.text or "")
    if not dates:
        raise ValidationFailed("Send the end date like 2026-06-30 or 'today'")
    bt = await _bt(state)
    await _apply_typed(m, ctx, state, [bt["date_from"], dates[-1]])


# ---- options --------------------------------------------------------------------------------------------------------------
async def _options(event: Any, ctx: Any, state: FSMContext) -> None:
    bt = await _bt(state)
    o = bt["opts"]
    st = await ctx.strategies.get(bt["strategy_id"])
    acc = await ctx.accounts.get(bt["account_id"])
    z = lambda v: "from Trade settings" if not v else v          # noqa: E731
    lines = ["🧪 <b>Backtest setup</b>", f"Strategy: <b>{esc(st.name)}</b>", f"History from: {esc(acc.name)} ({acc.environment})",
             f"Symbols: <b>{esc(', '.join(bt['symbols']))}</b>", f"Timeframes: <b>{', '.join(bt['timeframes'])}</b>",
             f"Days: <b>{bt['date_from']} → {bt['date_to']}</b>", "",
             f"• Balance: <b>{o['initial_balance']:g}</b>", f"• Lot size: <b>{z(o['lot_size'])}</b>",
             f"• Stop loss (points): <b>{z(o['sl_points'])}</b>", f"• Take profit target (points): <b>{z(o['tp_points'])}</b>",
             f"• Spread: <b>{o['spread']}</b> (auto: gold 2.5 pts, oil 4, forex 1.2)", f"• Timezone: <b>UTC{o['tz_offset']:+g}</b>", f"• Output: <b>{o['output']}</b>"]
    ts_now = ctx.trade_settings.get()
    g = lambda v: f"{v:g}" if v else "—"                    # noqa: E731
    lines.append("• Used per timeframe (lot · TP · SL, points):")
    for tf in bt["timeframes"]:
        lv = backtest_levels(ts_now, tf, o["sl_points"], o["tp_points"], o["lot_size"])
        lines.append(f"   {tf}: {g(lv['lot'] or 0.10)} · TP {g(lv['tp'])} · SL {g(lv['sl'])}")
    if bt["params"]:
        lines.append(f"• Strategy overrides: <code>{esc(str(bt['params']))}</code>")
    rows = [[btn(OPTS["initial_balance"][0], C("bt", "o", "initial_balance")), btn(OPTS["lot_size"][0], C("bt", "o", "lot_size"))],
            [btn(OPTS["sl_points"][0], C("bt", "o", "sl_points")), btn(OPTS["tp_points"][0], C("bt", "o", "tp_points"))],
            [btn("📏 Spread", C("bt", "o", "spread")), btn(OPTS["tz_offset"][0], C("bt", "o", "tz_offset"))],
            [btn("📤 Output: " + o["output"], "bt:out"), btn("⚙️ Strategy params", "bt:params")],
            [btn("📅 Change days", "bt:cd"), btn("🕒 Change timeframes", "bt:tfs")],
            [btn("🎚 TP / SL per timeframe", "ts:menu:bt"), btn("🔣 Change symbols", "bt:syms")],
            [btn("📐 Points table", "inst:menu")],
            [btn("🚀 RUN BACKTEST", "bt:go")], [btn("❌ Cancel", "bt:menu")]]
    await show(event, "\n".join(lines), kb(rows))


@router.callback_query(F.data == "bt:opts")
async def opts_cb(cb: CallbackQuery, ctx: Any, state: FSMContext) -> None:
    await _options(cb, ctx, state)
    await toast(cb, "")


@router.callback_query(F.data == "bt:tfs")
async def change_tfs(cb: CallbackQuery, state: FSMContext) -> None:
    bt = await _bt(state)
    await start_tf_pick(cb, state, bt.get("timeframes", []), "bt_tf2", False, "Timeframes (select several)")
    await toast(cb, "")


@router.callback_query(F.data == "bt:syms")
async def change_syms(cb: CallbackQuery, ctx: Any, state: FSMContext) -> None:
    bt = await _bt(state)
    await start_symbol_pick(cb, state, ctx, bt["account_id"], bt.get("symbols", []), "bt_sym2", "Symbols (select several)")
    await toast(cb, "")


async def _tf2_done(cb: CallbackQuery, state: FSMContext, ctx: Any, sel: list[str]) -> None:
    bt = await _bt(state)
    bt["timeframes"] = sel
    await _save(state, bt)
    await _options(cb, ctx, state)


async def _sym2_done(cb: CallbackQuery, state: FSMContext, ctx: Any, sel: list[str]) -> None:
    bt = await _bt(state)
    bt["symbols"] = sel
    await _save(state, bt)
    await _options(cb, ctx, state)


register_flow("bt_tf2", _tf2_done)
register_flow("bt_sym2", _sym2_done)


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
        await show(cb, "Send the spread in <b>points</b> for every symbol (e.g. <code>2.5</code>), or <code>auto</code> for per-instrument defaults.\nGold: 1 point = 0.1 → spread 0.30 = 3 points.")
    else:
        await show(cb, f"<b>{esc(OPTS[key][1])}</b>\nSend a number between {OPTS[key][3]:g} and {OPTS[key][4]:g}.")
    await toast(cb, "")


@router.message(BacktestForm.option)
async def opt_value(m: Message, state: FSMContext, ctx: Any) -> None:
    d = await state.get_data()
    key, raw = d["opt_key"], (m.text or "").strip().translate(AR_DIGITS).replace(",", ".").replace("٫", ".")
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
                raise ValidationFailed("Spread must be between 0 and 1000 points")
            bt["opts"]["spread"] = v
    else:
        _, _, typ, lo, hi = OPTS[key]
        try:
            v = float(raw)
        except ValueError:
            raise ValidationFailed("Send a number")
        if not lo <= v <= hi:
            raise ValidationFailed(f"Value must be between {lo:g} and {hi:g}")
        bt["opts"][key] = v
    await _save(state, bt)
    await state.set_state(None)
    await _options(m, ctx, state)


# ---- strategy parameter overrides -----------------------------------------------------------------------------------------------
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
    key, cur, raw = d["param_key"], ver.parameters[d["param_key"]], (m.text or "").strip().translate(AR_DIGITS)
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


# ---- run ---------------------------------------------------------------------------------------------------------------------------------
def _to_ms(date_s: str, tz: float, end: bool = False) -> int:
    d = datetime.strptime(date_s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end:
        d += timedelta(days=1)
    return int((d.timestamp() - tz * 3600) * 1000)


@router.callback_query(F.data == "bt:go")
async def run(cb: CallbackQuery, ctx: Any, state: FSMContext, role: Role, bot: Bot) -> None:
    need(role, Permission.BOT_CONTROL)
    bt = await _bt(state)
    for k in ("strategy_id", "account_id", "symbols", "timeframes", "date_from", "date_to"):
        if k not in bt:
            await toast(cb, "Setup incomplete — start again (/backtest)", True)
            return
    tz = float(bt["opts"]["tz_offset"])
    to_ms = min(_to_ms(bt["date_to"], tz, True), int(time.time() * 1000))
    req = BtRequest(user_id=cb.from_user.id, strategy_id=bt["strategy_id"], account_id=bt["account_id"], symbols=bt["symbols"], timeframes=bt["timeframes"],
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
            await bot.send_document(chat_id, BufferedInputFile(outcome.xlsx, filename=f"{outcome.filename_base}.xlsx"), caption="📊 Excel: الملخص (مقارنة الفريمات) · الصفقات · حسب الرمز · يومي · شهري · منحنى الرصيد · الإعدادات")
        if outcome.csv:
            await bot.send_document(chat_id, BufferedInputFile(outcome.csv, filename=f"{outcome.filename_base}.csv"), caption="🧾 CSV: all trades (all timeframes)")
        await bot.send_message(chat_id, "Done ✅", reply_markup=kb([[btn("🧪 Another backtest", "bt:new")], [btn("🏠 Menu", "home")]]))

    job = ctx.backtests.start_job(req, chat_id, progress, done)
    await state.update_data(job_id=job.job_id)
    await ctx.audit.record(action="BACKTEST_START", user_id=cb.from_user.id, target=bt["strategy_id"],
                           new_state={"symbols": bt["symbols"], "tfs": bt["timeframes"], "from": bt["date_from"], "to": bt["date_to"]})
    await toast(cb, "Started")


@router.callback_query(F.data == "bt:cancel")
async def cancel(cb: CallbackQuery, ctx: Any) -> None:
    n = 0
    for job in list(ctx.backtests.jobs.values()):
        if job.user_id == cb.from_user.id:
            job.cancel.set()
            n += 1
    await toast(cb, "Cancelling…" if n else "No running backtest", False)
