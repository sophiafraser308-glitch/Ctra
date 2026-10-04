"""BotManager: lifecycle state machine with per-bot locks, idempotent transitions and restart recovery."""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from app.audit import AuditService
from app.config import Settings
from app.core.enums import BotStatus, Category, LockScope, Severity
from app.core.exceptions import ConflictError, NotFoundError, SafetyError, ValidationFailed
from app.core.utils import utcnow
from app.database import Database
from app.logging import get_logger
from app.models import Bot, RiskLock, TradingAccount
from app.notifications.service import Notifier
from app.schemas.strategy import TIMEFRAMES
from app.workers.heartbeat import HeartbeatService

log = get_logger(Category.BOT)
S = BotStatus
ALLOWED: dict[S, set[S]] = {
    S.CREATED: {S.STARTING}, S.STOPPED: {S.STARTING}, S.ERROR: {S.STARTING, S.STOPPED}, S.CRASHED: {S.STARTING, S.STOPPED, S.RESTARTING},
    S.STARTING: {S.RUNNING, S.ERROR, S.STOPPING}, S.RUNNING: {S.PAUSED, S.STOPPING, S.RESTARTING, S.LOCKED, S.CRASHED, S.ERROR},
    S.PAUSED: {S.RUNNING, S.STOPPING, S.LOCKED, S.CRASHED}, S.LOCKED: {S.RUNNING, S.PAUSED, S.STOPPING, S.CRASHED},
    S.STOPPING: {S.STOPPED}, S.RESTARTING: {S.STARTING, S.STOPPED, S.ERROR},
}
LIVE_STATES = (S.RUNNING.value, S.STARTING.value, S.PAUSED.value, S.LOCKED.value, S.RESTARTING.value)


class BotManager:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.db: Database = ctx.db
        self.runners: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, bot_id: str) -> asyncio.Lock:
        return self._locks.setdefault(bot_id, asyncio.Lock())

    # ---- queries -------------------------------------------------------------------
    async def get(self, bot_id: str) -> Bot:
        async with self.db.session() as s:
            b = await s.get(Bot, bot_id)
        if b is None or b.deleted_at is not None:
            raise NotFoundError("Bot not found")
        return b

    async def list(self, include_stopped: bool = True) -> list[Bot]:
        async with self.db.session() as s:
            q = select(Bot).where(Bot.deleted_at.is_(None)).order_by(Bot.created_at)
            return list((await s.execute(q)).scalars())

    async def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for b in await self.list():
            out[b.status] = out.get(b.status, 0) + 1
        return out

    # ---- state machine -----------------------------------------------------------------
    async def _set(self, bot_id: str, new: S, error: str | None = None, **fields: Any) -> Bot:
        async with self.db.session() as s:
            b = await s.get(Bot, bot_id)
            old = S(b.status)
            if new != old and new not in ALLOWED.get(old, set()):
                raise ConflictError(f"Illegal bot transition {old.value} -> {new.value}")
            b.status = new.value
            if error is not None:
                b.last_error = error[:500]
            for k, v in fields.items():
                setattr(b, k, v)
            await s.flush()
        if new != old:
            log.notice("bot %s %s -> %s", bot_id, old.value, new.value, ctx={"bot": bot_id})
            self.ctx.heartbeat.update(f"bot:{bot_id}", {"RUNNING": "OK", "PAUSED": "OK", "LOCKED": "DEGRADED"}.get(new.value, "DOWN" if new.value in ("ERROR", "CRASHED", "STOPPED") else "STARTING"), operation=new.value)
        return b

    # ---- create / update / delete ---------------------------------------------------------
    async def create(self, *, name: str, account_id: str, strategy_id: str, version_id: str | None, symbols: list[str],
                     timeframes: list[str], parameters: dict[str, Any] | None, risk_profile_id: str | None, user_id: int) -> Bot:
        name = name.strip()
        if not (1 <= len(name) <= 64):
            raise ValidationFailed("Bot name must be 1-64 characters")
        acc = await self.ctx.accounts.get(account_id)
        st = await self.ctx.strategies.get(strategy_id)
        vid = version_id or st.active_version_id
        if not vid:
            raise ValidationFailed("Strategy has no active version — activate one first")
        ver = await self.ctx.strategies.get_version(vid)
        symbols = [s.strip().upper() for s in symbols if s.strip()]
        if not symbols:
            raise ValidationFailed("At least one symbol is required")
        timeframes = [t.upper() for t in timeframes] or ["M5"]
        bad = [t for t in timeframes if t not in TIMEFRAMES]
        if bad:
            raise ValidationFailed(f"Unsupported timeframes {bad}; allowed {list(TIMEFRAMES)}")
        parameters = parameters or {}
        unknown = [k for k in parameters if k not in ver.parameters]
        if unknown:
            raise ValidationFailed(f"Unknown strategy parameters: {unknown}")
        async with self.db.session() as s:
            bot = Bot(name=name, account_id=account_id, environment=acc.environment, strategy_id=strategy_id, strategy_version_id=vid,
                      symbols=symbols, timeframes=timeframes, parameters=parameters,
                      risk_profile_id=risk_profile_id or acc.risk_profile_id, status=S.CREATED.value, desired_state="STOPPED")
            s.add(bot)
            await s.flush()
        await self.ctx.audit.record(action="BOT_CREATED", user_id=user_id, target=bot.id, new_state={"name": name, "account": account_id, "strategy": f"{st.name}@{ver.version}", "symbols": symbols, "env": acc.environment})
        return bot

    async def update(self, bot_id: str, user_id: int, *, symbols: list[str] | None = None, timeframes: list[str] | None = None,
                     parameters: dict[str, Any] | None = None, version_id: str | None = None, risk_profile_id: str | None = None) -> Bot:
        async with self._lock(bot_id):
            bot = await self.get(bot_id)
            if bot.status in LIVE_STATES:
                raise ConflictError("Stop the bot before changing its configuration")
            prev = {"symbols": bot.symbols, "timeframes": bot.timeframes, "parameters": bot.parameters, "version": bot.strategy_version_id, "risk": bot.risk_profile_id}
            async with self.db.session() as s:
                b = await s.get(Bot, bot_id)
                if symbols is not None:
                    b.symbols = [x.strip().upper() for x in symbols if x.strip()] or b.symbols
                if timeframes is not None:
                    tf = [t.upper() for t in timeframes]
                    if any(t not in TIMEFRAMES for t in tf):
                        raise ValidationFailed(f"Allowed timeframes: {list(TIMEFRAMES)}")
                    b.timeframes = tf
                if version_id is not None:
                    v = await self.ctx.strategies.get_version(version_id)
                    if v.strategy_id != b.strategy_id:
                        raise ValidationFailed("Version belongs to another strategy")
                    b.strategy_version_id = version_id
                    b.parameters = {k: x for k, x in (b.parameters or {}).items() if k in v.parameters}
                if parameters is not None:
                    v = await self.ctx.strategies.get_version(b.strategy_version_id)
                    if any(k not in v.parameters for k in parameters):
                        raise ValidationFailed("Unknown strategy parameter")
                    b.parameters = {**(b.parameters or {}), **parameters}
                if risk_profile_id is not None:
                    b.risk_profile_id = risk_profile_id
            await self.ctx.audit.record(action="BOT_UPDATED", user_id=user_id, target=bot_id, previous_state=prev)
            return await self.get(bot_id)

    async def delete(self, bot_id: str, user_id: int) -> None:
        async with self._lock(bot_id):
            bot = await self.get(bot_id)
            if bot.status in LIVE_STATES:
                raise ConflictError("Stop the bot before deleting it")
            async with self.db.session() as s:
                b = await s.get(Bot, bot_id)
                b.deleted_at = utcnow()
        await self.ctx.audit.record(action="BOT_DELETED", user_id=user_id, target=bot_id)

    # ---- lifecycle ---------------------------------------------------------------------------
    async def start(self, bot_id: str, user_id: int | None) -> str:
        async with self._lock(bot_id):
            bot = await self.get(bot_id)
            if bot.status == S.RUNNING.value and self.runners.get(bot_id) and self.runners[bot_id].running():
                return "already running"
            if bot.status in (S.STARTING.value, S.PAUSED.value, S.LOCKED.value):
                return f"already {bot.status.lower()}"
            acc = await self.ctx.accounts.get(bot.account_id)
            if acc.environment == "LIVE" and not self.ctx.sys_settings.live_allowed:
                raise SafetyError("LIVE trading is disabled (env gate and/or runtime switch). Enable it in Settings first.")
            if self.ctx.sys_settings.new_trading_disabled:
                raise SafetyError("New trading is disabled by emergency switch")
            if bot.status == S.RUNNING.value:      # DB says running but no runner (stale after crash) -> normalise
                await self._set(bot_id, S.CRASHED, "runner missing")
            if bot.status == S.STOPPING.value:
                raise ConflictError("Bot is stopping")
            await self._set(bot_id, S.STARTING, desired_state="RUNNING", last_error=None)
            from app.bots.runner import BotRunner
            runner = BotRunner(self.ctx, bot_id)
            self.runners[bot_id] = runner
            try:
                await runner.prepare()
                await asyncio.wait_for(runner.started.wait(), 25)
            except Exception as exc:
                await runner.shutdown()
                self.runners.pop(bot_id, None)
                await self._set(bot_id, S.ERROR, f"{type(exc).__name__}: {str(exc)[:300]}", desired_state="STOPPED")
                await self.ctx.audit.record(action="BOT_START", user_id=user_id, target=bot_id, result="FAILED", error=str(exc)[:300])
                raise
            locks = await self.ctx.locks.active(account_id=bot.account_id, bot_id=bot_id, strategy_id=bot.strategy_id)
            if locks:
                runner.locked = True
                await self._set(bot_id, S.RUNNING, started_at=utcnow(), lock_reason=None)
                await self._set(bot_id, S.LOCKED, lock_reason=locks[0].reason)
            else:
                await self._set(bot_id, S.RUNNING, started_at=utcnow(), lock_reason=None)
            await self.ctx.audit.record(action="BOT_START", user_id=user_id, target=bot_id)
            return "started"

    async def pause(self, bot_id: str, user_id: int | None) -> str:
        async with self._lock(bot_id):
            bot = await self.get(bot_id)
            if bot.status == S.PAUSED.value:
                return "already paused"
            if bot.status != S.RUNNING.value and bot.status != S.LOCKED.value:
                raise ConflictError(f"Cannot pause a bot in state {bot.status}")
            self.runners[bot_id].paused = True
            await self._set(bot_id, S.PAUSED, desired_state="PAUSED")
            await self.ctx.audit.record(action="BOT_PAUSE", user_id=user_id, target=bot_id)
            return "paused"

    async def resume(self, bot_id: str, user_id: int | None) -> str:
        async with self._lock(bot_id):
            bot = await self.get(bot_id)
            if bot.status == S.RUNNING.value:
                return "already running"
            if bot.status != S.PAUSED.value:
                raise ConflictError(f"Cannot resume a bot in state {bot.status}")
            runner = self.runners[bot_id]
            runner.paused = False
            locks = await self.ctx.locks.active(account_id=bot.account_id, bot_id=bot_id, strategy_id=bot.strategy_id)
            runner.locked = bool(locks)
            await self._set(bot_id, S.LOCKED if locks else S.RUNNING, desired_state="RUNNING")
            await self.ctx.audit.record(action="BOT_RESUME", user_id=user_id, target=bot_id)
            return "resumed"

    async def stop(self, bot_id: str, user_id: int | None, *, reason: str = "manual") -> str:
        async with self._lock(bot_id):
            bot = await self.get(bot_id)
            if bot.status in (S.STOPPED.value, S.CREATED.value):
                async with self.db.session() as s:
                    b = await s.get(Bot, bot_id)
                    b.desired_state = "STOPPED"
                return "already stopped"
            if bot.status in (S.ERROR.value, S.CRASHED.value):
                runner = self.runners.pop(bot_id, None)
                if runner:
                    await runner.shutdown()
                await self._set(bot_id, S.STOPPED, desired_state="STOPPED", stopped_at=utcnow())
                return "stopped"
            if bot.status == S.RESTARTING.value:
                await self._set(bot_id, S.STOPPED, desired_state="STOPPED", stopped_at=utcnow())
                return "stopped"
            await self._set(bot_id, S.STOPPING, desired_state="STOPPED")
            runner = self.runners.pop(bot_id, None)
            if runner:
                await runner.shutdown()
            await self._set(bot_id, S.STOPPED, stopped_at=utcnow())
            await self.ctx.audit.record(action="BOT_STOP", user_id=user_id, target=bot_id, meta={"reason": reason})
            return "stopped"

    async def restart(self, bot_id: str, user_id: int | None) -> str:
        bot = await self.get(bot_id)
        if bot.status in LIVE_STATES or bot.status in (S.CRASHED.value, S.ERROR.value):
            await self.stop(bot_id, user_id, reason="restart")
        async with self.db.session() as s:
            b = await s.get(Bot, bot_id)
            b.restart_count += 1
        res = await self.start(bot_id, user_id)
        await self.ctx.audit.record(action="BOT_RESTART", user_id=user_id, target=bot_id)
        return res

    async def stop_all(self, user_id: int | None, reason: str = "stop_all") -> int:
        n = 0
        for b in await self.list():
            if b.status in LIVE_STATES or b.status in (S.CRASHED.value, S.ERROR.value):
                try:
                    await self.stop(b.id, user_id, reason=reason)
                    n += 1
                except Exception as exc:
                    log.error("stop_all: bot %s: %s", b.id, str(exc)[:100])
        return n

    # ---- callbacks -------------------------------------------------------------------------------
    async def beat(self, bot_id: str) -> None:
        now = utcnow()
        self.ctx.heartbeat.update(f"bot:{bot_id}", "OK", operation="loop")
        try:
            async with self.db.session() as s:
                b = await s.get(Bot, bot_id)
                if b:
                    b.last_heartbeat_at = now
        except Exception:
            pass

    async def on_runner_crash(self, bot_id: str, message: str) -> None:
        try:
            bot = await self.get(bot_id)
            if bot.status in (S.STOPPING.value, S.STOPPED.value):
                return
            await self._set(bot_id, S.CRASHED, message)
            await self.ctx.notifier.notify("BOT_CRASH", f"Bot '{bot.name}' crashed: {message[:200]}", Severity.ERROR, dedup_key=f"crash:{bot_id}")
        except Exception as exc:
            log.error("on_runner_crash failed: %s", type(exc).__name__)

    async def _affected(self, lock: RiskLock) -> list[Bot]:
        bots = [b for b in await self.list() if b.id in self.runners]
        if lock.scope_type == LockScope.GLOBAL.value:
            return bots
        if lock.scope_type == LockScope.ACCOUNT.value:
            return [b for b in bots if b.account_id == lock.scope_id]
        if lock.scope_type == LockScope.BOT.value:
            return [b for b in bots if b.id == lock.scope_id]
        return [b for b in bots if b.strategy_id == lock.scope_id]

    async def on_lock(self, lock: RiskLock) -> None:
        for b in await self._affected(lock):
            async with self._lock(b.id):
                runner = self.runners.get(b.id)
                if runner:
                    runner.locked = True
                if b.status == S.RUNNING.value:
                    await self._set(b.id, S.LOCKED, lock_reason=lock.reason)

    async def on_release(self, lock: RiskLock) -> None:
        for b in await self._affected(lock):
            async with self._lock(b.id):
                still = await self.ctx.locks.active(account_id=b.account_id, bot_id=b.id, strategy_id=b.strategy_id)
                runner = self.runners.get(b.id)
                if not still and runner:
                    runner.locked = False
                    cur = await self.get(b.id)
                    if cur.status == S.LOCKED.value and not runner.paused:
                        await self._set(b.id, S.RUNNING, lock_reason=None)

    async def recover_on_startup(self) -> None:
        """Restart recovery: bots whose desired state was RUNNING are started again; stale statuses normalised."""
        for b in await self.list():
            if b.status in LIVE_STATES or b.status in (S.CRASHED.value, S.ERROR.value):
                async with self.db.session() as s:
                    row = await s.get(Bot, b.id)
                    row.status = S.STOPPED.value if b.desired_state != "RUNNING" else S.CRASHED.value
                    row.last_error = "recovered after process restart"
            if b.desired_state in ("RUNNING", "PAUSED"):
                try:
                    await self.start(b.id, None)
                    if b.desired_state == "PAUSED":
                        await self.pause(b.id, None)
                except Exception as exc:
                    log.error("recovery start failed bot=%s: %s", b.id, str(exc)[:150])

    async def shutdown(self) -> None:
        for bid, runner in list(self.runners.items()):
            try:
                await runner.shutdown()
            except Exception:
                pass
        self.runners.clear()

    async def stats(self, bot_id: str) -> dict[str, Any]:
        from app.models import Signal, Trade, Order
        from sqlalchemy import func
        async with self.db.session() as s:
            trades = (await s.execute(select(Trade).where(Trade.bot_id == bot_id))).scalars().all()
            sig = (await s.execute(select(func.count()).select_from(Signal).where(Signal.bot_id == bot_id))).scalar_one()
            rej = (await s.execute(select(func.count()).select_from(Signal).where(Signal.bot_id == bot_id, Signal.risk_decision == "REJECTED"))).scalar_one()
            orders = (await s.execute(select(func.count()).select_from(Order).where(Order.bot_id == bot_id))).scalar_one()
        n = len(trades)
        wins = sum(1 for t in trades if t.net_pnl > 0)
        return {"signals": sig, "rejected": rej, "orders": orders, "trades": n, "wins": wins, "win_rate": (wins / n * 100) if n else 0.0, "net_pnl": sum(t.net_pnl for t in trades)}
