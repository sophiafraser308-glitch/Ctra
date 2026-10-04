from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.callbacks.data import cb as C, split
from app.bot.handlers.common import confirm_gate, esc, fmt_ago, need, num, run_command, show, toast
from app.bot.keyboards.common import back_home, btn, kb
from app.bot.states.forms import RiskEdit
from app.core.enums import Role
from app.core.exceptions import ValidationFailed
from app.models import RiskProfile
from app.schemas.risk import BOOL_FIELDS, RISK_FIELDS, parse_risk_value
from app.security.rbac import Permission

router = Router(name="risk")
LIST_FIELDS = {"allowed_symbols": "Allowed symbols (comma / all)", "allowed_sessions": "Allowed sessions UTC (07:00-16:00,…)", "lock_policy": "Lock policy (MANUAL|NEXT_DAY)"}


@router.callback_query(F.data == "risk:menu")
async def menu(cb: CallbackQuery, ctx: Any) -> None:
    locks = await ctx.locks.list_active()
    async with ctx.db.session() as s:
        profiles = (await s.execute(select(RiskProfile).order_by(RiskProfile.name))).scalars().all()
    rows = [[btn(("⭐ " if p.is_default else "") + p.name[:30], C("risk", "view", p.id))] for p in profiles[:10]]
    rows += [[btn("➕ New profile", "risk:new"), btn(f"🔒 Active locks ({len(locks)})", "risk:locks")], [btn("📋 Account risk state", "risk:state")], [btn("🏠 Menu", "home")]]
    await show(cb, "🛡 <b>Risk</b>\nProfiles apply to accounts (and optionally per bot). The Risk Engine is the only component that can approve an order.", kb(rows))
    await toast(cb, "")


def _text(p: RiskProfile) -> str:
    lines = [f"🛡 <b>{esc(p.name)}</b>{' ⭐default' if p.is_default else ''}"]
    for k, (_, _, _, desc) in RISK_FIELDS.items():
        lines.append(f"• {desc}: <b>{getattr(p, k):g}</b>")
    for k, desc in BOOL_FIELDS.items():
        lines.append(f"• {desc}: <b>{'ON' if getattr(p, k) else 'OFF'}</b>")
    lines.append(f"• Allowed symbols: <b>{', '.join(p.allowed_symbols) or 'all'}</b>")
    lines.append(f"• Sessions (UTC): <b>{', '.join(p.allowed_sessions) or 'always'}</b>")
    lines.append(f"• Lock policy: <b>{p.lock_policy}</b>")
    return "\n".join(lines)


@router.callback_query(F.data.startswith("risk:view:"))
async def view(cb: CallbackQuery, ctx: Any) -> None:
    pid = split(cb.data)[2]
    async with ctx.db.session() as s:
        p = await s.get(RiskProfile, pid)
    if p is None:
        await toast(cb, "Profile not found", True)
        return
    nums = [btn(k.replace("max_", "").replace("_pct", "%").replace("_", " ")[:18], C("risk", "ed", pid, k)) for k in RISK_FIELDS]
    rows = [nums[i:i + 3] for i in range(0, len(nums), 3)]
    rows += [[btn(f"{'✅' if getattr(p, k) else '⬜'} {d[:22]}", C("risk", "tg", pid, k)) for k, d in list(BOOL_FIELDS.items())[:2]],
             [btn(f"{'✅' if p.trading_enabled else '⬜'} Trading permission", C("risk", "tg", pid, "trading_enabled"))]]
    rows += [[btn("🔣 Symbols", C("risk", "ed", pid, "allowed_symbols")), btn("🕒 Sessions", C("risk", "ed", pid, "allowed_sessions"))],
             [btn("🔒 Lock policy", C("risk", "ed", pid, "lock_policy")), btn("⭐ Make default", C("risk", "def", pid))], back_home("risk:menu")]
    await show(cb, _text(p) + "\n\nTap a field to edit.", kb(rows))
    await toast(cb, "")


@router.callback_query(F.data.startswith("risk:ed:"))
async def edit_start(cb: CallbackQuery, state: FSMContext, role: Role) -> None:
    need(role, Permission.RISK_MANAGE)
    _, _, pid, field = split(cb.data)[:4]
    desc = RISK_FIELDS[field][3] if field in RISK_FIELDS else LIST_FIELDS[field]
    rng = f" (range {RISK_FIELDS[field][1]}–{RISK_FIELDS[field][2]})" if field in RISK_FIELDS else ""
    await state.set_state(RiskEdit.value)
    await state.update_data(pid=pid, field=field)
    await show(cb, f"✏️ <b>{esc(desc)}</b>{rng}\nSend the new value, or /cancel.")
    await toast(cb, "")


@router.message(RiskEdit.value)
async def edit_value(m: Message, state: FSMContext, ctx: Any, role: Role) -> None:
    need(role, Permission.RISK_MANAGE)
    d = await state.get_data()
    val = parse_risk_value(d["field"], m.text or "")
    async with ctx.db.session() as s:
        p = await s.get(RiskProfile, d["pid"])
        prev = getattr(p, d["field"])
        setattr(p, d["field"], val)
    await ctx.audit.record(action="RISK_PROFILE_EDIT", user_id=m.from_user.id, target=d["pid"], previous_state={d["field"]: prev}, new_state={d["field"]: val})
    await state.clear()
    await m.answer(f"✅ <b>{esc(d['field'])}</b> = <b>{esc(str(val))}</b>", parse_mode="HTML", reply_markup=kb([[btn("🛡 Back to profile", C("risk", "view", d["pid"]))]]))


@router.callback_query(F.data.startswith("risk:tg:"))
async def toggle(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.RISK_MANAGE)
    _, _, pid, field = split(cb.data)[:4]
    if field not in BOOL_FIELDS:
        raise ValidationFailed("Unknown field")
    async with ctx.db.session() as s:
        p = await s.get(RiskProfile, pid)
        prev = getattr(p, field)
        setattr(p, field, not prev)
    await ctx.audit.record(action="RISK_PROFILE_EDIT", user_id=cb.from_user.id, target=pid, previous_state={field: prev}, new_state={field: not prev})
    await view(cb, ctx)


@router.callback_query(F.data.startswith("risk:def:"))
async def make_default(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.RISK_MANAGE)
    pid = split(cb.data)[2]
    async with ctx.db.session() as s:
        for p in (await s.execute(select(RiskProfile))).scalars():
            p.is_default = p.id == pid
    await toast(cb, "Default profile set")
    await view(cb, ctx)


@router.callback_query(F.data == "risk:new")
async def new_start(cb: CallbackQuery, state: FSMContext, role: Role) -> None:
    need(role, Permission.RISK_MANAGE)
    await state.set_state(RiskEdit.new_profile)
    await show(cb, "Send a name for the new profile (cloned from the default):")
    await toast(cb, "")


@router.message(RiskEdit.new_profile)
async def new_profile(m: Message, state: FSMContext, ctx: Any, role: Role) -> None:
    need(role, Permission.RISK_MANAGE)
    name = (m.text or "").strip()
    if not (1 <= len(name) <= 64):
        raise ValidationFailed("Name must be 1-64 characters")
    base = await ctx.accounts.default_risk_profile()
    async with ctx.db.session() as s:
        if (await s.execute(select(RiskProfile).where(RiskProfile.name == name))).scalar_one_or_none():
            raise ValidationFailed("A profile with this name exists")
        data = {c.name: getattr(base, c.name) for c in RiskProfile.__table__.columns if c.name not in ("id", "name", "is_default", "created_at", "updated_at")}
        p = RiskProfile(name=name, is_default=False, **data)
        s.add(p)
        await s.flush()
        pid = p.id
    await ctx.audit.record(action="RISK_PROFILE_CREATE", user_id=m.from_user.id, target=pid, new_state={"name": name})
    await state.clear()
    await m.answer(f"✅ Profile <b>{esc(name)}</b> created.", parse_mode="HTML", reply_markup=kb([[btn("🛡 Open", C("risk", "view", pid))]]))


@router.callback_query(F.data == "risk:locks")
async def locks(cb: CallbackQuery, ctx: Any) -> None:
    ls = await ctx.locks.list_active()
    rows = [[btn(f"🔓 {l.scope_type}:{(l.scope_id or '')[:8]} {l.reason.replace('RISK_REJECTED_', '')[:20]}", C("risk", "unlock", l.id))] for l in ls[:10]]
    rows.append(back_home("risk:menu"))
    text = "🔒 <b>Active locks</b>\n" + ("\n".join(f"• {l.scope_type} {l.scope_id or ''}: {esc(l.reason)} — {esc(l.detail or '')} ({fmt_ago(l.created_at)}){' · expires ' + l.expires_at.strftime('%m-%d %H:%M') if l.expires_at else ''}" for l in ls) or "None")
    await show(cb, text, kb(rows))
    await toast(cb, "")


@router.callback_query(F.data.startswith("risk:unlock:"))
async def unlock(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.EMERGENCY_RESET)
    lid = split(cb.data)[2]
    if not await confirm_gate(cb, title="Release this risk lock? Trading limits will apply again immediately; the underlying cause is NOT fixed by this.", cancel="risk:locks"):
        return
    await run_command(ctx, cb, "RISK_LOCK_RESET", lambda cid: ctx.locks.release(lid, cb.from_user.id), target_type="lock", target_id=lid)
    await locks(cb, ctx)


@router.callback_query(F.data == "risk:state")
async def state_view(cb: CallbackQuery, ctx: Any) -> None:
    lines = []
    for a in await ctx.accounts.list():
        st = await ctx.risk.get_state(a.id)
        prof = await ctx.risk.profile_for(a)
        lines.append(f"<b>{esc(a.name)}</b> ({a.environment})\n  trades today {st.trades_today}/{prof.max_trades_per_day} · consecutive losses {st.consecutive_losses}/{prof.max_consecutive_losses}\n  realized today {num(st.realized_today)} · day-start {num(st.day_start_balance)} · peak eq {num(st.peak_equity)}")
    await show(cb, "📋 <b>Risk state</b>\n" + ("\n".join(lines) or "No accounts."), kb([back_home("risk:menu")]))
    await toast(cb, "")
