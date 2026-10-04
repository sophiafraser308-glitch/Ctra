"""Watchdog: detects failures and applies safe automatic recovery (backoff, crash-loop lock, never blind order resend)."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from app.core.enums import BotStatus, Category, LockScope, Severity
from app.core.utils import utcnow
from app.logging import db_handler, get_logger
from app.models import Bot

log = get_logger(Category.WATCHDOG)


class WatchdogService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self._task: asyncio.Task | None = None
        self._restart_times: dict[str, list[float]] = {}
        self._next_restart_ok: dict[str, float] = {}
        self._acct_retry: dict[str, tuple[int, float]] = {}
        self._mgr_down_since: dict[str, float] = {}
        self.last_run_at = None
        self.last_findings: list[str] = []
        self._err_alerted = 0.0

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="watchdog")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.ctx.settings.watchdog_interval_seconds)
            findings: list[str] = []
            for check in (self._check_database, self._check_telegram, self._check_ctrader, self._check_bots,
                          self._check_orders, self._check_reconciliation, self._check_errors, self._check_tokens):
                try:
                    findings += await check()
                except Exception as exc:
                    findings.append(f"{check.__name__} crashed: {type(exc).__name__}")
                    log.error("watchdog check %s failed", check.__name__, exc_info=True)
            self.last_findings = findings
            self.last_run_at = utcnow()
            self.ctx.heartbeat.update("watchdog", "OK" if not findings else "DEGRADED", operation="cycle", findings=len(findings))

    # ---- checks -------------------------------------------------------------------------------
    async def _check_database(self) -> list[str]:
        ok = await self.ctx.db.ping()
        self.ctx.heartbeat.update("database", "OK" if ok else "DOWN", operation="ping")
        if not ok:
            log.critical("DATABASE UNREACHABLE")
            await self.ctx.notifier.notify("DATABASE_DOWN", "Database unreachable!", Severity.CRITICAL, dedup_key="db-down")
            return ["database down"]
        return []

    async def _check_telegram(self) -> list[str]:
        bot = self.ctx.bot_api
        if bot is None:
            return []
        try:
            await asyncio.wait_for(bot.get_me(), 10)
            self.ctx.heartbeat.update("telegram", "OK", operation="get_me")
            return []
        except Exception as exc:
            self.ctx.heartbeat.update("telegram", "DEGRADED", error=type(exc).__name__)
            log.warning("telegram check failed: %s", type(exc).__name__)
            return ["telegram unreachable"]

    async def _check_ctrader(self) -> list[str]:
        out: list[str] = []
        gw = self.ctx.gateway
        now = time.monotonic()
        for env, mgr in gw.managers.items():
            if mgr.is_ready:
                self._mgr_down_since.pop(env, None)
            else:
                since = self._mgr_down_since.setdefault(env, now)
                out.append(f"ctrader {env} {mgr.state.value}")
                if now - since > 180:
                    log.warning("restarting cTrader %s connection manager", env)
                    self._mgr_down_since[env] = now
                    await mgr.restart()
        any_ready = any(m.is_ready for m in gw.managers.values())
        self.ctx.heartbeat.update("ctrader", "OK" if (any_ready or not gw.managers) else "DOWN", sessions=len(gw.sessions))
        for aid, sess in list(gw.sessions.items()):
            if sess.desired and not sess.authed:
                n, nxt = self._acct_retry.get(aid, (0, 0.0))
                if now >= nxt:
                    self._acct_retry[aid] = (n + 1, now + min(30 * 2 ** n, 600))
                    try:
                        await self.ctx.accounts.connect(aid)
                        self._acct_retry.pop(aid, None)
                    except Exception as exc:
                        out.append(f"account {aid} reconnect failed")
                        log.warning("watchdog reconnect %s failed: %s", aid, str(exc)[:100])
            else:
                self._acct_retry.pop(aid, None)
        return out

    async def _check_bots(self) -> list[str]:
        out: list[str] = []
        now = time.monotonic()
        bots = await self.ctx.bots.list()
        cfg = self.ctx.settings
        for b in bots:
            runner = self.ctx.bots.runners.get(b.id)
            if b.status in (BotStatus.RUNNING.value, BotStatus.LOCKED.value, BotStatus.PAUSED.value):
                if runner is None or not runner.running() or (runner.host and not runner.host.alive):
                    out.append(f"bot {b.name} dead")
                    await self.ctx.bots.on_runner_crash(b.id, "watchdog: runner/strategy process not alive")
                elif (utcnow() - runner.last_loop_at).total_seconds() > 90:
                    out.append(f"bot {b.name} loop stalled")
            if b.status == BotStatus.CRASHED.value and b.desired_state == "RUNNING":
                times = [t for t in self._restart_times.get(b.id, []) if now - t < cfg.crash_loop_window_seconds]
                self._restart_times[b.id] = times
                if len(times) >= cfg.crash_loop_max_restarts:
                    await self.ctx.locks.create(LockScope.BOT, b.id, "RISK_LOCK_CRASH_LOOP", f"{len(times)} crashes within {cfg.crash_loop_window_seconds}s")
                    async with self.ctx.db.session() as s:
                        row = await s.get(Bot, b.id)
                        row.desired_state = "STOPPED"
                    await self.ctx.bots.stop(b.id, None, reason="crash_loop")
                    await self.ctx.notifier.notify("BOT_CRASH_LOOP", f"Bot '{b.name}' is in a crash loop and was stopped + locked. Check strategy code, then reset the lock.", Severity.CRITICAL, dedup_key=f"loop:{b.id}")
                    out.append(f"bot {b.name} crash loop")
                    continue
                if now >= self._next_restart_ok.get(b.id, 0):
                    self._restart_times.setdefault(b.id, []).append(now)
                    self._next_restart_ok[b.id] = now + min(10 * 2 ** len(times), 300)
                    try:
                        await self.ctx.bots.restart(b.id, None)
                        log.notice("watchdog restarted bot %s", b.id)
                    except Exception as exc:
                        out.append(f"bot {b.name} restart failed")
                        log.error("watchdog restart bot %s failed: %s", b.id, str(exc)[:150])
        self.ctx.heartbeat.update("bots", "OK" if not out else "DEGRADED", running=len(self.ctx.bots.runners))
        return out

    async def _check_orders(self) -> list[str]:
        stuck = await self.ctx.orders.stuck_processing(90)
        for o in stuck:
            log.error("order %s stuck in %s — marking UNKNOWN", o.id, o.status)
            await self.ctx.orders._mark_unknown(o.id, f"stuck in {o.status} (process interrupted?)")
        unknown = await self.ctx.orders.list(statuses=["UNKNOWN"], limit=20)
        for o in unknown:
            if (utcnow() - o.updated_at).total_seconds() > 60:
                asyncio.create_task(self.ctx.recon.resolve_unknown(o.id))
        return [f"{len(stuck)} stuck orders"] if stuck else []

    async def _check_reconciliation(self) -> list[str]:
        interval = self.ctx.settings.reconcile_interval_seconds
        stale = []
        for acc in await self.ctx.accounts.list():
            if self.ctx.gateway.is_connected(acc.id):
                last = self.ctx.recon.last_run.get(acc.id)
                if last is None or time.monotonic() - last > 3 * interval:
                    stale.append(acc.id)
                    asyncio.create_task(self.ctx.recon.reconcile(acc.id, "watchdog"))
        return [f"reconciliation overdue for {len(stale)} account(s)"] if stale else []

    async def _check_errors(self) -> list[str]:
        h = db_handler()
        if not h:
            return []
        n = h.recent_error_count(300)
        if n >= 25 and time.monotonic() - self._err_alerted > 600:
            self._err_alerted = time.monotonic()
            await self.ctx.notifier.notify("CRITICAL_ERROR", f"{n} ERROR/CRITICAL log records in the last 5 minutes. Running safety reconciliation.", Severity.CRITICAL, dedup_key="err-burst")
            asyncio.create_task(self.ctx.recon.reconcile_all("critical_error"))
            return [f"error burst ({n})"]
        return []

    async def _check_tokens(self) -> list[str]:
        if int(time.monotonic()) % 900 < self.ctx.settings.watchdog_interval_seconds:
            await self.ctx.accounts.refresh_tokens()
        return []
