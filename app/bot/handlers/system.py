"""Connections, System Health, Logs & Audit, Notifications."""
from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot.callbacks.data import cb as C, split
from app.bot.handlers.common import esc, fmt_ago, fmt_dt, need, run_command, show, status_icon, toast
from app.bot.keyboards.common import back_home, btn, kb, nav_row, paginate
from app.core.enums import Category, Role
from app.core.exceptions import ValidationFailed
from app.security.masking import mask_secret
from app.security.rbac import Permission
from app.core.utils import utcnow

router = Router(name="system")


# ---- Connections -----------------------------------------------------------------------------------
@router.callback_query(F.data == "conn:menu")
async def conn_menu(cb: CallbackQuery, ctx: Any) -> None:
    s = ctx.settings
    info = ctx.gateway.info()
    mg = "\n".join(f"  {e}: {status_icon('OK' if v['state'] == 'READY' else 'DOWN')} {v['state']} · {v['host']} · reconnects {v['reconnects']}" + (f" · {esc(v['last_error'] or '')}" if v['last_error'] else "") for e, v in info["managers"].items()) or "  idle (no account connected yet)"
    accs = "\n".join(f"  {status_icon(a.status)} {esc(a.name)} ({a.environment}) {a.status}" for a in await ctx.accounts.list()) or "  none"
    text = (f"🔌 <b>Connections</b>\n<b>cTrader app</b> client_id {mask_secret(s.ctrader_client_id)} · {'configured ✅' if s.ctrader_configured else 'NOT configured ⚠️'}\n"
            f"Redirect {esc(s.ctrader_redirect_uri or '—')}\nScope {s.ctrader_scope}\n<b>Transport</b>\n{mg}\n<b>Accounts</b>\n{accs}\nLast broker event {fmt_ago(info['last_event_at'])}")
    await show(cb, text, kb([[btn("🔁 Reconnect all", "conn:all"), btn("🔑 Refresh tokens", "conn:tok")], [btn("📜 Connection events", "conn:ev")], [btn("🔄 Refresh", "conn:menu")], [btn("🏠 Menu", "home")]]))
    await toast(cb, "")


@router.callback_query(F.data == "conn:all")
async def conn_all(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.ACCOUNT_MANAGE)
    await toast(cb, "Reconnecting all…")
    ok = fail = 0
    for a in await ctx.accounts.list():
        try:
            await ctx.accounts.reconnect(a.id, cb.from_user.id)
            ok += 1
        except Exception:
            fail += 1
    await toast(cb, f"Reconnected {ok}, failed {fail}", True)
    await conn_menu(cb, ctx)


@router.callback_query(F.data == "conn:tok")
async def conn_tok(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.ACCOUNT_MANAGE)
    await ctx.accounts.refresh_tokens()
    await toast(cb, "Token check completed (see logs for failures)", True)


@router.callback_query(F.data == "conn:ev")
async def conn_ev(cb: CallbackQuery, ctx: Any) -> None:
    ev = await ctx.logs.connection_events(limit=12)
    await show(cb, "📜 <b>Connection events</b>\n" + ("\n".join(f"• {e.ts:%m-%d %H:%M:%S} {e.event} {esc((e.detail or '')[:60])}" for e in ev) or "None"), kb([back_home("conn:menu")]))
    await toast(cb, "")


# ---- System health -----------------------------------------------------------------------------------
@router.callback_query(F.data == "health:menu")
async def health(cb: CallbackQuery, ctx: Any) -> None:
    comps = [c for c in ctx.heartbeat.snapshot() if not c.component.startswith("bot:")]
    lines = [f"{status_icon(c.status)} <b>{esc(c.component)}</b> {c.status} · {esc(c.last_operation or '')} · {fmt_ago(c.ts)}" + (f"\n    ❗ {esc(c.last_error[:80])}" if c.last_error else "") for c in comps]
    botlines = [f"{status_icon(c.status)} {esc(c.component)} {c.status} {fmt_ago(c.ts)}" for c in ctx.heartbeat.snapshot() if c.component.startswith("bot:")]
    wd = ctx.watchdog
    text = (f"🩺 <b>System health</b>\n" + "\n".join(lines) + ("\n<b>Bot workers</b>\n" + "\n".join(botlines) if botlines else "")
            + f"\n<b>Watchdog</b> last {fmt_ago(wd.last_run_at)} · findings: {esc('; '.join(wd.last_findings) or 'none')}"
            + f"\n<b>Reconciliation</b> last {fmt_ago(ctx.recon.last_run_at)} · stale market symbols {len(ctx.market.stale_list())}\nHeartbeat writer {fmt_ago(ctx.heartbeat.last_beat_at)}")
    await show(cb, text, kb([[btn("🔄 Refresh", "health:menu"), btn("🧮 Reconcile now", "health:recon")], [btn("🧾 Reconciliation events", "health:rev")], [btn("🏠 Menu", "home")]]))
    await toast(cb, "")


@router.callback_query(F.data == "health:recon")
async def health_recon(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.TRADE)
    await toast(cb, "Reconciling…")
    res = await ctx.recon.reconcile_all("manual")
    await toast(cb, f"Done: {res or 'no divergence'}", True)
    await health(cb, ctx)


@router.callback_query(F.data == "health:rev")
async def health_rev(cb: CallbackQuery, ctx: Any) -> None:
    ev = await ctx.logs.recon_events(12)
    await show(cb, "🧾 <b>Reconciliation events</b>\n" + ("\n".join(f"• {e.ts:%m-%d %H:%M} {e.trigger} <b>{e.kind}</b> {esc((e.action or '')[:60])}" for e in ev) or "None"), kb([back_home("health:menu")]))
    await toast(cb, "")


# ---- Logs & audit ---------------------------------------------------------------------------------------
@router.callback_query(F.data == "logs:menu")
async def logs_menu(cb: CallbackQuery) -> None:
    cats = [c.value for c in Category]
    rows = [[btn("🧯 Errors only", "logs:l:ERR:0"), btn("📋 All logs", "logs:l:ALL:0")]]
    cb_btns = [btn(c.title()[:12], f"logs:l:{c}:0") for c in cats]
    rows += [cb_btns[i:i + 3] for i in range(0, len(cb_btns), 3)]
    rows += [[btn("🧾 Audit trail", "logs:audit:0"), btn("⌨️ Commands", "logs:cmd:0")], [btn("🏠 Menu", "home")]]
    await show(cb, "📜 <b>Logs & Audit</b>\nChoose a category:", kb(rows))
    await toast(cb, "")


@router.callback_query(F.data.startswith("logs:l:"))
async def logs_list(cb: CallbackQuery, ctx: Any) -> None:
    _, _, cat, page = split(cb.data)[:4]
    page = int(page)
    q_cat = None if cat in ("ALL", "ERR") else cat
    rows, total = await ctx.logs.logs(q_cat, None, page * 8, 8) if cat != "ERR" else await _errors(ctx, page)
    pages = max(1, (total + 7) // 8)
    text = f"📜 <b>Logs · {cat}</b> ({total})\n" + ("\n".join(f"<code>{r.ts:%m-%d %H:%M:%S}</code> {r.severity[:4]} {esc(r.message[:110])}" for r in rows) or "None")
    await show(cb, text, kb([nav_row(f"logs:l:{cat}", page, pages), back_home("logs:menu")]))
    await toast(cb, "")


async def _errors(ctx: Any, page: int):
    from sqlalchemy import func, select
    from app.models import LogEntry
    async with ctx.db.session() as s:
        rows = list((await s.execute(select(LogEntry).where(LogEntry.severity.in_(["ERROR", "CRITICAL"])).order_by(LogEntry.id.desc()).offset(page * 8).limit(8))).scalars())
        n = (await s.execute(select(func.count()).select_from(LogEntry).where(LogEntry.severity.in_(["ERROR", "CRITICAL"])))).scalar_one()
    return rows, int(n)


@router.callback_query(F.data.startswith("logs:audit:"))
async def audit_list(cb: CallbackQuery, ctx: Any) -> None:
    page = int(split(cb.data)[2])
    rows = await ctx.audit.recent(8, page * 8)
    total = await ctx.audit.count()
    pages = max(1, (total + 7) // 8)
    text = "🧾 <b>Audit trail</b>\n" + "\n".join(f"<code>{r.ts:%m-%d %H:%M}</code> u{r.user_id or '-'} <b>{esc(r.action)}</b> {esc((r.target or '')[:24])} {r.result}" for r in rows)
    await show(cb, text, kb([nav_row("logs:audit", page, pages), back_home("logs:menu")]))
    await toast(cb, "")


@router.callback_query(F.data.startswith("logs:cmd:"))
async def cmd_list(cb: CallbackQuery, ctx: Any) -> None:
    page = int(split(cb.data)[2])
    rows, total = await ctx.logs.commands(page * 8, 8)
    pages = max(1, (total + 7) // 8)
    text = "⌨️ <b>Commands</b>\n" + "\n".join(f"<code>{r.created_at:%m-%d %H:%M}</code> {esc(r.type)} {esc(r.target_id or '')} <b>{r.status}</b> u{r.user_id}" for r in rows)
    await show(cb, text, kb([nav_row("logs:cmd", page, pages), back_home("logs:menu")]))
    await toast(cb, "")


# ---- Notifications ----------------------------------------------------------------------------------------
KINDS = ["EMERGENCY", "RISK_LOCK", "ORDER_REJECTED", "ORDER_UNKNOWN", "ORDER_FILLED", "TRADE_CLOSED", "BOT_CRASH", "CTRADER_DISCONNECT", "STALE_MARKET_DATA", "RECONCILIATION", "AUTH_FAILURE"]
UNMUTABLE = {"EMERGENCY", "RISK_LOCK", "DAILY_LOSS", "CONSECUTIVE_LOSS", "CRITICAL_ERROR"}


@router.callback_query(F.data.startswith("notif:menu"))
async def notif_menu(cb: CallbackQuery, ctx: Any) -> None:
    muted_all = ctx.sys_settings.get("notifications_muted")
    muted = set(ctx.sys_settings.get("muted_notification_kinds") or [])
    rows = [[btn(("🔕 Unmute all" if muted_all else "🔔 Mute all (non-critical)"), "notif:all")]]
    kinds = [k for k in KINDS if k not in UNMUTABLE]
    rows += [[btn(("🔕 " if k in muted else "🔔 ") + k.replace("_", " ").title()[:24], C("notif", "k", k))] for k in kinds]
    rows += [[btn("📨 Send test", "notif:test"), btn("📜 Recent", "notif:list:0")], [btn("🏠 Menu", "home")]]
    await show(cb, f"🔔 <b>Notifications</b>\nCritical kinds (emergency, risk locks, daily-loss, critical errors) can never be muted.\nThrottle window {ctx.settings.notification_throttle_seconds}s per event key.", kb(rows))
    await toast(cb, "")


@router.callback_query(F.data == "notif:all")
async def notif_all(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.SETTINGS)
    await ctx.sys_settings.set("notifications_muted", not ctx.sys_settings.get("notifications_muted"), cb.from_user.id)
    await notif_menu(cb, ctx)


@router.callback_query(F.data.startswith("notif:k:"))
async def notif_kind(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.SETTINGS)
    k = split(cb.data)[2]
    if k in UNMUTABLE or k not in KINDS:
        raise ValidationFailed("This notification kind cannot be muted")
    muted = set(ctx.sys_settings.get("muted_notification_kinds") or [])
    muted.symmetric_difference_update({k})
    await ctx.sys_settings.set("muted_notification_kinds", sorted(muted), cb.from_user.id)
    await notif_menu(cb, ctx)


@router.callback_query(F.data == "notif:test")
async def notif_test(cb: CallbackQuery, ctx: Any) -> None:
    await ctx.notifier.notify("TEST", "Test notification from the Notifications menu", dedup_key=f"test:{utcnow().timestamp()}", throttle_seconds=0)
    await toast(cb, "Test notification queued")


@router.callback_query(F.data.startswith("notif:list:"))
async def notif_list(cb: CallbackQuery, ctx: Any) -> None:
    page = int(split(cb.data)[2])
    rows, total = await ctx.logs.notifications(page * 8, 8)
    pages = max(1, (total + 7) // 8)
    text = "📜 <b>Recent notifications</b>\n" + "\n".join(f"<code>{r.ts:%m-%d %H:%M}</code> {'✅' if r.delivered else '⏳'} <b>{esc(r.kind)}</b> {esc(r.message[:70])}" for r in rows)
    await show(cb, text, kb([nav_row("notif:list", page, pages), back_home("notif:menu")]))
    await toast(cb, "")
