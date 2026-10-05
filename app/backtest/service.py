"""BacktestService: downloads history from the broker, runs the strategy in the SAME sandbox used for live bots,
simulates execution with BacktestEngine and builds the reports. Never places orders and never touches live state."""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.backtest.engine import BacktestEngine, BtConfig, BtResult, ConvSeries, SymbolSpec
from app.backtest.report import build_csv, build_summary_text, build_xlsx
from app.core.enums import Category
from app.core.exceptions import PlatformError, ValidationFailed
from app.core.timeframes import TF_SECONDS
from app.core.utils import utcnow
from app.logging import get_logger
from app.market_data.catalog import AUTO_SPREAD_PIPS, classify
from app.models import BacktestRun
from app.strategies.host import StrategyHost

log = get_logger(Category.STRATEGY)
MAX_BARS = 150_000
CHUNK = 1000
WARMUP = 190
TOTAL_TIMEOUT = 20 * 60


class BacktestCancelled(PlatformError):
    code = "BACKTEST_CANCELLED"


@dataclass
class BtRequest:
    user_id: int
    strategy_id: str
    account_id: str
    symbols: list[str]
    timeframe: str
    date_from_ms: int
    date_to_ms: int
    options: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)       # overrides of the strategy's parameters


@dataclass
class BtOutcome:
    result: BtResult
    meta: dict[str, Any]
    summary_text: str
    xlsx: bytes | None
    csv: bytes | None
    run_id: str
    filename_base: str


@dataclass
class BtJob:
    job_id: str
    user_id: int
    chat_id: int
    task: asyncio.Task | None = None
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    status_text: str = "starting"


DEFAULT_OPTIONS: dict[str, Any] = {"initial_balance": 10_000.0, "spread": "auto", "commission_per_lot": 0.0, "risk_pct": 1.0,
                                   "max_open_positions": 10, "daily_loss_limit": 0.0, "daily_profit_target": 0.0,
                                   "tz_offset": 3.0, "output": "both"}


class BacktestService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self._sem = asyncio.Semaphore(1)
        self.jobs: dict[str, BtJob] = {}

    @property
    def busy(self) -> bool:
        return self._sem.locked()

    # ---- validation ------------------------------------------------------------------------------------------
    def preflight(self, req: BtRequest) -> None:
        if req.timeframe not in TF_SECONDS:
            raise ValidationFailed("Unknown timeframe")
        if not req.symbols:
            raise ValidationFailed("Select at least one symbol")
        now_ms = int(time.time() * 1000)
        if req.date_from_ms >= req.date_to_ms:
            raise ValidationFailed("Start date must be before the end date")
        if req.date_to_ms > now_ms + 86_400_000:
            raise ValidationFailed("End date cannot be in the future")
        est = (req.date_to_ms - req.date_from_ms) / (TF_SECONDS[req.timeframe] * 1000) * (5 / 7) * len(req.symbols)
        if req.timeframe not in ("D1", "W1", "MN1") and est > MAX_BARS:
            raise ValidationFailed(f"Too many bars (~{int(est):,} > {MAX_BARS:,}). Shorten the period, use a larger timeframe or fewer symbols.")
        if not self.ctx.gateway.is_connected(req.account_id):
            raise ValidationFailed("The selected account is not connected (history is downloaded from it)")

    # ---- job management ---------------------------------------------------------------------------------------
    def start_job(self, req: BtRequest, chat_id: int, on_progress: Callable[[str], Awaitable[None]],
                  on_done: Callable[[BtOutcome | None, str | None], Awaitable[None]]) -> BtJob:
        self.preflight(req)
        if self.busy:
            raise ValidationFailed("Another backtest is running. Wait for it to finish (or cancel it).")
        job = BtJob(job_id=utcnow().strftime("%H%M%S") + str(req.user_id)[-3:], user_id=req.user_id, chat_id=chat_id)

        async def runner() -> None:
            outcome, err = None, None
            try:
                async with self._sem:
                    outcome = await asyncio.wait_for(self.run(req, on_progress, job), TOTAL_TIMEOUT)
            except BacktestCancelled:
                err = "cancelled"
            except asyncio.TimeoutError:
                err = f"timed out after {TOTAL_TIMEOUT // 60} minutes — shorten the period or use a larger timeframe"
            except Exception as exc:
                log.error("backtest failed: %s", type(exc).__name__, exc_info=True)
                err = f"{type(exc).__name__}: {str(exc)[:300]}"
            finally:
                self.jobs.pop(job.job_id, None)
            try:
                await on_done(outcome, err)
            except Exception as exc:
                log.error("backtest delivery failed: %s", type(exc).__name__)

        job.task = asyncio.create_task(runner(), name=f"backtest-{job.job_id}")
        self.jobs[job.job_id] = job
        return job

    # ---- the run ----------------------------------------------------------------------------------------------------
    async def run(self, req: BtRequest, progress: Callable[[str], Awaitable[None]], job: BtJob) -> BtOutcome:
        ctx, started = self.ctx, time.monotonic()
        o = {**DEFAULT_OPTIONS, **req.options}
        tf, tf_ms = req.timeframe, TF_SECONDS[req.timeframe] * 1000
        acc = await ctx.accounts.get(req.account_id)
        st = await ctx.strategies.get(req.strategy_id)
        vid = st.active_version_id or (await ctx.strategies.versions(req.strategy_id))[0].id
        source, ver = await ctx.strategies.load_source(vid)
        unknown = [k for k in req.params if k not in ver.parameters]
        if unknown:
            raise ValidationFailed(f"Unknown strategy parameters: {unknown}")
        params = {**ver.parameters, **req.params}
        run_id = await self._create_row(req, ver.id, o)

        async def status(text: str) -> None:
            job.status_text = text
            await progress(text)

        try:
            await status("⬇️ Downloading history…")
            sess = ctx.gateway.session(req.account_id)
            deposit = acc.currency or sess.assets.get(sess.trader.deposit_asset_id or -1, "USD") if sess.trader else (acc.currency or "USD")
            warm_from = req.date_from_ms - tf_ms * WARMUP * 2
            specs: dict[str, SymbolSpec] = {}
            series: dict[str, list] = {}
            for i, name in enumerate(req.symbols, 1):
                if job.cancel.is_set():
                    raise BacktestCancelled("cancelled")
                sym = await ctx.gateway.get_symbol(req.account_id, name)
                await status(f"⬇️ Downloading {name} ({i}/{len(req.symbols)})…")
                bars = await ctx.gateway.get_bars_range(req.account_id, sym.symbol_id, tf, warm_from, req.date_to_ms)
                if not bars:
                    raise ValidationFailed(f"No history returned for {name} {tf} in this period")
                series[name] = bars
                quote = sess.assets.get(sym.quote_asset_id or -1, "")
                spread = o["spread"]
                if spread == "auto":
                    c = classify(name)
                    spread = AUTO_SPREAD_PIPS.get(c.category, 1.5) if c else 1.5
                spec = SymbolSpec(name=name, pip_size=sym.pip_size, lot_units=sym.lot_size / 100.0, digits=sym.digits,
                                  min_lots=sym.min_lots, step_lots=sym.step_lots, max_lots=min(sym.max_lots, 1000.0),
                                  spread_pips=float(spread), quote_ccy=quote)
                if quote and quote != deposit:
                    spec.conv = await self._conversion(req.account_id, quote, deposit, warm_from, req.date_to_ms, tf)
                    spec.conv_missing = spec.conv is None
                specs[name] = spec

            items: list[tuple[int, str, dict, bool]] = []
            warm_items: list[tuple[int, str, dict, bool]] = []
            for name, bars in series.items():
                before = [b for b in bars if b.ts_ms < req.date_from_ms][-WARMUP:]
                warm_items += [(b.ts_ms, name, self._bar(b), True) for b in before]
                items += [(b.ts_ms, name, self._bar(b), False) for b in bars if req.date_from_ms <= b.ts_ms < req.date_to_ms]
            items.sort(key=lambda x: (x[0], x[1]))
            if not items:
                raise ValidationFailed("No bars inside the selected period")
            if len(items) > MAX_BARS and tf not in ("D1", "W1", "MN1"):
                raise ValidationFailed(f"Too many bars ({len(items):,}); shorten the period")
            timeline = warm_items + items

            cfg = BtConfig(initial_balance=float(o["initial_balance"]), commission_per_lot=float(o["commission_per_lot"]), risk_pct=float(o["risk_pct"]),
                           max_open_positions=int(o["max_open_positions"]), daily_loss_limit=float(o["daily_loss_limit"]),
                           daily_profit_target=float(o["daily_profit_target"]), tz_offset_hours=float(o["tz_offset"]), tf_ms=tf_ms, deposit_ccy=deposit)
            engine = BacktestEngine(cfg, specs)
            host = StrategyHost(ctx.settings, f"backtest:{st.name}", source, ver.meta["class_name"], params,
                                {"symbols": req.symbols, "timeframes": [tf], "environment": "BACKTEST", "bot_id": "backtest"})
            await status("🧠 Starting strategy sandbox…")
            await host.start()
            try:
                await host.call("on_start", {}, timeout=30)
                total = len(timeline)
                for base in range(0, total, CHUNK):
                    if job.cancel.is_set():
                        raise BacktestCancelled("cancelled")
                    chunk = timeline[base:base + CHUNK]
                    res = await host.call("bt_chunk", {"base": base, "items": [[s, tf, b, w] for _, s, b, w in chunk]}, timeout=240)
                    sig_by_idx = {r["i"]: r["s"] for r in res}
                    for k, (ts, sym_name, bar, warm) in enumerate(chunk):
                        if not warm:
                            engine.feed(ts, sym_name, bar, sig_by_idx.get(base + k, []))
                    pct = min(100, int((base + len(chunk)) / total * 100))
                    await status(f"⚙️ Simulating… {pct}% ({base + len(chunk):,}/{total:,} bars) · trades {len(engine.trades)}")
                    await asyncio.sleep(0)
            finally:
                await host.stop()
            result = engine.finish()
            seconds = time.monotonic() - started
            tz = float(o["tz_offset"])
            tzs = int(tz * 3_600_000)
            meta = {"strategy": st.name, "version": ver.version, "account": f"{acc.name} ({acc.environment})", "symbols": req.symbols,
                    "timeframe": tf, "date_from": self._d(req.date_from_ms, tzs), "date_to": self._d(req.date_to_ms - 1, tzs),
                    "tz_label": f"UTC{tz:+g}", "bars": len(items), "seconds": seconds, "params": params,
                    "spreads": {n: s.spread_pips for n, s in specs.items()}}
            await status("📝 Building reports…")
            want = o["output"]
            xlsx = await asyncio.to_thread(build_xlsx, result, meta) if want in ("both", "xlsx") else None
            csv_b = build_csv(result, meta) if want in ("both", "csv") else None
            text = build_summary_text(result, meta)
            await self._finish_row(run_id, "DONE", len(items), self._brief(result), None)
            safe = re.sub(r"[^A-Za-z0-9_-]+", "_", st.name)
            base_name = f"backtest_{safe}_{tf}_{meta['date_from']}_{meta['date_to']}"
            return BtOutcome(result, meta, text, xlsx, csv_b, run_id, base_name)
        except BacktestCancelled:
            await self._finish_row(run_id, "CANCELLED", 0, None, "cancelled by user")
            raise
        except Exception as exc:
            await self._finish_row(run_id, "FAILED", 0, None, f"{type(exc).__name__}: {str(exc)[:500]}")
            raise

    # ---- helpers -----------------------------------------------------------------------------------------------------------
    @staticmethod
    def _bar(b: Any) -> dict[str, Any]:
        return {"t": b.ts_ms, "o": b.open, "h": b.high, "l": b.low, "c": b.close, "v": b.volume}

    @staticmethod
    def _d(ms: int, tz_ms: int) -> str:
        return datetime.fromtimestamp((ms + tz_ms) / 1000, timezone.utc).strftime("%Y-%m-%d")

    async def _conversion(self, account_id: str, quote: str, deposit: str, from_ms: int, to_ms: int, tf: str) -> ConvSeries | None:
        """quote-currency -> deposit-currency rate history (direct pair or inverse). None if the broker has no such pair."""
        sess = self.ctx.gateway.session(account_id)
        conv_tf = tf if TF_SECONDS[tf] >= 3600 else "H1"
        for key, inverse in ((f"{quote}{deposit}", False), (f"{deposit}{quote}", True)):
            info = sess.symbols_by_name.get(key)
            if info is None:
                continue
            bars = await self.ctx.gateway.get_bars_range(account_id, info.symbol_id, conv_tf, from_ms, to_ms)
            if bars:
                return ConvSeries([b.ts_ms for b in bars], [(1.0 / b.close) if inverse and b.close else b.close for b in bars])
        return None

    async def _create_row(self, req: BtRequest, version_id: str, o: dict[str, Any]) -> str:
        async with self.ctx.db.session() as s:
            row = BacktestRun(user_id=req.user_id, strategy_id=req.strategy_id, strategy_version_id=version_id, account_id=req.account_id,
                              symbols=req.symbols, timeframe=req.timeframe,
                              date_from=datetime.fromtimestamp(req.date_from_ms / 1000, timezone.utc),
                              date_to=datetime.fromtimestamp(req.date_to_ms / 1000, timezone.utc), config={"options": o, "params": req.params})
            s.add(row)
            await s.flush()
            return row.id

    async def _finish_row(self, run_id: str, status: str, bars: int, summary: dict | None, error: str | None) -> None:
        try:
            async with self.ctx.db.session() as s:
                row = await s.get(BacktestRun, run_id)
                if row:
                    row.status, row.bars_processed, row.summary, row.error, row.finished_at = status, bars, summary, error, utcnow()
        except Exception as exc:
            log.error("backtest row update failed: %s", type(exc).__name__)

    @staticmethod
    def _brief(r: BtResult) -> dict[str, Any]:
        s = r.stats
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()
                if k in ("net_profit", "return_pct", "total_trades", "win_rate", "profit_factor", "max_drawdown_pct", "final_balance")}

    async def recent(self, user_id: int | None = None, limit: int = 6) -> list[BacktestRun]:
        from sqlalchemy import select
        async with self.ctx.db.session() as s:
            q = select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(limit)
            if user_id:
                q = q.where(BacktestRun.user_id == user_id)
            return list((await s.execute(q)).scalars())
