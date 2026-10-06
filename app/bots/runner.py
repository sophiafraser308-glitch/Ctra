"""BotRunner: one asyncio task per bot. Feeds market data to the sandboxed strategy and forwards signals."""
from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

from app.core.enums import BotStatus, Category
from app.core.exceptions import StrategyCrashed, StrategyTimeout
from app.core.utils import utcnow
from app.ctrader.types import BarData
from app.logging import get_logger
from app.strategies.host import StrategyHost

log = get_logger(Category.BOT)


class BotRunner:
    def __init__(self, ctx: Any, bot_id: str) -> None:
        self.ctx, self.bot_id = ctx, bot_id
        self.host: StrategyHost | None = None
        self.task: asyncio.Task | None = None
        self.paused = False
        self.locked = False
        self.crashed: str | None = None
        self.started = asyncio.Event()
        self._ticks: dict[str, Any] = {}
        self._bars: deque[tuple[str, BarData]] = deque(maxlen=500)
        self._events: deque[tuple[str, dict]] = deque(maxlen=200)
        self._wake = asyncio.Event()
        self._stop = False
        self._sym_names: dict[int, str] = {}
        self.last_loop_at = utcnow()

    @property
    def consumer_id(self) -> str:
        return f"bot:{self.bot_id}"

    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def prepare(self) -> None:
        """Load strategy, start sandbox, subscribe market data. Raises on failure (BotManager marks ERROR)."""
        ctx = self.ctx
        bot = await ctx.bots.get(self.bot_id)
        acc = await ctx.accounts.get(bot.account_id)
        if not ctx.gateway.is_connected(acc.id):
            await ctx.accounts.connect(acc.id)
        source, ver = await ctx.strategies.load_source(bot.strategy_version_id)
        params = {**ver.parameters, **(bot.parameters or {})}
        context = {"symbols": bot.symbols, "timeframes": bot.timeframes, "environment": acc.environment, "bot_id": bot.id}
        self.host = StrategyHost(ctx.settings, f"{bot.name}:{bot.id}", source, ver.meta["class_name"], params, context)
        await self.host.start()
        infos = await ctx.market.subscribe(self.consumer_id, acc.id, bot.symbols, bot.timeframes, self._on_tick, self._on_bar)
        self._sym_names = {i.symbol_id: n for n, i in infos.items()}
        self.task = asyncio.create_task(self._loop(), name=f"bot-{self.bot_id}")
        ctx.gateway.execution_handlers.append(self._on_exec)

    # ---- inbound (called synchronously by market data) ---------------------------------------
    def _on_tick(self, symbol: str, quote: Any) -> None:
        self._ticks[symbol] = (quote.bid, quote.ask, int(utcnow().timestamp() * 1000))   # coalesced: latest only
        self._wake.set()

    def _on_bar(self, symbol: str, bar: BarData) -> None:
        self._bars.append((symbol, bar))
        self._wake.set()

    async def _on_exec(self, account_id: str, evt: Any) -> None:
        if evt.order and evt.order.client_order_id:
            self._events.append(("on_order_update", {"update": {"type": evt.execution_type, "order_id": evt.order.order_id, "client_order_id": evt.order.client_order_id, "status": evt.order.status}}))
            self._wake.set()

    # ---- main loop -----------------------------------------------------------------------------
    async def _loop(self) -> None:
        ctx = self.ctx
        try:
            sigs = await self.host.call("on_start")  # type: ignore[union-attr]
            await self._emit(sigs)
            self.started.set()
            while not self._stop:
                try:
                    await asyncio.wait_for(self._wake.wait(), 5)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                self.last_loop_at = utcnow()
                await ctx.bots.beat(self.bot_id)
                if self._stop:
                    break
                if self.paused or self.locked:
                    self._ticks.clear(); self._bars.clear(); self._events.clear()
                    continue
                if not (self.host and self.host.alive):
                    raise StrategyCrashed(self.host.dead_reason if self.host else "no host")
                while self._bars:
                    sym, bar = self._bars.popleft()
                    hist = ctx.market.get_bars(await self._acc_id(), bar.symbol_id, bar.timeframe, 200)
                    hist_d = [self._bar_dict(b) for b in hist if b.ts_ms < bar.ts_ms]
                    sigs = await self.host.call("on_bar", {"symbol": sym, "timeframe": bar.timeframe, "bar": self._bar_dict(bar), "history": hist_d})
                    for sg in sigs:
                        sg["timeframe"] = str(bar.timeframe).upper()      # the platform (not the strategy) records which timeframe fired
                    await self._emit(sigs)
                while self._events:
                    m, p = self._events.popleft()
                    await self.host.call(m, p)
                if self._ticks:
                    ticks, self._ticks = self._ticks, {}
                    for sym, (bid, ask, ts) in ticks.items():
                        await self._emit(await self.host.call("on_tick", {"symbol": sym, "bid": bid, "ask": ask, "ts_ms": ts}))
        except asyncio.CancelledError:
            raise
        except (StrategyCrashed, StrategyTimeout) as exc:
            self.crashed = f"{type(exc).__name__}: {exc}"
            log.error("bot %s strategy failure: %s | stderr: %s", self.bot_id, exc, self.host.stderr_tail()[-400:] if self.host else "", ctx={"bot": self.bot_id})
            await ctx.bots.on_runner_crash(self.bot_id, self.crashed)
        except Exception as exc:
            self.crashed = f"{type(exc).__name__}: {str(exc)[:200]}"
            log.error("bot %s runner error: %s", self.bot_id, self.crashed, exc_info=True)
            await ctx.bots.on_runner_crash(self.bot_id, self.crashed)

    async def _acc_id(self) -> str:
        return (await self.ctx.bots.get(self.bot_id)).account_id

    @staticmethod
    def _bar_dict(b: BarData) -> dict:
        return {"t": b.ts_ms, "o": b.open, "h": b.high, "l": b.low, "c": b.close, "v": b.volume}

    async def _emit(self, signals: list[dict]) -> None:
        if not signals:
            return
        bot = await self.ctx.bots.get(self.bot_id)
        await self.ctx.pipeline.process(bot, signals)

    # ---- shutdown --------------------------------------------------------------------------------
    async def shutdown(self) -> None:
        self._stop = True
        self._wake.set()
        try:
            self.ctx.gateway.execution_handlers.remove(self._on_exec)
        except ValueError:
            pass
        try:
            await self.ctx.market.unsubscribe(self.consumer_id)
        except Exception:
            pass
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await asyncio.wait_for(self.task, 5)
            except (asyncio.CancelledError, Exception):
                pass
        if self.host:
            await self.host.stop()
