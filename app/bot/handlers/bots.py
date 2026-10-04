from __future__ import annotations

import json
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks.data import cb as C, split
from app.bot.handlers.common import confirm_gate, env_tag, esc, fmt_ago, need, num, pnl, run_command, show, status_icon, toast
from app.bot.keyboards.common import back_home, btn, kb, nav_row, paginate
from app.bot.states.forms import EditBot, NewBot
from app.core.enums import Role
from app.core.exceptions import ValidationFailed
from app.security.rbac import Permission

router = Router(name="bots")


def line(b: Any) -> str:
    return f"{status_icon(b.status)} {esc(b.name)} · {b.status} · {env_tag(b.environment)}"


@router.callback_query(F.data == "bot:menu")
async def menu(cb: CallbackQuery, ctx: Any) -> None:
    c = await ctx.bots.counts()
    summary = ", ".join(f"{k.lower()} {v}" for k, v in sorted(c.items())) or "no bots"
    await show(cb, f"🤖 <b>Trading Bots</b>\n{summary}", kb([[btn("➕ Create bot", "bot:new"), btn("📋 My bots", "bot:list:0")],
                                                            [btn("⏹ Stop ALL bots", "bot:stopall")], [btn("🏠 Menu", "home")]]))
    await toast(cb, "")


@router.callback_query(F.data.startswith("bot:list:"))
async def bot_list(cb: CallbackQuery, ctx: Any) -> None:
    items, page, pages = paginate(await ctx.bots.list(), int(split(cb.data)[2]))
    rows = [[btn(line(b)[:60], C("bot", "view", b.id))] for b in items]
    if pages > 1:
        rows.append(nav_row("bot:list", page, pages))
    rows.append(back_home("bot:menu"))
    await show(cb, "🤖 <b>Bots</b>" + ("" if items else "\nNone yet."), kb(rows))
    await toast(cb, "")


async def _view(event: CallbackQuery | Message, ctx: Any, bot_id: str) -> None:
    b = await ctx.bots.get(bot_id)
    acc = await ctx.accounts.get(b.account_id)
    st = await ctx.strategies.get(b.strategy_id)
    ver = await ctx.strategies.get_version(b.strategy_version_id)
    runner = ctx.bots.runners.get(b.id)
    proc = f"pid {runner.host.pid}" if runner and runner.host and runner.host.alive else "no process"
    text = (f"{line(b)}\n<b>Account</b> {esc(acc.name)} ({acc.status})\n<b>Strategy</b> {esc(st.name)}@{ver.version}"
            f"{' (active)' if st.active_version_id == ver.id else ' (pinned, not active)'}\n<b>Symbols</b> {', '.join(b.symbols)} · <b>TF</b> {', '.join(b.timeframes)}\n"
            f"<b>Params</b> <code>{esc(json.dumps({**ver.parameters, **(b.parameters or {})})[:300])}</code>\n"
            f"Started {fmt_ago(b.started_at)} · heartbeat {fmt_ago(b.last_heartbeat_at)} · {proc}\nLast signal {fmt_ago(b.last_signal_at)} · last order {fmt_ago(b.last_order_at)} · restarts {b.restart_count}"
            + (f"\n🔒 Lock: {esc(b.lock_reason)}" if b.lock_reason else "") + (f"\n❗ {esc(b.last_error[:200])}" if b.last_error else ""))
    rows = [[btn("▶️ Start", C("bot", "start", bot_id)), btn("⏸ Pause", C("bot", "pause", bot_id)), btn("⏯ Resume", C("bot", "resume", bot_id))],
            [btn("⏹ Stop", C("bot", "stop", bot_id)), btn("🔁 Restart", C("bot", "restart", bot_id))],
            [btn("📊 Stats", C("bot", "stats", bot_id)), btn("📜 Signals", C("bot", "sigs", bot_id, 0))],
            [btn("⚙️ Settings", C("bot", "set", bot_id)), btn("🗑 Delete", C("bot", "del", bot_id))],
            [btn("🔄 Refresh", C("bot", "view", bot_id))], back_home("bot:list:0")]
    await show(event, text, kb(rows))


@router.callback_query(F.data.startswith("bot:view:"))
async def bot_view(cb: CallbackQuery, ctx: Any) -> None:
    await _view(cb, ctx, split(cb.data)[2])
    await toast(cb, "")


async def _lifecycle(cb: CallbackQuery, ctx: Any, role: Role, action: str, perm: Permission = Permission.BOT_CONTROL) -> None:
    need(role, perm)
    bid = split(cb.data)[2]
    b = await ctx.bots.get(bid)
    fn = getattr(ctx.bots, action)
    live = b.environment == "LIVE"
    if action in ("stop", "restart") or (action == "start" and live):
        verb = {"start": "Start", "stop": "Stop", "restart": "Restart"}[action]
        if not await confirm_gate(cb, title=f"{verb} bot <b>{esc(b.name)}</b>?", live=live and action == "start", cancel=C("bot", "view", bid)):
            return
    await toast(cb, f"{action}…")
    _, res = await run_command(ctx, cb, f"BOT_{action.upper()}", lambda cid: fn(bid, cb.from_user.id), target_type="bot", target_id=bid)
    if isinstance(res, str):
        await toast(cb, res)
    await _view(cb, ctx, bid)


for _a in ("start", "pause", "resume", "stop", "restart"):
    def _make(a: str):
        async def h(cb: CallbackQuery, ctx: Any, role: Role) -> None:
            await _lifecycle(cb, ctx, role, a)
        h.__name__ = f"bot_{a}"
        return h
    router.callback_query.register(_make(_a), F.data.startswith(f"bot:{_a}:"))


@router.callback_query(F.data.startswith("bot:stopall"))
async def stop_all(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.BOT_CONTROL)
    if not await confirm_gate(cb, title="Stop ALL bots?", live=False, cancel="bot:menu"):
        return
    _, n = await run_command(ctx, cb, "BOT_STOP_ALL", lambda cid: ctx.bots.stop_all(cb.from_user.id))
    await show(cb, f"⏹ Stopped {n} bot(s).", kb([back_home("bot:menu")]))


@router.callback_query(F.data.startswith("bot:stats:"))
async def bot_stats(cb: CallbackQuery, ctx: Any) -> None:
    bid = split(cb.data)[2]
    s = await ctx.bots.stats(bid)
    await show(cb, f"📊 <b>Bot statistics</b>\nSignals {s['signals']} (rejected {s['rejected']})\nOrders {s['orders']} · Trades {s['trades']} · wins {s['wins']} · win rate {s['win_rate']:.1f}%\nNet P/L {pnl(s['net_pnl'])}",
               kb([back_home(C("bot", "view", bid))]))
    await toast(cb, "")


@router.callback_query(F.data.startswith("bot:sigs:"))
async def bot_sigs(cb: CallbackQuery, ctx: Any) -> None:
    from sqlalchemy import select
    from app.models import Signal
    _, _, bid, page = split(cb.data)[:4]
    async with ctx.db.session() as s:
        rows = list((await s.execute(select(Signal).where(Signal.bot_id == bid).order_by(Signal.created_at.desc()).limit(60))).scalars())
    items, page, pages = paginate(rows, int(page), 8)
    text = "📜 <b>Recent signals</b>\n" + ("\n".join(f"{'✅' if x.risk_decision == 'APPROVED' else '⛔'} {x.side} {x.symbol} · {esc(x.risk_reason or '?')} · {fmt_ago(x.created_at)}" for x in items) or "None")
    nav = nav_row(f"bot:sigs:{bid}", page, pages)
    await show(cb, text, kb([nav, back_home(C("bot", "view", bid))]))
    await toast(cb, "")


@router.callback_query(F.data.startswith("bot:del:"))
async def bot_del(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.BOT_MANAGE)
    bid = split(cb.data)[2]
    b = await ctx.bots.get(bid)
    if not await confirm_gate(cb, title=f"Delete bot <b>{esc(b.name)}</b>? History is kept.", live=False, cancel=C("bot", "view", bid)):
        return
    await run_command(ctx, cb, "BOT_DELETE", lambda cid: ctx.bots.delete(bid, cb.from_user.id), target_type="bot", target_id=bid)
    await show(cb, "🗑 Bot deleted.", kb([back_home("bot:menu")]))


# ---- settings / edit -----------------------------------------------------------------------------
@router.callback_query(F.data.startswith("bot:set:"))
async def bot_set(cb: CallbackQuery, ctx: Any) -> None:
    bid = split(cb.data)[2]
    b = await ctx.bots.get(bid)
    ver = await ctx.strategies.get_version(b.strategy_version_id)
    prows = [[btn(f"{k} = {({**ver.parameters, **(b.parameters or {})})[k]}"[:40], C("bot", "pe", bid, k))] for k in list(ver.parameters)[:10]]
    rows = [[btn("🔣 Symbols", C("bot", "esym", bid)), btn("🕒 Timeframes", C("bot", "etf", bid))],
            [btn("🧬 Strategy version", C("bot", "ever", bid)), btn("🛡 Risk profile", C("bot", "erisk", bid))]] + prows + [back_home(C("bot", "view", bid))]
    await show(cb, f"⚙️ <b>{esc(b.name)}</b> settings" + ("\n⚠️ Stop the bot to change settings." if b.status in ("RUNNING", "STARTING", "PAUSED", "LOCKED") else ""), kb(rows))
    await toast(cb, "")


@router.callback_query(F.data.startswith("bot:esym:") | F.data.startswith("bot:etf:") | F.data.startswith("bot:pe:"))
async def bot_edit_start(cb: CallbackQuery, state: FSMContext, role: Role) -> None:
    need(role, Permission.BOT_MANAGE)
    parts = split(cb.data)
    kind, bid = parts[1], parts[2]
    await state.update_data(bid=bid, key=parts[3] if len(parts) > 3 else None)
    await state.set_state({"esym": EditBot.symbols, "etf": EditBot.timeframes, "pe": EditBot.param}[kind])
    prompt = {"esym": "Send symbols, comma separated (e.g. EURUSD,GBPUSD)", "etf": "Send timeframes, comma separated (M1,M5,M15,M30,H1,H4,D1)", "pe": f"Send the new value for <b>{esc(parts[3] if len(parts) > 3 else '')}</b>"}[kind]
    await show(cb, prompt + "\n/cancel to abort.")
    await toast(cb, "")


@router.message(EditBot.symbols)
async def edit_symbols(m: Message, state: FSMContext, ctx: Any, role: Role) -> None:
    need(role, Permission.BOT_MANAGE)
    bid = (await state.get_data())["bid"]
    await ctx.bots.update(bid, m.from_user.id, symbols=(m.text or "").replace(";", ",").split(","))
    await state.clear()
    await _view(m, ctx, bid)


@router.message(EditBot.timeframes)
async def edit_tf(m: Message, state: FSMContext, ctx: Any, role: Role) -> None:
    need(role, Permission.BOT_MANAGE)
    bid = (await state.get_data())["bid"]
    await ctx.bots.update(bid, m.from_user.id, timeframes=[t.strip() for t in (m.text or "").split(",") if t.strip()])
    await state.clear()
    await _view(m, ctx, bid)


@router.message(EditBot.param)
async def edit_param(m: Message, state: FSMContext, ctx: Any, role: Role) -> None:
    need(role, Permission.BOT_MANAGE)
    d = await state.get_data()
    b = await ctx.bots.get(d["bid"])
    ver = await ctx.strategies.get_version(b.strategy_version_id)
    cur = ver.parameters[d["key"]]
    raw = (m.text or "").strip()
    try:
        val: Any = (raw.lower() in ("true", "1", "yes", "on")) if isinstance(cur, bool) else int(raw) if isinstance(cur, int) else float(raw) if isinstance(cur, float) else raw[:200]
    except ValueError:
        raise ValidationFailed(f"Expected {type(cur).__name__}")
    await ctx.bots.update(d["bid"], m.from_user.id, parameters={d["key"]: val})
    await state.clear()
    await _view(m, ctx, d["bid"])


@router.callback_query(F.data.startswith("bot:ever:"))
async def bot_ever(cb: CallbackQuery, ctx: Any) -> None:
    bid = split(cb.data)[2]
    b = await ctx.bots.get(bid)
    vers = await ctx.strategies.versions(b.strategy_id)
    rows = [[btn(("✅ " if v.id == b.strategy_version_id else "") + f"v{v.version}" + (" ★active" if v.is_active else ""), C("bot", "sv", bid, v.id))] for v in vers[:8]]
    rows.append(back_home(C("bot", "set", bid)))
    await show(cb, "🧬 Choose strategy version for this bot:", kb(rows))
    await toast(cb, "")


@router.callback_query(F.data.startswith("bot:sv:"))
async def bot_sv(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.BOT_MANAGE)
    _, _, bid, vid = split(cb.data)[:4]
    await ctx.bots.update(bid, cb.from_user.id, version_id=vid)
    await toast(cb, "Version updated")
    await _view(cb, ctx, bid)


@router.callback_query(F.data.startswith("bot:erisk:"))
async def bot_erisk(cb: CallbackQuery, ctx: Any) -> None:
    from sqlalchemy import select
    from app.models import RiskProfile
    bid = split(cb.data)[2]
    b = await ctx.bots.get(bid)
    async with ctx.db.session() as s:
        profiles = (await s.execute(select(RiskProfile).order_by(RiskProfile.name))).scalars().all()
    rows = [[btn(("✅ " if p.id == b.risk_profile_id else "") + p.name[:30], C("bot", "rp", bid, p.id))] for p in profiles[:8]]
    rows.append(back_home(C("bot", "set", bid)))
    await show(cb, "🛡 Risk profile for this bot:", kb(rows))
    await toast(cb, "")


@router.callback_query(F.data.startswith("bot:rp:"))
async def bot_rp(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.BOT_MANAGE)
    _, _, bid, pid = split(cb.data)[:4]
    await ctx.bots.update(bid, cb.from_user.id, risk_profile_id=pid)
    await toast(cb, "Risk profile updated")
    await _view(cb, ctx, bid)


# ---- creation wizard -----------------------------------------------------------------------------------
@router.callback_query(F.data == "bot:new")
async def new_start(cb: CallbackQuery, state: FSMContext, role: Role) -> None:
    need(role, Permission.BOT_MANAGE)
    await state.set_state(NewBot.name)
    await show(cb, "➕ <b>New bot</b>\nSend a name for the bot (or /cancel):")
    await toast(cb, "")


@router.message(NewBot.name)
async def new_name(m: Message, state: FSMContext, ctx: Any) -> None:
    name = (m.text or "").strip()
    if not (1 <= len(name) <= 64):
        raise ValidationFailed("Name must be 1-64 characters")
    await state.update_data(name=name)
    accs = await ctx.accounts.list()
    if not accs:
        await state.clear()
        await m.answer("Add an account first.", reply_markup=kb([back_home("acc:menu")]))
        return
    await m.answer("Choose the account:", reply_markup=kb([[btn(f"{a.name[:24]} · {a.environment}", C("bot", "na", a.id))] for a in accs[:10]] + [[btn("❌ Cancel", "home")]]))


@router.callback_query(F.data.startswith("bot:na:"))
async def new_account(cb: CallbackQuery, state: FSMContext, ctx: Any) -> None:
    await state.update_data(account_id=split(cb.data)[2])
    strats = [s for s in await ctx.strategies.list() if s.active_version_id]
    if not strats:
        await show(cb, "No strategy with an active version. Upload & activate one first.", kb([back_home("str:menu")]))
        return
    await show(cb, "Choose the strategy (active version will be used):", kb([[btn(s.name[:40], C("bot", "ns", s.id))] for s in strats[:10]] + [[btn("❌ Cancel", "home")]]))
    await toast(cb, "")


@router.callback_query(F.data.startswith("bot:ns:"))
async def new_strategy(cb: CallbackQuery, state: FSMContext, ctx: Any) -> None:
    sid = split(cb.data)[2]
    st = await ctx.strategies.get(sid)
    ver = await ctx.strategies.get_version(st.active_version_id)
    await state.update_data(strategy_id=sid)
    await state.set_state(NewBot.symbols)
    hint = ", ".join(ver.meta.get("symbols", [])) or "EURUSD"
    await show(cb, f"Send the symbols to trade, comma separated.\nSuggested by strategy: <code>{esc(hint)}</code>")
    await toast(cb, "")


@router.message(NewBot.symbols)
async def new_symbols(m: Message, state: FSMContext) -> None:
    syms = [s.strip().upper() for s in (m.text or "").replace(";", ",").split(",") if s.strip()]
    if not syms:
        raise ValidationFailed("Provide at least one symbol")
    await state.update_data(symbols=syms)
    await state.set_state(NewBot.timeframes)
    await m.answer("Send timeframes, comma separated (M1,M5,M15,M30,H1,H4,D1), e.g. <code>M5</code>", parse_mode="HTML")


@router.message(NewBot.timeframes)
async def new_tf(m: Message, state: FSMContext, ctx: Any, role: Role) -> None:
    need(role, Permission.BOT_MANAGE)
    d = await state.get_data()
    tfs = [t.strip().upper() for t in (m.text or "").split(",") if t.strip()] or ["M5"]
    bot = await ctx.bots.create(name=d["name"], account_id=d["account_id"], strategy_id=d["strategy_id"], version_id=None, symbols=d["symbols"],
                                timeframes=tfs, parameters=None, risk_profile_id=None, user_id=m.from_user.id)
    await state.clear()
    await _view(m, ctx, bot.id)
