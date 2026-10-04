"""Market data layer: spot cache, bar building, subscriptions (ref-counted), freshness/STALE detection."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from app.config import Settings
from app.core.enums import Category, MarketDataStatus, Severity
from app.core.exceptions import CTraderError
from app.core.utils import utcnow
from app.ctrader.gateway import CTraderGateway
from app.ctrader.market_data import TF_SECONDS
from app.ctrader.types import BarData, SymbolInfo, TickEvent
from app.logging import get_logger
from app.notifications.service import Notifier
from app.workers.heartbeat import HeartbeatService

log = get_logger(Category.CTRADER)


@dataclass
class Quote:
    bid: float | None = None
    ask: float | None = None
    received_monotonic: float = 0.0
    received_at: datetime | None = None

    @property
    def mid(self) -> float | None:
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2
        return self.bid if self.bid is not None else self.ask


@dataclass
class Consumer:
    consumer_id: str
    account_id: str
    symbol_ids: dict[int, str]                  # symbol_id -> name
    timeframes: list[str]
    on_tick: Callable[[str, Quote], None] | None
    on_bar: Callable[[str, BarData], None] | None


class MarketDataService:
    HISTORY = 500

    def __init__(self, settings: Settings, gateway: CTraderGateway, hb: HeartbeatService, notifier: Notifier) -> None:
        self.s, self.gateway, self.hb, self.notifier = settings, gateway, hb, notifier
        self.quotes: dict[tuple[str, int], Quote] = {}
        self.bars: dict[tuple[str, int, str], deque[BarData]] = defaultdict(lambda: deque(maxlen=self.HISTORY))
        self._building: dict[tuple[str, int, str], BarData] = {}
        self.consumers: dict[str, Consumer] = {}
        self._status_prev: dict[tuple[str, int], MarketDataStatus] = {}
        self._task: asyncio.Task | None = None
        self.last_tick_at: datetime | None = None
        gateway.tick_handlers.append(self._on_tick)

    # ---- lifecycle -------------------------------------------------
    async def start(self) -> None:
        self._task = asyncio.create_task(self._watch(), name="market-data-watch")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    # ---- subscriptions ----------------------------------------------
    async def subscribe(self, consumer_id: str, account_id: str, symbols: list[str], timeframes: list[str],
                        on_tick: Callable[[str, Quote], None] | None = None,
                        on_bar: Callable[[str, BarData], None] | None = None) -> dict[str, SymbolInfo]:
        infos: dict[str, SymbolInfo] = {}
        for name in symbols:
            infos[name] = await self.gateway.get_symbol(account_id, name)
        self.consumers[consumer_id] = Consumer(consumer_id, account_id, {i.symbol_id: n for n, i in infos.items()},
                                               list(timeframes), on_tick, on_bar)
        await self.gateway.subscribe_spots(account_id, [i.symbol_id for i in infos.values()])
        for name, info in infos.items():
            for tf in timeframes:
                key = (account_id, info.symbol_id, tf)
                if not self.bars[key]:
                    try:
                        hist = await self.gateway.get_bars(account_id, info.symbol_id, tf, 200)
                        bucket = int(time.time() * 1000) // (TF_SECONDS[tf] * 1000) * TF_SECONDS[tf] * 1000
                        self.bars[key].extend(b for b in hist if b.ts_ms < bucket)
                    except CTraderError as exc:
                        log.warning("history backfill failed %s %s: %s", name, tf, str(exc)[:100])
        return infos

    async def unsubscribe(self, consumer_id: str) -> None:
        c = self.consumers.pop(consumer_id, None)
        if not c:
            return
        still = {sid for o in self.consumers.values() if o.account_id == c.account_id for sid in o.symbol_ids}
        gone = [sid for sid in c.symbol_ids if sid not in still]
        if gone:
            await self.gateway.unsubscribe_spots(c.account_id, gone)

    # ---- tick handling (called synchronously from the gateway) ---------
    def _on_tick(self, account_id: str, t: TickEvent) -> None:
        key = (account_id, t.symbol_id)
        q = self.quotes.setdefault(key, Quote())
        if t.bid is not None:
            q.bid = t.bid
        if t.ask is not None:
            q.ask = t.ask
        q.received_monotonic = time.monotonic()
        q.received_at = utcnow()
        self.last_tick_at = q.received_at
        self.hb.update("market_data", "OK", market_data=True)
        price = q.bid if q.bid is not None else q.ask
        if price is None:
            return
        now_ms = int(time.time() * 1000)
        tfs = {tf for c in self.consumers.values() if c.account_id == account_id and t.symbol_id in c.symbol_ids for tf in c.timeframes}
        for tf in tfs:
            self._update_bar(account_id, t.symbol_id, tf, price, now_ms)
        for c in self.consumers.values():
            if c.account_id == account_id and t.symbol_id in c.symbol_ids and c.on_tick:
                try:
                    c.on_tick(c.symbol_ids[t.symbol_id], q)
                except Exception as exc:
                    log.error("consumer tick error: %s", type(exc).__name__)

    def _update_bar(self, account_id: str, symbol_id: int, tf: str, price: float, now_ms: int) -> None:
        span = TF_SECONDS[tf] * 1000
        bucket = now_ms // span * span
        key = (account_id, symbol_id, tf)
        cur = self._building.get(key)
        if cur is None or cur.ts_ms != bucket:
            if cur is not None:
                self.bars[key].append(cur)
                for c in self.consumers.values():
                    if c.account_id == account_id and symbol_id in c.symbol_ids and tf in c.timeframes and c.on_bar:
                        try:
                            c.on_bar(c.symbol_ids[symbol_id], cur)
                        except Exception as exc:
                            log.error("consumer bar error: %s", type(exc).__name__)
            self._building[key] = BarData(symbol_id, tf, bucket, price, price, price, price, 1)
        else:
            cur.high, cur.low, cur.close = max(cur.high, price), min(cur.low, price), price
            cur.volume += 1

    # ---- queries ------------------------------------------------------------
    def quote(self, account_id: str, symbol_id: int) -> Quote | None:
        return self.quotes.get((account_id, symbol_id))

    def status(self, account_id: str, symbol_id: int) -> MarketDataStatus:
        q = self.quotes.get((account_id, symbol_id))
        if q is None or q.received_monotonic == 0:
            return MarketDataStatus.NO_DATA
        return MarketDataStatus.STALE if time.monotonic() - q.received_monotonic > self.s.market_data_stale_seconds else MarketDataStatus.FRESH

    def age_seconds(self, account_id: str, symbol_id: int) -> float | None:
        q = self.quotes.get((account_id, symbol_id))
        return None if q is None else time.monotonic() - q.received_monotonic

    def get_bars(self, account_id: str, symbol_id: int, tf: str, n: int = 100) -> list[BarData]:
        return list(self.bars[(account_id, symbol_id, tf)])[-n:]

    def spread_pips(self, account_id: str, sym: SymbolInfo) -> float | None:
        q = self.quote(account_id, sym.symbol_id)
        if q and q.bid is not None and q.ask is not None:
            return (q.ask - q.bid) / sym.pip_size
        return None

    async def ensure_quote(self, account_id: str, sym: SymbolInfo, wait: float = 3.0) -> Quote | None:
        """Subscribe on demand (e.g. conversion pairs for risk sizing) and wait briefly for the first tick."""
        if self.quote(account_id, sym.symbol_id) is None:
            await self.gateway.subscribe_spots(account_id, [sym.symbol_id])
            end = time.monotonic() + wait
            while time.monotonic() < end and self.quote(account_id, sym.symbol_id) is None:
                await asyncio.sleep(0.1)
        return self.quote(account_id, sym.symbol_id)

    async def conversion_rate(self, account_id: str, from_ccy: str, to_ccy: str) -> float | None:
        """Rate to convert 1 unit of from_ccy into to_ccy using subscribed/known symbols."""
        if from_ccy == to_ccy:
            return 1.0
        sess = self.gateway.sessions.get(account_id)
        if not sess:
            return None
        direct = sess.symbols_by_name.get(f"{from_ccy}{to_ccy}")
        if direct:
            q = await self.ensure_quote(account_id, direct)
            return q.mid if q and q.mid else None
        inverse = sess.symbols_by_name.get(f"{to_ccy}{from_ccy}")
        if inverse:
            q = await self.ensure_quote(account_id, inverse)
            return (1.0 / q.mid) if q and q.mid else None
        return None

    def stale_list(self) -> list[tuple[str, str]]:
        out = []
        for c in self.consumers.values():
            for sid, name in c.symbol_ids.items():
                if self.status(c.account_id, sid) != MarketDataStatus.FRESH:
                    out.append((c.account_id, name))
        return out

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(5)
            try:
                stale = 0
                for c in list(self.consumers.values()):
                    for sid, name in c.symbol_ids.items():
                        st = self.status(c.account_id, sid)
                        key = (c.account_id, sid)
                        prev = self._status_prev.get(key)
                        self._status_prev[key] = st
                        if st != MarketDataStatus.FRESH:
                            stale += 1
                        if st == MarketDataStatus.STALE and prev == MarketDataStatus.FRESH and self.gateway.is_connected(c.account_id):
                            await self.notifier.notify("STALE_MARKET_DATA", f"Market data STALE for {name} (account {c.account_id}). New trading is blocked by risk policy.",
                                                       Severity.WARNING, dedup_key=f"stale:{c.account_id}:{name}")
                        elif st == MarketDataStatus.FRESH and prev == MarketDataStatus.STALE:
                            log.info("market data recovered %s", name)
                self.hb.update("market_data", "OK" if stale == 0 else "DEGRADED", subscriptions=len(self.consumers), stale=stale)
            except Exception as exc:
                log.error("market data watch error: %s", type(exc).__name__)
