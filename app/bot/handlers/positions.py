from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks.data import cb as C, split
from app.bot.handlers.common import confirm_gate, env_tag, esc, fmt_ago, need, num, pnl, run_command, show, toast
from app.bot.keyboards.common import back_home, btn, kb, nav_row, paginate
from app.bot.states.forms import PositionInput
from app.core.enums import Role
from app.core.exceptions import ValidationFailed
from app.security.rbac import Permission

router = Router(name="positions")


def pos_line(p: object) -> str:
    return f"{'🟩' if p.side == 'BUY' else '🟥'} {p.symbol} {p.volume_lots:g}L {pnl(p.unrealized_pnl)}"


async def _list(cb: CallbackQuery, ctx: Any, account_id: str | None, page: int, prefix: str, back: str) -> None:
    items, page, pages = paginate(await ctx.positions.list_open(account_id, limit=500), page)
    rows = [[btn(pos_line(p), C("pos", "view", p.id))] for p in items]
    if pages > 1:
        rows.append(nav_row(prefix, page, pages))
    rows.append([btn("🔄 Refresh", C(*prefix.split(":"), page))])
    rows.append(back_home(back))
    await show(cb, "📈 <b>Open positions</b>" + ("" if items else "\nNone."), kb(rows))
    await toast(cb, "")


@router.callback_query(F.data == "pos:menu")
async def menu(cb: CallbackQuery, ctx: Any) -> None:
    n = await ctx.positions.count_open()
    await show(cb, f"📈 <b>Positions</b> — {n} open",
               kb([[btn("📋 All open", "pos:list:0")], [btn("🏦 By account", "acc:pick:pos:0")], [btn("🧹 Close ALL positions", "pos:closeall")], [btn("🏠 Menu", "home")]]))
    await toast(cb, "")


@router.callback_query(F.data.startswith("pos:list:"))
async def pos_list(cb: CallbackQuery, ctx: Any) -> None:
    await _list(cb, ctx, None, int(split(cb.data)[2]), "pos:list", "pos:menu")


@router.callback_query(F.data.startswith("pos:acc:"))
async def pos_acc(cb: CallbackQuery, ctx: Any) -> None:
    _, _, aid, page = split(cb.data)[:4]
    await _list(cb, ctx, aid, int(page), f"pos:acc:{aid}", "acc:menu")


async def _view(event: CallbackQuery | Message, ctx: Any, pid: str) -> None:
    p = await ctx.positions.get(pid)
    acc = await ctx.accounts.get(p.account_id)
    bot = strat = "—"
    if p.bot_id:
        try:
            b = await ctx.bots.get(p.bot_id)
            bot = esc(b.name)
        except Exception:
            bot = p.bot_id
    if p.strategy_id:
        try:
            v = await ctx.strategies.get_version(p.strategy_version_id) if p.strategy_version_id else None
            s = await ctx.strategies.get(p.strategy_id)
            strat = f"{esc(s.name)}@{v.version if v else '?'}"
        except Exception:
            strat = p.strategy_id
    text = (f"{pos_line(p)} · {env_tag(acc.environment)} · {esc(acc.name)}\n<b>Status</b> {p.status} · source {p.source}\n"
            f"Entry {p.entry_price} · Now {p.current_price or '—'}\nSL {p.stop_loss or '—'} · TP {p.take_profit or '—'}\n"
            f"Floating {pnl(p.unrealized_pnl)} · Realized {pnl(p.realized_pnl)} · Swap {num(p.swap)} · Comm {num(p.commission)}\n"
            f"<b>Trace</b> bot {bot} · strategy {strat} · order {p.order_id or '—'} · signal {p.signal_id or '—'}\n"
            f"Opened {fmt_ago(p.opened_at)} · broker id {p.broker_position_id}")
    rows = []
    if p.status == "OPEN":
        rows += [[btn("🛑 Set SL", C("pos", "sl", pid)), btn("🎯 Set TP", C("pos", "tp", pid))],
                 [btn("❎ Close", C("pos", "close", pid)), btn("✂️ Partial close", C("pos", "part", pid))]]
    rows += [[btn("🔄 Refresh", C("pos", "view", pid))], back_home("pos:list:0")]
    await show(event, text, kb(rows))


@router.callback_query(F.data.startswith("pos:view:"))
async def pos_view(cb: CallbackQuery, ctx: Any) -> None:
    await _view(cb, ctx, split(cb.data)[2])
    await toast(cb, "")


@router.callback_query(F.data.startswith("pos:sl:") | F.data.startswith("pos:tp:"))
async def pos_sltp(cb: CallbackQuery, state: FSMContext, role: Role) -> None:
    need(role, Permission.TRADE)
    _, kind, pid = split(cb.data)[:3]
    await state.set_state(PositionInput.sl if kind == "sl" else PositionInput.tp)
    await state.update_data(pid=pid)
    await show(cb, f"Send the new <b>{'stop loss' if kind == 'sl' else 'take profit'}</b> price (number), or <code>0</code> to leave unchanged. /cancel to abort.")
    await toast(cb, "")


async def _sltp_in(m: Message, state: FSMContext, ctx: Any, role: Role, kind: str) -> None:
    need(role, Permission.TRADE)
    pid = (await state.get_data())["pid"]
    try:
        val = float((m.text or "").replace(",", "."))
    except ValueError:
        raise ValidationFailed("Please send a number")
    if val <= 0:
        raise ValidationFailed("Price must be positive")
    p = await ctx.positions.get(pid)
    acc = await ctx.accounts.get(p.account_id)
    await state.clear()
    await ctx.positions.modify_sltp(pid, val if kind == "sl" else None, val if kind == "tp" else None, m.from_user.id)
    await _view(m, ctx, pid)


@router.message(PositionInput.sl)
async def sl_in(m: Message, state: FSMContext, ctx: Any, role: Role) -> None:
    await _sltp_in(m, state, ctx, role, "sl")


@router.message(PositionInput.tp)
async def tp_in(m: Message, state: FSMContext, ctx: Any, role: Role) -> None:
    await _sltp_in(m, state, ctx, role, "tp")


@router.callback_query(F.data.startswith("pos:close:"))
async def pos_close(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.TRADE)
    pid = split(cb.data)[2]
    p = await ctx.positions.get(pid)
    acc = await ctx.accounts.get(p.account_id)
    if not await confirm_gate(cb, title=f"Close {pos_line(p)} on <b>{esc(acc.name)}</b> at market?", live=acc.environment == "LIVE", cancel=C("pos", "view", pid)):
        return
    await run_command(ctx, cb, "POSITION_CLOSE", lambda cid: ctx.positions.close(pid, cb.from_user.id), target_type="position", target_id=pid)
    await toast(cb, "Close order sent")
    await _view(cb, ctx, pid)


@router.callback_query(F.data.startswith("pos:part:"))
async def pos_part(cb: CallbackQuery, state: FSMContext, role: Role) -> None:
    need(role, Permission.TRADE)
    await state.set_state(PositionInput.partial)
    await state.update_data(pid=split(cb.data)[2])
    await show(cb, "Send the volume to close in <b>lots</b> (e.g. 0.05). /cancel to abort.")
    await toast(cb, "")


@router.message(PositionInput.partial)
async def part_in(m: Message, state: FSMContext, ctx: Any, role: Role) -> None:
    need(role, Permission.TRADE)
    pid = (await state.get_data())["pid"]
    try:
        lots = float((m.text or "").replace(",", "."))
    except ValueError:
        raise ValidationFailed("Please send a number of lots")
    p = await ctx.positions.get(pid)
    if not 0 < lots < p.volume_lots:
        raise ValidationFailed(f"Partial volume must be > 0 and < {p.volume_lots:g} lots (use Close for all)")
    await state.clear()
    await m.answer(f"Close <b>{lots:g}</b> of {pos_line(p)}?", parse_mode="HTML", reply_markup=kb([[btn("✅ Confirm", C("pos", "cp", pid, f"{lots:g}", "c1")), btn("❌ Cancel", C("pos", "view", pid))]]))


@router.callback_query(F.data.startswith("pos:cp:"))
async def pos_cp(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.TRADE)
    parts = split(cb.data)
    pid, lots = parts[2], float(parts[3])
    p = await ctx.positions.get(pid)
    acc = await ctx.accounts.get(p.account_id)
    if not await confirm_gate(cb, title=f"Close {lots:g} lots of {pos_line(p)} on <b>{esc(acc.name)}</b>?", live=acc.environment == "LIVE", cancel=C("pos", "view", pid)):
        return
    await run_command(ctx, cb, "POSITION_PARTIAL_CLOSE", lambda cid: ctx.positions.close(pid, cb.from_user.id, lots=lots), target_type="position", target_id=pid)
    await toast(cb, "Partial close sent")
    await _view(cb, ctx, pid)


@router.callback_query(F.data.startswith("pos:closeall"))
async def pos_closeall(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.TRADE)
    open_pos = await ctx.positions.list_open(limit=1000)
    live = False
    for p in open_pos:
        live = live or (await ctx.accounts.get(p.account_id)).environment == "LIVE"
    if not await confirm_gate(cb, title=f"Close ALL {len(open_pos)} open positions at market?", live=live, cancel="pos:menu"):
        return
    _, res = await run_command(ctx, cb, "POSITIONS_CLOSE_ALL", lambda cid: ctx.positions.close_all(None, cb.from_user.id, reason="manual_close_all"))
    await show(cb, f"🧹 Close-all finished: {res}", kb([back_home("pos:menu")]))
