"""Settings (live toggle, users, config view) and Emergency controls."""
from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks.data import cb as C, split
from app.bot.handlers.common import confirm_gate, esc, need, run_command, show, toast
from app.bot.keyboards.common import back_home, btn, kb
from app.bot.states.forms import UserInput
from app.core.enums import Role
from app.core.exceptions import SafetyError, ValidationFailed
from app.security.masking import mask_secret
from app.security.rbac import Permission

router = Router(name="settings")


@router.callback_query(F.data == "set:menu")
async def menu(cb: CallbackQuery, ctx: Any) -> None:
    s = ctx.sys_settings
    live = "🔴 ENABLED" if s.live_allowed else "🟢 disabled"
    gate = "on" if ctx.settings.live_trading_enabled else "OFF (env LIVE_TRADING_ENABLED=false)"
    await show(cb, f"⚙️ <b>Settings</b>\nLive trading: <b>{live}</b> (env gate {gate})\nNew trading disabled: <b>{'YES ⛔' if s.new_trading_disabled else 'no'}</b>\nEnvironment: {esc(ctx.settings.app_env)}",
               kb([[btn(("🟢 Disable LIVE trading" if s.live_allowed else "🔴 Enable LIVE trading"), "set:live")],
                   [btn("👥 Users", "set:users"), btn("🧩 Configuration", "set:cfg")], [btn("📐 Points & decimals", "inst:menu")], [btn("🏠 Menu", "home")]]))
    await toast(cb, "")


@router.callback_query(F.data.startswith("set:live"))
async def live_toggle(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.SETTINGS)
    enabling = not ctx.sys_settings.live_allowed
    if enabling:
        if not ctx.settings.live_trading_enabled:
            raise SafetyError("Hard gate: LIVE_TRADING_ENABLED=false in the environment. Change it (production only) and restart.")
        if not await confirm_gate(cb, title="Enable <b>LIVE trading</b> for ALL live accounts/bots? Real money will be traded by automated strategies.", live=True, cancel="set:menu"):
            return
    await run_command(ctx, cb, "SET_LIVE_TRADING", lambda cid: ctx.sys_settings.set_live_trading(enabling, cb.from_user.id))
    await ctx.audit.record(action="SETTINGS_LIVE_TRADING", user_id=cb.from_user.id, new_state={"enabled": enabling})
    if not enabling:
        await ctx.notifier.notify("EMERGENCY", "LIVE trading was DISABLED from Telegram", dedup_key="live-off", throttle_seconds=0)
    await menu(cb, ctx)


@router.callback_query(F.data == "set:cfg")
async def cfg(cb: CallbackQuery, ctx: Any) -> None:
    s = ctx.settings
    text = (f"🧩 <b>Effective configuration</b> (secrets hidden)\nenv {s.app_env} · log {s.log_level}\nDB {'SQLite' if s.is_sqlite else 'PostgreSQL'}\n"
            f"Telegram token {'set' if s.telegram_bot_token.get_secret_value() else 'MISSING'} · admins {len(s.admin_ids)}\nEncryption key {'set' if s.encryption_key.get_secret_value() else 'MISSING'}\n"
            f"cTrader client {mask_secret(s.ctrader_client_id)} · redirect {esc(s.ctrader_redirect_uri or '—')}\nScope {s.ctrader_scope}\n"
            f"Sandbox {s.strategy_sandbox_mode} · call timeout {s.strategy_call_timeout}s · mem {s.strategy_memory_mb}MB\nAllowed imports: {esc(', '.join(sorted(s.strategy_allowed_imports)))}\n"
            f"Stale after {s.market_data_stale_seconds}s · reconcile every {s.reconcile_interval_seconds}s · heartbeat {s.heartbeat_interval_seconds}s")
    await show(cb, text, kb([back_home("set:menu")]))
    await toast(cb, "")


@router.callback_query(F.data == "set:users")
async def users(cb: CallbackQuery, ctx: Any) -> None:
    rows_ = await ctx.users.list()
    text = "👥 <b>Authorized users</b>\n" + "\n".join(f"• <code>{i}</code> {esc(u or '')} — {r}" for i, u, r in rows_)
    await show(cb, text, kb([[btn("➕ Add operator", "set:uadd:OPERATOR"), btn("➕ Add read-only", "set:uadd:READ_ONLY")], [btn("➕ Add admin", "set:uadd:ADMIN"), btn("➖ Remove user", "set:urm")], back_home("set:menu")]))
    await toast(cb, "")


@router.callback_query(F.data.startswith("set:uadd:"))
async def user_add_start(cb: CallbackQuery, state: FSMContext, role: Role) -> None:
    need(role, Permission.USER_MANAGE)
    await state.set_state(UserInput.add_id)
    await state.update_data(role=split(cb.data)[2])
    await show(cb, f"Send the numeric Telegram ID of the new <b>{split(cb.data)[2]}</b> (/cancel to abort).\nThe person can find it via @userinfobot.")
    await toast(cb, "")


@router.message(UserInput.add_id)
async def user_add(m: Message, state: FSMContext, ctx: Any, role: Role) -> None:
    need(role, Permission.USER_MANAGE)
    txt = (m.text or "").strip()
    if not txt.isdigit():
        raise ValidationFailed("Telegram ID must be numeric")
    r = Role((await state.get_data())["role"])
    await ctx.users.add(int(txt), r, m.from_user.id)
    await state.clear()
    await m.answer(f"✅ User {txt} added as {r.value}.", reply_markup=kb([[btn("👥 Users", "set:users")]]))


@router.callback_query(F.data == "set:urm")
async def user_rm_start(cb: CallbackQuery, state: FSMContext, role: Role) -> None:
    need(role, Permission.USER_MANAGE)
    await state.set_state(UserInput.remove_id)
    await show(cb, "Send the numeric Telegram ID to remove (/cancel to abort).")
    await toast(cb, "")


@router.message(UserInput.remove_id)
async def user_rm(m: Message, state: FSMContext, ctx: Any, role: Role) -> None:
    need(role, Permission.USER_MANAGE)
    txt = (m.text or "").strip()
    if not txt.isdigit():
        raise ValidationFailed("Telegram ID must be numeric")
    if int(txt) == m.from_user.id:
        raise ValidationFailed("You cannot remove yourself")
    await ctx.users.remove(int(txt), m.from_user.id)
    await state.clear()
    await m.answer(f"✅ User {txt} removed.", reply_markup=kb([[btn("👥 Users", "set:users")]]))


# ---- Emergency ---------------------------------------------------------------------------------------------
@router.callback_query(F.data == "em:menu")
async def em_menu(cb: CallbackQuery, ctx: Any) -> None:
    dis = ctx.sys_settings.new_trading_disabled
    await show(cb, "🚨 <b>Emergency controls</b>\nAll actions are audited and notify every admin/operator.",
               kb([[btn("⏹ Stop ALL bots", "em:stop")],
                   [btn(("✅ Re-enable new trading" if dis else "⛔ Disable NEW trading"), "em:dis")],
                   [btn("❎ Close ALL positions", "em:close"), btn("🚫 Cancel ALL pending", "em:cancel")],
                   [btn("🔓 Reset ALL locks", "em:reset"), btn("📋 Status", "em:status")], [btn("🧾 Emergency audit", "em:audit")], [btn("🏠 Menu", "home")]]))
    await toast(cb, "")


async def _any_live(ctx: Any) -> bool:
    return any(a.environment == "LIVE" for a in await ctx.accounts.list())


@router.callback_query(F.data.startswith("em:stop"))
async def em_stop(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.EMERGENCY)
    if not await confirm_gate(cb, title="Stop ALL bots now?", live=False, cancel="em:menu"):
        return
    _, n = await run_command(ctx, cb, "EMERGENCY_STOP_BOTS", lambda cid: ctx.emergency.stop_all_bots(cb.from_user.id))
    await show(cb, f"⏹ {n} bot(s) stopped.", kb([back_home("em:menu")]))


@router.callback_query(F.data.startswith("em:dis"))
async def em_dis(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.EMERGENCY)
    target = not ctx.sys_settings.new_trading_disabled
    if not target:
        need(role, Permission.EMERGENCY_RESET)       # re-enabling needs admin
    if not await confirm_gate(cb, title=("Disable ALL new orders system-wide?" if target else "Re-enable new trading?"), live=False, cancel="em:menu"):
        return
    await run_command(ctx, cb, "EMERGENCY_NEW_TRADING", lambda cid: ctx.emergency.set_new_trading_disabled(cb.from_user.id, target))
    await em_menu(cb, ctx)


@router.callback_query(F.data.startswith("em:close"))
async def em_close(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.EMERGENCY)
    n = await ctx.positions.count_open()
    if not await confirm_gate(cb, title=f"Close ALL {n} open positions at market?", live=await _any_live(ctx), cancel="em:menu"):
        return
    _, res = await run_command(ctx, cb, "EMERGENCY_CLOSE_ALL", lambda cid: ctx.emergency.close_all_positions(cb.from_user.id))
    await show(cb, f"❎ Close-all: {res}", kb([back_home("em:menu")]))


@router.callback_query(F.data.startswith("em:cancel"))
async def em_cancel(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.EMERGENCY)
    if not await confirm_gate(cb, title="Cancel ALL pending (working) orders?", live=await _any_live(ctx), cancel="em:menu"):
        return
    _, res = await run_command(ctx, cb, "EMERGENCY_CANCEL_PENDING", lambda cid: ctx.emergency.cancel_all_pending(cb.from_user.id))
    await show(cb, f"🚫 Cancel pending: {res}", kb([back_home("em:menu")]))


@router.callback_query(F.data.startswith("em:reset"))
async def em_reset(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.EMERGENCY_RESET)
    if not await confirm_gate(cb, title="Release ALL risk locks and reset consecutive-loss counters?", live=await _any_live(ctx), cancel="em:menu"):
        return
    _, n = await run_command(ctx, cb, "EMERGENCY_RESET_LOCKS", lambda cid: ctx.emergency.reset_locks(cb.from_user.id))
    await show(cb, f"🔓 {n} lock(s) released.", kb([back_home("em:menu")]))


@router.callback_query(F.data == "em:status")
async def em_status(cb: CallbackQuery, ctx: Any) -> None:
    d = await ctx.dashboard.snapshot()
    locks = "\n".join(f"  🔒 {t} {i or ''} {r}" for t, i, r in d["locks"]) or "  none"
    await show(cb, f"📋 <b>Emergency status</b>\nNew trading disabled: <b>{d['new_trading_disabled']}</b>\nLIVE allowed: <b>{d['live_allowed']}</b>\nBots: {d['bots']}\nOpen positions {d['open_positions']} · pending {d['pending_orders']} · UNKNOWN {d['unknown_orders']}\nLocks:\n{locks}",
               kb([[btn("🔄 Refresh", "em:status")], back_home("em:menu")]))
    await toast(cb, "")


@router.callback_query(F.data == "em:audit")
async def em_audit(cb: CallbackQuery, ctx: Any) -> None:
    rows = await ctx.audit.recent(10, 0, "EMERGENCY")
    await show(cb, "🧾 <b>Emergency audit</b>\n" + ("\n".join(f"<code>{r.ts:%m-%d %H:%M}</code> u{r.user_id} {esc(r.action)} {esc(str((r.new_state or {}).get('detail', ''))[:60])}" for r in rows) or "None"), kb([back_home("em:menu")]))
    await toast(cb, "")
