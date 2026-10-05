from __future__ import annotations

import io
from typing import Any

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks.data import cb as C, split
from app.bot.handlers.common import confirm_gate, esc, fmt_ago, need, pnl, run_command, show, toast
from app.bot.keyboards.common import back_home, btn, kb, nav_row, paginate
from app.bot.states.forms import StrategyParam, UploadStrategy
from app.core.enums import Role
from app.core.exceptions import ValidationFailed
from app.security.rbac import Permission

router = Router(name="strategies")
MODES = [("view", "ℹ️ Strategy details"), ("validate", "✅ Validate"), ("act", "▶️ Activate"), ("deact", "⏹ Deactivate"), ("rb", "↩️ Rollback"),
         ("ver", "📦 Versions"), ("params", "⚙️ Parameters"), ("stats", "📊 Statistics"), ("del", "🗑 Delete")]


@router.callback_query(F.data == "str:menu")
async def menu(cb: CallbackQuery, ctx: Any) -> None:
    n = len(await ctx.strategies.list())
    rows = [[btn("⬆️ Upload strategy (.py)", "str:upload"), btn("📋 My strategies", "str:list:0")]]
    mb = [btn(t, C("str", "pick", m, 0)) for m, t in MODES]
    rows += [mb[i:i + 2] for i in range(0, len(mb), 2)]
    rows.append([btn("🧪 Backtest", "bt:menu"), btn("🏠 Menu", "home")])
    await show(cb, f"🧠 <b>Strategies</b> — {n} stored\nSandbox: <b>{ctx.settings.strategy_sandbox_mode}</b>", kb(rows))
    await toast(cb, "")


@router.callback_query(F.data.startswith("str:list:"))
async def str_list(cb: CallbackQuery, ctx: Any) -> None:
    await _picker(cb, ctx, "view", int(split(cb.data)[2]), "str:list")


@router.callback_query(F.data.startswith("str:pick:"))
async def str_pick(cb: CallbackQuery, ctx: Any) -> None:
    _, _, mode, page = split(cb.data)[:4]
    await _picker(cb, ctx, mode, int(page), f"str:pick:{mode}")


async def _picker(cb: CallbackQuery, ctx: Any, mode: str, page: int, nav_prefix: str) -> None:
    items, page, pages = paginate(await ctx.strategies.list(), page)
    rows = []
    for s in items:
        rows.append([btn(f"{'🟢' if s.active_version_id else '⚪'} {s.name[:40]}", C("str", mode, s.id))])
    if pages > 1:
        rows.append(nav_row(nav_prefix, page, pages))
    rows.append(back_home("str:menu"))
    await show(cb, f"🧠 {dict(MODES).get(mode, 'Strategies')} — choose:" + ("" if items else "\nNo strategies yet."), kb(rows))
    await toast(cb, "")


async def _detail(event: CallbackQuery | Message, ctx: Any, sid: str) -> None:
    st = await ctx.strategies.get(sid)
    vers = await ctx.strategies.versions(sid)
    act = next((v for v in vers if v.id == st.active_version_id), None)
    text = (f"🧠 <b>{esc(st.name)}</b>\n{esc(st.description or '')}\nAuthor {esc(st.author or '—')}\n"
            f"<b>Active</b> {('v' + act.version) if act else 'none'} · versions {len(vers)}\n"
            + (f"Symbols {', '.join(act.meta.get('symbols', [])) or '—'} · TF {', '.join(act.meta.get('timeframes', []))}\nDeps {', '.join(act.dependencies) or 'none'}\n"
               f"Hash <code>{act.file_hash[:16]}</code> · activated {fmt_ago(act.activated_at)}" if act else ""))
    rows = [[btn("📦 Versions", C("str", "ver", sid)), btn("⚙️ Params", C("str", "params", sid))],
            [btn("📊 Stats", C("str", "stats", sid)), btn("🗑 Delete", C("str", "del", sid))], back_home("str:list:0")]
    await show(event, text, kb(rows))


@router.callback_query(F.data.startswith("str:view:"))
async def str_view(cb: CallbackQuery, ctx: Any) -> None:
    await _detail(cb, ctx, split(cb.data)[2])
    await toast(cb, "")


# ---- upload -------------------------------------------------------------------------------------------
@router.callback_query(F.data == "str:upload")
async def upload_start(cb: CallbackQuery, state: FSMContext, role: Role) -> None:
    need(role, Permission.STRATEGY_MANAGE)
    await state.set_state(UploadStrategy.waiting_file)
    await show(cb, "⬆️ <b>Upload strategy</b>\nSend a <b>.py</b> file as a Telegram document. It must:\n• define <code>STRATEGY_INFO = {...}</code> (literal)\n• <code>from strategy_sdk import BaseStrategy</code> and subclass it once\n• use only allow-listed imports\nSend /cancel to abort.")
    await toast(cb, "")


@router.message(UploadStrategy.waiting_file, F.document)
async def upload_file(m: Message, state: FSMContext, ctx: Any, role: Role, bot: Bot) -> None:
    need(role, Permission.STRATEGY_MANAGE)
    doc = m.document
    if not (doc.file_name or "").lower().endswith(".py"):
        raise ValidationFailed("Only .py files are accepted")
    if (doc.file_size or 0) > ctx.settings.strategy_max_file_bytes:
        raise ValidationFailed(f"File too large (max {ctx.settings.strategy_max_file_bytes} bytes)")
    buf = io.BytesIO()
    await bot.download(doc, destination=buf)
    report, st, ver = await ctx.strategies.ingest(buf.getvalue(), doc.file_name, m.from_user.id)
    await state.clear()
    if not report.ok or ver is None:
        errs = "\n".join(f"• {esc(e)}" for e in report.errors[:12])
        await m.answer(f"❌ <b>Validation failed</b>\n{errs}", parse_mode="HTML", reply_markup=kb([[btn("⬆️ Try again", "str:upload")], back_home("str:menu")]))
        return
    warns = ("\n⚠️ " + "; ".join(esc(w) for w in report.warnings)) if report.warnings else ""
    await m.answer(f"✅ <b>{esc(st.name)}</b> v{ver.version} stored (hash <code>{ver.file_hash[:12]}</code>){warns}\nParameters: <code>{esc(str(ver.parameters)[:300])}</code>\nIt is NOT active yet.",
                   parse_mode="HTML", reply_markup=kb([[btn("▶️ Activate this version", C("str", "av", ver.id))], [btn("⚙️ Parameters", C("str", "params", st.id))], back_home("str:menu")]))


@router.message(UploadStrategy.waiting_file)
async def upload_wrong(m: Message) -> None:
    await m.answer("Please send the strategy as a <b>.py document</b> (or /cancel).", parse_mode="HTML")


# ---- activation -----------------------------------------------------------------------------------------
async def _activate(cb: CallbackQuery, ctx: Any, role: Role, vid: str, rollback: bool = False) -> None:
    need(role, Permission.STRATEGY_MANAGE)
    v = await ctx.strategies.get_version(vid)
    st = await ctx.strategies.get(v.strategy_id)
    if not await confirm_gate(cb, title=f"{'Rollback to' if rollback else 'Activate'} <b>{esc(st.name)}</b> v{v.version}?\nExisting bots keep their pinned version until you change it.", cancel=C("str", "view", st.id)):
        return
    await run_command(ctx, cb, "STRATEGY_ROLLBACK" if rollback else "STRATEGY_ACTIVATE", lambda cid: ctx.strategies.activate(vid, cb.from_user.id, rollback=rollback), target_type="strategy_version", target_id=vid)
    await _detail(cb, ctx, st.id)


@router.callback_query(F.data.startswith("str:av:"))
async def act_version(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    await _activate(cb, ctx, role, split(cb.data)[2])


@router.callback_query(F.data.startswith("str:rv:"))
async def rb_version(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    await _activate(cb, ctx, role, split(cb.data)[2], rollback=True)


async def _version_picker(cb: CallbackQuery, ctx: Any, sid: str, action: str, title: str) -> None:
    vers = await ctx.strategies.versions(sid)
    rows = [[btn(f"{'★ ' if v.is_active else ''}v{v.version} · {v.status}", C("str", action, v.id))] for v in vers[:10]]
    rows.append(back_home(C("str", "view", sid)))
    await show(cb, title, kb(rows))
    await toast(cb, "")


@router.callback_query(F.data.startswith("str:act:"))
async def act_pick(cb: CallbackQuery, ctx: Any) -> None:
    await _version_picker(cb, ctx, split(cb.data)[2], "av", "▶️ Choose the version to activate:")


@router.callback_query(F.data.startswith("str:rb:"))
async def rb_pick(cb: CallbackQuery, ctx: Any) -> None:
    sid = split(cb.data)[2]
    st = await ctx.strategies.get(sid)
    vers = [v for v in await ctx.strategies.versions(sid) if v.id != st.active_version_id]
    rows = [[btn(f"v{v.version} · {v.uploaded_at:%Y-%m-%d}", C("str", "rv", v.id))] for v in vers[:10]]
    rows.append(back_home(C("str", "view", sid)))
    await show(cb, "↩️ Roll back to which version?" + ("" if vers else "\nNo other versions."), kb(rows))
    await toast(cb, "")


@router.callback_query(F.data.startswith("str:deact:"))
async def deact(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.STRATEGY_MANAGE)
    sid = split(cb.data)[2]
    st = await ctx.strategies.get(sid)
    if not await confirm_gate(cb, title=f"Deactivate <b>{esc(st.name)}</b>? New bots cannot use it until re-activated.", cancel=C("str", "view", sid)):
        return
    await run_command(ctx, cb, "STRATEGY_DEACTIVATE", lambda cid: ctx.strategies.deactivate(sid, cb.from_user.id), target_type="strategy", target_id=sid)
    await _detail(cb, ctx, sid)


@router.callback_query(F.data.startswith("str:ver:"))
async def versions(cb: CallbackQuery, ctx: Any) -> None:
    sid = split(cb.data)[2]
    st = await ctx.strategies.get(sid)
    vers = await ctx.strategies.versions(sid)
    text = f"📦 <b>{esc(st.name)}</b> versions\n" + "\n".join(f"{'★' if v.is_active else '•'} v{v.version} · {v.status} · {v.uploaded_at:%Y-%m-%d %H:%M} · <code>{v.file_hash[:8]}</code>" for v in vers[:15])
    await show(cb, text, kb([[btn("▶️ Activate…", C("str", "act", sid)), btn("↩️ Rollback…", C("str", "rb", sid))], [btn("✅ Validate…", C("str", "validate", sid))], back_home(C("str", "view", sid))]))
    await toast(cb, "")


@router.callback_query(F.data.startswith("str:validate:"))
async def validate(cb: CallbackQuery, ctx: Any) -> None:
    await _version_picker(cb, ctx, split(cb.data)[2], "vv", "✅ Re-validate which version?")


@router.callback_query(F.data.startswith("str:vv:"))
async def validate_version(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.STRATEGY_MANAGE)
    vid = split(cb.data)[2]
    v = await ctx.strategies.get_version(vid)
    rep = await ctx.strategies.revalidate(vid, cb.from_user.id)
    text = ("✅ <b>Valid</b>" if rep.ok else "❌ <b>Invalid</b>\n" + "\n".join(f"• {esc(e)}" for e in rep.errors[:10])) + f"\nsha256 <code>{rep.sha256[:16]}</code> · {rep.size} bytes" + ("\n⚠️ " + "; ".join(map(esc, rep.warnings)) if rep.warnings else "")
    await show(cb, text, kb([back_home(C("str", "ver", v.strategy_id))]))
    await toast(cb, "")


# ---- parameters ---------------------------------------------------------------------------------------------
@router.callback_query(F.data.startswith("str:params:"))
async def params(cb: CallbackQuery, ctx: Any) -> None:
    sid = split(cb.data)[2]
    st = await ctx.strategies.get(sid)
    vers = await ctx.strategies.versions(sid)
    ver = next((v for v in vers if v.id == st.active_version_id), vers[0] if vers else None)
    if ver is None:
        await show(cb, "No versions.", kb([back_home("str:menu")]))
        return
    rows = [[btn(f"{k} = {val}"[:40], C("str", "pe", ver.id, k))] for k, val in list(ver.parameters.items())[:12]]
    rows.append(back_home(C("str", "view", sid)))
    await show(cb, f"⚙️ <b>{esc(st.name)}</b> v{ver.version} default parameters\nTap one to change its default (bots can override per bot).", kb(rows))
    await toast(cb, "")


@router.callback_query(F.data.startswith("str:pe:"))
async def param_edit(cb: CallbackQuery, state: FSMContext, role: Role) -> None:
    need(role, Permission.STRATEGY_MANAGE)
    _, _, vid, key = split(cb.data)[:4]
    await state.set_state(StrategyParam.value)
    await state.update_data(vid=vid, key=key)
    await show(cb, f"Send the new value for <b>{esc(key)}</b> (/cancel to abort):")
    await toast(cb, "")


@router.message(StrategyParam.value)
async def param_value(m: Message, state: FSMContext, ctx: Any, role: Role) -> None:
    need(role, Permission.STRATEGY_MANAGE)
    d = await state.get_data()
    await ctx.strategies.set_param(d["vid"], d["key"], m.text or "", m.from_user.id)
    await state.clear()
    v = await ctx.strategies.get_version(d["vid"])
    await m.answer(f"✅ <b>{esc(d['key'])}</b> updated.", parse_mode="HTML", reply_markup=kb([[btn("⚙️ Parameters", C("str", "params", v.strategy_id))], back_home("str:menu")]))


@router.callback_query(F.data.startswith("str:stats:"))
async def stats(cb: CallbackQuery, ctx: Any) -> None:
    sid = split(cb.data)[2]
    st = await ctx.strategies.get(sid)
    s = await ctx.strategies.stats(sid)
    vers = {v.id: v.version for v in await ctx.strategies.versions(sid)}
    per = "\n".join(f"  v{vers.get(k, '?')}: {int(d['trades'])} trades, {pnl(d['net'])}" for k, d in s["by_version"].items()) or "  —"
    await show(cb, f"📊 <b>{esc(st.name)}</b>\nBots {s['bots']} · trades {s['trades']} · win rate {s['win_rate']:.1f}% · net {pnl(s['net_pnl'])}\nPer version:\n{per}", kb([back_home(C("str", "view", sid))]))
    await toast(cb, "")


@router.callback_query(F.data.startswith("str:del:"))
async def delete(cb: CallbackQuery, ctx: Any, role: Role) -> None:
    need(role, Permission.STRATEGY_MANAGE)
    sid = split(cb.data)[2]
    st = await ctx.strategies.get(sid)
    if not await confirm_gate(cb, title=f"Delete strategy <b>{esc(st.name)}</b>? (no bot may be using it)", cancel=C("str", "view", sid)):
        return
    await run_command(ctx, cb, "STRATEGY_DELETE", lambda cid: ctx.strategies.delete(sid, cb.from_user.id), target_type="strategy", target_id=sid)
    await show(cb, "🗑 Strategy deleted.", kb([back_home("str:menu")]))
