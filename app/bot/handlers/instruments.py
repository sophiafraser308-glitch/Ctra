"""📐 Points & decimals table: what 1 point means per symbol, decimals shown, and the money value of 1 point per 1 lot."""
from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks.data import cb as C, split
from app.bot.handlers.common import esc, show, toast
from app.bot.keyboards.common import back_home, btn, kb
from app.market_data.catalog import CATEGORY_LABEL
from app.market_data.instruments import STATIC_TABLE, definition_for

router = Router(name="instruments")
HEADER = ("📐 <b>Points, decimals & point value</b>\n"
          "• <b>Gold</b>: 1 point = <b>0.1</b>, no decimals shown — 4150 → 4152 = <b>20 points</b> (= $10 per point per lot)\n"
          "• <b>Oil</b>: 1 point = 0.01 · <b>Forex</b>: 1 point = 1 pip = 0.0001 (JPY pairs 0.01)\n"
          "Stop loss / take profit / spread are all in these points.\n")


@router.message(Command("points"))
async def cmd_points(m: Message, ctx: Any) -> None:
    await _menu(m, ctx)


@router.callback_query(F.data == "inst:menu")
async def menu_cb(cb: CallbackQuery, ctx: Any) -> None:
    await _menu(cb, ctx)
    await toast(cb, "")


async def _menu(event: Any, ctx: Any) -> None:
    accs = [a for a in await ctx.accounts.list() if ctx.gateway.is_connected(a.id)]
    rows = [[btn(f"{a.name[:26]} · {a.environment}", C("inst", "a", a.id))] for a in accs[:8]]
    rows += [[btn("📋 Reference table (no account needed)", "inst:static")], [btn("🏠 Menu", "home")]]
    await show(event, HEADER + "\nChoose an account to see the exact symbols and live point values from your broker:", kb(rows))


@router.callback_query(F.data == "inst:static")
async def static_table(cb: CallbackQuery) -> None:
    lines = [HEADER]
    for name, d, units, ccy in STATIC_TABLE:
        v = units * d.point_size
        approx = "" if ccy == "USD" else f" ≈ {v:g}/{'USD' + ccy if ccy in ('CHF', 'CAD', 'JPY') else ccy} USD"
        lines.append(f"<b>{esc(name)}</b> · point {d.point_size:g} · decimals {d.display_decimals} · 1 point/lot = {v:g} {ccy}{approx}")
    lines.append("\n<i>Broker symbol names/suffixes may differ; the account view shows your exact list.</i>")
    await show(cb, "\n".join(lines), kb([back_home("inst:menu")]))
    await toast(cb, "")


@router.callback_query(F.data.startswith("inst:a:"))
async def account_table(cb: CallbackQuery, ctx: Any) -> None:
    aid = split(cb.data)[2]
    acc = await ctx.accounts.get(aid)
    cat, verified = ctx.catalog.for_account(aid)
    sess = ctx.gateway.sessions.get(aid)
    if not sess or not verified:
        await show(cb, "Account symbols are not available (connect the account first).", kb([back_home("inst:menu")]))
        return
    await toast(cb, "Loading…")
    infos = [sess.symbols_by_name.get(c.name.upper().replace("/", "").replace(" ", "")) or next((i for i in sess.symbols_by_id.values() if i.name == c.name), None) for c in cat]
    infos = [i for i in infos if i is not None][:30]
    try:
        await ctx.gateway.ensure_details(aid, [i.symbol_id for i in infos])
    except Exception:
        pass
    deposit = acc.currency or "USD"
    rates: dict[str, float | None] = {}
    lines = [HEADER, f"<b>{esc(acc.name)}</b> ({acc.environment}) · account currency {esc(deposit)}\n"]
    for i in infos:
        d = definition_for(i.name)
        quote = sess.assets.get(i.quote_asset_id or -1, deposit)
        if quote not in rates:
            try:
                rates[quote] = await ctx.market.conversion_rate(aid, quote, deposit)
            except Exception:
                rates[quote] = None
        rate = rates[quote]
        units = i.lot_size / 100.0
        val = units * i.pip_size * rate if rate else None
        icon = CATEGORY_LABEL.get(d.category, "•").split(" ", 1)[0] if d else "•"
        dec = i.display_decimals if i.display_decimals is not None else i.digits
        lines.append(f"{icon} <b>{esc(i.name)}</b> · point {i.pip_size:g} · decimals {dec} · contract {units:g} · "
                     + (f"1 point/lot = <b>{val:,.2f} {esc(deposit)}</b>" if val else f"1 point/lot = {units * i.pip_size:g} {esc(quote)}")
                     + f" · min lot {i.min_lots:g} (broker pip {10 ** -i.pip_position:g})")
    await show(cb, "\n".join(lines), kb([[btn("🔄 Refresh", C("inst", "a", aid))], back_home("inst:menu")]))
