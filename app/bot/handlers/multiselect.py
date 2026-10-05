"""Reusable multi-select pickers for SYMBOLS and TIMEFRAMES (tap to toggle, ✅ Done).

Used by: bot creation wizard, bot settings, backtest wizard. The caller stores a *flow name* in FSM data
(`ms_next_s` / `ms_next_f`); on "Done" the registered coroutine in FLOWS is awaited with the chosen values.
FSM keys: sym_opts [name,...], sym_cat {name: CATEGORY}, sym_sel [name,...], tf_sel [tf,...], tf_single bool.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.callbacks.data import split
from app.bot.handlers.common import esc, show, toast
from app.bot.keyboards.common import btn, kb
from app.core.timeframes import TF_LABEL, TF_ORDER
from app.market_data.catalog import CATEGORY_LABEL, CATEGORY_ORDER

router = Router(name="multiselect")
FlowFn = Callable[[CallbackQuery, FSMContext, Any, list[str]], Awaitable[None]]
FLOWS: dict[str, FlowFn] = {}


def register_flow(name: str, fn: FlowFn) -> None:
    FLOWS[name] = fn


# ---- symbols ----------------------------------------------------------------------------------------
def _sym_kb(opts: list[str], cats: dict[str, str], sel: set[str]):
    rows = []
    present = [c for c in CATEGORY_ORDER if any(cats.get(o) == c for o in opts)]
    rows.append([btn(f"{CATEGORY_LABEL[c].split(' ', 1)[0]} all {c.title()}", f"ms:s:c:{c}") for c in present])
    items = [btn(("✅ " if o in sel else "⬜ ") + o, f"ms:s:t:{i}") for i, o in enumerate(opts)]
    rows += [items[i:i + 2] for i in range(0, len(items), 2)]
    rows.append([btn("🧹 Clear", "ms:s:x"), btn(f"✔️ Done ({len(sel)})", "ms:s:ok")])
    rows.append([btn("❌ Cancel", "home")])
    return kb(rows)


async def start_symbol_pick(event: Any, state: FSMContext, ctx: Any, account_id: str, preselected: list[str], next_flow: str,
                            title: str = "Choose symbols") -> None:
    cat, verified = ctx.catalog.for_account(account_id)
    opts = [c.name for c in cat]
    pre = [s for s in preselected if s in opts]
    await state.update_data(sym_opts=opts, sym_cat={c.name: c.category for c in cat}, sym_sel=pre, ms_next_s=next_flow)
    note = "" if verified else "\n⚠️ Account is offline — this is a generic list; names may differ from your broker. Connect the account for the exact list."
    await show(event, f"🔣 <b>{esc(title)}</b>\nTap to select one or more. Gold · Oil · Forex majors only.{note}", _sym_kb(opts, {c.name: c.category for c in cat}, set(pre)))


@router.callback_query(F.data.startswith("ms:s:"))
async def symbol_action(cb: CallbackQuery, state: FSMContext, ctx: Any) -> None:
    d = await state.get_data()
    opts: list[str] = d.get("sym_opts") or []
    if not opts:
        await toast(cb, "Session expired — start again", True)
        return
    cats: dict[str, str] = d.get("sym_cat", {})
    sel: list[str] = list(d.get("sym_sel", []))
    act, arg = split(cb.data)[2], (split(cb.data)[3] if len(split(cb.data)) > 3 else "")
    if act == "t":
        name = opts[int(arg)]
        sel = [s for s in sel if s != name] if name in sel else sel + [name]
    elif act == "c":
        group = [o for o in opts if cats.get(o) == arg]
        sel = [s for s in sel if s not in group] if all(g in sel for g in group) else sel + [g for g in group if g not in sel]
    elif act == "x":
        sel = []
    elif act == "ok":
        if not sel:
            await toast(cb, "Select at least one symbol", True)
            return
        await state.update_data(sym_sel=sel)
        await FLOWS[d["ms_next_s"]](cb, state, ctx, sel)
        return
    await state.update_data(sym_sel=sel)
    await cb.message.edit_reply_markup(reply_markup=_sym_kb(opts, cats, set(sel)))  # type: ignore[union-attr]
    await toast(cb, f"{len(sel)} selected")


# ---- timeframes ----------------------------------------------------------------------------------------
def _tf_kb(sel: set[str], single: bool):
    items = [btn(("✅ " if t in sel else "") + t, f"ms:f:t:{t}") for t in TF_ORDER]
    rows = [items[i:i + 4] for i in range(0, len(items), 4)]
    if not single:
        rows.append([btn("⏱ Scalping M1-M5", "ms:f:g:scalp"), btn("📊 Intraday M15-H1", "ms:f:g:intra")])
        rows.append([btn("☑️ All", "ms:f:g:all"), btn("🧹 Clear", "ms:f:x")])
    rows.append([btn(f"✔️ Done ({len(sel)})", "ms:f:ok")])
    rows.append([btn("❌ Cancel", "home")])
    return kb(rows)


async def start_tf_pick(event: Any, state: FSMContext, preselected: list[str], next_flow: str, single: bool = False,
                        title: str = "Choose timeframes") -> None:
    pre = [t for t in preselected if t in TF_ORDER]
    if single:
        pre = pre[:1]
    await state.update_data(tf_sel=pre, tf_single=single, ms_next_f=next_flow)
    legend = "  ".join(f"{t}={TF_LABEL[t]}" for t in ("M1", "M5", "M15", "H1", "H4", "D1", "W1", "MN1"))
    await show(event, f"🕒 <b>{esc(title)}</b>\n{'Pick ONE timeframe.' if single else 'Tap to select one or more (M1 → MN1).'}\n<i>{legend}</i>", _tf_kb(set(pre), single))


@router.callback_query(F.data.startswith("ms:f:"))
async def tf_action(cb: CallbackQuery, state: FSMContext, ctx: Any) -> None:
    d = await state.get_data()
    if "ms_next_f" not in d:
        await toast(cb, "Session expired — start again", True)
        return
    single = bool(d.get("tf_single"))
    sel: list[str] = list(d.get("tf_sel", []))
    parts = split(cb.data)
    act, arg = parts[2], (parts[3] if len(parts) > 3 else "")
    if act == "t":
        if single:
            sel = [arg]
        else:
            sel = [s for s in sel if s != arg] if arg in sel else sel + [arg]
    elif act == "g":
        sel = {"scalp": ["M1", "M2", "M3", "M4", "M5"], "intra": ["M15", "M30", "H1"], "all": list(TF_ORDER)}[arg]
    elif act == "x":
        sel = []
    elif act == "ok":
        if not sel:
            await toast(cb, "Select at least one timeframe", True)
            return
        sel = [t for t in TF_ORDER if t in sel]
        await state.update_data(tf_sel=sel)
        await FLOWS[d["ms_next_f"]](cb, state, ctx, sel)
        return
    sel = [t for t in TF_ORDER if t in sel]
    await state.update_data(tf_sel=sel)
    await cb.message.edit_reply_markup(reply_markup=_tf_kb(set(sel), single))  # type: ignore[union-attr]
    await toast(cb, ", ".join(sel) or "none")
