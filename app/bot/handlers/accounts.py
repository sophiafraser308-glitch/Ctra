from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from app.bot.callbacks.data import cb as C, split
from app.bot.handlers.common import confirm_gate, env_tag, esc, fmt_ago, need, num, pnl, run_command, show, status_icon, toast
from app.bot.keyboards.common import back_home, btn, kb, nav_row, paginate
from app.bot.states.forms import AddAccount, EditAccount
from app.core.enums import Role
from app.core.exceptions import ValidationFailed
from app.models import Trade, RiskProfile
from app.security.rbac import Permission

router = Router(name="accounts")
MODES = [("conn", "🔌 Connect"), ("disc", "⏏️ Disconnect"), ("reconn", "🔁 Reconnect"), ("refresh", "🔄 Refresh"), ("view", "ℹ️ Details"),
         ("bal", "💰 Balance"), ("stats", "📊 Statistics"), ("pos", "📈 Positions"), ("ord", "🧾 Orders"), ("risk", "🛡 Risk Settings"),
         ("edit", "✏️ Edit"), ("rm", "🗑 Remove")]


def acc_line(a: Any) -> str:
    return f"{status_icon(a.status)} {esc(a.name)} · {env_tag(a.environment)} · {a.ctid_trader_account_id}"


async def _detail_text(ctx: Any, a: Any) -> str:
    sess_ok = ctx.gateway.is_connected(a.id)
    prof = await ctx.risk.profile_for(a)
    return (f"{acc_line(a)}\n<b>Status</b> {a.status}{' (session live)' if sess_ok else ''}\n"
            f"<b>Broker</b> {esc(a.broker or '—')} · login {a.trader_login or '—'} · leverage {num(a.leverage, 0)}\n"
            f"<b>Balance</b> {num(a.balance)} {esc(a.currency or '')} · <b>Equity</b> {num(a.equity)}\n"
            f"<b>Margin used</b> {num(a.used_margin)} · <b>Free</b> {num(a.free_margin)} · <b>Floating</b> {pnl(a.unrealized_pnl)}\n"
            f"<b>Trading permission</b> {'ON' if a.trading_enabled else 'OFF'} · <b>Risk profile</b> {esc(prof.name)}\n"
            f"<b>Last sync</b> {fmt_ago(a.last_sync_at)}" + (f"\n<b>Last error</b> {esc(a.last_error[:150])}" if a.last_error else ""))


def _detail_kb(a: Any):
    return kb([[btn("🔌 Connect", C("acc", "conn", a.id)), btn("⏏️ Disconnect", C("acc", "disc", a.id)), btn("🔁 Reconnect", C("acc", "reconn", a.id))],
               [btn("🔄 Refresh", C("acc", "refresh", a.id)), btn("💰 Balance", C("acc", "bal", a.id)), btn("📊 Stats", C("acc", "stats", a.id))],
               [btn("📈 Positions", C("pos", "acc", a.id, 0)), btn("🧾 Orders", C("ord", "acc", a.id, 0)), btn("🛡 Risk", C("acc", "risk", a.id))],
               [btn("✏️ Edit", C("acc", "edit", a.id)), btn("🗑 Remove", C("acc", "rm", a.id))], back_home("acc:menu")])


@router.callback_query(F.data == "acc:menu")
async def menu(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    n = await ctx.accounts.counts()
    rows = [[btn("➕ Add Account", "acc:add"), btn("📋 My Accounts", "acc:list:0")]]
    mode_btns = [btn(t, C("acc", "pick", m, 0)) for m, t in MODES]
    rows += [mode_btns[i:i + 2] for i in range(0, len(mode_btns), 2)]
    rows.append([btn("🏠 Menu", "home")])
    await show(cb, f"🏦 <b>Accounts</b>\n{n['total']} total · demo {n['demo']} · live {n['live']} · connected {n['connected']}", kb(rows))
    await toast(cb, "")


@router.callback_query(F.data.startswith("acc:list:"))
async def acc_list(cb: CallbackQuery, ctx: Any) -> None:
    page = int(split(cb.data)[2])
    items, page, pages = paginate(await ctx.accounts.list(), page)
    rows = [[btn(acc_line(a)[:60], C("acc", "view", a.id))] for a in items]
    if pages > 1:
        rows.append(nav_row("acc:list", page, pages))
    rows.append(back_home("acc:menu"))
    await show(cb, "🏦 <b>Accounts</b>" + ("" if items else "\nNone yet — press ➕ Add Account."), kb(rows))
    await toast(cb, "")


@router.callback_query(F.data.startswith("acc:pick:"))
async def acc_pick(cb: CallbackQuery, ctx: Any) -> None:
    _, _, mode, page = split(cb.data)[:4]
    items, p, pages = paginate(await ctx.accounts.list(), int(page))
    def target(a: Any) -> str:
        return {"pos": C("pos", "acc", a.id, 0), "ord": C("ord", "acc", a.id, 0)}.get(mode, C("acc", mode, a.id))
    rows = [[btn(acc_line(a)[:60], target(a))] for a in items]
    if pages > 1:
        rows.append(nav_row(f"acc:pick:{mode}", p, pages))
    rows.append(back_home("acc:menu"))
    title = dict(MODES).get(mode, mode)
    await show(cb, f"{title} — choose an account:" + ("" if items else "\nNo accounts."), kb(rows))
    await toast(cb, "")


@router.callback_query(F.data.startswith("acc:view:"))
async def acc_view(cb: CallbackQuery, ctx: Any) -> None:
    a = await ctx.accounts.get(split(cb.data)[2])
    await show(cb, await _detail_text(ctx, a), _detail_kb(a))
    await toast(cb, "")


@router.callback_query(F.data.startswith("acc:conn:"))
async def acc_conn(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.ACCOUNT_MANAGE)
    aid = split(cb.data)[2]
    await toast(cb, "Connecting…")
    ex, _ = await run_command(ctx, cb, "ACCOUNT_CONNECT", lambda cid: ctx.accounts.connect(aid, cb.from_user.id), target_type="account", target_id=aid)
    a = await ctx.accounts.get(aid)
    await show(cb, await _detail_text(ctx, a), _detail_kb(a))


@router.callback_query(F.data.startswith("acc:disc:"))
async def acc_disc(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.ACCOUNT_MANAGE)
    aid = split(cb.data)[2]
    a = await ctx.accounts.get(aid)
    if not await confirm_gate(cb, title=f"Disconnect <b>{esc(a.name)}</b>? Running bots on it cannot trade until reconnected.", live=False, cancel=C("acc", "view", aid)):
        return
    await run_command(ctx, cb, "ACCOUNT_DISCONNECT", lambda cid: ctx.accounts.disconnect(aid, cb.from_user.id), target_type="account", target_id=aid)
    a = await ctx.accounts.get(aid)
    await show(cb, await _detail_text(ctx, a), _detail_kb(a))


@router.callback_query(F.data.startswith("acc:reconn:"))
async def acc_reconn(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.ACCOUNT_MANAGE)
    aid = split(cb.data)[2]
    await toast(cb, "Reconnecting…")
    await run_command(ctx, cb, "ACCOUNT_RECONNECT", lambda cid: ctx.accounts.reconnect(aid, cb.from_user.id), target_type="account", target_id=aid)
    a = await ctx.accounts.get(aid)
    await show(cb, await _detail_text(ctx, a), _detail_kb(a))


@router.callback_query(F.data.startswith("acc:refresh:"))
async def acc_refresh(cb: CallbackQuery, ctx: Any) -> None:
    aid = split(cb.data)[2]
    await toast(cb, "Syncing with broker…")
    await ctx.accounts.refresh(aid)
    a = await ctx.accounts.get(aid)
    await show(cb, await _detail_text(ctx, a), _detail_kb(a))


@router.callback_query(F.data.startswith("acc:bal:"))
async def acc_bal(cb: CallbackQuery, ctx: Any) -> None:
    aid = split(cb.data)[2]
    a = await ctx.accounts.get(aid)
    st = await ctx.risk.get_state(aid)
    text = (f"💰 <b>{esc(a.name)}</b> ({a.environment})\nBalance <b>{num(a.balance)}</b> {esc(a.currency or '')}\nEquity <b>{num(a.equity)}</b>\n"
            f"Used margin {num(a.used_margin)} · Free margin {num(a.free_margin)}\nFloating P/L {pnl(a.unrealized_pnl)}\n"
            f"Day-start balance {num(st.day_start_balance)} · Realized today {pnl(st.realized_today)}\nPeak equity {num(st.peak_equity)}\nSynced {fmt_ago(a.last_sync_at)}")
    await show(cb, text, kb([[btn("🔄 Refresh", C("acc", "refresh", aid))], back_home(C("acc", "view", aid))]))
    await toast(cb, "")


@router.callback_query(F.data.startswith("acc:stats:"))
async def acc_stats(cb: CallbackQuery, ctx: Any) -> None:
    aid = split(cb.data)[2]
    a = await ctx.accounts.get(aid)
    st = await ctx.risk.get_state(aid)
    async with ctx.db.session() as s:
        trades = (await s.execute(select(Trade).where(Trade.account_id == aid))).scalars().all()
    n, wins = len(trades), sum(1 for t in trades if t.net_pnl > 0)
    net = sum(t.net_pnl for t in trades)
    dd = ((st.peak_equity - a.equity) / st.peak_equity * 100) if st.peak_equity and a.equity is not None else 0.0
    text = (f"📊 <b>{esc(a.name)}</b> statistics\nTrades {n} · wins {wins} · win rate {(wins / n * 100) if n else 0:.1f}%\nNet P/L {pnl(net)}\n"
            f"Trades today {st.trades_today} · realized today {pnl(st.realized_today)}\nConsecutive losses {st.consecutive_losses}\nCurrent drawdown {dd:.2f}%")
    await show(cb, text, kb([back_home(C("acc", "view", aid))]))
    await toast(cb, "")


@router.callback_query(F.data.startswith("acc:risk:"))
async def acc_risk(cb: CallbackQuery, ctx: Any) -> None:
    await _risk_view(cb, ctx, split(cb.data)[2])


async def _risk_view(cb: CallbackQuery, ctx: Any, aid: str) -> None:
    a = await ctx.accounts.get(aid)
    cur = await ctx.risk.profile_for(a)
    async with ctx.db.session() as s:
        profiles = (await s.execute(select(RiskProfile).order_by(RiskProfile.name))).scalars().all()
    rows = [[btn(("✅ " if p.id == cur.id else "") + p.name[:30], C("acc", "setrisk", aid, p.id))] for p in profiles[:8]]
    rows.append([btn("✏️ Edit current profile", C("risk", "view", cur.id))])
    rows.append(back_home(C("acc", "view", aid)))
    await show(cb, f"🛡 <b>{esc(a.name)}</b> risk profile: <b>{esc(cur.name)}</b>\nPick another profile or edit the current one.", kb(rows))
    await toast(cb, "")


@router.callback_query(F.data.startswith("acc:setrisk:"))
async def acc_setrisk(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.RISK_MANAGE)
    _, _, aid, pid = split(cb.data)
    await ctx.accounts.set_risk_profile(aid, pid, cb.from_user.id)
    await toast(cb, "Risk profile updated")
    await _risk_view(cb, ctx, aid)


@router.callback_query(F.data.startswith("acc:edit:"))
async def acc_edit(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    aid = split(cb.data)[2]
    a = await ctx.accounts.get(aid)
    await show(cb, f"✏️ <b>{esc(a.name)}</b>\nTrading permission: <b>{'ON' if a.trading_enabled else 'OFF'}</b>",
               kb([[btn("✏️ Rename", C("acc", "rename", aid))], [btn(("⛔ Disable" if a.trading_enabled else "✅ Enable") + " trading", C("acc", "tp", aid))], back_home(C("acc", "view", aid))]))
    await toast(cb, "")


@router.callback_query(F.data.startswith("acc:rename:"))
async def acc_rename(cb: CallbackQuery, state: FSMContext, role: Role) -> None:
    need(role, Permission.ACCOUNT_MANAGE)
    await state.set_state(EditAccount.rename)
    await state.update_data(aid=split(cb.data)[2])
    await show(cb, "Send the new account name (or /cancel):")
    await toast(cb, "")


@router.message(EditAccount.rename)
async def acc_rename_in(m: Message, state: FSMContext, ctx: Any, role: Role) -> None:
    need(role, Permission.ACCOUNT_MANAGE)
    aid = (await state.get_data())["aid"]
    await ctx.accounts.rename(aid, m.text or "", m.from_user.id)
    await state.clear()
    a = await ctx.accounts.get(aid)
    await show(m, await _detail_text(ctx, a), _detail_kb(a))


@router.callback_query(F.data.startswith("acc:tp:"))
async def acc_tp(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.ACCOUNT_MANAGE)
    aid = split(cb.data)[2]
    a = await ctx.accounts.get(aid)
    enabling = not a.trading_enabled
    if enabling and not await confirm_gate(cb, title=f"Enable trading on <b>{esc(a.name)}</b> ({a.environment})?", live=a.environment == "LIVE", cancel=C("acc", "edit", aid)):
        return
    if not enabling:
        # disabling is a safe direction: no confirmation chain needed, but strip any c-token
        pass
    await ctx.accounts.set_trading_enabled(aid, enabling, cb.from_user.id)
    await toast(cb, "Trading enabled" if enabling else "Trading disabled")
    a = await ctx.accounts.get(aid)
    await show(cb, await _detail_text(ctx, a), _detail_kb(a))


@router.callback_query(F.data.startswith("acc:rm:"))
async def acc_rm(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.ACCOUNT_MANAGE)
    aid = split(cb.data)[2]
    a = await ctx.accounts.get(aid)
    if not await confirm_gate(cb, title=f"Remove account <b>{esc(a.name)}</b>? Stored tokens will be deleted.", live=a.environment == "LIVE", cancel=C("acc", "view", aid)):
        return
    await run_command(ctx, cb, "ACCOUNT_REMOVE", lambda cid: ctx.accounts.remove(aid, cb.from_user.id), target_type="account", target_id=aid)
    await show(cb, "🗑 Account removed.", kb([back_home("acc:menu")]))


# ---- add account flow --------------------------------------------------------------------------
@router.callback_query(F.data == "acc:add")
async def acc_add(cb: CallbackQuery, state: FSMContext, ctx: Any, role: Role) -> None:
    need(role, Permission.ACCOUNT_MANAGE)
    if not ctx.settings.ctrader_configured:
        await show(cb, "⚠️ cTrader application is not configured. Set CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET and CTRADER_REDIRECT_URI, then restart.", kb([back_home("acc:menu")]))
        return
    await state.set_state(AddAccount.name)
    await show(cb, "➕ <b>Add account</b>\nSend a name for this account (e.g. <i>Main demo</i>), or /cancel.")
    await toast(cb, "")


@router.message(AddAccount.name)
async def acc_add_name(m: Message, state: FSMContext) -> None:
    name = (m.text or "").strip()
    if not (1 <= len(name) <= 64):
        raise ValidationFailed("Name must be 1-64 characters")
    await state.update_data(name=name)
    await m.answer(f"Name: <b>{esc(name)}</b>\nWhich environment? This is a permanent choice for this account.", parse_mode="HTML",
                   reply_markup=kb([[btn("🟢 DEMO", "acc:env:DEMO"), btn("🔴 LIVE", "acc:env:LIVE")], [btn("❌ Cancel", "home")]]))


@router.callback_query(F.data.startswith("acc:env:"))
async def acc_add_env(cb: CallbackQuery, state: FSMContext, ctx: Any, role: Role) -> None:
    need(role, Permission.ACCOUNT_MANAGE)
    env = split(cb.data)[2]
    name = (await state.get_data()).get("name")
    if not name:
        await toast(cb, "Session expired — start again", True)
        return
    state_token, url = await ctx.accounts.start_oauth(cb.from_user.id, cb.message.chat.id, name, env)
    await state.clear()
    warn = "\n⚠️ <b>LIVE account.</b> Live trading stays disabled until an admin enables it in Settings." if env == "LIVE" else ""
    await show(cb, f"🔐 <b>Authorize on cTrader</b> ({env}){warn}\nTap the button, log in to your cTrader ID and grant access. I will message you here when it completes (valid 15 min).",
               kb([[btn("🔐 Open cTrader authorization", url=url)], [btn("❌ Cancel", "home")]]))
    await toast(cb, "")


async def notify_oauth_authorized(ctx: Any, row: Any) -> None:
    """Hook called by AccountService when the OAuth redirect finished: ask the user which account to add."""
    if row.accounts_json is None:
        await ctx.bot_api.send_message(row.chat_id, "✅ Authorization received, but fetching your accounts from cTrader failed:\n" + esc((row.error or "unknown")[:200]) + "\n\nThe authorization is saved — tap retry.",
                                       parse_mode="HTML", reply_markup=kb([[btn("🔄 Fetch accounts", C("acc", "fa", row.state))], [btn("❌ Cancel", "home")]]))
        return
    accounts = row.accounts_json or []
    if not accounts:
        await ctx.bot_api.send_message(row.chat_id, f"⚠️ Authorization succeeded but no {row.environment} accounts were found for this cTID.")
        return
    rows = [[btn(f"{a['ctid']} · login {a.get('login') or '?'}", C("acc", "ap", row.state, a["ctid"]))] for a in accounts[:8]]
    rows.append([btn("❌ Cancel", "home")])
    await ctx.bot_api.send_message(row.chat_id, f"✅ Authorized. Select the {row.environment} account to add as <b>{esc(row.account_name)}</b>:", parse_mode="HTML", reply_markup=kb(rows))


@router.callback_query(F.data.startswith("acc:ap:"))
async def acc_pick_oauth(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.ACCOUNT_MANAGE)
    base = cb.data.rsplit(":", 1)[0] if cb.data.split(":")[-1] in ("c1", "c2") else cb.data
    _, _, state_token, ctid = base.split(":")[:4]
    row = await ctx.accounts.oauth_state(state_token)
    if not await confirm_gate(cb, title=f"Add account <b>{ctid}</b> ({row.environment}) as <b>{esc(row.account_name)}</b>?", live=row.environment == "LIVE", cancel="home"):
        return
    await toast(cb, "Adding & connecting…")
    acc = await ctx.accounts.pick_account(state_token, int(ctid), cb.from_user.id)
    await show(cb, "✅ Account added.\n\n" + await _detail_text(ctx, acc), _detail_kb(acc))


@router.callback_query(F.data.startswith("acc:fa:"))
async def acc_fetch_accounts(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.ACCOUNT_MANAGE)
    state_token = split(cb.data)[2]
    await toast(cb, "Fetching accounts…")
    try:
        row = await ctx.accounts.fetch_accounts(state_token, cb.from_user.id)
    except Exception as exc:
        await show(cb, f"⚠️ Still failing: {esc(str(exc)[:200])}\nTry again in a moment.", kb([[btn("🔄 Fetch accounts", C("acc", "fa", state_token))], [btn("❌ Cancel", "home")]]))
        return
    accounts = row.accounts_json or []
    if not accounts:
        await show(cb, f"⚠️ No {row.environment} accounts found for this cTID.", kb([back_home("acc:menu")]))
        return
    rows = [[btn(f"{a['ctid']} · login {a.get('login') or '?'}", C("acc", "ap", row.state, a["ctid"]))] for a in accounts[:8]]
    rows.append([btn("❌ Cancel", "home")])
    await show(cb, f"✅ Select the {row.environment} account to add as <b>{esc(row.account_name)}</b>:", kb(rows))
