from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.bot.callbacks.data import cb as C, split
from app.bot.handlers.common import confirm_gate, env_tag, esc, fmt_ago, need, run_command, show, toast
from app.bot.keyboards.common import back_home, btn, kb, nav_row, paginate
from app.core.enums import Role
from app.models import Signal
from app.security.rbac import Permission

router = Router(name="orders")
FILTERS = {"active": (["PENDING", "PROCESSING", "SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED"], "Active"), "pending": (["ACCEPTED", "SUBMITTED"], "Pending (working)"),
           "filled": (["FILLED"], "Filled"), "rejected": (["REJECTED", "FAILED"], "Rejected / Failed"), "cancelled": (["CANCELLED"], "Cancelled"),
           "unknown": (["UNKNOWN"], "UNKNOWN"), "recent": (None, "Recent (all)")}
ICON = {"FILLED": "✅", "REJECTED": "⛔", "FAILED": "⛔", "CANCELLED": "🚫", "UNKNOWN": "❓", "ACCEPTED": "⏳", "SUBMITTED": "📤", "PROCESSING": "⚙️", "PENDING": "🕓", "PARTIALLY_FILLED": "◐"}


def line(o: Any) -> str:
    return f"{ICON.get(o.status, '•')} {o.symbol} {o.side} {o.volume_lots:g}L {o.status} · {fmt_ago(o.created_at)}"


@router.callback_query(F.data == "ord:menu")
async def menu(cb: CallbackQuery, ctx: Any) -> None:
    unk = await ctx.orders.count(["UNKNOWN"])
    rows = [[btn(("❓ " if k == "unknown" and unk else "") + FILTERS[k][1], C("ord", "list", k, 0))] for k in FILTERS]
    rows.insert(len(rows), [btn("🏦 By account", "acc:pick:ord:0")])
    rows.append([btn("🏠 Menu", "home")])
    await show(cb, f"🧾 <b>Orders</b>" + (f"\n⚠️ {unk} order(s) with UNKNOWN outcome — never auto-resent; being reconciled." if unk else ""), kb(rows))
    await toast(cb, "")


async def _render(cb: CallbackQuery, ctx: Any, orders: list, page: int, prefix: str, title: str, back: str) -> None:
    items, page, pages = paginate(orders, page)
    rows = [[btn(line(o)[:60], C("ord", "view", o.id))] for o in items]
    if pages > 1:
        rows.append(nav_row(prefix, page, pages))
    rows.append(back_home(back))
    await show(cb, f"🧾 <b>{title}</b>" + ("" if items else "\nNone."), kb(rows))
    await toast(cb, "")


@router.callback_query(F.data.startswith("ord:list:"))
async def ord_list(cb: CallbackQuery, ctx: Any) -> None:
    _, _, key, page = split(cb.data)[:4]
    statuses, title = FILTERS[key]
    await _render(cb, ctx, await ctx.orders.list(statuses=statuses, limit=300), int(page), f"ord:list:{key}", title, "ord:menu")


@router.callback_query(F.data.startswith("ord:acc:"))
async def ord_acc(cb: CallbackQuery, ctx: Any) -> None:
    _, _, aid, page = split(cb.data)[:4]
    await _render(cb, ctx, await ctx.orders.list(account_id=aid, limit=300), int(page), f"ord:acc:{aid}", "Orders for account", "acc:menu")


async def _view(cb: CallbackQuery, ctx: Any, oid: str) -> None:
    o = await ctx.orders.get(oid)
    acc = await ctx.accounts.get(o.account_id)
    sig = None
    if o.signal_id:
        async with ctx.db.session() as s:
            sig = await s.get(Signal, o.signal_id)
    strat = bot = "—"
    try:
        if o.bot_id:
            bot = esc((await ctx.bots.get(o.bot_id)).name)
        if o.strategy_id:
            v = await ctx.strategies.get_version(o.strategy_version_id) if o.strategy_version_id else None
            strat = f"{esc((await ctx.strategies.get(o.strategy_id)).name)}@{v.version if v else '?'}"
    except Exception:
        pass
    text = (f"{line(o)}\n{env_tag(acc.environment)} · {esc(acc.name)}\n<b>Type</b> {o.order_type} · price {o.price or 'mkt'} · fill {o.avg_fill_price or '—'}\n"
            f"SL {o.stop_loss_pips or '—'}p · TP {o.take_profit_pips or '—'}p · filled {o.filled_volume_lots:g}L\n"
            f"<b>Trace</b> signal {o.signal_id or '—'}" + (f" ({sig.risk_decision}: {sig.risk_reason})" if sig else "") + f"\nbot {bot} · strategy {strat}\n"
            f"request <code>{esc(o.request_id[:40])}</code>\nclientOrderId <code>{esc(o.client_order_id)}</code>\nbroker order {o.broker_order_id or '—'} · position {o.broker_position_id or '—'}\n"
            f"corr <code>{o.correlation_id}</code>" + (f"\n<b>Reject/Error</b> {esc((o.reject_reason or o.error or '')[:200])}" if (o.reject_reason or o.error) else ""))
    rows = []
    if o.status == "UNKNOWN":
        rows.append([btn("🔍 Reconcile now", C("ord", "recon", oid))])
    if o.status in ("ACCEPTED", "SUBMITTED") and o.broker_order_id and o.order_type != "MARKET":
        rows.append([btn("🚫 Cancel order", C("ord", "cancel", oid))])
    if (o.extra or {}).get("retry_allowed"):
        rows.append([btn("🔁 Retry (re-checked by Risk)", C("ord", "retry", oid))])
    rows += [[btn("🔄 Refresh", C("ord", "view", oid))], back_home("ord:menu")]
    await show(cb, text, kb(rows))


@router.callback_query(F.data.startswith("ord:view:"))
async def ord_view(cb: CallbackQuery, ctx: Any) -> None:
    await _view(cb, ctx, split(cb.data)[2])
    await toast(cb, "")


@router.callback_query(F.data.startswith("ord:recon:"))
async def ord_recon(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.TRADE)
    oid = split(cb.data)[2]
    await toast(cb, "Querying broker…")
    res = await ctx.recon.resolve_unknown(oid)
    await toast(cb, res[:180], True)
    await _view(cb, ctx, oid)


@router.callback_query(F.data.startswith("ord:cancel:"))
async def ord_cancel(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.TRADE)
    oid = split(cb.data)[2]
    o = await ctx.orders.get(oid)
    acc = await ctx.accounts.get(o.account_id)
    if not await confirm_gate(cb, title=f"Cancel pending order {line(o)}?", live=acc.environment == "LIVE", cancel=C("ord", "view", oid)):
        return
    await run_command(ctx, cb, "ORDER_CANCEL", lambda cid: ctx.orders.cancel(oid, cb.from_user.id), target_type="order", target_id=oid)
    await _view(cb, ctx, oid)


@router.callback_query(F.data.startswith("ord:retry:"))
async def ord_retry(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.TRADE)
    oid = split(cb.data)[2]
    o = await ctx.orders.get(oid)
    acc = await ctx.accounts.get(o.account_id)
    if not await confirm_gate(cb, title=f"Retry {line(o)} as a NEW order (the Risk Engine re-evaluates it)?", live=acc.environment == "LIVE", cancel=C("ord", "view", oid)):
        return
    _, new = await run_command(ctx, cb, "ORDER_RETRY", lambda cid: ctx.orders.retry(oid, cb.from_user.id), target_type="order", target_id=oid)
    await _view(cb, ctx, new.id if new is not None and hasattr(new, "id") else oid)
